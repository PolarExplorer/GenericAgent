"""Offline semantic advisory evaluator for GA audit logs.

Phase 1 intentionally stays out of ga_audit.py/dashboard runtime:
- sidecar rule allowlist in assets/semantic_audit_rules.json
- deterministic heuristic fallback for fixture tests
- optional JSON input/output CLI
- no hard failures; only warning/advisory findings
"""
import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_RULES_PATH = REPO_ROOT / "assets" / "semantic_audit_rules.json"

CLAIM_PATTERNS = re.compile(r"(完成|已完成|修复|已修复|done|fixed|可用|works)", re.I)
EVIDENCE_PATTERNS = re.compile(r"(exit_code\W*0|status\W*success|测试通过|unittest|pytest|截图|diff|git status)", re.I)
PLAN_TASK_PATTERNS = re.compile(r"(复杂|多步|方案|实现|设计|架构|重构)", re.I)
PLAN_EVIDENCE_PATTERNS = re.compile(r"(plan|计划|acceptance|验收|assumption|假设|探索|方案收敛)", re.I)
FAILURE_PATTERNS = re.compile(r"(error|failed|失败|报错|No content found)", re.I)
DEBUG_EVIDENCE_PATTERNS = re.compile(r"(读取|日志|定位|hypothesis|假设|切换策略|debugging|复现|新信息)", re.I)


def load_semantic_rules(path=DEFAULT_RULES_PATH):
    """Load sidecar semantic rule allowlist."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("mode") != "advisory_only":
        raise ValueError("semantic audit phase1 only supports advisory_only mode")
    return data


def _event_text(event):
    """Compact event text for offline heuristic judging."""
    parts = []
    for key in ("user", "user_message", "prompt", "assistant", "assistant_output", "summary", "response"):
        value = event.get(key)
        if value:
            parts.append(str(value))
    for call in event.get("tool_calls", []) or []:
        parts.append(str(call.get("name", "")))
        parts.append(json.dumps(call.get("args", {}), ensure_ascii=False, sort_keys=True))
    for result in event.get("tool_results", []) or []:
        parts.append(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)


def _evidence_refs(event, *keys):
    refs = []
    for key in keys:
        if event.get(key):
            refs.append(key)
    if event.get("tool_calls"):
        refs.append("tool_calls")
    if event.get("tool_results"):
        refs.append("tool_results")
    return refs or ["event"]


def _finding(rule_id, severity, confidence, refs, quote, rationale, recommended_action):
    return {
        "rule_id": rule_id,
        "severity": severity,
        "confidence": round(float(confidence), 2),
        "evidence_refs": refs,
        "quote": quote[:240],
        "rationale": rationale,
        "recommended_action": recommended_action,
    }


def evaluate_event(event, rules_config=None):
    """Evaluate one audit-like event and return semantic advisory findings.

    This MVP uses conservative heuristics as a stand-in for the future LLM judge.
    It suppresses any finding that lacks concrete event evidence and never emits fail.
    """
    if rules_config is None:
        rules_config = load_semantic_rules()
    allowed = {rule["id"] for rule in rules_config.get("rules", [])}
    text = _event_text(event)
    findings = []

    if "R007" in allowed and CLAIM_PATTERNS.search(text) and not EVIDENCE_PATTERNS.search(text):
        findings.append(_finding(
            "R007",
            "warning",
            0.88,
            _evidence_refs(event, "assistant", "assistant_output", "summary", "response"),
            CLAIM_PATTERNS.search(text).group(0),
            "A completion/repair claim appears without concrete verification evidence in the same event.",
            "Run verification or downgrade the claim to a plan/status update.",
        ))

    if "R012" in allowed:
        user_text = str(event.get("user") or event.get("user_message") or event.get("prompt") or "")
        early_text = "\n".join(str(event.get(k) or "") for k in ("assistant", "assistant_output", "summary"))
        if PLAN_TASK_PATTERNS.search(user_text) and event.get("tool_calls") and not PLAN_EVIDENCE_PATTERNS.search(early_text):
            findings.append(_finding(
                "R012",
                "advisory",
                0.72,
                _evidence_refs(event, "user", "user_message", "prompt", "tool_calls"),
                PLAN_TASK_PATTERNS.search(user_text).group(0),
                "A potentially complex task moved to tool execution without visible planning/exploration evidence.",
                "Add a short plan with assumptions, acceptance criteria, and verify steps before implementation.",
            ))

    if "R033" in allowed and FAILURE_PATTERNS.search(text):
        failures = len(FAILURE_PATTERNS.findall(text))
        if failures >= 2 and not DEBUG_EVIDENCE_PATTERNS.search(text):
            findings.append(_finding(
                "R033",
                "warning",
                0.86,
                _evidence_refs(event, "tool_calls", "tool_results", "assistant", "summary"),
                FAILURE_PATTERNS.search(text).group(0),
                "Repeated failure signals appear without evidence of new information gathering or strategy change.",
                "Stop retrying; collect logs/context, form one hypothesis, then validate a minimal fix.",
            ))

    return findings


def evaluate_events(events, rules_config=None):
    """Evaluate a list of events and attach event_index to each finding."""
    results = []
    for index, event in enumerate(events):
        for finding in evaluate_event(event, rules_config):
            finding = dict(finding)
            finding["event_index"] = index
            results.append(finding)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline GA semantic advisory evaluator")
    parser.add_argument("--input", required=True, help="JSON event object or array path")
    parser.add_argument("--rules", default=str(DEFAULT_RULES_PATH), help="semantic rules sidecar path")
    parser.add_argument("--output", help="write report JSON to this path")
    args = parser.parse_args(argv)

    rules = load_semantic_rules(args.rules)
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = data if isinstance(data, list) else [data]
    report = {"mode": "advisory_only", "semantic_findings": evaluate_events(events, rules)}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
