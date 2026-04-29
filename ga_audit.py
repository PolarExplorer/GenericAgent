"""GA Audit Module — 零侵入审计 hook，采集每轮事件并检测约束违规。

Usage:
    import ga_audit
    ga_audit.install(agent)  # agent = GeneraticAgent 实例
"""
import hashlib, json, os, re, sys, time, threading, types, uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
_REGISTRY_PATH = _SCRIPT_DIR / "assets" / "constraints_registry.json"
_DASHBOARD_TEMPLATE_PATH = _SCRIPT_DIR / "assets" / "audit_dashboard.html"
_DSL_CONSTRAINTS_PATH = _SCRIPT_DIR / "assets" / "constraints_dsl.json"
_DASHBOARD_DIR = _SCRIPT_DIR / "temp" / "dashboard"
_DASHBOARD_HTML_PATH = _DASHBOARD_DIR / "dashboard.html"
_AUDIT_LOG_PATH = _DASHBOARD_DIR / "audit_log.json"

_registry_cache = None
_registry_mtime = 0
_agent_ref = None
_SUBAGENT_AVAILABLE = None  # None=unknown, True/False after install()
_control_server = None
_dashboard_server = None
_CONTROL_HOST = "127.0.0.1"
_CONTROL_PORT = 8766
_DASHBOARD_PORT = 8765
_BUDGET_SESSION_TASK_ID = None
_BUDGET_SESSION_LAST_TURN = None

# ── Hot-reload infrastructure ──
_SOURCE_PATH = Path(__file__).resolve()
_source_mtime = 0.0  # last known mtime
_reload_lock = threading.Lock()
_RELOAD_PRESERVE = {  # state vars to preserve across reload
    "_agent_ref", "_control_server", "_dashboard_server",
    "_SUBAGENT_AVAILABLE", "_CONSEC_EXEC_HISTORY",
    "_LAST_EVIDENCE_TURN", "_BUDGET_SESSION_TASK_ID",
    "_BUDGET_SESSION_LAST_TURN", "_source_mtime", "_reload_lock",
    "_SOURCE_PATH", "_RELOAD_PRESERVE",
}


def _hot_reload(force=False):
    """Check ga_audit.py mtime; if changed, exec new code and patch functions/classes in-place.

    Preserves: agent ref, HTTP servers, accumulated state.
    Returns: (reloaded: bool, error: str|None)
    """
    global _source_mtime
    try:
        cur_mtime = _SOURCE_PATH.stat().st_mtime
    except OSError:
        return False, "source file not found"
    if not force and cur_mtime == _source_mtime:
        return False, None
    with _reload_lock:
        # Double-check after acquiring lock
        try:
            cur_mtime = _SOURCE_PATH.stat().st_mtime
        except OSError:
            return False, "source file not found"
        if not force and cur_mtime == _source_mtime:
            return False, None
        try:
            src = _SOURCE_PATH.read_text(encoding="utf-8")
            compile(src, str(_SOURCE_PATH), "exec")  # syntax check first
        except Exception as e:
            _source_mtime = cur_mtime  # don't retry same broken version
            return False, f"compile error: {e}"
        # Snapshot state to preserve
        mod = sys.modules.get(__name__)
        preserved = {}
        if mod:
            for k in _RELOAD_PRESERVE:
                if hasattr(mod, k):
                    preserved[k] = getattr(mod, k)
        # Exec into temp namespace
        ns = {"__name__": __name__, "__file__": str(_SOURCE_PATH)}
        try:
            exec(src, ns)
        except Exception as e:
            _source_mtime = cur_mtime
            return False, f"exec error: {e}"
        # Inject preserved state into ns so new functions see them
        # (new functions' __globals__ points to ns, not mod)
        ns.update(preserved)
        # Patch: replace functions and non-preserved globals
        if mod:
            for k, v in ns.items():
                if k.startswith("__"):
                    continue
                if k in _RELOAD_PRESERVE:
                    continue
                if isinstance(v, (types.FunctionType, type)):
                    setattr(mod, k, v)
            # Re-bind the turn_end hook on agent if available
            agent = preserved.get("_agent_ref")
            new_on_turn_end = ns.get("_on_turn_end")
            if agent and new_on_turn_end and hasattr(agent, "_turn_end_hooks"):
                agent._turn_end_hooks["ga_audit"] = _make_trampoline()
            # Restore preserved state
            for k, v in preserved.items():
                setattr(mod, k, v)
            # Sync new handler class to running control server
            new_handler = ns.get("_ControlHandler")
            srv = getattr(mod, "_control_server", None)
            if new_handler and srv:
                srv.RequestHandlerClass = new_handler
        _source_mtime = cur_mtime
        try:
            _DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
            with open(_DASHBOARD_DIR / "audit_error.log", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} HOT-RELOAD OK (mtime={cur_mtime})\n")
        except Exception:
            pass
        return True, None


def _make_trampoline():
    """Return a trampoline function that auto-reloads before calling _on_turn_end."""
    def _trampoline(ctx):
        _hot_reload()  # check & reload if needed (no-op if unchanged)
        mod = sys.modules.get(__name__)
        fn = getattr(mod, "_on_turn_end", None) if mod else None
        if fn and fn is not _trampoline:
            return fn(ctx)
    return _trampoline


def _new_task_id(source="user"):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_source = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(source or "user"))[:24]
    return f"{ts}-{safe_source}-{uuid.uuid4().hex[:8]}"


def _current_task_id(agent=None):
    agent = agent or _agent_ref
    return getattr(agent, "_ga_audit_current_task_id", None) if agent else None


def _install_task_id_hook(agent):
    if getattr(agent, "_ga_audit_task_id_hooked", False):
        return
    orig_put_task = agent.put_task
    orig_queue_get = agent.task_queue.get

    def audited_put_task(query, source="user", images=None):
        task_id = _new_task_id(source)
        display_queue = orig_put_task(query, source=source, images=images)
        try:
            with agent.task_queue.mutex:
                for item in reversed(agent.task_queue.queue):
                    if isinstance(item, dict) and item.get("output") is display_queue:
                        item["task_id"] = task_id
                        break
        except Exception:
            pass
        agent._ga_audit_current_task_id = task_id
        agent._ga_audit_current_task_source = source
        return display_queue

    def audited_queue_get(*args, **kwargs):
        task = orig_queue_get(*args, **kwargs)
        if isinstance(task, dict):
            agent._ga_audit_current_task_id = task.get("task_id") or agent._ga_audit_current_task_id
            agent._ga_audit_current_task_source = task.get("source")
        return task

    agent.put_task = audited_put_task
    agent.task_queue.get = audited_queue_get
    agent._ga_audit_task_id_hooked = True


