import sys, os, re, json, time, threading, importlib
from datetime import datetime
from pathlib import Path
import tempfile, traceback, subprocess, itertools, collections, difflib, shutil
if sys.stdout is None: sys.stdout = open(os.devnull, "w")
if sys.stderr is None: sys.stderr = open(os.devnull, "w")
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent_loop import BaseHandler, StepOutcome, json_default
from memory.mem_manager import compress_working_memory
from ga_dispatch_gate import DispatchGate
from ga_coding_gate import CodingGate
script_dir = os.path.dirname(os.path.abspath(__file__))

def code_run(code, code_type="python", timeout=60, cwd=None, code_cwd=None, stop_signal=None, maxlen=10000):
    """代码执行器
    python: 运行复杂的 .py 脚本（文件模式）
    powershell/bash: 运行单行指令（命令模式）
    优先使用python，仅在必要系统操作时使用powershell"""
    preview = (code[:60].replace('\n', ' ') + '...') if len(code) > 60 else code.strip()
    yield f"[Action] Running {code_type} in {os.path.basename(cwd)}: {preview}\n"
    cwd = cwd or os.path.join(script_dir, 'temp'); tmp_path = None
    if code_type in ["python", "py"]:
        tmp_file = tempfile.NamedTemporaryFile(suffix=".ai.py", delete=False, mode='w', encoding='utf-8', dir=code_cwd)
        cr_header = os.path.join(script_dir, 'assets', 'code_run_header.py')
        if os.path.exists(cr_header): tmp_file.write(open(cr_header, encoding='utf-8').read())
        tmp_file.write(code)
        tmp_path = tmp_file.name
        tmp_file.close()
        cmd = [sys.executable, "-X", "utf8", "-u", tmp_path]   
    elif code_type in ["powershell", "bash", "sh", "shell", "ps1", "pwsh"]:
        if os.name == 'nt':
            _ps = "pwsh" if shutil.which("pwsh") else "powershell"
            utf8_prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            cmd = [_ps, "-NoProfile", "-NonInteractive", "-Command", utf8_prefix + code]
        else: cmd = ["bash", "-c", code]
    else:
        return {"status": "error", "msg": f"不支持的类型: {code_type}"}
    print("code run output:") 
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0 # SW_HIDE
    full_stdout = []

    def stream_reader(proc, logs):
        try:
            for line_bytes in iter(proc.stdout.readline, b''):
                try: line = line_bytes.decode('utf-8')
                except UnicodeDecodeError: line = line_bytes.decode('gbk', errors='ignore')
                logs.append(line)
                try: print(line, end="") 
                except: pass
        except: pass

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, cwd=cwd, startupinfo=startupinfo,
            creationflags=0x08000000 if os.name == 'nt' else 0
        )
        start_t = time.time()
        t = threading.Thread(target=stream_reader, args=(process, full_stdout), daemon=True)
        t.start()

        while t.is_alive():
            istimeout = time.time() - start_t > timeout
            if istimeout or stop_signal:
                process.kill()
                print("[Debug] Process killed due to timeout or stop signal.")
                if istimeout: full_stdout.append("\n[Timeout Error] 超时强制终止")
                else: full_stdout.append("\n[Stopped] 用户强制终止")
                break
            time.sleep(1)

        t.join(timeout=1)
        exit_code = process.poll()

        stdout_str = "".join(full_stdout)
        status = "success" if exit_code == 0 else "error"
        status_icon = "✅" if exit_code == 0 else "❌"
        if exit_code is None: status_icon = "⏳" 
        output_snippet = smart_format(stdout_str, max_str_len=600, omit_str='\n\n[omitted long output]\n\n')
        output_snippet = re.sub(r'`{4,}', lambda m: m.group(0)[:3] + '\u200b' + m.group(0)[3:], output_snippet)
        yield f"[Status] {status_icon} Exit Code: {exit_code}\n[Stdout]\n{output_snippet}\n"
        if process.stdout: threading.Thread(target=process.stdout.close, daemon=True).start()
        return {
            "status": status,
            "stdout": smart_format(stdout_str, max_str_len=maxlen, omit_str='\n\n[omitted long output]\n\n'),
            "exit_code": exit_code
        }
    except Exception as e:
        if 'process' in locals(): process.kill()
        return {"status": "error", "msg": str(e)}
    finally:
        if code_type == "python" and tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)


def ask_user(question, candidates=None):
    """question: 向用户提出的问题。candidates: 可选的候选项列表"""
    return {"status": "INTERRUPT", "intent": "HUMAN_INTERVENTION",
        "data": {"question": question, "candidates": candidates or []}}

import simphtml
from ga_resource_lock import browser_lock, hid_lock
driver = None
def first_init_driver():
    global driver
    from TMWebDriver import TMWebDriver
    driver = TMWebDriver()
    for i in range(20):
        time.sleep(1)
        sess = driver.get_all_sessions()
        if len(sess) > 0: break
    if len(sess) == 0: return 
    if len(sess) == 1: 
        #driver.newtab()
        time.sleep(3)

