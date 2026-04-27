"""GA Audit Module — 零侵入审计 hook，采集每轮事件并检测约束违规。

Usage:
    import ga_audit
    ga_audit.install(agent)  # agent = GeneraticAgent 实例
"""
import json, os, re, time, threading, uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
_REGISTRY_PATH = _SCRIPT_DIR / "assets" / "constraints_registry.json"
_DASHBOARD_TEMPLATE_PATH = _SCRIPT_DIR / "assets" / "audit_dashboard.html"
_DASHBOARD_DIR = _SCRIPT_DIR / "temp" / "dashboard"
_DASHBOARD_HTML_PATH = _DASHBOARD_DIR / "dashboard.html"
_AUDIT_LOG_PATH = _DASHBOARD_DIR / "audit_log.json"

_registry_cache = None
_registry_mtime = 0
_agent_ref = None
_control_server = None
_dashboard_server = None
_CONTROL_HOST = "127.0.0.1"
_CONTROL_PORT = 8766
_DASHBOARD_PORT = 8765


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
            should_copy = "semantic-findings" not in existing or "renderSemanticFindings" not in existing
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


def _extract_tool_args_text(tool_calls, scope="tool_args"):
    """Flatten tool args into searchable text.

    scope="tool_args"  – all tools (default, backward-compat)
    scope="exec_only"  – only code-execution tools (code_run/shell/bash/web_execute_js)
    """
    _CODE_EXEC = {"code_run", "shell", "bash", "web_execute_js"}
    parts = []
    for tc in (tool_calls or []):
        if scope == "exec_only" and tc.get("name", "") not in _CODE_EXEC:
            continue
        args = tc.get("args", {})
        for k, v in args.items():
            if isinstance(v, str):
                parts.append(v)
    return "\n".join(parts)


def _check_code_pattern(pattern, text):
    """Check if pattern exists in text (regex or plain)."""
    try:
        return bool(re.search(pattern, text, re.IGNORECASE))
    except re.error:
        return pattern.lower() in text.lower()


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
        return "pass"

    tool_names = {tc.get("tool_name", "") for tc in (tool_calls or [])}
    has_write = bool(tool_names & _WRITE_TOOLS)
    return "fail" if has_write else "pass"


def _check_consecutive_execution(rule, tool_calls, ctx):
    """R004: >=threshold consecutive turns of execution tools without subagent = fail.
    NOTE: _CONSEC_EXEC_HISTORY is populated by _on_turn_end BEFORE this is called."""
    tool_names = [tc.get("tool_name", "") for tc in (tool_calls or [])]
    has_exec = bool(set(tool_names) & _EXEC_TOOLS)
    if not has_exec:
        return "pass"

    threshold = rule.get("detection", {}).get("threshold", 3)

    # Check last N entries (already includes current turn from _on_turn_end)
    recent = _CONSEC_EXEC_HISTORY[-threshold:]
    if len(recent) < threshold:
        return "pass"

    all_exec_no_sub = all(r["exec"] and not r["subagent"] for r in recent)
    return "fail" if all_exec_no_sub else "pass"


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


def _extract_tokens(ctx, response):
    """Best-effort token extraction across OpenAI/Anthropic/GA budget shapes."""
    usage = _first_value(
        ctx.get("usage"),
        ctx.get("tokens"),
        _get_nested(response, ["usage"]),
        _get_nested(response, ["response", "usage"]),
    )
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
        _get_nested(usage, ["prompt_tokens_details", "cached_tokens"]),
    ) or 0
    try:
        import budget_tracker
        if not (input_tokens or output_tokens) and hasattr(budget_tracker, "last_usage"):
            last_usage = budget_tracker.last_usage()
            input_tokens = _first_value(_get_nested(last_usage, ["input"]), _get_nested(last_usage, ["input_tokens"])) or 0
            output_tokens = _first_value(_get_nested(last_usage, ["output"]), _get_nested(last_usage, ["output_tokens"])) or 0
            cached_tokens = _first_value(_get_nested(last_usage, ["cached"]), _get_nested(last_usage, ["cache_read_input_tokens"])) or 0
    except Exception:
        pass
    try:
        import llmcore
        if not (input_tokens or output_tokens) and hasattr(llmcore, "last_usage"):
            last_usage = llmcore.last_usage()
            input_tokens = _first_value(_get_nested(last_usage, ["input"]), _get_nested(last_usage, ["input_tokens"]), _get_nested(last_usage, ["prompt_tokens"])) or 0
            output_tokens = _first_value(_get_nested(last_usage, ["output"]), _get_nested(last_usage, ["output_tokens"]), _get_nested(last_usage, ["completion_tokens"])) or 0
            cached_tokens = _first_value(
                _get_nested(last_usage, ["cached"]),
                _get_nested(last_usage, ["cache_read_input_tokens"]),
                _get_nested(last_usage, ["input_tokens_details", "cached_tokens"]),
                _get_nested(last_usage, ["prompt_tokens_details", "cached_tokens"]),
            ) or 0
    except Exception:
        pass
    return {"input": int(input_tokens or 0), "output": int(output_tokens or 0), "cached": int(cached_tokens or 0)}


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
    """Check if tool calls access sensitive files (mykey.py, .env, credentials, etc.)."""
    import re
    _SENSITIVE_FILES = {'mykey.py', '.env', '.env.local', 'credentials.json',
                        'git-credentials', '.pem', '.key'}
    _SENSITIVE_PATTERNS = [
        re.compile(r'mykey', re.I),
        re.compile(r'\.env\b', re.I),
        re.compile(r'api[_-]?key', re.I),
        re.compile(r'secret[_-]?key', re.I),
        re.compile(r'credentials', re.I),
        re.compile(r'GITHUB_TOKEN', re.I),
    ]
    for tc in (tool_calls or []):
        args = tc.get("args", {})
        args_text = json.dumps(args, ensure_ascii=False)
        path_val = args.get("path", "") or ""
        # Check file path targeting sensitive files
        for sf in _SENSITIVE_FILES:
            if sf in path_val or sf in args_text:
                return "fail"
        # Check patterns in args
        for sp in _SENSITIVE_PATTERNS:
            if sp.search(args_text):
                return "fail"
    return "pass"