def _ensure_dashboard_assets():
    """Materialize the tracked dashboard template into the runtime dashboard dir."""
    try:
        if not _DASHBOARD_TEMPLATE_PATH.exists():
            return
        should_copy = not _DASHBOARD_HTML_PATH.exists()
        if not should_copy:
            existing = _DASHBOARD_HTML_PATH.read_text(encoding="utf-8", errors="ignore")
            template_mtime = _DASHBOARD_TEMPLATE_PATH.stat().st_mtime
            served_mtime = _DASHBOARD_HTML_PATH.stat().st_mtime
            should_copy = (
                "semantic-findings" not in existing
                or "renderSemanticFindings" not in existing
                or "token_breakdown" not in existing
                or served_mtime < template_mtime
            )
        if should_copy:
            _DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
            template = _DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8")
            _DASHBOARD_HTML_PATH.write_text(template, encoding="utf-8")
    except Exception:
        pass


def _load_registry():
    """Load constraints registry, with mtime-based cache."""
    global _registry_cache, _registry_mtime
    try:
        mt = os.path.getmtime(_REGISTRY_PATH)
        if _registry_cache is None or mt != _registry_mtime:
            with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
                _registry_cache = json.load(f)
            _registry_mtime = mt
    except (FileNotFoundError, json.JSONDecodeError):
        _registry_cache = {"constraints": [], "rules": []}
    return _registry_cache


def _normalize_tool_calls(tool_calls):
    """Return a list of dict-like tool call records for audit robustness.

    GA contexts may contain compact string tool markers in ctx["tool_calls"].
    Audit checks assume dict records, so normalize here instead of letting one
    malformed entry abort the whole dashboard append path.
    """
    if not tool_calls:
        return []
    if isinstance(tool_calls, dict):
        tool_calls = [tool_calls]
    elif not isinstance(tool_calls, (list, tuple)):
        tool_calls = [tool_calls]

    normalized = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            normalized.append(tc)
        elif isinstance(tc, str):
            normalized.append({"name": tc, "args": {}, "raw": tc})
        else:
            normalized.append({"name": type(tc).__name__, "args": {}, "raw": repr(tc)[:500]})
    return normalized


def _extract_tool_args_text(tool_calls, scope="tool_args"):
    """Flatten tool args into searchable text.

    scope="tool_args"  – all tools (default, backward-compat)
    scope="exec_only"  – only code-execution tools (code_run/shell/bash/web_execute_js)
    """
    _CODE_EXEC = {"code_run", "shell", "bash", "web_execute_js"}
    parts = []
    for tc in _normalize_tool_calls(tool_calls):
        if scope == "exec_only" and _coerce_tool_name(tc) not in _CODE_EXEC:
            continue
        args = _coerce_tool_args(tc)
        for k, v in args.items():
            if isinstance(v, str):
                parts.append(v)
    return "\n".join(parts)


def _check_code_pattern(pattern, text, negative_context=None):
    """Check if pattern exists in text (regex or plain).

    If *negative_context* regex is given, matches that are immediately preceded
    (within 20 chars) by a negative-context phrase are excluded (= not a real hit).
    This prevents false positives when a rule merely *mentions* the forbidden term
    (e.g. "禁用 mimo-v2.5-pro" in working-memory text).
    """
    try:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
    except re.error:
        if pattern.lower() in text.lower():
            matches = [type('M', (), {'start': lambda s: text.lower().find(pattern.lower())})()]
        else:
            return False
    if not matches:
        return False
    if not negative_context:
        return True
    # Filter out matches preceded by negative context
    for m in matches:
        start = max(0, m.start() - 20)
        preceding = text[start:m.start()]
        if not re.search(negative_context, preceding, re.IGNORECASE):
            return True  # genuine hit without negation prefix
    return False  # all matches were negated


def _coerce_tool_name(tool_call):
    if not isinstance(tool_call, dict):
        return ""
    return str(tool_call.get("tool_name") or tool_call.get("name") or "")


def _coerce_tool_args(tool_call):
    if not isinstance(tool_call, dict):
        return {}
    args = tool_call.get("args", {})
    return args if isinstance(args, dict) else {}


def _extract_subagent_from_command(command):
    """Best-effort parse of agentmain.py subagent launch command; never raise."""
    if not isinstance(command, str) or not command.strip():
        return None
    lowered = command.lower()
    if "agentmain.py" not in lowered:
        return None
    markers = ("--task", "--input", "--llm_no", "--bg")
    if not any(marker in lowered for marker in markers):
        return None
    result = {"source_tool": "command", "command": command.strip()}

    def _assign_flag(flag_name, target_key):
        patterns = [
            rf'{re.escape(flag_name)}\s+"([^"]+)"',
            rf"{re.escape(flag_name)}\s+'([^']+)'",
            rf'{re.escape(flag_name)}\s+(\S+)',
            rf'{re.escape(flag_name)}=([^\s]+)',
        ]
        for pattern in patterns:
            m = re.search(pattern, command)
            if m:
                result[target_key] = m.group(1)
                return

    _assign_flag("--task", "task")
    _assign_flag("--input", "input")
    _assign_flag("--llm_no", "llm_no")
    if re.search(r'(^|\s)--bg(?:\s|$|=)', command):
        if "--bg=" in command:
            m = re.search(r'--bg=([^\s]+)', command)
            if m:
                result["bg"] = m.group(1).strip().lower() not in ("", "0", "false", "no")
        else:
            result["bg"] = True

    try:
        parts = shlex.split(command, posix=True)
    except Exception:
        parts = command.strip().split()
    for idx, part in enumerate(parts):
        normalized = str(part).strip()
        key = normalized.lower()
        next_val = parts[idx + 1].strip() if idx + 1 < len(parts) and isinstance(parts[idx + 1], str) else ""
        if key == "--task" and next_val and "task" not in result:
            result["task"] = next_val
        elif key == "--input" and next_val and "input" not in result:
            result["input"] = next_val
        elif key == "--llm_no" and next_val and "llm_no" not in result:
            result["llm_no"] = next_val
        elif key == "--bg" and "bg" not in result:
            result["bg"] = True
        elif key.startswith("--task=") and "task" not in result:
            result["task"] = normalized.split("=", 1)[1]
        elif key.startswith("--input=") and "input" not in result:
            result["input"] = normalized.split("=", 1)[1]
        elif key.startswith("--llm_no=") and "llm_no" not in result:
            result["llm_no"] = normalized.split("=", 1)[1]
        elif key.startswith("--bg=") and "bg" not in result:
            val = normalized.split("=", 1)[1].strip().lower()
            result["bg"] = val not in ("", "0", "false", "no")
    # Truncate input to summary for readability
    if "input" in result and isinstance(result["input"], str) and len(result["input"]) > 120:
        result["input"] = result["input"][:120] + "…"
    return result


