"""
ga_coding_gate.py
Created: 2026-05-02
Version: 1.0.0

Scripts-level coding gate for Codex-first workflow.
Modes:
- audit: log only, no prompt injection
- warn: warn on high-confidence coding without Codex invocation
- block: block high-confidence coding without Codex invocation or valid skip reason
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple


_CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cs",
    ".cpp", ".c", ".h", ".hpp", ".php", ".rb", ".swift", ".kt", ".sql",
    ".sh", ".ps1", ".bat", ".lua", ".zig", ".vue", ".svelte",
})


_EXEMPT_PATH_RE = re.compile(
    r"(?i)"
    r"(memory[/\\])"
    r"|(\.md$)"
    r"|(\.txt$)"
    r"|(\.json$)"
    r"|(\.yaml$|\.yml$)"
    r"|(temp[/\\])"
    r"|(Desktop[/\\])"
    r"|(issues?[/\\])"
)


_GATE_INTERNAL_RE = re.compile(
    r"(?i)"
    r"(ga_coding_gate\.py)"
    r"|(ga_dispatch_gate\.py)"
    r"|(ga_constraint_engine\.py)"
    r"|(coding_gate_events)"
    r"|(ga\.py$)"
    r"|(launch\.pyw$)"
    r"|(ga_watchdog)"
    r"|(script_guard)"
)


_CODEX_RE = re.compile(
    r"(?i)"
    r"(codex\s)"
    r"|(codex\.exe)"
    r"|(codex\s+--)"
    r"|(npx\s+codex)"
    r"|(codex-cli)"
    r"|(\bcodex\b)"
)


_USER_OVERRIDE_RE = re.compile(
    r"(?i)"
    r"(不要.*codex)"
    r"|(你直接改)"
    r"|(直接\s*patch)"
    r"|(不用\s*codex)"
    r"|(GA\s*直接)"
    r"|(user_direct)"
)


_WARN_PROMPT = (
    "\n\n⚠️ [CODING GATE] 检测到你正在直接修改代码文件 `{path}`，但没有 Codex CLI 调用记录。"
    "\n根据 tool_dispatch_sop，编码任务应优先使用 Codex CLI（成本降低约 40%）。"
    "\n请选择："
    "\n1. 调用 Codex CLI 完成此编码任务"
    "\n2. 记录跳过理由（micro_patch / probing / user_direct / codex_unavailable / codex_failed / emergency_fix / memory_or_doc）"
    "\n判定原因: {reason}"
)


_BLOCK_PROMPT = (
    "\n\n⛔ [CODING GATE] 编码门禁已激活。你正在直接修改代码文件 `{path}`，"
    "但没有 Codex CLI 调用记录，也没有合法跳过理由。"
    "\n此操作已被阻断。你必须："
    "\n1. 调用 Codex CLI 完成编码 → gate.record_codex_invoked()"
    "\n2. 或记录跳过理由 → gate.record_skip_reason('理由')"
    "\n3. 或获取用户授权 → gate.record_user_override()"
    "\n4. 或开启紧急旁路 → gate.set_emergency_bypass(True)"
    "\n合法跳过理由: micro_patch | probing | user_direct | codex_unavailable | codex_failed | emergency_fix | memory_or_doc | gate_internal | non_coding"
    "\n判定原因: {reason}"
)


VALID_SKIP_REASONS = frozenset({
    "micro_patch",
    "probing",
    "user_direct",
    "memory_or_doc",
    "codex_unavailable",
    "codex_failed",
    "emergency_fix",
    "gate_internal",
    "non_coding",
})


def _is_code_file(path: str) -> bool:
    """Return True when file suffix belongs to known code extensions."""
    return Path(path).suffix.lower() in _CODE_EXTENSIONS


def _is_exempt_path(path: str) -> bool:
    return bool(_EXEMPT_PATH_RE.search(path))


def _is_gate_internal(path: str) -> bool:
    return bool(_GATE_INTERNAL_RE.search(path))


def _is_memory_py(path: str) -> bool:
    normalized = str(path).replace("\\", "/")
    return "/memory/" in f"/{normalized.lower()}" and normalized.lower().endswith(".py")


def _is_micro_patch(tool_name: str, tool_args: dict) -> bool:
    """<=20 lines and file_patch is considered micro patch."""
    if tool_name == "file_patch":
        new_content = tool_args.get("new_content", "") or ""
        return new_content.count("\n") <= 20
    if tool_name == "file_write":
        _ = tool_args.get("_content_length", 0)
        mode = tool_args.get("mode", "overwrite")
        if mode in ("append", "prepend"):
            return True
        return False
    return False


def _is_codex_invocation(script: str) -> bool:
    """Detect whether a code_run script calls Codex CLI."""
    return bool(_CODEX_RE.search(script or ""))


def _detect_user_override(text: str) -> bool:
    return bool(_USER_OVERRIDE_RE.search(text or ""))


class CodingGate:
    """Graduated coding gate for enforcing Codex-first code modifications."""

    def __init__(self, mode: str = "audit", log_dir: str = "temp"):
        """
        Args:
            mode: "audit" | "warn" | "block"
            log_dir: event log directory
        """
        if mode not in ("audit", "warn", "block"):
            raise ValueError("mode must be one of: audit, warn, block")

        self.mode = mode
        self.codex_invoked = False
        self.skip_reason: Optional[str] = None
        self.user_direct_override = False
        self.emergency_bypass = False
        self._log_path = Path(log_dir) / "coding_gate_events.jsonl"

    def check_tool(self, tool_name: str, tool_args: dict, assistant_text: str = "") -> Tuple[str, str]:
        """
        Pre-tool guard check.

        Returns:
            (decision, message)
        """
        tool_args = tool_args or {}
        path = tool_args.get("path", "") or tool_args.get("cwd", "") or ""
        script = tool_args.get("script", "") or ""
        _ = tool_args.get("new_content", "") or ""

        if tool_name not in ("file_patch", "file_write"):
            if tool_name == "code_run":
                if _is_codex_invocation(script):
                    self.record_codex_invoked()
                    return self._decide("ALLOW", "codex_invocation_detected", tool_name, path)
                if _detect_user_override(assistant_text):
                    self.record_user_override()
            return self._decide("ALLOW", "not_a_write_tool", tool_name, path)

        if self.emergency_bypass:
            return self._decide("ALLOW", "emergency_bypass", tool_name, path)

        if self.user_direct_override:
            return self._decide("ALLOW", "user_direct_override", tool_name, path)

        if self.codex_invoked:
            return self._decide("ALLOW", "codex_already_invoked", tool_name, path)

        if self.skip_reason in VALID_SKIP_REASONS:
            return self._decide("ALLOW", f"skip:{self.skip_reason}", tool_name, path)

        if _is_gate_internal(path):
            return self._decide("ALLOW", "gate_internal_file", tool_name, path)

        if _is_exempt_path(path):
            return self._decide("ALLOW", "exempt_path", tool_name, path)

        if not _is_code_file(path):
            return self._decide("ALLOW", "non_code_file", tool_name, path)

        if _is_micro_patch(tool_name, tool_args):
            if self.mode == "block":
                return self._decide("WARN", "micro_patch_no_codex", tool_name, path)
            return self._decide("ALLOW", "micro_patch", tool_name, path)

        if self.mode == "audit":
            return self._decide("ALLOW", "audit_mode_passthrough", tool_name, path)
        if self.mode == "warn":
            return self._decide("WARN", "coding_without_codex", tool_name, path)
        return self._decide("BLOCK", "coding_without_codex_blocked", tool_name, path)

    def on_turn_end(self, tool_calls: list, response_text: str = "") -> Tuple[str, str]:
        """
        Post-turn audit.

        Returns:
            (decision, prompt_injection)
        """
        for tc in tool_calls:
            if tc.get("tool_name") == "code_run":
                script = tc.get("args", {}).get("script", "") or ""
                if _is_codex_invocation(script):
                    self.record_codex_invoked()

        if _is_codex_invocation(response_text):
            self.record_codex_invoked()

        if _detect_user_override(response_text):
            self.record_user_override()

        code_writes = []
        for tc in tool_calls:
            tn = tc.get("tool_name", "")
            args = tc.get("args", {}) or {}
            path = args.get("path", "") or ""
            if tn in ("file_patch", "file_write") and _is_code_file(path) and not _is_exempt_path(path):
                code_writes.append(path)

        if not code_writes:
            return "ALLOW", ""

        if self.codex_invoked or self.user_direct_override or self.skip_reason:
            return "ALLOW", ""

        paths = ", ".join(code_writes[:3])
        event = {
            "time": datetime.now(timezone.utc).isoformat(),
            "type": "turn_end_audit",
            "mode": self.mode,
            "code_files_modified": code_writes,
            "codex_invoked": False,
            "skip_reason": None,
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass

        if self.mode == "audit":
            return "ALLOW", ""
        if self.mode == "warn":
            return "WARN", _WARN_PROMPT.format(path=paths, reason="turn_end_audit_no_codex")
        return "WARN", _WARN_PROMPT.format(path=paths, reason="turn_end_audit_no_codex")

    def record_codex_invoked(self):
        """Mark that Codex CLI has been invoked in this task cycle."""
        self.codex_invoked = True

    def record_skip_reason(self, reason: str):
        """Record structured reason for skipping Codex."""
        self.skip_reason = reason

    def record_user_override(self):
        """Record explicit user authorization for direct edits."""
        self.user_direct_override = True

    def set_emergency_bypass(self, enabled: bool = True):
        """Enable/disable emergency bypass."""
        self.emergency_bypass = bool(enabled)

    def reset(self):
        """Reset state for a new task/user turn."""
        self.codex_invoked = False
        self.skip_reason = None
        self.user_direct_override = False
        self.emergency_bypass = False

    def get_stats(self) -> dict:
        """Return aggregate stats from coding gate JSONL log."""
        stats = {"total": 0, "ALLOW": 0, "WARN": 0, "BLOCK": 0, "skip_reasons": {}}
        try:
            with open(self._log_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    decision = ev.get("decision", "ALLOW")
                    stats["total"] += 1
                    stats[decision] = stats.get(decision, 0) + 1
                    sr = ev.get("skip_reason")
                    if sr:
                        stats["skip_reasons"][sr] = stats["skip_reasons"].get(sr, 0) + 1
        except FileNotFoundError:
            pass
        return stats

    def _decide(self, decision: str, reason: str, tool_name: str, path: str) -> Tuple[str, str]:
        """Log a decision event and return (decision, prompt_message)."""
        if reason == "exempt_path" and _is_memory_py(path):
            reason = "memory_py_skill"

        event = {
            "time": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "decision": decision,
            "reason": reason,
            "tool": tool_name,
            "path": path,
            "codex_invoked": self.codex_invoked,
            "skip_reason": self.skip_reason,
            "user_override": self.user_direct_override,
            "emergency": self.emergency_bypass,
        }

        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass

        if decision == "ALLOW":
            return "ALLOW", ""
        if decision == "WARN":
            return "WARN", _WARN_PROMPT.format(path=path, reason=reason)
        return "BLOCK", _BLOCK_PROMPT.format(path=path, reason=reason)

    def __repr__(self):
        return (
            f"CodingGate(mode={self.mode!r}, codex_invoked={self.codex_invoked}, "
            f"skip_reason={self.skip_reason!r}, user_direct_override={self.user_direct_override}, "
            f"emergency_bypass={self.emergency_bypass})"
        )
