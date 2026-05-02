from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

try:
    from ._common import EXIT_FAIL, EXIT_OK, fail, info, ok, warn
except ImportError:
    from _common import EXIT_FAIL, EXIT_OK, fail, info, ok, warn  # type: ignore[no-redef]


class GAParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        fail(f"Argument error: {message}")
        raise ValueError(message)


@dataclass
class Finding:
    severity: str
    title: str
    detail: str


def _safe_json_loads(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_json_layers(value: Any, max_depth: int = 4) -> Any:
    cur = value
    for _ in range(max_depth):
        if isinstance(cur, str):
            parsed = _safe_json_loads(cur.strip())
            if parsed is None:
                return cur
            cur = parsed
            continue
        return cur
    return cur


def _parse_input_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Input file is empty.")

    direct = _safe_json_loads(stripped)
    if direct is not None:
        return _parse_json_layers(direct)

    candidates: list[str] = [stripped]
    for left, right in (("{", "}"), ("[", "]")):
        start = stripped.find(left)
        end = stripped.rfind(right)
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        parsed = _safe_json_loads(candidate)
        if parsed is not None:
            return _parse_json_layers(parsed)

    raise ValueError("Cannot parse input as JSON or escaped JSON.")


def _stringify(value: Any) -> str:
    if value is None:
        return "<null>"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _find_first_key(obj: Any, keys: set[str]) -> Any | None:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj:
                return obj[key]
        for value in obj.values():
            found = _find_first_key(value, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_first_key(item, keys)
            if found is not None:
                return found
    return None


def _collect_messages(root: Any) -> list[dict[str, Any]]:
    if isinstance(root, dict):
        payload = root.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            return [m for m in payload["messages"] if isinstance(m, dict)]
        if isinstance(root.get("messages"), list):
            return [m for m in root["messages"] if isinstance(m, dict)]
    found = _find_first_key(root, {"messages"})
    if isinstance(found, list):
        return [m for m in found if isinstance(m, dict)]
    return []


def _has_text_content(content: Any) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str) and item.strip():
                return True
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    return True
                if item.get("type") == "input_text":
                    value = item.get("input_text")
                    if isinstance(value, str) and value.strip():
                        return True
    return False


def _is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    return True


def _collect_payload_issues(messages: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    tool_calls_from_assistant: set[str] = set()
    tool_outputs: set[str] = set()

    if not messages:
        issues.append("payload.messages is empty or missing.")
        return issues

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            issues.append(f"messages[{idx}].role is invalid: {role!r}")

        content = msg.get("content")
        tool_calls = msg.get("tool_calls")
        reasoning_content = msg.get("reasoning_content")

        if role in {"system", "user"} and content is None:
            issues.append(f"messages[{idx}] ({role}) has null content.")

        if role == "assistant":
            has_any = _has_text_content(content) or _is_nonempty(reasoning_content) or _is_nonempty(tool_calls)
            if not has_any:
                issues.append(
                    f"messages[{idx}] assistant must have content/reasoning_content/tool_calls."
                )

            if isinstance(tool_calls, list):
                for t_idx, call in enumerate(tool_calls):
                    if not isinstance(call, dict):
                        issues.append(f"messages[{idx}].tool_calls[{t_idx}] is not object.")
                        continue
                    call_id = call.get("id")
                    call_type = call.get("type")
                    fn = call.get("function")
                    if not isinstance(call_id, str) or not call_id.strip():
                        issues.append(f"messages[{idx}].tool_calls[{t_idx}] missing id.")
                    else:
                        tool_calls_from_assistant.add(call_id)
                    if call_type != "function":
                        issues.append(f"messages[{idx}].tool_calls[{t_idx}] type should be 'function'.")
                    if not isinstance(fn, dict):
                        issues.append(f"messages[{idx}].tool_calls[{t_idx}].function missing.")
                    else:
                        if not isinstance(fn.get("name"), str) or not fn.get("name"):
                            issues.append(f"messages[{idx}].tool_calls[{t_idx}].function.name missing.")
                        args = fn.get("arguments")
                        if not isinstance(args, str):
                            issues.append(
                                f"messages[{idx}].tool_calls[{t_idx}].function.arguments should be string."
                            )

        if role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                issues.append(f"messages[{idx}] tool message missing tool_call_id.")
            else:
                tool_outputs.add(tool_call_id)
            if content is None:
                issues.append(f"messages[{idx}] tool message has null content.")

    missing_outputs = sorted(tool_calls_from_assistant - tool_outputs)
    if missing_outputs:
        issues.append("missing tool outputs for tool_call_id(s): " + ", ".join(missing_outputs))

    return issues


def _parse_maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        parsed = _safe_json_loads(value.strip())
        if parsed is not None:
            return _parse_json_layers(parsed)
    return value


def _build_enriched_root(root: Any) -> Any:
    if not isinstance(root, dict):
        return root
    out = dict(root)
    for field in ("response_body", "body", "response"):
        parsed = _parse_maybe_json(out.get(field))
        if parsed is not out.get(field):
            out[f"_{field}_json"] = parsed
    return out


def _extract_error_payload(root: Any) -> dict[str, Any]:
    if not isinstance(root, dict):
        return {}

    candidates: list[Any] = []
    for key in ("_response_body_json", "_body_json", "_response_json"):
        candidates.append(root.get(key))
    candidates.append(root.get("error"))
    candidates.append(root)

    for item in candidates:
        if not isinstance(item, dict):
            continue
        err = item.get("error")
        if isinstance(err, dict):
            return err
        if any(k in item for k in ("message", "type", "param", "code", "details", "errors")):
            return item
    return {}


def _build_findings(root: Any, status: int | None, message_text: str, err_type: str) -> list[Finding]:
    findings: list[Finding] = []
    lowered_message = message_text.lower()
    lowered_type = err_type.lower()
    messages = _collect_messages(root)

    marker = "must have content/reasoning_content or tool_calls"
    if marker in lowered_message:
        bad_idx: list[int] = []
        for idx, msg in enumerate(messages):
            if msg.get("role") != "assistant":
                continue
            has_any = _has_text_content(msg.get("content")) or _is_nonempty(msg.get("reasoning_content")) or _is_nonempty(msg.get("tool_calls"))
            if not has_any:
                bad_idx.append(idx)
        if bad_idx:
            findings.append(
                Finding(
                    "HIGH",
                    "Assistant message payload is invalid",
                    f"assistant message index(es) with empty content/reasoning/tool_calls: {bad_idx}",
                )
            )
        else:
            findings.append(
                Finding(
                    "MEDIUM",
                    "Assistant message likely invalid",
                    "Server reports missing content/reasoning_content/tool_calls, but local index was not pinpointed.",
                )
            )

    no_tool_output_marker = "no tool output found for function call"
    if no_tool_output_marker in lowered_message:
        issues = _collect_payload_issues(messages)
        missing = [x for x in issues if x.startswith("missing tool outputs for tool_call_id")]
        detail = missing[0] if missing else "tool_call was emitted by assistant but corresponding tool message is missing."
        findings.append(Finding("HIGH", "Missing tool output", detail))

    if "invalid_request_error" in lowered_type or "invalid_request_error" in lowered_message:
        issues = _collect_payload_issues(messages)
        if issues:
            findings.append(
                Finding(
                    "HIGH",
                    "Request schema check failed",
                    "; ".join(issues),
                )
            )
        else:
            findings.append(
                Finding(
                    "MEDIUM",
                    "invalid_request_error returned by API",
                    "Inspect message schema, tool call flow, and null fields carefully.",
                )
            )

    if status in {401, 403}:
        findings.append(
            Finding(
                "HIGH",
                f"HTTP {status} auth/permission issue",
                "Check API key, endpoint allowlist, account permission, organization/project scope.",
            )
        )
    elif status == 429:
        findings.append(
            Finding(
                "MEDIUM",
                "HTTP 429 rate limit",
                "Apply retry with exponential backoff + jitter, and reduce request burst/concurrency.",
            )
        )
    elif status is not None and status >= 500:
        findings.append(
            Finding(
                "MEDIUM",
                f"HTTP {status} server-side error",
                "Retry with bounded backoff. Capture request id/time window for provider support if persistent.",
            )
        )

    return findings


def _overall_risk(findings: list[Finding], status: int | None) -> str:
    severity_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    max_rank = 1
    for item in findings:
        max_rank = max(max_rank, severity_rank.get(item.severity, 1))
    if status is not None and status >= 500:
        max_rank = max(max_rank, 2)
    if status in {401, 403, 422}:
        max_rank = max(max_rank, 3)
    for label, rank in severity_rank.items():
        if rank == max_rank:
            return label
    return "LOW"


def _print_fix_suggestions(findings: list[Finding], status: int | None) -> None:
    suggestions: list[str] = []
    text_blob = " ".join([f"{f.title} {f.detail}" for f in findings]).lower()

    if (
        "missing tool outputs for tool_call_id" in text_blob
        or "missing tool output" in text_blob
        or "no tool output found" in text_blob
    ):
        suggestions.append("Ensure each assistant tool_call is followed by a tool role message with matching tool_call_id.")
    if "assistant message payload is invalid" in text_blob or "must have content/reasoning_content/tool_calls" in text_blob:
        suggestions.append("For assistant messages, provide non-empty content or tool_calls (or reasoning_content where supported).")
    if "request schema check failed" in text_blob or "invalid_request_error" in text_blob:
        suggestions.append("Validate all messages: role is valid, required fields non-null, tool message includes tool_call_id.")
    if status in {401, 403}:
        suggestions.append("Rotate/verify API key and confirm model + project permission for this endpoint.")
    if status == 429:
        suggestions.append("Add retry with exponential backoff+jitter and throttle concurrency.")
    if status is not None and status >= 500:
        suggestions.append("Retry idempotent requests and log request id + timestamp for escalation.")

    if not suggestions:
        suggestions.append("Capture full request/response JSON and compare with official API schema constraints.")

    info("Fix suggestions:")
    for idx, item in enumerate(suggestions, start=1):
        print(f"  {idx}. {item}")


def cmd_analyze(args: argparse.Namespace) -> int:
    target = Path(args.file).expanduser().resolve()
    if not target.exists() or not target.is_file():
        fail(f"File not found: {target}")
        return EXIT_FAIL

    try:
        raw_text = target.read_text(encoding="utf-8", errors="replace")
        parsed = _parse_input_json(raw_text)
    except Exception as exc:
        fail(f"Unable to parse {target.name}: {exc}")
        return EXIT_FAIL

    enriched = _build_enriched_root(parsed)
    err_payload = _extract_error_payload(enriched)

    status_val = _find_first_key(enriched, {"status", "status_code", "http_code", "http_status"})
    try:
        status = int(status_val) if status_val is not None else None
    except Exception:
        status = None

    code = err_payload.get("code")
    if code is None:
        code = _find_first_key(enriched, {"code", "error_code"})
    err_type = _stringify(err_payload.get("type"))
    if err_type == "<null>":
        err_type = _stringify(_find_first_key(enriched, {"error_type", "type"}))
    message = _stringify(err_payload.get("message"))
    if message == "<null>":
        message = _stringify(_find_first_key(enriched, {"error_message", "message"}))
    param = _stringify(err_payload.get("param"))
    if param == "<null>":
        param = _stringify(_find_first_key(enriched, {"param"}))
    details = err_payload.get("details")
    if details is None:
        details = err_payload.get("errors")
    if details is None:
        details = _find_first_key(enriched, {"details", "detail", "errors"})

    info(f"Analyzed file: {target}")
    print("Extracted fields:")
    print(f"  status : {_stringify(status)}")
    print(f"  code   : {_stringify(code)}")
    print(f"  type   : {err_type}")
    print(f"  param  : {param}")
    print(f"  message: {message}")
    print(f"  details: {_stringify(details)}")

    findings = _build_findings(enriched, status, message, err_type)
    risk = _overall_risk(findings, status)

    print("")
    print(f"Risk: {risk}")
    if findings:
        info("Findings:")
        for item in findings:
            print(f"  - [{item.severity}] {item.title}: {item.detail}")
    else:
        info("Findings: no direct rule hit; inspect raw payload and response context.")

    _print_fix_suggestions(findings, status)
    return EXIT_OK


def _scan_recent_files(base_dir: Path) -> list[Path]:
    patterns = ("*400*.json", "*error*.log")
    results: dict[str, Path] = {}

    for root, _dirs, files in os.walk(base_dir):
        root_path = Path(root)
        for name in files:
            lowered = name.lower()
            matched = (
                lowered == "debug_400_dump.json"
                or fnmatch(lowered, patterns[0])
                or fnmatch(lowered, patterns[1])
            )
            if not matched:
                continue
            full_path = (root_path / name).resolve()
            results[str(full_path).lower()] = full_path
    return list(results.values())


def cmd_recent(args: argparse.Namespace) -> int:
    base = Path(args.dir).expanduser().resolve() if args.dir else Path.cwd().resolve()
    if not base.exists() or not base.is_dir():
        fail(f"Directory not found: {base}")
        return EXIT_FAIL

    limit = max(1, int(args.limit))
    files = _scan_recent_files(base)

    if not files:
        warn(f"No matching files found under: {base}")
        return EXIT_OK

    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    top_n = files[:limit]

    info(f"Recent diagnostic files under: {base}")
    print(f"{'MTIME':<20} {'SIZE(B)':<10} PATH")
    for path in top_n:
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{mtime:<20} {stat.st_size:<10} {path}")
    ok(f"Listed {len(top_n)} of {len(files)} matching file(s).")
    return EXIT_OK


def _hint_map() -> dict[int, list[str]]:
    return {
        400: [
            "Validate request schema: required fields, non-null content, correct role order.",
            "If using tool calls: assistant tool_calls must be followed by tool message with matching tool_call_id.",
            "Check message content types/shape match endpoint expectations.",
        ],
        401: [
            "Verify API key/token validity and header format.",
            "Confirm endpoint supports the credential scope (project/org/model).",
            "Check system clock skew and revoked credentials.",
        ],
        403: [
            "Credential is recognized but lacks permission.",
            "Check model access, organization/project policy, endpoint restrictions.",
            "Confirm IP/network allowlist and security policies.",
        ],
        404: [
            "Check endpoint URL/version and resource identifiers.",
            "Confirm model/resource exists in your account/region.",
        ],
        408: [
            "Client timeout: increase timeout and retry with backoff.",
            "Inspect upstream latency and payload size.",
        ],
        409: [
            "Resolve request conflict/idempotency collision.",
            "Use stable idempotency keys for retried writes.",
        ],
        413: [
            "Payload too large: reduce input size or chunk requests.",
            "Compress or trim message history/context.",
        ],
        415: [
            "Unsupported media type: verify Content-Type and payload format.",
        ],
        422: [
            "Semantically invalid request: inspect field values and model constraints.",
            "Validate enums/parameter ranges and tool schema compatibility.",
        ],
        429: [
            "Rate limit exceeded: apply exponential backoff + jitter.",
            "Reduce concurrency/QPS and add client-side queueing.",
            "Review per-model and per-project quotas.",
        ],
        500: [
            "Server error: retry idempotent requests with bounded backoff.",
            "Log request id, timestamp, and minimal repro for escalation.",
        ],
        502: [
            "Bad gateway: retry and monitor upstream/provider status.",
        ],
        503: [
            "Service unavailable: retry later with gradual backoff.",
            "Consider fallback model/region if supported.",
        ],
        504: [
            "Gateway timeout: reduce payload size and raise client timeout budget.",
            "Retry with jitter and monitor network path stability.",
        ],
    }


def cmd_hint(args: argparse.Namespace) -> int:
    code = int(args.code)
    hints = _hint_map().get(code)
    info(f"HTTP {code} checklist")
    if hints is None:
        hints = [
            "Capture request + response body (redact secrets).",
            "Classify by 4xx(client)/5xx(server) and apply matching retry or schema checks.",
            "Record timestamp/request id for reproducible support escalation.",
        ]
    for idx, item in enumerate(hints, start=1):
        print(f"  {idx}. {item}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = GAParser(description="Diagnose API error payloads and recent logs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_analyze = subparsers.add_parser("analyze", help="Analyze one JSON/escaped-JSON diagnostic file")
    p_analyze.add_argument("--file", required=True, help="Input file path")
    p_analyze.set_defaults(func=cmd_analyze)

    p_recent = subparsers.add_parser("recent", help="Find recent *400*.json/*error*.log files")
    p_recent.add_argument("--dir", default=None, help="Base directory (default: current working directory)")
    p_recent.add_argument("--limit", type=int, default=10, help="Max files to show")
    p_recent.set_defaults(func=cmd_recent)

    p_hint = subparsers.add_parser("hint", help="Show quick checklist by HTTP status code")
    p_hint.add_argument("--code", required=True, type=int, help="HTTP status code")
    p_hint.set_defaults(func=cmd_hint)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.func(args))
    except ValueError:
        return EXIT_FAIL
    except KeyboardInterrupt:
        warn("Interrupted by user.")
        return EXIT_FAIL
    except Exception as exc:
        fail(str(exc))
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