def _extract_subagent(tool_calls, turn):
    """Detect direct subagent tools and agentmain.py-launched subagents; never raise."""
    try:
        tool_names = []
        direct_tools = []
        for tc in (tool_calls or []):
            tool_name = _coerce_tool_name(tc)
            if tool_name:
                tool_names.append(tool_name)
                if "subagent" in tool_name.lower():
                    direct_tools.append(tool_name)
        if direct_tools:
            result = {"source_tool": direct_tools[0], "tools": tool_names, "turn": turn}
            # Extract structured info from direct subagent tool args
            for tc in (tool_calls or []):
                tn = _coerce_tool_name(tc)
                if "subagent" not in tn.lower():
                    continue
                args = _coerce_tool_args(tc)
                for src_key, dst_key in [("task", "task"), ("task_name", "task"),
                                         ("input", "input"), ("prompt", "input"),
                                         ("llm_no", "llm_no"), ("model", "llm_no"),
                                         ("bg", "bg"), ("background", "bg")]:
                    val = args.get(src_key)
                    if val and dst_key not in result:
                        result[dst_key] = val
                break
            # Truncate input to summary
            if "input" in result and isinstance(result["input"], str) and len(result["input"]) > 120:
                result["input"] = result["input"][:120] + "…"
            # Fallback: use first 60 chars of input/prompt as task name if task is missing
            if "task" not in result:
                fallback_src = result.get("input") or ""
                if fallback_src:
                    result["task"] = fallback_src[:60].strip()
            return result

        exec_tools = {"code_run", "shell", "bash"}
        for tc in (tool_calls or []):
            tool_name = _coerce_tool_name(tc)
            if tool_name not in exec_tools:
                continue
            args = _coerce_tool_args(tc)
            commands = []
            for key in ("script", "command", "cmd", "code", "input", "text"):
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    commands.append(value)
            if not commands:
                try:
                    serialized = json.dumps(args, ensure_ascii=False)
                except Exception:
                    serialized = ""
                if serialized:
                    commands.append(serialized)
            for command in commands:
                parsed = _extract_subagent_from_command(command)
                if parsed:
                    parsed["source_tool"] = tool_name
                    return parsed
    except Exception:
        return None
    return None


_CONSEC_EXEC_HISTORY = []  # module-level: tracks per-turn execution tool usage for R004

_CONSULTATION_TRIGGERS = [
    "你看", "需要吗", "怎么样", "建议", "好办法", "你觉得", "有没有",
    "是否应该", "合适吗", "可以吗", "要不要", "如何", "什么方案",
    "有什么想法", "推荐", "看法", "意见",
]
_WRITE_TOOLS = {
    "file_write", "file_patch", "code_run", "web_execute_js",
    "file_create", "file_overwrite", "shell", "bash",
}
_EXEC_TOOLS = {
    "code_run", "file_write", "file_patch", "file_create",
    "file_overwrite", "shell", "bash", "web_execute_js",
}
_TASK_EXEC_TOOLS = {
    "code_run", "file_write", "file_patch", "file_create",
    "file_overwrite", "shell", "bash", "web_execute_js",
}

_DELEGATION_EXEMPTION_MARKERS = (
    "subagent不可用", "subagent 不可用", "无法委托", "不能委托",
    "记录豁免", "豁免", "exemption", "delegate exemption",
    "短任务", "轻量任务", "单步验证", "最小验证", "最终验证",
    "用户要求直接", "直接处理", "无需委托",
)


def _has_delegation_exemption(ctx):
    """Detect explicit delegation exemption/reflection in current turn context."""
    try:
        parts = []
        for key in ("summary", "reflection", "r061_exemption", "delegation_exemption", "user_message", "_user_message", "query"):
            value = ctx.get(key, "") if isinstance(ctx, dict) else ""
            if value:
                parts.append(str(value))
        text = "\n".join(parts).lower()
        if not text:
            return False

        # Avoid treating test labels such as "R004_R061_no_exemption_case" as
        # explicit delegation exemptions. Chinese markers are phrase-based;
        # English markers require token boundaries and are blocked by common
        # negated/tag forms.
        if re.search(r"(?:^|[\s_\-/])(?:no|without|non)[\s_\-/]+(?:delegation[\s_\-/]+)?exemption(?:$|[\s_\-/])", text):
            return False

        for marker in _DELEGATION_EXEMPTION_MARKERS:
            marker_l = marker.lower()
            if marker_l in {"exemption", "delegate exemption"}:
                pattern = r"(?<![a-z0-9_])" + re.escape(marker_l).replace(r"\ ", r"[\s_-]+") + r"(?![a-z0-9_])"
                if re.search(pattern, text):
                    return True
            elif marker_l in text:
                return True
        return False
    except Exception:
        return False


def _check_context(rule, tool_calls, ctx):
    """R003: If user message contains consultation triggers, using write tools = fail."""
    user_msg = ctx.get("_user_message", "")
    if not user_msg:
        ga_self = ctx.get("self")
        for attr in ("_current_query", "current_query", "last_query", "_last_user_message"):
            user_msg = getattr(ga_self, attr, "") if ga_self else ""
            if user_msg:
                break
    if not user_msg:
        user_msg = ctx.get("user_message", "") or ctx.get("query", "")
    if not user_msg:
        return "skip"

    has_trigger = any(t in user_msg for t in _CONSULTATION_TRIGGERS)
    if not has_trigger:
        return "skip"  # no consultation trigger → rule not applicable

    tool_names = {tc.get("tool_name", "") for tc in (tool_calls or [])}
    has_write = bool(tool_names & _WRITE_TOOLS)
    return "fail" if has_write else "pass"


def _check_consecutive_execution(rule, tool_calls, ctx):
    """R004: budget-aware delegation guard for consecutive direct execution.

    Short/light direct execution is allowed. Only >=threshold consecutive direct
    execution turns fail when subagent is available and no explicit exemption was
    recorded (e.g. short task, final/minimal verification, user asked direct run).
    NOTE: _CONSEC_EXEC_HISTORY is populated by _on_turn_end BEFORE this is called.
    """
    tool_names = [_coerce_tool_name(tc) for tc in (tool_calls or [])]
    has_exec = bool(set(tool_names) & _EXEC_TOOLS)
    if not has_exec:
        return "skip"  # no exec tools this turn → not applicable

    threshold = rule.get("detection", {}).get("threshold", 3)
    recent = _CONSEC_EXEC_HISTORY[-threshold:]
    if len(recent) < threshold:
        return "skip"  # not enough history to evaluate

    all_exec_no_sub = all(r.get("exec") and not r.get("subagent") for r in recent)
    if not all_exec_no_sub:
        return "pass"  # rule applicable, delegation happening → genuinely passing
    if _SUBAGENT_AVAILABLE is False:
        return "skip"  # subagent tool not in environment, cannot delegate
    if any(r.get("exemption") for r in recent) or _has_delegation_exemption(ctx):
        return "pass"
    return "fail"


def _check_subagent_delegation_guard(rule, tool_calls, ctx):
    """R061: fail only on true long-running direct task execution without delegation/exemption."""
    tool_names = [_coerce_tool_name(tc) for tc in (tool_calls or [])]
    has_task_exec = bool(set(tool_names) & _TASK_EXEC_TOOLS)
    if not has_task_exec:
        return "skip"  # no task-exec tools this turn → not applicable

    threshold = rule.get("detection", {}).get("threshold", 3)
    recent = _CONSEC_EXEC_HISTORY[-threshold:]
    if len(recent) < threshold:
        return "skip"  # not enough history to evaluate

    all_direct_task_exec = all(r.get("task_exec") and not r.get("subagent") for r in recent)
    if not all_direct_task_exec:
        return "pass"  # rule applicable, delegation happening → genuinely passing
    if _SUBAGENT_AVAILABLE is False:
        return "skip"  # subagent tool not in environment, cannot delegate
    if any(r.get("exemption") for r in recent) or _has_delegation_exemption(ctx):
        return "pass"
    return "fail"