def _check_claim_without_evidence(det, tool_calls, ctx):
    """Check if assistant claims completion without verification evidence in this turn."""
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
    _EVIDENCE_TOOLS = {'code_run', 'web_scan', 'web_execute_js', 'file_read'}
    _EVIDENCE_KW = ['test', 'verify', 'screenshot', '截图', 'diff', 'DOM', 'stdout', '验证', 'pass', 'PASS']

    has_claim = any(p.search(text) for p in _CLAIM_PATTERNS)
    if not has_claim:
        return "skip"
    # Check tool calls for evidence actions
    tool_names = {tc.get("tool_name", "") for tc in (tool_calls or [])}
    if tool_names & _EVIDENCE_TOOLS:
        return "pass"
    # Check tool args / response for evidence keywords
    all_args = json.dumps([tc.get("args", {}) for tc in (tool_calls or [])], ensure_ascii=False).lower()
    for kw in _EVIDENCE_KW:
        if kw.lower() in all_args or kw.lower() in text.lower():
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
    results = []
    args_text = _extract_tool_args_text(tool_calls)
    all_items = registry.get("constraints", []) + registry.get("rules", [])

    for item in all_items:
        if not item.get("active", True):
            continue
        det = item.get("detection", {})
        det_type = det.get("type", "")
        status = "skip"

        if det_type == "code_pattern":
            pattern = det.get("pattern", "")
            scope = det.get("scope", "tool_args")
            scoped_text = _extract_tool_args_text(tool_calls, scope)
            if scoped_text:
                found = _check_code_pattern(pattern, scoped_text)
                # For constraints (forbidden), finding = fail
                if found and item["id"].startswith("C"):
                    status = "fail"
                elif found and item["id"].startswith("R"):
                    # R005/R006 are also "forbidden" patterns
                    status = "fail"
                elif not found:
                    status = "pass"
            else:
                # No relevant tools in scope → constraint not triggered → pass
                status = "pass"

        elif det_type == "tool_negative":
            status = _check_tool_negative(item, tool_calls)

        elif det_type == "sequence_check":
            status = _check_sequence(item, tool_calls)

        elif det_type == "consecutive_execution_check":
            status = _check_consecutive_execution(item, tool_calls, ctx)

        elif det_type == "context_check":
            status = _check_context(item, tool_calls, ctx)

        elif det_type == "manual":
            status = "skip"

        # ── MVP-v4 automated detection branches ──
        elif det_type == "summary_check":
            status = _check_summary_present(det, ctx)

        elif det_type == "sensitive_access":
            status = _check_sensitive_access(det, tool_calls)

        elif det_type == "claim_without_evidence":
            status = _check_claim_without_evidence(det, tool_calls, ctx)

        elif det_type == "memory_write_check":
            status = _check_memory_write(det, tool_calls, ctx)

        results.append({
            "id": item["id"],
            "name": item["name"],
            "status": status
        })

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
        tool_calls = ctx.get("tool_calls", [])
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

        # Update consecutive execution history for R004
        tool_names = [tc.get("tool_name", "") for tc in (tool_calls or [])]
        has_exec = any(t in _EXEC_TOOLS for t in tool_names)
        has_subagent = any("subagent" in t.lower() for t in tool_names)
        _CONSEC_EXEC_HISTORY.append({"exec": has_exec, "subagent": has_subagent})
        if len(_CONSEC_EXEC_HISTORY) > 20:
            _CONSEC_EXEC_HISTORY[:] = _CONSEC_EXEC_HISTORY[-20:]
        model = _extract_model(ctx, ga_self, response)
        tokens = _extract_tokens(ctx, response)

        # Budget info (try import budget_tracker)
        budget = {"used_pct": 0, "tier": "unknown", "signal": "N/A"}
        try:
            import budget_tracker
            if hasattr(budget_tracker, 'get_status'):
                budget = budget_tracker.get_status()
        except (ImportError, Exception):
            pass

        # Load registry and run checks
        registry = _load_registry()
        checks = _run_checks(registry, tool_calls, ctx)

        # Detect subagent usage
        subagent = None
        tool_names = [tc.get("tool_name", "") for tc in (tool_calls or [])]
        if any("subagent" in tn.lower() for tn in tool_names):
            subagent = {"tools": tool_names, "turn": turn}

        # Violations
        violations = [c for c in checks if c["status"] == "fail"]

        # Build event record
        event = {
            "task_id": _current_task_id(_agent_ref),
            "turn": turn,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "summary": summary[:200] if summary else "",
            "model": model,
            "tools_used": tool_names,
            "tokens": tokens,
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
            with open(_DASHBOARD_DIR / "audit_error.log", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} ERROR: {e}\n")
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
    if not hasattr(agent, '_turn_end_hooks'):
        agent._turn_end_hooks = {}
    agent._turn_end_hooks['ga_audit'] = _on_turn_end
    # Write initial registry snapshot for dashboard
    _DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    registry = _load_registry()
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
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {"ok": True})

    def do_POST(self):
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
    global _agent_ref
    _agent_ref = agent
    _ensure_dashboard_assets()
    _install_task_id_hook(agent)
    ok = _orig_install(agent)
    _ensure_control_server()
    _ensure_dashboard_server()
    return ok