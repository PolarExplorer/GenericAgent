"""Default-off observation compression shadow hook for GA tool results.

This module intentionally keeps the runtime contract conservative:
- disabled by default;
- never mutates the tool result content returned to the LLM;
- failures are swallowed and recorded only when the optional shadow log is enabled.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import traceback
from pathlib import Path
from typing import Any, Dict

_ENV_ENABLED = "GA_OBSERVATION_COMPRESSION_SHADOW"
_ENV_LOG_PATH = "GA_OBSERVATION_COMPRESSION_LOG"
_DEFAULT_LOG_PATH = Path("temp/observation_compression_shadow_runtime.jsonl")
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^\s,'\"]{6,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
]
_RISK_WORDS = (
    "error", "failed", "failure", "traceback", "exception", "permission", "denied",
    "unsafe", "delete", "overwrite", "secret", "token", "password", "api_key",
    "medium risk", "high risk", "finding", "limitation", "violation",
)
_HARD_DENY_TOOLS = {"ask_user"}
_HARD_DENY_SNIPPETS = (
    "llm1", "code review", "review verdict", "```diff", "diff --git", "@@",
    "begin patch", "exact patch", "apply_patch", "file]", "line-numbered",
    "bearer ", "-----begin", "private key",
)
_ALLOWLIST_TOOLS = {"code_run", "web_scan"}
_MAX_CANDIDATE_CHARS = 1800


def observation_compression_enabled() -> bool:
    """Return True only when shadow compression is explicitly enabled."""
    return os.environ.get(_ENV_ENABLED, "").strip().lower() in {"1", "true", "yes", "on"}


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def _redact(text: str) -> tuple[str, bool]:
    found = False
    out = text
    for pat in _SECRET_PATTERNS:
        if pat.search(out):
            found = True
            out = pat.sub("[REDACTED_SECRET]", out)
    return out, found


def _risk_signals(text: str) -> list[str]:
    low = text.lower()
    return sorted({word for word in _RISK_WORDS if word in low})


def _evidence_lines(redacted_text: str, limit: int = 8) -> list[str]:
    lines = [line.strip() for line in redacted_text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
    if len(lines) == 1 and len(lines[0]) > 500:
        lines = [lines[0][i:i + 240] for i in range(0, len(lines[0]), 240)]
    scored = []
    for idx, line in enumerate(lines):
        low = line.lower()
        score = 1 + (8 if any(word in low for word in _RISK_WORDS) else 0)
        score += 4 if any(token in low for token in ("status", "exit_code", "return_code", "success", "pass")) else 0
        scored.append((-score, idx, line[:360]))
    return [line for _, _, line in sorted(scored)[:limit]]


def _is_hard_deny(content: str, tool_name: str, secret_detected: bool) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    low = content.lower()
    if tool_name in _HARD_DENY_TOOLS:
        reasons.append("deny_tool")
    if secret_detected:
        reasons.append("secret_raw")
    for marker in _HARD_DENY_SNIPPETS:
        if marker in low:
            reasons.append(f"deny_marker:{marker}")
    if any(word in low for word in ("permission", "denied", "unsafe")):
        reasons.append("permission_or_safety_sensitive")
    return bool(reasons), sorted(set(reasons))


def _build_candidate_text(redacted_text: str, evidence: list[str], tool_name: str, raw_chars: int) -> str:
    header = f"[shadow compression candidate: tool={tool_name}, raw_chars={raw_chars}]"
    body = "\n".join(f"- {line}" for line in evidence)
    candidate = f"{header}\n{body}" if body else header
    if len(candidate) > _MAX_CANDIDATE_CHARS:
        candidate = candidate[:_MAX_CANDIDATE_CHARS - 32] + "\n...[truncated_candidate]"
    return candidate


def build_compression_candidate_evaluation(content: str, *, tool_name: str = "generic", source_ref: str = "agent_loop") -> Dict[str, Any]:
    """Build a shadow-only candidate compression evaluation without changing content.

    The result is a sidecar for measurement only. It must never be interpreted as
    permission to replace the prompt-visible observation in this phase.
    """
    redacted, secret = _redact(content)
    risks = _risk_signals(content)
    evidence = _evidence_lines(redacted)
    hard_deny, deny_reasons = _is_hard_deny(content, tool_name, secret)
    allowlisted = tool_name in _ALLOWLIST_TOOLS
    raw_chars = len(content)
    candidate = _build_candidate_text(redacted, evidence, tool_name, raw_chars)
    candidate_sha = _sha12(candidate)
    ratio = round(len(candidate) / raw_chars, 3) if raw_chars else 1.0
    boilerplate_only = allowlisted and not hard_deny and raw_chars >= 1200 and ratio <= 0.7
    loss_class = "boilerplate-only" if boilerplate_only else "lossy-risk"
    return {
        "schema": "ga.observation_compression_candidate.v1",
        "shadow_only": True,
        "returned_observation_identity": "unchanged_by_contract",
        "tool": tool_name,
        "source_ref": source_ref,
        "candidate_preview": candidate,
        "candidate_sha256_12": candidate_sha,
        "candidate_chars": len(candidate),
        "candidate_raw_ratio": ratio,
        "allowlisted_tool": allowlisted,
        "hard_deny": hard_deny,
        "deny_reasons": deny_reasons,
        "loss_class": loss_class,
        "gate": {
            "sensitive_safe": not secret,
            "traceable": bool(source_ref) and raw_chars > 0,
            "decision_equivalent": boilerplate_only,
            "eligible_for_future_replacement": False,
        },
        "notes": "P16 evaluation only; never replaces LLM-visible observation.",
        "risk_signals": risks,
    }


def build_observation_shadow_record(content: str, *, tool_name: str = "generic", tool_use_id: str = "", source_ref: str = "agent_loop") -> Dict[str, Any]:
    """Build a compact, redacted side-channel record for a tool result string."""
    redacted, secret = _redact(content)
    evidence = _evidence_lines(redacted)
    row = {
        "schema": "ga.observation_shadow.v1",
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "shadow_only": True,
        "returned_observation_identity": "unchanged_by_contract",
        "source_ref": source_ref,
        "tool": tool_name,
        "tool_use_id": tool_use_id,
        "raw_sha256_12": _sha12(content),
        "raw_chars": len(content),
        "raw_preview": redacted[:1000],
        "raw_preview_redacted": secret,
        "secret_detected_raw": secret,
        "risk_signals": _risk_signals(content),
        "evidence_lines": evidence,
        "decision_gate": {
            "sensitive_safe": (not secret) or ("[REDACTED_SECRET]" in json.dumps(evidence, ensure_ascii=False)),
            "traceable": bool(source_ref) and bool(_sha12(content)),
            "decision_equivalent": bool(evidence),
        },
    }
    row["compression_candidate"] = build_compression_candidate_evaluation(content, tool_name=tool_name, source_ref=source_ref)
    return row


def _append_jsonl(row: Dict[str, Any]) -> None:
    path = Path(os.environ.get(_ENV_LOG_PATH, "") or _DEFAULT_LOG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def observe_tool_result_content(content: str, *, tool_name: str = "generic", tool_use_id: str = "", source_ref: str = "agent_loop") -> str:
    """Shadow-log a compressed observation record, returning content unchanged.

    Default-off and fail-open by design: this helper must not affect the main LLM-visible
    tool_result payload unless future phases explicitly change the contract.
    """
    if not observation_compression_enabled():
        return content
    try:
        _append_jsonl(build_observation_shadow_record(content, tool_name=tool_name, tool_use_id=tool_use_id, source_ref=source_ref))
    except Exception:
        try:
            _append_jsonl({
                "schema": "ga.observation_shadow_error.v1",
                "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
                "shadow_only": True,
                "tool": tool_name,
                "tool_use_id": tool_use_id,
                "source_ref": source_ref,
                "shadow_error": traceback.format_exc().splitlines()[-6:],
            })
        except Exception:
            pass
    return content


__all__ = [
    "build_observation_shadow_record",
    "build_compression_candidate_evaluation",
    "observe_tool_result_content",
    "observation_compression_enabled",
]