def _r061_metadata(status, ctx):
    """Attach R061 governance metadata: soft block at 3 direct exec turns, hard at 4+."""
    if status != "fail":
        return {}
    consecutive = _r061_consecutive_count(_CONSEC_EXEC_HISTORY)
    severity = "hard_block" if consecutive >= 4 else "soft_block"
    action = "delegate_to_subagent_or_record_exemption"
    if severity == "soft_block":
        action = "stop_and_reflect_delegate_or_explain_exemption"
    return {
        "severity": severity,
        "consecutive_exec_turns": consecutive,
        "recommended_action": action,
    }


def _r061_consecutive_count(history):
    consecutive = 0
    for record in reversed(history):
        if record.get("task_exec") and not record.get("subagent"):
            consecutive += 1
        else:
            break
    return consecutive


def preview_r061_pre_tool_guard(tool_calls, ctx=None):
    """Dry-run R061 before executing tools. Returns warning metadata; never blocks."""
    try:
        if _SUBAGENT_AVAILABLE is False or _has_delegation_exemption(ctx):
            return None
        tool_names = [_coerce_tool_name(tc) for tc in (tool_calls or [])]
        subagent = _extract_subagent(tool_calls, None)
        record = {
            "exec": any(t in _EXEC_TOOLS for t in tool_names),
            "task_exec": any(t in _TASK_EXEC_TOOLS for t in tool_names),
            "subagent": bool(subagent),
            "exemption": _has_delegation_exemption(ctx),
        }
        projected = (_CONSEC_EXEC_HISTORY + [record])[-20:]
        consecutive = _r061_consecutive_count(projected)
        if consecutive < 3:
            return None
        severity = "hard_block" if consecutive >= 4 else "soft_block"
        action = "delegate_to_subagent_or_record_exemption"
        if severity == "soft_block":
            action = "stop_and_reflect_delegate_or_explain_exemption"
        return {
            "id": "R061",
            "mode": "dry_run",
            "severity": severity,
            "consecutive_exec_turns": consecutive,
            "recommended_action": action,
        }
    except Exception:
        return None


def _check_tool_negative(rule, tool_calls):
    """Check forbidden tool usage when required context is present."""
    args_text = _extract_tool_args_text(tool_calls)
    tool_names = [tc.get("name", tc.get("tool_name", "")) for tc in (tool_calls or [])]
    detection = rule["detection"]
    forbidden = detection.get("forbidden", [])
    required = detection.get("required", "")

    # Only trigger when required context is present
    if required and (required in args_text or any(required in tn for tn in tool_names)):
        # Check if any forbidden tool/pattern is used
        for fp in forbidden:
            if any(fp.lower() == tn.lower() for tn in tool_names):
                return "fail"
            if fp.lower() in args_text.lower():
                return "fail"
        return "pass"
    return "skip"


def _check_sequence(rule, tool_calls, _history=[]):
    """Check if required_prior tool was called before before_tool."""
    detection = rule["detection"]
    before_tool = detection.get("before_tool", "")
    required_prior = detection.get("required_prior", "")
    tool_names = [tc.get("tool_name", "") for tc in (tool_calls or [])]

    if before_tool in tool_names:
        if required_prior in _history:
            return "pass"
        return "fail"
    # Track history
    _history.extend(tool_names)
    if len(_history) > 50:
        _history[:] = _history[-50:]
    return "skip"


def _get_nested(obj, path, default=None):
    """Safely read nested dict/object attributes."""
    cur = obj
    for part in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
    return default if cur is None else cur


def _first_value(*values):
    for value in values:
        if value not in (None, "", 0):
            return value
    return ""


def _extract_model(ctx, ga_self, response):
    """Best-effort model extraction across GA/SDK response shapes."""
    try:
        parent = _get_nested(ga_self, ["parent"])
        parent_model = ""
        try:
            if parent and hasattr(parent, "get_llm_name"):
                parent_model = parent.get_llm_name(model=True)
        except Exception:
            parent_model = ""
        return _first_value(
            ctx.get("model"),
            _get_nested(response, ["model"]),
            _get_nested(response, ["response", "model"]),
            getattr(ga_self, "_last_model", "") if ga_self else "",
            getattr(ga_self, "model", "") if ga_self else "",
            _get_nested(ga_self, ["parent", "model"]),
            parent_model,
            _get_nested(parent, ["llmclient", "model"]),
            _get_nested(parent, ["llmclient", "model_name"]),
            _get_nested(parent, ["llmclient", "default_model"]),
            _get_nested(ga_self, ["llm", "model"]),
            _get_nested(ga_self, ["client", "model"]),
        )
    except Exception:
        return ""


def _extract_usage(ctx, response):
    """Best-effort raw usage extraction across OpenAI/Anthropic/GA shapes."""
    usage = _first_value(
        ctx.get("usage"),
        ctx.get("tokens"),
        _get_nested(response, ["usage"]),
        _get_nested(response, ["response", "usage"]),
    )
    try:
        import budget_tracker
        if not usage and hasattr(budget_tracker, "last_usage"):
            usage = budget_tracker.last_usage()
    except Exception:
        pass
    try:
        import llmcore
        if not usage and hasattr(llmcore, "last_usage"):
            usage = llmcore.last_usage()
    except Exception:
        pass
    return usage or {}


def _extract_tokens(ctx, response):
    """Best-effort token extraction across OpenAI/Anthropic/GA budget shapes."""
    usage = _extract_usage(ctx, response)
    input_tokens = _first_value(
        _get_nested(usage, ["input"]),
        _get_nested(usage, ["input_tokens"]),
        _get_nested(usage, ["prompt_tokens"]),
        _get_nested(usage, ["prompt_token_count"]),
    ) or 0
    output_tokens = _first_value(
        _get_nested(usage, ["output"]),
        _get_nested(usage, ["output_tokens"]),
        _get_nested(usage, ["completion_tokens"]),
        _get_nested(usage, ["candidates_token_count"]),
    ) or 0
    cached_tokens = _first_value(
        _get_nested(usage, ["cached"]),
        _get_nested(usage, ["cache_read_input_tokens"]),
        _get_nested(usage, ["input_tokens_details", "cached_tokens"]),
        _get_nested(usage, ["prompt_tokens_details", "cached_tokens"]),
    ) or 0
    return {"input": int(input_tokens or 0), "output": int(output_tokens or 0), "cached": int(cached_tokens or 0)}