def web_scan(tabs_only=False, switch_tab_id=None, text_only=False, maxlen=35000):
    """获取当前页面的简化HTML内容和标签页列表。注意：简化过程会过滤边栏、浮动元素等非主体内容。
    tabs_only: 仅返回标签页列表，不获取HTML内容（节省token）。
    switch_tab_id: 可选参数，如果提供，则在扫描前切换到该标签页。
    应当多用execute_js，少全量观察html"""
    global driver
    with browser_lock:
        try:
            if driver is None: first_init_driver()
            if len(driver.get_all_sessions()) == 0:
                return {"status": "error", "msg": "没有可用的浏览器标签页，查L3记忆分析原因。"}
            tabs = []
            for sess in driver.get_all_sessions():
                sess.pop('connected_at', None)
                sess.pop('type', None)
                sess['url'] = sess.get('url', '')[:50] + ("..." if len(sess.get('url', '')) > 50 else "")
                tabs.append(sess)
            if switch_tab_id: driver.default_session_id = switch_tab_id
            result = {
                "status": "success",
                "metadata": {
                    "tabs_count": len(tabs), "tabs": tabs,
                    "active_tab": driver.default_session_id
                }
            }
            if not tabs_only:
                importlib.reload(simphtml); result["content"] = simphtml.get_html(driver, cutlist=True, maxchars=maxlen, text_only=text_only)
                if text_only: result['content'] = smart_format(result['content'], max_str_len=maxlen//3, omit_str='\n\n[omitted long content]\n\n')
            return result
        except Exception as e:
            return {"status": "error", "msg": format_error(e)}
    
def format_error(e):
    exc_type, exc_value, exc_traceback = sys.exc_info()
    tb = traceback.extract_tb(exc_traceback)
    if tb:
        f = tb[-1]
        fname = os.path.basename(f.filename)
        return f"{exc_type.__name__}: {str(e)} @ {fname}:{f.lineno}, {f.name} -> `{f.line}`"
    return f"{exc_type.__name__}: {str(e)}"

def log_memory_access(path):
    if 'memory' not in path: return
    stats_file = os.path.join(script_dir, 'memory/file_access_stats.json')
    try:
        with open(stats_file, 'r', encoding='utf-8') as f: stats = json.load(f)
    except: stats = {}
    fname = os.path.basename(path)
    stats[fname] = {'count': stats.get(fname, {}).get('count', 0) + 1, 'last': datetime.now().strftime('%Y-%m-%d')}
    with open(stats_file, 'w', encoding='utf-8') as f: json.dump(stats, f, indent=2, ensure_ascii=False)

def web_execute_js(script, switch_tab_id=None, no_monitor=False):
    """执行 JS 脚本来控制浏览器，并捕获结果和页面变化"""
    global driver
    with browser_lock:
        try:
            if driver is None: first_init_driver()
            if len(driver.get_all_sessions()) == 0: return {"status": "error", "msg": "没有可用的浏览器标签页，查L3记忆分析原因。"}
            if switch_tab_id: driver.default_session_id = switch_tab_id
            result = simphtml.execute_js_rich(script, driver, no_monitor=no_monitor)
            return result
        except Exception as e: return {"status": "error", "msg": format_error(e)}

def expand_file_refs(text, base_dir=None):
    """展开文本中的 {{file:路径:起始行:结束行}} 引用为实际文件内容。
    可与普通文本混排。展开失败抛 ValueError。
    base_dir: 相对路径的基准目录，默认为进程 cwd"""
    pattern = r'\{\{file:(.+?):(\d+):(\d+)\}\}'
    def replacer(match):
        path, start, end = match.group(1), int(match.group(2)), int(match.group(3))
        path = os.path.abspath(os.path.join(base_dir or '.', path))
        if not os.path.isfile(path): raise ValueError(f"引用文件不存在: {path}")
        with open(path, 'r', encoding='utf-8') as f: lines = f.readlines()
        if start < 1 or end > len(lines) or start > end: raise ValueError(f"行号越界: {path} 共{len(lines)}行, 请求{start}-{end}")
        return ''.join(lines[start-1:end])
    return re.sub(pattern, replacer, text)
    
def file_patch(path: str, old_content: str, new_content: str):
    """在文件中寻找唯一的 old_content 块并替换为 new_content"""
    path = str(Path(path).resolve())
    try:
        if not os.path.exists(path): return {"status": "error", "msg": "文件不存在"}
        with open(path, 'r', encoding='utf-8') as f: full_text = f.read()
        if not old_content: return {"status": "error", "msg": "old_content 为空，请确认 arguments"}
        count = full_text.count(old_content)
        if count == 0: return {"status": "error", "msg": "未找到匹配的旧文本块，建议：先用 file_read 确认当前内容，再分小段进行 patch。若多次失败则询问用户，严禁自行使用 overwrite 或代码替换。"}
        if count > 1: return {"status": "error", "msg": f"找到 {count} 处匹配，无法确定唯一位置。请提供更长、更具体的旧文本块以确保唯一性。建议：包含上下文行来增强特征，或分小段逐个修改。"}
        updated_text = full_text.replace(old_content, new_content)
        # ── ScriptGuard: validate memory/*.py before write ──
        try:
            from script_guard import validate_python_write as _sg_validate
            _sg_ok, _sg_err = _sg_validate(path, updated_text)
            if not _sg_ok: return {"status": "error", "msg": f"[ScriptGuard] 写入被拦截 - {_sg_err}"}
        except ImportError: pass
        with open(path, 'w', encoding='utf-8') as f: f.write(updated_text)
        return {"status": "success", "msg": "文件局部修改成功"}
    except Exception as e: return {"status": "error", "msg": str(e)}

_read_dirs = set()
def _scan_files(base, depth=2):
    try:
        for e in os.scandir(base):
            if e.is_file(): yield (e.name, e.path)
            elif depth > 0 and e.is_dir(follow_symlinks=False): yield from _scan_files(e.path, depth - 1)
    except (PermissionError, OSError): pass
def file_read(path, start=1, keyword=None, count=120, show_linenos=True):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            stream = ((i, l.rstrip('\r\n')) for i, l in enumerate(f, 1))
            stream = itertools.dropwhile(lambda x: x[0] < start, stream)
            if keyword:
                before = collections.deque(maxlen=max(0, count//3))
                for i, l in stream:
                    if keyword.lower() in l.lower():
                        res = list(before) + [(i, l)] + list(itertools.islice(stream, max(0, count - len(before) - 1)))
                        break
                    before.append((i, l))
                else: return f"Keyword '{keyword}' not found after line {start}. Falling back to content from line {start}:\n\n" \
                               + file_read(path, start, None, count, show_linenos)
            else: res = list(itertools.islice(stream, count))
            realcnt = len(res); L_MAX = min(max(100, 256000//max(realcnt,1)), 8000); TAG = " ... [TRUNCATED]"
            remaining = sum(1 for _ in itertools.islice(stream, 5000))
            total_lines = (res[0][0] - 1 if res else start - 1) + realcnt + remaining
            tl_str = f"{total_lines}+" if remaining >= 5000 else str(total_lines)
            partial = total_lines > realcnt
            governance_hint = " Prefer keyword or start+count ranges before requesting more." if partial and not keyword else ""
            total_tag = f"[FILE] {tl_str} lines" + (f" | PARTIAL showing {realcnt}; assess need for more.{governance_hint}" if partial else "") + "\n"
            res = [(i, l if len(l) <= L_MAX else l[:L_MAX] + TAG) for i, l in res]
            result = "\n".join(f"{i}|{l}" if show_linenos else l for i, l in res)
            if show_linenos: result = total_tag + result
            elif partial: result += f"\n\n[FILE PARTIAL: showing {realcnt}/{tl_str} lines; assess need for more.{governance_hint}]"
            _read_dirs.add(os.path.dirname(os.path.abspath(path)))
            return result
    except FileNotFoundError:
        msg = f"Error: File not found: {path}"
        try:
            tgt = os.path.basename(path); scan = os.path.dirname(os.path.dirname(os.path.abspath(path)))
            roots = [scan] + [d for d in _read_dirs if not d.startswith(scan)]
            cands = list(itertools.islice((c for base in roots for c in _scan_files(base)), 2000))
            top = sorted([(difflib.SequenceMatcher(None, tgt.lower(), c[0].lower()).ratio(), c) for c in cands[:2000]], key=lambda x: -x[0])[:5]
            top = [(s, c) for s, c in top if s > 0.3]
            if top: msg += "\n\nDid you mean:\n" + "\n".join(f"  {c[1]}  ({s:.0%})" for s, c in top)
        except Exception: pass
        return msg
    except Exception as e: return f"Error: {str(e)}"

def smart_format(data, max_str_len=100, omit_str=' ... '):
    if not isinstance(data, str): data = str(data)
    if len(data) < max_str_len + len(omit_str)*2: return data
    return f"{data[:max_str_len//2]}{omit_str}{data[-max_str_len//2:]}"


def build_execution_memory_cycle_prompt(response_text, tool_calls, tool_results, violations=None, history_info=None):
    """Build a compact turn-end prompt for the lightweight Execution Memory Cycle.

    The cycle is intentionally advisory only: recall intent -> evidence gap -> repair candidate.
    It must not mutate task state or replace existing constraint/dispatch gates.
    """
    response_text = response_text or ""
    tool_calls = tool_calls or []
    tool_results = tool_results or []
    violations = violations or []
    history_info = history_info or []

    tool_names = [tc.get('tool_name', '') for tc in tool_calls if isinstance(tc, dict)]
    has_verify_tool = any(name in {'code_run', 'web_execute_js', 'web_scan'} for name in tool_names)
    has_memory_checkpoint = any(name == 'update_working_checkpoint' for name in tool_names)
    read_sop_or_memory = False
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        args = tc.get('args', {}) or {}
        path = str(args.get('path', '')).lower()
        if tc.get('tool_name') == 'file_read' and ('memory/' in path or path.endswith('.md') or '_sop' in path):
            read_sop_or_memory = True
            break

    failed_tools = []
    for tr in tool_results:
        s = str(tr)
        sl = s.lower()
        if '"status": "error"' in sl or "'status': 'error'" in sl or 'traceback' in sl or 'exit_code": 1' in sl:
            failed_tools.append(smart_format(s, 180))

    completion_claim = re.search(r'(已完成|已修复|通过验证|验证通过|可用|done|fixed|passed)', response_text, re.IGNORECASE)
    evidence_terms = re.search(r'(py_compile|pytest|测试|验证|截图|diff|git status|exit_code|stdout|工具证据)', response_text, re.IGNORECASE)

    gaps = []
    repairs = []
    has_constraint_remediation = bool(violations) and any(
        isinstance(h, str) and (
            '[ENGINE]' in h or
            '约束引擎检测到' in h or
            '[DANGER]' in h or
            '[Execution Memory Cycle]' in h
        )
        for h in history_info[-8:]
    )
    checkpoint_risky_context = (
        bool(failed_tools) or
        bool(violations) or
        bool(completion_claim) or
        any(kw in response_text for kw in ('多步', '复杂', 'memory', '记忆', '失败', '报错')) or
        len(tool_calls) >= 3
    )
    if violations and not has_constraint_remediation:
        vids = ', '.join(str(v.get('constraint_id', '?')) for v in violations[:3] if isinstance(v, dict))
        gaps.append(f'constraint violation(s): {vids or "unknown"}')
        repairs.append('before next action, state which rule failed and close it with one targeted tool call')
    if failed_tools:
        gaps.append('tool failure/error observed')
        repairs.append('collect error details, form one hypothesis, then retry or switch strategy (no blind repeat)')
    if completion_claim and not (has_verify_tool or evidence_terms):
        gaps.append('completion/fix/pass claim without explicit verification evidence')
        repairs.append('run or cite concrete verification before claiming done/fixed/usable')
    if read_sop_or_memory and not has_memory_checkpoint and checkpoint_risky_context:
        gaps.append('SOP/memory was read but key constraints may not be persisted')
        repairs.append('if task is multi-step, call update_working_checkpoint with extracted constraints')

    if not gaps:
        return ''

    last_user = ''
    for h in reversed(history_info[-20:]):
        if isinstance(h, str) and (h.startswith('[User]') or h.startswith('[USER]')):
            last_user = h.split(']', 1)[-1].strip()
            break
    recall = smart_format(last_user, 120) if last_user else 'continue current user task under active constraints'
    gap_text = '; '.join(gaps[:3])
    repair_text = '; '.join(dict.fromkeys(repairs[:3]))
    return ("\n[Execution Memory Cycle] "
            f"Recall Intent: {recall}\n"
            f"Evidence Gap: {gap_text}\n"
            f"Repair Candidate: {repair_text}")


def build_glue_gate_prompt(response_text, tool_calls, history_info=None):
    """Advisory reminder for reuse-first design before adding generic capability."""
    response_text = response_text or ""
    tool_calls = tool_calls or []
    history_info = history_info or []

    recent = "\n".join(str(x) for x in history_info[-12:])
    last_user = ""
    for h in reversed(history_info[-20:]):
        if isinstance(h, str) and (h.startswith('[User]') or h.startswith('[USER]')):
            last_user = h.split(']', 1)[-1].strip()
            break

    checked_glue = 'glue_coding_gate_sop' in (recent + '\n' + response_text)
    if not checked_glue:
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            args = tc.get('args', {}) or {}
            path = str(args.get('path', '')).lower()
            if tc.get('tool_name') == 'file_read' and 'glue_coding_gate_sop' in path:
                checked_glue = True
                break
    if checked_glue:
        return ''

    trigger_text = f"{last_user}\n{response_text}"
    trigger_re = re.compile(
        r"(?i)(新增|新建|引入|接入|封装|适配|自研|重构|架构|依赖|SDK|API|infra|infrastructure|utility|adapter|wrapper|client|auth|queue|scheduler|workflow|orchestration|from scratch)"
    )
    if not trigger_re.search(trigger_text):
        return ''

    return ("\n\n[Glue Gate Reminder] 当前任务可能涉及新增能力/依赖/基础设施/通用工具或工作流编排。"
            "在写代码或定方案前，请先读取 `memory/glue_coding_gate_sop.md`，输出成熟候选、证据、胶水边界、验证与回滚；"
            "若选择自研，需说明偏离复用路径的理由。")


def consume_file(dr, file):
    if dr and os.path.exists(os.path.join(dr, file)): 
        with open(os.path.join(dr, file), encoding='utf-8', errors='replace') as f: content = f.read()
        os.remove(os.path.join(dr, file))
        return content

class GenericAgentHandler(BaseHandler):
    '''Generic Agent 工具库，包含多种工具的实现。工具函数自动加上了 do_ 前缀。实际工具名没有前缀。'''
    def __init__(self, parent, last_history=None, cwd='./temp'):
        self.parent = parent
        self.working = {}
        self.cwd = cwd;  self.current_turn = 0
        self.history_info = last_history if last_history else []
        self.code_stop_signal = []
        self._done_hooks = []
        self._dispatch_gate = DispatchGate()
        self._coding_gate = CodingGate(mode="audit")

    def _check_dispatch_gate(self, tool_name, args):
        """Check dispatch gate; returns StepOutcome if blocked, else None."""
        allowed, reason = self._dispatch_gate.check_tool(tool_name, args)
        if not allowed:
            return StepOutcome(reason, next_prompt="\n" + reason + "\n")
        return None

    def _check_coding_gate(self, tool_name, args, response):
        """Check coding gate; returns StepOutcome if blocked, else None. WARN handled in turn_end."""
        assistant_text = getattr(response, 'content', '') or ''
        decision, message = self._coding_gate.check_tool(tool_name, args, assistant_text)
        if decision == "BLOCK":
            return StepOutcome({"status": "error", "msg": message}, next_prompt="\n" + message + "\n")
        return None

    def _get_abs_path(self, path):
        if not path: return ""
        return os.path.abspath(os.path.join(self.cwd, path))   

    def _extract_code_block(self, response, code_type):
        code_type = {'python':'python|py', 'powershell':'powershell|ps1|pwsh', 'bash':'bash|sh|shell'}.get(code_type, re.escape(code_type))
        matches = re.findall(rf"```(?:{code_type})\n(.*?)\n```", response.content, re.DOTALL)
        return matches[-1].strip() if matches else None

    def do_code_run(self, args, response):
        '''执行代码片段，有长度限制，不允许代码中放大量数据，如有需要应当通过文件读取进行。'''
        _blocked = self._check_dispatch_gate('code_run', args)
        if _blocked: return _blocked
        _cg = self._check_coding_gate('code_run', args, response)
        if _cg: return _cg
        code_type = args.get("type", "python")
        code = args.get("code") or args.get("script")
        if not code:
            code = self._extract_code_block(response, code_type)
            if not code: return StepOutcome("[Error] Code missing. Must use reply code block or 'script' arg.", next_prompt="\n")
        try: timeout = int(args.get("timeout", 60))
        except: timeout = 60
        raw_path = os.path.join(self.cwd, args.get("cwd", './'))
        cwd = os.path.normpath(os.path.abspath(raw_path))
        code_cwd = os.path.normpath(self.cwd)
        maxlen = 10000 // args.get('_tool_num', 1)
        if code_type == 'python' and args.get("inline_eval"):
            ns = {'handler':self, 'parent':self.parent, 'history':json.dumps(self.parent.llmclient.backend.history)}
            old_cwd = os.getcwd()
            try:
                os.chdir(cwd)
                try:
                    try: result = repr(eval(code, ns))
                    except SyntaxError: exec(code, ns); result = ns.get('_r', 'OK')
                except Exception as e: result = f'Error: {e}'
            finally: os.chdir(old_cwd)
        else: result = yield from code_run(code, code_type, timeout, cwd, code_cwd=code_cwd, stop_signal=self.code_stop_signal, maxlen=maxlen)
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome(result, next_prompt=next_prompt)
    
    def do_ask_user(self, args, response):
        question = args.get("question", "请提供输入：")
        candidates = args.get("candidates", [])
        result = ask_user(question, candidates)
        yield f"Waiting for your answer ...\n"
        return StepOutcome(result, next_prompt="", should_exit=True)
    
    def do_web_scan(self, args, response):
        '''获取当前页面内容和标签页列表。也可用于切换标签页。
        注意：HTML经过简化，边栏/浮动元素等可能被过滤。如需查看被过滤的内容请用execute_js。
        tabs_only=true时仅返回标签页列表，不获取HTML（省token）'''
        tabs_only = args.get("tabs_only", False)
        switch_tab_id = args.get("switch_tab_id", None)
        text_only = args.get("text_only", False)
        maxlen = 35000 // args.get('_tool_num', 1)
        result = web_scan(tabs_only=tabs_only, switch_tab_id=switch_tab_id, text_only=text_only, maxlen=maxlen)
        content = result.pop("content", None)
        yield f'[Info] {str(result)}\n'
        if content: result = json.dumps(result, ensure_ascii=False, default=json_default) + f"\n```html\n{content}\n```"
        next_prompt = "\n"
        return StepOutcome(result, next_prompt=next_prompt)
    
    def do_web_execute_js(self, args, response):
        '''web情况下的优先使用工具，执行任何js达成对浏览器的*完全*控制。支持将结果保存到文件供后续读取分析。'''
        _blocked = self._check_dispatch_gate('web_execute_js', args)
        if _blocked: return _blocked
        script = args.get("script", "") or self._extract_code_block(response, "javascript")
        if not script: return StepOutcome("[Error] Script missing. Use ```javascript block or 'script' arg.", next_prompt="\n")
        abs_path = self._get_abs_path(script.strip())
        if os.path.isfile(abs_path):
            with open(abs_path, 'r', encoding='utf-8') as f: script = f.read()
        save_to_file = args.get("save_to_file", "")
        switch_tab_id = args.get("switch_tab_id") or args.get("tab_id")
        no_monitor = args.get("no_monitor", False)
        result = web_execute_js(script, switch_tab_id=switch_tab_id, no_monitor=no_monitor)
        if save_to_file and "js_return" in result:
            content = str(result["js_return"] or '')
            abs_path = self._get_abs_path(save_to_file)
            result["js_return"] = smart_format(content, max_str_len=170)
            try:
                with open(abs_path, 'w', encoding='utf-8') as f: f.write(str(content))
                result["js_return"] += f"\n\n[已保存完整内容到 {abs_path}]"
            except: result['js_return'] += f"\n\n[保存失败，无法写入文件 {abs_path}]"
        show = smart_format(json.dumps(result, ensure_ascii=False, indent=2, default=json_default), max_str_len=300)
        try: print("Web Execute JS Result:", show)
        except: pass
        yield f"JS 执行结果:\n{show}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        result = json.dumps(result, ensure_ascii=False, default=json_default)
        maxlen = 8000 // args.get('_tool_num', 1)
        return StepOutcome(smart_format(result, max_str_len=maxlen), next_prompt=next_prompt)
    
    def do_file_patch(self, args, response):
        _blocked = self._check_dispatch_gate('file_patch', args)
        if _blocked: return _blocked
        _cg = self._check_coding_gate('file_patch', args, response)
        if _cg: return _cg
        path = self._get_abs_path(args.get("path", ""))
        old_content = args.get("old_content", "")
        new_content = args.get("new_content", "")
        try: new_content = expand_file_refs(new_content, base_dir=self.cwd)
        except ValueError as e:
            yield f"[Status] ❌ 引用展开失败: {e}\n"
            return StepOutcome({"status": "error", "msg": str(e)}, next_prompt="\n")
        result = file_patch(path, old_content, new_content)
        yield f"\n{str(result)}\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        return StepOutcome(result, next_prompt=next_prompt)
    
    def do_file_write(self, args, response):
        '''用于对整个文件的大量处理，精细修改要用file_patch。
        需要将要写入的内容放在<file_content>标签内，或者放在代码块中'''
        _blocked = self._check_dispatch_gate('file_write', args)
        if _blocked: return _blocked
        _cg = self._check_coding_gate('file_write', args, response)
        if _cg: return _cg
        path = self._get_abs_path(args.get("path", ""))
        mode = args.get("mode", "overwrite")  # overwrite/append/prepend
        action_str = {"prepend": "Prepending to", "append": "Appending to"}.get(mode, "Overwriting")
        yield f"[Action] {action_str} file: {os.path.basename(path)}\n"

        def extract_robust_content(text):
            tags = re.findall(r"<file_content[^>]*>(.*?)</file_content>", text, re.DOTALL)
            if tags: return tags[-1].strip()
            blocks = re.findall(r"```[^\n]*\n([\s\S]*?)```", text)
            if blocks: return blocks[-1].strip()
            return None
        
        content = args.get('content') or extract_robust_content(response.content)
        if not content:
            yield f"[Status] ❌ 失败: 未在回复中找到<file_content>代码块内容\n"
            return StepOutcome({"status": "error", "msg": "No content found. Blank is not supported. Put content inside <file_content>...</file_content> tags in your reply body before call file_write."}, next_prompt="\n")
        try:
            new_content = expand_file_refs(content, base_dir=self.cwd)
            # ── ScriptGuard: validate memory/*.py before write ──
            try:
                from script_guard import validate_python_write as _sg_validate
                _sg_final = new_content
                if mode in ("prepend", "append"):
                    _sg_old = open(path, 'r', encoding="utf-8").read() if os.path.exists(path) else ""
                    _sg_final = (new_content + _sg_old) if mode == "prepend" else (_sg_old + new_content)
                _sg_ok, _sg_err = _sg_validate(path, _sg_final)
                if not _sg_ok:
                    yield f"[ScriptGuard] BLOCKED: {_sg_err}\n"
                    return StepOutcome({"status": "error", "msg": f"[ScriptGuard] {_sg_err}"}, next_prompt="\n")
            except ImportError: pass
            if mode == "prepend":
                old = open(path, 'r', encoding="utf-8").read() if os.path.exists(path) else ""
                open(path, 'w', encoding="utf-8").write(new_content + old)
            else:
                with open(path, 'a' if mode == "append" else 'w', encoding="utf-8") as f: f.write(new_content)
            yield f"[Status] ✅ {mode.capitalize()} 成功 ({len(new_content)} bytes)\n"
            next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
            return StepOutcome({"status": "success", 'writed_bytes': len(new_content)}, next_prompt=next_prompt)
        except Exception as e:
            yield f"[Status] ❌ 写入异常: {str(e)}\n"
            return StepOutcome({"status": "error", "msg": str(e)}, next_prompt="\n")
        
    def do_file_read(self, args, response):
        '''读取文件内容。从第start行开始读取。如有keyword则返回第一个keyword(忽略大小写)周边内容'''
        path = self._get_abs_path(args.get("path", ""))
        yield f"\n[Action] Reading file: {path}\n"
        start = args.get("start", 1)
        keyword = args.get("keyword")
        show_linenos = args.get("show_linenos", True)
        count_note = ""
        try:
            count = int(args.get("count", 120))
        except (TypeError, ValueError):
            count = 120
            count_note = "[file_read governance] Invalid count normalized to 120.\n"
        if count < 1:
            count = 1
            count_note = "[file_read governance] count below 1 normalized to 1.\n"
        elif count > 300:
            count = 300
            count_note = "[file_read governance] count capped at 300; prefer keyword or start+count ranges for large files.\n"
        result = file_read(path, start=start, keyword=keyword,
                           count=count, show_linenos=show_linenos)
        if count_note and not result.startswith("Error:"):
            result = count_note + result
        if show_linenos and not result.startswith("Error:"): result = '由于设置了show_linenos，以下返回信息为：(行号|)内容 。\n' + result 
        if ' ... [TRUNCATED]' in result: result += '\n\n（某些行被截断，如需完整内容可改用 code_run 读取）'
        maxlen = 15000 // args.get('_tool_num', 1)
        result = smart_format(result, max_str_len=maxlen, omit_str='\n\n[omitted long content]\n\n')
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        log_memory_access(path)
        if 'memory' in path or 'sop' in path: 
            next_prompt += "\n[SYSTEM TIPS] 正在读取记忆或SOP文件，若决定按sop执行请提取sop中的关键点（特别是靠后的）update working memory."
        return StepOutcome(result, next_prompt=next_prompt)
    
    def _in_plan_mode(self): return self.working.get('in_plan_mode')
    def _exit_plan_mode(self): self.working.pop('in_plan_mode', None)
    def enter_plan_mode(self, plan_path): 
        self.working['in_plan_mode'] = plan_path; self.max_turns = 100
        print(f"[Info] Entered plan mode with plan file: {plan_path}"); return plan_path
    def _check_plan_completion(self):
        if not os.path.isfile(p:=self._in_plan_mode() or ''): return None
        try: return len(re.findall(r'\[ \]', open(p, encoding='utf-8', errors='replace').read()))
        except: return None
    
    def do_update_working_checkpoint(self, args, response):
        '''为整个任务设定后续需要临时记忆的重点。'''
        key_info = args.get("key_info", "")
        related_sop = args.get("related_sop", "")
        if "key_info" in args: self.working['key_info'] = key_info
        if "related_sop" in args: self.working['related_sop'] = related_sop
        self.working['passed_sessions'] = 0
        yield f"[Info] Updated key_info and related_sop.\n"
        next_prompt = self._get_anchor_prompt(skip=args.get('_index', 0) > 0)
        #next_prompt += '\n[SYSTEM TIPS] 此函数一般在任务开始或中间时调用，如果任务已成功完成应该是start_long_term_update用于结算长期记忆。\n'
        return StepOutcome({"result": "working key_info updated"}, next_prompt=next_prompt)

    def _retry_or_exit(self, prompt):
        self._empty_ct = getattr(self, '_empty_ct', 0) + 1
        if self._empty_ct >= 3: return StepOutcome({}, should_exit=True)
        return StepOutcome({}, next_prompt=prompt)

    def do_no_tool(self, args, response):
        '''这是一个特殊工具，由引擎自主调用，不要包含在TOOLS_SCHEMA里。
        当模型在一轮中未显式调用任何工具时，由引擎自动触发。
        二次确认仅在回复几乎只包含<thinking>/<summary>和一段大代码块时触发。'''
        content = getattr(response, 'content', '') or ""
        thinking = getattr(response, 'thinking', '') or ""
        if not response or (not content.strip() and not thinking.strip()):
            self._empty_ct = getattr(self, '_empty_ct', 0) + 1
            if self._empty_ct >= 3: return StepOutcome({}, should_exit=True)
            yield "[Warn] LLM returned an empty response. Retrying...\n"
            return self._retry_or_exit("[System] Blank response, regenerate and tooluse")
        if '[!!! 流异常中断' in content[-100:] or '!!!Error:' in content[-100:]:
            return self._retry_or_exit("[System] Incomplete response. Regenerate and tooluse.")
        if 'max_tokens !!!]' in content[-100:]:
            return self._retry_or_exit("[System] max_tokens limit reached. Use multi small steps to do it.")
        
        if self._in_plan_mode() and any(kw in content for kw in ['任务完成', '全部完成', '已完成所有', '🏁']):
            if 'VERDICT' not in content and '[VERIFY]' not in content and '验证subagent' not in content:
                yield "[Warn] Plan模式完成声明拦截。\n"
                return StepOutcome({}, next_prompt="⛔ [验证拦截] 检测到你在plan模式下声称完成，但未执行[VERIFY]验证步骤。请先按plan_sop §四启动验证subagent，获得VERDICT后才能声称完成。")
            
        # 2. 检测"包含较大代码块但未调用工具"的情况
        # 关键特征：恰好1个大代码块 + 代码块直接结尾（后面只有空白）
        code_block_pattern = r"```[a-zA-Z0-9_]*\n[\s\S]{50,}?```"
        blocks = re.findall(code_block_pattern, content)
        if len(blocks) == 1:
            m = re.search(code_block_pattern, content)
            after_block = content[m.end():]
            if not after_block.strip():
                residual = content.replace(m.group(0), "")
                residual = re.sub(r"<thinking>[\s\S]*?</thinking>", "", residual, flags=re.IGNORECASE)
                residual = re.sub(r"<summary>[\s\S]*?</summary>", "", residual, flags=re.IGNORECASE)
                clean_residual = re.sub(r"\s+", "", residual)
                if len(clean_residual) <= 30:
                    yield "[Info] Detected large code block without tool call and no extra natural language. Requesting clarification.\n"
                    next_prompt = (
                        "[System] 检测到你在上一轮回复中主要内容是较大代码块，且本轮未调用任何工具。\n"
                        "如果这些代码需要执行、写入文件或进一步分析，请重新组织回复并显式调用相应工具"
                        "（例如：code_run、file_write、file_patch 等）；\n"
                        "如果只是向用户展示或讲解代码片段，请在回复中补充自然语言说明，"
                        "并明确是否还需要额外的实际操作。"
                    )
                    return StepOutcome({}, next_prompt=next_prompt)
                
        if self._in_plan_mode():
            remaining = self._check_plan_completion()
            if remaining == 0:
                self._exit_plan_mode(); yield "[Info] Plan完成：plan.md中0个[ ]残留，退出plan模式。\n"
        
        yield "[Info] Final response to user.\n"
        return StepOutcome(response, next_prompt=None)
    
    def do_start_long_term_update(self, args, response):
        '''Agent觉得当前任务完成后有重要信息需要记忆时调用此工具。'''
        prompt = '''### [总结提炼经验] 既然你觉得当前任务有重要信息需要记忆，请提取最近一次任务中【事实验证成功且长期有效】的环境事实、用户偏好、重要步骤，更新记忆。
本工具是标记开启结算过程，若已在更新记忆过程或没有值得记忆的点，忽略本次调用。
**如果没有经验证的，未来能用上的信息，忽略本次调用！**
**只能提取行动验证成功的信息**：
- **环境事实**（路径/凭证/配置）→ `file_patch` 更新 L2，同步 L1
- **复杂任务经验**（关键坑点/前置条件/重要步骤）→ L3 精简 SOP（只记你被坑得多次重试的核心要点）
**禁止**：临时变量、具体推理过程、未验证信息、通用常识、你可以轻松复现的细节、只是做了但没有验证的信息
**操作**：严格遵循提供的L0的记忆更新SOP。先 `file_read` 看现有 → 判断类型 → 最小化更新 → 无新内容跳过，保证对记忆库最小局部修改。\n
''' + get_global_memory()
        yield "[Info] Start distilling good memory for long-term storage.\n"
        path = './memory/memory_management_sop.md'
        if os.path.exists(path): result = 'This is L0:\n' + file_read(path, show_linenos=False)
        else: result = "Memory Management SOP not found. Do not update memory."
        return StepOutcome(result, next_prompt=prompt)

    def _fold_earlier(self, lines):
        FALLBACK = '直接回答了用户问题'
        parts, cnt, last = [], 0, ''
        def flush():
            if cnt:
                if FALLBACK in last: parts.append(f'[Agent]（{cnt} turns）')
                else: parts.append(f'{last}（{cnt} turns）')
        for line in lines:
            if line.startswith('[USER]'):
                flush(); parts.append(line); cnt = 0; last = ''
            else: cnt += 1; last = line
        flush()
        return "\n".join(parts[-100:])

    def _get_anchor_prompt(self, skip=False):
        if skip: return "\n"
        h = self.history_info; W = 30
        earlier = f'<earlier_context>\n{self._fold_earlier(h[:-W])}\n</earlier_context>\n' if len(h) > W else ""
        h_str = "\n".join(h[-W:])
        prompt = f"\n### [WORKING MEMORY]\n{earlier}<history>\n{h_str}\n</history>"
        prompt += f"\nCurrent turn: {self.current_turn}\n"
        if self.working.get('key_info'): prompt += f"\n<key_info>{self.working.get('key_info')}</key_info>"
        if self.working.get('related_sop'): prompt += f"\n有不清晰的地方请再次读取{self.working.get('related_sop')}"
        if getattr(self.parent, 'verbose', False):
            try: print(prompt)
            except: pass
        return prompt
    
    def turn_end_callback(self, response, tool_calls, tool_results, turn, next_prompt, exit_reason):
        # === Constraint Engine Monitor (接入点) ===
        try:
            import os as _os
            from ga_constraint_engine import load_constraints, evaluate_all
            _dsl_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'assets', 'constraints_dsl.json')
            _ce_constraints = load_constraints(_dsl_path)
            if _ce_constraints:
                _scripts = [tc['args'].get('script', '') for tc in tool_calls if tc.get('tool_name') == 'code_run' and tc.get('args', {}).get('script')]
                _usr = ''
                for _h in reversed(self.history_info):
                    if _h.startswith('[User]') or _h.startswith('[USER]'):
                        _usr = _h.split(']', 1)[-1].strip(); break
                _ce_ctx = {
                    'history': '\n'.join(self.history_info[-40:]),
                    'response_text': response.content,
                    'tool_calls': [{'tool_name': tc.get('tool_name', ''), 'args': tc.get('args', {})} for tc in tool_calls],
                    'scripts': _scripts,
                    'user_message': _usr,
                }
                _ce_results = evaluate_all(_ce_constraints, _ce_ctx)
                _violations = [r for r in _ce_results if isinstance(r, dict) and r.get('status') == 'fail']
                if _violations:
                    _vids = ', '.join(v.get('constraint_id', '?') for v in _violations[:5])
                    _vsum = '\n'.join(f"  - [{v.get('constraint_id','?')}] {v.get('reason','')}" for v in _violations[:5])
                    next_prompt += f"\n[ENGINE] 约束引擎检测到 {len(_violations)} 项违规 ({_vids})：\n{_vsum}"
                    # === Auto-remediation: inject MUST instructions for recoverable constraint failures ===
                    _remediations = []
                    for _v in _violations[:5]:
                        _vid = _v.get('constraint_id', '')
                        if _vid == 'REG-R009':
                            _remediations.append('[MUST] 你刚刚因决策前未查记忆/SOP而触发 REG-R009。在调用任何执行类工具（file_write/file_patch/code_run/web_execute_js等）之前，必须先 file_read 相关SOP或全局记忆文件。')
                        elif _vid == 'REG-R040':
                            _remediations.append('[MUST] 你刚刚因修改代码后未运行验证而触发 REG-R040。在本轮或下一轮中，必须调用 code_run 执行 test/lint/build/语法检查，并在结果中汇报验证状态。')
                    if _remediations:
                        next_prompt += '\n' + '\n'.join(_remediations)
        except Exception:
            pass
        # === END Constraint Engine Monitor ===
        # === Execution Memory Cycle (lightweight advisory loop) ===
        try:
            _emc_prompt = build_execution_memory_cycle_prompt(
                response.content, tool_calls, tool_results, _violations if '_violations' in locals() else [], self.history_info
            )
            if _emc_prompt:
                next_prompt += _emc_prompt
        except Exception:
            pass
        # === END Execution Memory Cycle ===
        # === Dispatch Gate ===
        try:
            _gate_tc = [{'tool_name': tc['tool_name'], 'args': tc.get('args', {})} for tc in tool_calls]
            _gate_level, _gate_prompt = self._dispatch_gate.on_turn_end(_gate_tc, response.content)
            if _gate_prompt:
                next_prompt += _gate_prompt
        except Exception:
            pass
        # === END Dispatch Gate ===
        # === Coding Gate (turn-end audit) ===
        try:
            _cg_tc = [{'tool_name': tc['tool_name'], 'args': tc.get('args', {})} for tc in tool_calls]
            _cg_decision, _cg_prompt = self._coding_gate.on_turn_end(_cg_tc, response.content)
            if _cg_prompt:
                next_prompt += _cg_prompt
        except Exception:
            pass
        # === END Coding Gate ===
        # === Glue Coding Gate Reminder (advisory only) ===
        try:
            _gg_tc = [{'tool_name': tc['tool_name'], 'args': tc.get('args', {})} for tc in tool_calls]
            _gg_prompt = build_glue_gate_prompt(response.content, _gg_tc, self.history_info)
            if _gg_prompt:
                next_prompt += _gg_prompt
        except Exception:
            pass
        # === END Glue Coding Gate Reminder ===
        _c = re.sub(r'```.*?```|<thinking>.*?</thinking>', '', response.content, flags=re.DOTALL)
        rsumm = re.search(r"<summary>(.*?)</summary>", _c, re.DOTALL)
        if rsumm: summary = rsumm.group(1).strip()
        else:
            tc = tool_calls[0]; tool_name, args = tc['tool_name'], tc['args']   # at least one because no_tool
            clean_args = {k: v for k, v in args.items() if not k.startswith('_')}
            summary = f"调用工具{tool_name}, args: {clean_args}"
            if tool_name == 'no_tool': summary = "直接回答了用户问题"
            next_prompt += "\n[DANGER] 你遗漏了<summary>，必须按协议一直在每次回复中用<summary>中输出极简单行摘要！" 
        summary = smart_format(summary, max_str_len=100)
        # Preserve numbered list items from response for context retention
        _numbered = re.findall(r'^\d+[.、]\s*.+', _c, re.MULTILINE)
        if _numbered:
            _items = '; '.join(n.strip()[:60] for n in _numbered[:7])
            if len(_items) > 200: _items = _items[:200] + '…'
            summary += f' | 选项: {_items}'
        self.history_info.append(f'[Agent] {summary}')
        # === Session Event Logger (turn_end auto-record) ===
        try:
            from memory.session_event_log import log_event as _sel_log
            _tool_names = [tc.get('tool_name', '') for tc in tool_calls]
            _sid = os.path.basename(getattr(self.parent, 'task_dir', '')) or 'unknown'
            _sel_log(module='ga', event_type='turn_end', severity='info',
                     data={'turn': turn, 'summary': summary, 'tools': _tool_names,
                           'exit_reason': exit_reason},
                     session_id=_sid)
        except Exception:
            pass
        # === END Session Event Logger ===
        # === Pattern Registry Scan (on long-term memory update) ===
        try:
            _tn_b = [tc.get('tool_name', '') for tc in tool_calls]
            if 'start_long_term_update' in _tn_b:
                from memory.pattern_registry import get_mature_patterns as _get_mp
                _mature = _get_mp()
                if _mature:
                    _plist = ', '.join(p.get('pattern_id', '?') for p in _mature[:5])
                    next_prompt += f"\n\n[Pattern Registry] {len(_mature)}个成熟pattern待固化: {_plist}。考虑用skill_solidifier处理。"
        except Exception:
            pass
        # === END Pattern Registry Scan ===
        if turn % 65 == 0 and 'plan' not in str(self.working.get('related_sop')):
            next_prompt += f"\n\n[DANGER] 已连续执行第 {turn} 轮。你必须总结情况进行ask_user，不允许继续重试。"
        elif turn % 7 == 0:
            next_prompt += f"\n\n[DANGER] 已连续执行第 {turn} 轮。禁止无效重试。若无有效进展，必须切换策略：1. 探测物理边界 2. 请求用户协助。如有需要，可调用 update_working_checkpoint 保存关键上下文。"
        elif turn % 10 == 0: next_prompt += get_global_memory()

        _plan = self._in_plan_mode()
        if _plan and turn >= 10 and turn % 5 == 0:
            next_prompt = f"[Plan Hint] 你正在计划模式。必须 file_read({_plan}) 确认当前步骤，回复开头引用：📌 当前步骤：...\n\n" + next_prompt
        if _plan and turn >= 90: next_prompt += f"\n\n[DANGER] Plan模式已运行 {turn} 轮，已达上限。必须 ask_user 汇报进度并确认是否继续。"

        # === peer hint (cross-session) ===
        peer_hint = consume_file(self.parent.task_dir, '_peer_hint')
        if peer_hint: next_prompt += f"\n[Peer Hint] 来自其他Agent的提示：{peer_hint}\n"

        injkeyinfo = consume_file(self.parent.task_dir, '_keyinfo')
        injprompt = consume_file(self.parent.task_dir, '_intervene')
        if injkeyinfo: self.working['key_info'] = self.working.get('key_info', '') + f"\n[MASTER] {injkeyinfo}"
        if injprompt: next_prompt += f"\n\n[MASTER] {injprompt}\n"
        _lc = getattr(self.parent, 'llmclient', None)
        _backend = getattr(_lc, 'backend', None) if _lc else None
        model_trace = getattr(_backend, 'model_trace', {}) if _backend else {}
        for hook in list(getattr(self.parent, '_turn_end_hooks', {}).values()): hook(locals())  # current readonly
        # === Review Hook: track project root + trigger on task done ===
        try:
            from ga_review_hook import track_project_root, run_review
            track_project_root(tool_calls, self.working)
            if exit_reason == 'CURRENT_TASK_DONE' and self.working.get('project_root'):
                run_review(self.working, self.parent)
        except Exception:
            pass
        # === END Review Hook ===
        return next_prompt

def get_global_memory():
    prompt = "\n"
    try:
        suffix = '_en' if os.environ.get('GA_LANG', '') == 'en' else ''
        with open(os.path.join(script_dir, 'memory/global_mem_insight.txt'), 'r', encoding='utf-8', errors='replace') as f: insight = f.read()
        with open(os.path.join(script_dir, f'assets/insight_fixed_structure{suffix}.txt'), 'r', encoding='utf-8') as f: structure = f.read()
        prompt += f'cwd = {os.path.join(script_dir, "temp")} (./)\n'
        prompt += f"\n[Memory] (../memory)\n"
        prompt += structure + '\n../memory/global_mem_insight.txt:\n'
        prompt += insight + "\n"
    except FileNotFoundError: pass
    return prompt