def _extract_token_breakdown(ctx, response):
    """Detailed provider token counters for dashboard diagnosis."""
    usage = _extract_usage(ctx, response)
    completion_details = _get_nested(usage, ["completion_tokens_details"], {}) or {}
    prompt_details = _first_value(
        _get_nested(usage, ["prompt_tokens_details"]),
        _get_nested(usage, ["input_tokens_details"]),
        {},
    ) or {}
    return {
        "prompt": int(_first_value(_get_nested(usage, ["prompt_tokens"]), _get_nested(usage, ["input_tokens"]), _get_nested(usage, ["input"])) or 0),
        "completion": int(_first_value(_get_nested(usage, ["completion_tokens"]), _get_nested(usage, ["output_tokens"]), _get_nested(usage, ["output"])) or 0),
        "total": int(_first_value(_get_nested(usage, ["total_tokens"])) or 0),
        "cached": int(_first_value(_get_nested(prompt_details, ["cached_tokens"]), _get_nested(usage, ["cache_read_input_tokens"]), _get_nested(usage, ["cached"])) or 0),
        "reasoning": int(_first_value(_get_nested(completion_details, ["reasoning_tokens"]), _get_nested(usage, ["reasoning_tokens"])) or 0),
        "cache_creation": int(_first_value(_get_nested(usage, ["cache_creation_input_tokens"]), _get_nested(prompt_details, ["cache_creation_input_tokens"])) or 0),
    }


def _safe_json_chars(obj):
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str))
    except Exception:
        return 0


def _extract_context_breakdown(ctx, ga_self):
    """Approximate current GA context sizes that drive per-turn prompt cost."""
    data = {"history_messages": 0, "history_chars": 0, "tools_count": 0, "tools_schema_chars": 0, "system_prompt_chars": 0, "last_tool_result_chars": 0}
    try:
        parent = _get_nested(ga_self, ["parent"])
        backend = _get_nested(parent, ["llmclient", "backend"])
        history = getattr(backend, "history", None) or []
        data["history_messages"] = len(history)
        data["history_chars"] = _safe_json_chars(history)
        last_tool = ""
        for msg in reversed(history[-12:]):
            if isinstance(msg, dict) and str(msg.get("role", "")).lower() in ("tool", "function"):
                last_tool = msg.get("content", "")
                break
        data["last_tool_result_chars"] = len(str(last_tool))
    except Exception:
        pass
    try:
        tools = ctx.get("tools_schema") or globals().get("TOOLS_SCHEMA") or []
        data["tools_count"] = len(tools) if hasattr(tools, "__len__") else 0
        data["tools_schema_chars"] = _safe_json_chars(tools)
    except Exception:
        pass
    try:
        system_prompt = ctx.get("system_prompt") or ctx.get("sys_prompt") or ""
        data["system_prompt_chars"] = len(str(system_prompt))
    except Exception:
        pass
    return data


# ── MVP-v4: Automated detection functions ──────────────────────────────

def _check_summary_present(det, ctx):
    """Check if assistant response contains <summary> tag (via GA's summary extraction)."""
    turn = ctx.get("turn", 0)
    summary = ctx.get("summary", "")
    if turn <= 0:
        return "skip"  # First turn may not have summary
    if summary and summary.strip():
        return "pass"
    return "fail"


def _check_sensitive_access(det, tool_calls):
    """Check actual tool access to sensitive files, avoiding keyword-only false positives.

    Path-field check (strong signal): a tool's path/file arg directly points at a sensitive file → fail.
    Script check (weaker signal): require a file-access call whose *argument* is a sensitive path,
    not just co-occurrence of read_text and a sensitive name anywhere in the script.
    """
    if not tool_calls:
        return "skip"  # no tools this turn → not applicable
    import re
    sensitive_path_re = re.compile(
        r'(^|[\\/])mykey\.py$|(^|[\\/])\.env(\.[^\\/]*)?$|'
        r'(^|[\\/])credentials(\.json)?$|(^|[\\/])git-credentials$|'
        r'\.(pem|key)$',
        re.I,
    )
    # Patterns that indicate *direct* file access with a sensitive path as argument,
    # e.g. open("mykey.py"), Path("mykey.py").read_text(), read_text("../mykey.py")
    _DIRECT_ACCESS_RE = re.compile(
        r'''(?:open|read_text|read_bytes|Path)\s*\(\s*['"]([^'"]+)['"]'''
        r'''|(?:file_read|shutil\.copy|copyfile|move|rename)\s*\(\s*['"]([^'"]+)['"]''',
        re.I,
    )
    path_fields = ("path", "file", "filepath", "source", "src", "target", "dst", "save_to_file")
    for tc in (tool_calls or []):
        args = tc.get("args", {}) or {}
        # Strong signal: a tool path field directly points at a sensitive file.
        for field in path_fields:
            val = str(args.get(field, "") or "")
            if val and sensitive_path_re.search(val.replace("/", "\\")):
                return "fail"
        # Script check: extract actual path arguments from file-access calls,
        # only fail if the extracted path itself is sensitive.
        script_text = "\n".join(str(args.get(k, "") or "") for k in ("script", "code"))
        if script_text:
            for m in _DIRECT_ACCESS_RE.finditer(script_text):
                accessed_path = m.group(1) or m.group(2) or ""
                if accessed_path and sensitive_path_re.search(accessed_path.replace("/", "\\")):
                    return "fail"
    return "pass"


_LAST_EVIDENCE_TURN = {"turn": -999}


def _has_verification_evidence(tool_calls):
    """Best-effort evidence signal: tests/reads/browser scans/screenshots in recent tool calls."""
    _EVIDENCE_TOOLS = {'code_run', 'web_scan', 'web_execute_js', 'file_read'}
    _EVIDENCE_KW = ['test', 'verify', 'screenshot', '截图', 'diff', 'DOM', 'stdout', '验证', 'pass', 'PASS']
    tool_names = {tc.get("tool_name", "") for tc in (tool_calls or [])}
    if tool_names & _EVIDENCE_TOOLS:
        return True
    all_args = json.dumps([tc.get("args", {}) for tc in (tool_calls or [])], ensure_ascii=False).lower()
    return any(kw.lower() in all_args for kw in _EVIDENCE_KW)


def _check_claim_without_evidence(det, tool_calls, ctx):
    """Check if assistant claims completion without verification evidence in this or previous turn."""
    import re
    # Collect text from multiple sources
    texts = []
    response = ctx.get("response") or {}
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            texts.append(msg.get("content", ""))
        if not any(texts):
            texts.append(response.get("text", ""))
    elif isinstance(response, str):
        texts.append(response)
    # Also check summary (GA's own extraction)
    summary = ctx.get("summary", "")
    if summary:
        texts.append(summary)
    text = " ".join(t for t in texts if t).strip()
    if not text:
        return "skip"

    _CLAIM_PATTERNS = [
        re.compile(r'已完成|已修复|已解决|修复完成|实现完成|任务完成|已验证通过|已可用|可以使用|工作正常', re.I),
        re.compile(r'\bcomplet(ed|e)\b|\bfix(ed)\b|\bresolv(ed)\b|\bdone\b', re.I),
    ]
    has_claim = any(p.search(text) for p in _CLAIM_PATTERNS)
    if not has_claim:
        return "skip"
    # Check this turn, then allow a verification tool from the immediately previous turn.
    if _has_verification_evidence(tool_calls):
        return "pass"
    turn = ctx.get("turn", 0) or 0
    if turn - _LAST_EVIDENCE_TURN.get("turn", -999) <= 1:
        return "pass"
    # A final textual summary may mention "已验证通过"; keep that as soft evidence only
    # when it is not the sole signal across multiple turns.
    if "已验证" in text or "verified" in text.lower():
        return "pass"
    return "fail"


_MEMORY_META_SOP_STATE = {"read": False}

def _check_memory_write(det, tool_calls, ctx):
    """Check if memory files are written without reading META-SOP first."""
    memory_write = False
    for tc in (tool_calls or []):
        tool_name = tc.get("tool_name", "")
        args = tc.get("args", {})
        # Track META-SOP reads
        if tool_name == "file_read":
            path = args.get("path", "")
            if "memory_management_sop" in path:
                _MEMORY_META_SOP_STATE["read"] = True
        # Track memory writes
        if tool_name in ("file_write", "file_patch"):
            path = args.get("path", "")
            if "memory/" in path.lower() or "memory\\" in path.lower():
                memory_write = True
    if not memory_write:
        return "skip"
    if _MEMORY_META_SOP_STATE["read"]:
        return "pass"
    return "fail"


def _run_checks(registry, tool_calls, ctx):
    """Run all constraint/rule checks, return list of results."""
    tool_calls = _normalize_tool_calls(tool_calls)
    results = []
    args_text = _extract_tool_args_text(tool_calls)
    all_items = registry.get("constraints", []) + registry.get("rules", [])

    for item in all_items:
        if not item.get("active", True):
            continue
        det = item.get("detection", {})
        if not isinstance(det, dict):
            continue  # engine-only items handled by constraint engine, skip here
        det_type = det.get("type", "")
        status = "skip"
        evidence = ""  # capture detection context for audit trail

        if det_type == "code_pattern":
            pattern = det.get("pattern", "")
            scope = det.get("scope", "tool_args")
            scoped_text = _extract_tool_args_text(tool_calls, scope)
            if scoped_text:
                evidence = scoped_text[:300]
                # Browser-category constraints only apply when browser tools are used
                _BROWSER_TOOLS = {'web_execute_js', 'web_scan', 'web_navigate', 'web_click'}
                neg_ctx = det.get("negative_context")
                tool_names = {_coerce_tool_name(tc) for tc in (tool_calls or [])}
                if item.get("category") == "browser" and not tool_names & _BROWSER_TOOLS:
                    found = False
                    status = "skip"
                else:
                    found = _check_code_pattern(pattern, scoped_text, neg_ctx)
                    # code_pattern detectors are negative/forbidden-pattern checks.
                    # A hit is a violation; a miss alone does not prove the rule was
                    # applicable, so keep it as skip instead of inflating it to pass.
                    if found:
                        status = "fail"
                    else:
                        status = "skip"
            else:
                # No relevant tools in scope → not applicable this turn → skip
                status = "skip"

        elif det_type == "tool_negative":
            status = _check_tool_negative(item, tool_calls)
            evidence = ",".join(_coerce_tool_name(tc) for tc in (tool_calls or []))[:300]

        elif det_type == "sequence_check":
            status = _check_sequence(item, tool_calls)
            evidence = ",".join(_coerce_tool_name(tc) for tc in (tool_calls or []))[:300]

        elif det_type == "consecutive_execution_check":
            status = _check_consecutive_execution(item, tool_calls, ctx)

        elif det_type == "subagent_delegation_guard":
            status = _check_subagent_delegation_guard(item, tool_calls, ctx)

        elif det_type == "context_check":
            status = _check_context(item, tool_calls, ctx)
            evidence = (ctx.get("summary") or "")[:200]

        elif det_type == "manual":
            status = "skip"

        # ── MVP-v4 automated detection branches ──
        elif det_type == "summary_check":
            status = _check_summary_present(det, ctx)
            evidence = (ctx.get("summary") or "")[:200]

        elif det_type == "sensitive_access":
            status = _check_sensitive_access(det, tool_calls)
            # Capture accessed file paths as evidence
            _sa_paths = []
            for tc in (tool_calls or []):
                p = (tc.get("args") or {}).get("path", "")
                if p:
                    _sa_paths.append(p)
            evidence = ",".join(_sa_paths)[:300]

        elif det_type == "claim_without_evidence":
            status = _check_claim_without_evidence(det, tool_calls, ctx)
            evidence = (ctx.get("summary") or "")[:200]

        elif det_type == "memory_write_check":
            status = _check_memory_write(det, tool_calls, ctx)
            _mw_paths = []
            for tc in (tool_calls or []):
                if _coerce_tool_name(tc) in ("file_write", "file_patch"):
                    p = (tc.get("args") or {}).get("path", "")
                    if p:
                        _mw_paths.append(p)
            evidence = ",".join(_mw_paths)[:300]

        result = {
            "id": item["id"],
            "name": item["name"],
            "status": status
        }
        if evidence:
            result["evidence"] = evidence
        if item.get("id") == "R061":
            result.update(_r061_metadata(status, ctx))
        results.append(result)

    return results


def _run_semantic_advisory(event):
    """Run optional semantic advisory checks; never raise or emit hard failures."""
    try:
        from semantic_audit import evaluate_event, load_semantic_rules
        findings = evaluate_event(event, load_semantic_rules())
        safe_findings = []
        for finding in findings or []:
            if finding.get("severity") == "fail":
                continue
            safe_findings.append(finding)
        return safe_findings
    except Exception:
        return []


def _on_turn_end(ctx):
    """Hook called at end of each GA turn with locals() from turn_end_callback."""
    try:
        turn = ctx.get("turn", 0)
        summary = ctx.get("summary", "")
        tool_calls = _normalize_tool_calls(ctx.get("tool_calls", []))
        response = ctx.get("response")

        # Extract user_message from GA instance for context_check (R003)
        ga_self = ctx.get("self")
        user_message = ""
        if ga_self:
            for attr in ("current_query", "last_query", "_current_input", "query"):
                val = getattr(ga_self, attr, None)
                if val and isinstance(val, str):
                    user_message = val
                    break
        ctx["_user_message"] = user_message
        ctx.setdefault("user_message", user_message)
        ctx.setdefault("summary", summary)

        # Update consecutive execution history for R004 / delegation guard checks
        tool_names = [_coerce_tool_name(tc) for tc in (tool_calls or [])]
        subagent = _extract_subagent(tool_calls, turn)
        has_exec = any(t in _EXEC_TOOLS for t in tool_names)
        has_task_exec = any(t in _TASK_EXEC_TOOLS for t in tool_names)
        has_subagent = bool(subagent)
        _CONSEC_EXEC_HISTORY.append({
            "exec": has_exec,
            "task_exec": has_task_exec,
            "subagent": has_subagent,
            "exemption": _has_delegation_exemption(ctx),
        })
        if len(_CONSEC_EXEC_HISTORY) > 20:
            _CONSEC_EXEC_HISTORY[:] = _CONSEC_EXEC_HISTORY[-20:]
        model = _extract_model(ctx, ga_self, response)
        tokens = _extract_tokens(ctx, response)
        token_breakdown = _extract_token_breakdown(ctx, response)
        context_breakdown = _extract_context_breakdown(ctx, ga_self)

        # Feed tokens into budget_tracker so cumulative budget works
        budget = {"used_pct": 0, "tier": "unknown", "signal": "N/A"}
        try:
            try:
                import budget_tracker
            except ModuleNotFoundError:
                import sys
                _mem_dir = Path(__file__).resolve().parent / "memory"
                if str(_mem_dir) not in sys.path:
                    sys.path.insert(0, str(_mem_dir))
                import budget_tracker
            t = budget_tracker.tracker
            global _BUDGET_SESSION_TASK_ID, _BUDGET_SESSION_LAST_TURN
            _task_id = _current_task_id(_agent_ref) or user_message or summary or "unknown"
            _turn_num = turn if isinstance(turn, int) else 0
            _new_budget_session = (
                not getattr(t, "_started", False)
                or _BUDGET_SESSION_TASK_ID != _task_id
                or (_BUDGET_SESSION_LAST_TURN is not None and _turn_num <= _BUDGET_SESSION_LAST_TURN)
            )
            # llmcore usage can be recorded before audit hook runs; on a new
            # audit session, discard any pre-session singleton accumulation.
            if _new_budget_session:
                t.start_session(_task_id)
            _BUDGET_SESSION_TASK_ID = _task_id
            _BUDGET_SESSION_LAST_TURN = _turn_num
            t.record_turn()
            _inp = tokens.get("input", 0) or 0
            _out = tokens.get("output", 0) or 0
            _cch = tokens.get("cached", 0) or 0
            if _inp or _out or _cch:
                t.record(_inp, _out, _cch)
            sig = t.check()
            budget = {
                "used_pct": round(getattr(sig, "pct", 0) * 100, 1),
                "token_pct": round(getattr(sig, "pct_tokens", 0) * 100, 1),
                "turn_pct": round(getattr(sig, "pct_turns", 0) * 100, 1),
                "tier": getattr(t, "tier", "unknown"),
                "signal": getattr(sig, "level", "N/A"),
                "total_tokens": getattr(t, "total_tokens", 0),
                "effective_cost": getattr(t, "effective_cost", 0),
                "max_tokens": getattr(t, "max_tokens", 0),
                "turns": getattr(t, "turns", 0),
                "max_turns": getattr(t, "max_turns", 0),
                "remaining_tokens": getattr(sig, "remaining_tokens", 0),
                "remaining_turns": getattr(sig, "remaining_turns", 0),
            }
        except Exception as e:
            budget = {"used_pct": 0, "tier": "unknown", "signal": "error", "error": f"{type(e).__name__}: {e}"[:160]}

        # Load registry and run checks
        registry = _load_registry()
        checks = _run_checks(registry, tool_calls, ctx)

        # --- DSL Constraint Engine (parallel shadow layer) ---
        try:
            import ga_constraint_engine as _dsl_eng
            _dsl_constraints = _dsl_eng.load_constraints(str(_DSL_CONSTRAINTS_PATH))
            if _dsl_constraints:
                # Build ctx for DSL engine
                _resp_text = ""
                if isinstance(response, str):
                    _resp_text = response
                elif isinstance(response, dict):
                    _resp_text = response.get("content", "") or json.dumps(response, ensure_ascii=False)
                _scripts = []
                for _tc in (tool_calls or []):
                    if not isinstance(_tc, dict):
                        continue
                    _a = _tc.get("args", {})
                    for _k in ("script", "code", "command"):
                        if _k in _a:
                            _scripts.append(str(_a[_k]))
                _dsl_ctx = {
                    "tool_calls": tool_calls or [],
                    "response_text": _resp_text,
                    "scripts": _scripts,
                    "user_message": ctx.get("user_message", ""),
                    "history": ctx.get("history", []),
                }
                _dsl_results = _dsl_eng.evaluate_all(_dsl_constraints, _dsl_ctx)
                # Convert to ga_audit check format and append
                for _dr in _dsl_results:
                    checks.append({
                        "id": _dr.get("constraint_id", ""),
                        "name": _dr.get("constraint_name", ""),
                        "status": _dr.get("status", "skip"),
                        "evidence": _dr.get("reason", ""),
                        "source": "dsl_engine",
                    })
        except Exception as _dsl_err:
            checks.append({
                "id": "DSL-ENGINE-ERROR",
                "name": "DSL engine load/run error",
                "status": "error",
                "evidence": str(_dsl_err)[:500],
                "source": "dsl_engine",
            })

        if _has_verification_evidence(tool_calls):
            _LAST_EVIDENCE_TURN["turn"] = turn

        # Detect subagent usage
        tool_names = [_coerce_tool_name(tc) for tc in (tool_calls or [])]

        # Deduplicate checks: when both registry (R065) and DSL engine (REG-R065)
        # flag the same constraint, keep only the REG- prefixed version.
        _seen_base = {}
        _deduped = []
        for _c in checks:
            _cid = _c.get("id", "")
            _base = _cid[4:] if _cid.startswith("REG-") else _cid
            if _base not in _seen_base:
                _seen_base[_base] = _c
                _deduped.append(_c)
            else:
                # Prefer REG- prefixed version over bare version
                _existing = _seen_base[_base]
                if _cid.startswith("REG-") and not _existing.get("id", "").startswith("REG-"):
                    # Replace the bare version with REG- version
                    _idx = _deduped.index(_existing)
                    _deduped[_idx] = _c
                    _seen_base[_base] = _c
        checks = _deduped

        # Violations
        violations = [c for c in checks if c["status"] == "fail"]

        # Build tool_calls digest for audit trail (name + truncated args)
        tool_calls_digest = []
        for tc in (tool_calls or []):
            name = _coerce_tool_name(tc)
            args = tc.get("args") or tc.get("arguments") or {}
            digest_args = {}
            for k, v in (args.items() if isinstance(args, dict) else []):
                sv = str(v)
                digest_args[k] = sv[:200] + "…" if len(sv) > 200 else sv
            tool_calls_digest.append({"name": name, "args": digest_args})

        # Build event record
        event = {
            "task_id": _current_task_id(_agent_ref),
            "turn": turn,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "summary": summary[:200] if summary else "",
            "model": model,
            "tools_used": tool_names,
            "tool_calls_digest": tool_calls_digest,
            "tokens": tokens,
            "token_breakdown": token_breakdown,
            "context_breakdown": context_breakdown,
            "budget": budget,
            "constraint_checks": checks,
            "subagent": subagent,
            "violations": violations,
        }
        event["semantic_findings"] = _run_semantic_advisory(event)

        # Ensure dashboard dir exists and append
        _DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
        _append_event(event)

    except Exception as e:
        # Audit must never crash the agent
        try:
            _DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
            import traceback as _tb
            with open(_DASHBOARD_DIR / "audit_error.log", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} ERROR: {e}\n{_tb.format_exc()}\n")
        except:
            pass


def _append_event(event):
    """Append event to audit_log.json (JSON array file)."""
    log_path = _AUDIT_LOG_PATH
    events = []
    if log_path.exists():
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                events = json.load(f)
        except (json.JSONDecodeError, Exception):
            events = []
    events.append(event)
    # Keep last 500 events max
    if len(events) > 500:
        events = events[-500:]
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=1)


def install(agent):
    """Install audit hook on a GeneraticAgent instance. Zero-invasive."""
    global _agent_ref
    _agent_ref = agent
    if not hasattr(agent, '_turn_end_hooks'):
        agent._turn_end_hooks = {}
    agent._turn_end_hooks['ga_audit'] = _on_turn_end
    _install_task_id_hook(agent)
    # Detect subagent tool availability
    global _SUBAGENT_AVAILABLE
    _sa_names = {"subagent", "dispatch", "delegate", "sub_agent"}
    try:
        tools = getattr(agent, "tools", None) or []
        tool_names = {(t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")).lower() for t in tools}
        _SUBAGENT_AVAILABLE = bool(tool_names & _sa_names)
    except Exception:
        _SUBAGENT_AVAILABLE = True  # conservative: assume available
    # Write initial registry snapshot for dashboard (merged registry + DSL)
    _DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    registry = _load_registry()
    try:
        import ga_constraint_engine as _dsl_eng
        _dsl_cons = _dsl_eng.load_constraints(str(_DSL_CONSTRAINTS_PATH))
        # Convert DSL constraints to registry-like format for frontend
        dsl_rules = []
        for c in (_dsl_cons or []):
            dsl_rules.append({
                "id": c.get("id", ""),
                "name": c.get("name", ""),
                "description": c.get("source", ""),
                "check_type": c.get("check_type", ""),
                "detection": {"type": "auto"},
                "type": "dsl"
            })
        registry["dsl_constraints"] = dsl_rules
    except Exception as e:
        registry["dsl_constraints"] = []
        registry["dsl_load_error"] = str(e)[:200]
    
    with open(_DASHBOARD_DIR / "constraints_snapshot.json", "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=1)
    return True# Dashboard control API extension: local POST /api/stop calls GA native abort().
def _append_control_event(action, status, message=""):
    event = {
        "task_id": _current_task_id(),
        "turn": "control",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "summary": f"Dashboard control: {action} -> {status}" + (f" ({message})" if message else ""),
        "model": "dashboard-control",
        "tools_used": ["dashboard_control"],
        "tokens": {"input": 0, "output": 0, "cached": 0},
        "budget": {"used_pct": 0, "tier": "control", "signal": "N/A"},
        "constraint_checks": [],
        "subagent": None,
        "violations": [],
        "control": {"action": action, "status": status, "message": message},
    }
    _DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    _append_event(event)


def _module_status_payload():
    """Return runtime-loaded ga_audit module identity for dashboard/debug checks."""
    module_path = Path(__file__).resolve()
    try:
        module_sha256 = hashlib.sha256(module_path.read_bytes()).hexdigest()
    except Exception as e:
        module_sha256 = None
        module_hash_error = repr(e)
    else:
        module_hash_error = None
    try:
        module_mtime = datetime.fromtimestamp(module_path.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        module_mtime = None
    return {
        "ok": True,
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "ga_audit_file": str(module_path),
        "ga_audit_hash": module_sha256,
        "ga_audit_hash_error": module_hash_error,
        "ga_audit_mtime": module_mtime,
        "dashboard_dir": str(_DASHBOARD_DIR.resolve()),
        "audit_log_path": str(_AUDIT_LOG_PATH.resolve()),
        "agent_installed": globals().get("_agent_ref") is not None,
        "control_port": _CONTROL_PORT,
        "dashboard_port": _DASHBOARD_PORT,
    }


class _ControlHandler(BaseHTTPRequestHandler):
    """Local dashboard control API. Uses GA's native abort(), never kills processes."""
    server_version = "GAAuditControl/0.1"

    def log_message(self, fmt, *args):
        return

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_GET(self):
        if self.path in ("/status", "/api/status", "/debug/module"):
            self._send_json(200, _module_status_payload())
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        if self.path == "/api/reload":
            reloaded, err = _hot_reload(force=True)
            if err:
                self._send_json(500, {"ok": False, "reloaded": False, "error": err})
            else:
                self._send_json(200, {"ok": True, "reloaded": reloaded})
            return
        if self.path != "/api/stop":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        agent = globals().get("_agent_ref")
        if agent is None:
            _append_control_event("stop", "failed", "agent_not_installed")
            self._send_json(503, {"ok": False, "error": "agent_not_installed"})
            return
        try:
            if hasattr(agent, "abort"):
                agent.abort()
                _append_control_event("stop", "requested", "agent.abort()")
                self._send_json(200, {"ok": True, "status": "stop_requested", "method": "agent.abort"})
            else:
                task_dir = getattr(agent, "task_dir", None)
                if task_dir:
                    Path(task_dir, "_stop").write_text("dashboard\n", encoding="utf-8")
                    _append_control_event("stop", "requested", "task_dir/_stop")
                    self._send_json(200, {"ok": True, "status": "stop_requested", "method": "task_dir/_stop"})
                else:
                    _append_control_event("stop", "failed", "no_native_stop_entry")
                    self._send_json(500, {"ok": False, "error": "no_native_stop_entry"})
        except Exception as e:
            _append_control_event("stop", "failed", repr(e))
            self._send_json(500, {"ok": False, "error": repr(e)})


def _ensure_control_server():
    global _control_server
    if _control_server is not None:
        return True
    try:
        _control_server = ThreadingHTTPServer((_CONTROL_HOST, _CONTROL_PORT), _ControlHandler)
        t = threading.Thread(target=_control_server.serve_forever, name="ga-audit-control", daemon=True)
        t.start()
        return True
    except OSError:
        return False


def _ensure_dashboard_server():
    """Start a static file server on _DASHBOARD_PORT to serve dashboard assets."""
    global _dashboard_server
    if _dashboard_server is not None:
        return True
    try:
        handler = lambda *args, **kw: SimpleHTTPRequestHandler(
            *args, directory=str(_DASHBOARD_DIR), **kw
        )
        _dashboard_server = ThreadingHTTPServer((_CONTROL_HOST, _DASHBOARD_PORT), handler)
        t = threading.Thread(target=_dashboard_server.serve_forever, name="ga-audit-dashboard", daemon=True)
        t.start()
        return True
    except OSError:
        return False


_orig_install = install

def install(agent):
    global _agent_ref, _source_mtime
    _agent_ref = agent
    _ensure_dashboard_assets()
    _install_task_id_hook(agent)
    ok = _orig_install(agent)
    # Replace raw hook with hot-reload trampoline
    if hasattr(agent, '_turn_end_hooks') and 'ga_audit' in agent._turn_end_hooks:
        agent._turn_end_hooks['ga_audit'] = _make_trampoline()
    # Record initial mtime so first turn doesn't trigger spurious reload
    try:
        _source_mtime = _SOURCE_PATH.stat().st_mtime
    except OSError:
        pass
    _ensure_control_server()
    _ensure_dashboard_server()
    return ok
