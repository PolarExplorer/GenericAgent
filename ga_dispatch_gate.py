"""
ga_dispatch_gate.py - 分级硬门禁 for model_dispatch_sop
Graduated enforcement: open → warning → forced reflection → hard block

Thresholds (consecutive execution-tool turns without subagent dispatch):
  Level 0 (0-4):   open
  Level 1 (5-7):   soft warning - remind to reflect on dispatch
  Level 2 (8-10):  semi-block - forced reflection template, still allows execution
  Level 3 (11+):   hard block - refuse execution tools unless dispatching subagent

Reset conditions: user message / subagent call / ask_user / progress detected (halve)
Light exec (tests, small patches) count as 0.5 weight.
"""

import re

# Tools that count as "execution" (doing work the muscle model should do)
EXEC_TOOLS = frozenset({'code_run', 'file_write', 'file_patch', 'web_execute_js'})

# Patterns indicating a "light" exec (tests, small scripts) - weight 0.5
_LIGHT_EXEC_RE = re.compile(
    r'pytest|unittest|assert|test_|run.*test|verify|验证',
    re.IGNORECASE,
)

# Patterns indicating substantive progress in response text - triggers counter halving
_PROGRESS_RE = re.compile(
    r'PASS|FAIL|Error.*:|Traceback|根因|root cause|found|发现|修复|fixed|patched|'
    r'confirmed|确认|验证通过|测试通过|all.*pass',
    re.IGNORECASE,
)

# P2: partial decay signals. Strong reset is intentionally conservative: it is
# used only for explicit reflection/planning/user-handoff language and is capped
# near semi/hard-block thresholds by _apply_partial_decay().
_DECAY_PATTERNS = {
    'strong': re.compile(
        r'方案[一二三]|方案\s*[ABC]|风险|取舍|验收标准|需要你决定|请你决定|'
        r'请求用户|ask_user|停止同类尝试|进入\s*debugging_sop|分发\s*subagent|'
        r'DISPATCH REFLECTION|当前任务类型|是否应分发|不分发理由',
        re.IGNORECASE,
    ),
    'medium': re.compile(
        r'先验证|先探测|根据结果|下一步|假设|证据|日志|状态|边界|约束|'
        r'复现|信息收集|最小修复|验证闭环|diff|测试',
        re.IGNORECASE,
    ),
    'weak': re.compile(
        r'<summary>|当前|本次|意图|结果|发现|准备|继续',
        re.IGNORECASE,
    ),
}

# ---------------------------------------------------------------------------
# Task-type whitelist (P1): operations that are inherently "brain duties"
# and should NOT count toward the dispatch gate at all (weight = 0).
# Each entry can constrain tool names and path/script/assistant text regexes.
# ---------------------------------------------------------------------------
_WHITELIST_RULES = [
    # Memory maintenance: read/write/patch files under memory/
    {
        'name': 'memory_maintenance',
        'tools': {'file_write', 'file_patch', 'file_read'},
        'path_re': re.compile(r'memory[/\\]', re.IGNORECASE),
    },
    # Config/SOP editing: patching .md/.yaml/.json/.txt docs (small edits)
    {
        'name': 'config_sop_edit',
        'tools': {'file_patch'},
        'path_re': re.compile(r'\.(md|yaml|yml|json|txt)$', re.IGNORECASE),
    },
    # Debugging diagnostics: code_run with diagnostic patterns (not bulk coding)
    {
        'name': 'debug_diagnostics',
        'tools': {'code_run'},
        'script_re': re.compile(
            r'print\(|\.read\(|cat |type |head |tail |wc |ls |dir |'
            r'traceback|logging|debug|diagnos|状态|检查|查看|读取',
            re.IGNORECASE,
        ),
    },
    # Brain-duty response turns: planning / verification / memory / SOP work.
    {
        'name': 'brain_duty_response',
        'tools': None,
        'text_re': re.compile(
            r'记忆维护|记忆同步|验证收尾|最终验证|规划|决策|方案收敛|SOP维护|'
            r'模型分发门禁|dispatch gate|model_dispatch',
            re.IGNORECASE,
        ),
    },
]

# Tools always allowed regardless of gate level
ALWAYS_ALLOWED = frozenset({
    'ask_user', 'update_working_checkpoint', 'start_long_term_update',
    'file_read', 'web_scan', 'no_tool',
})

# Patterns in code_run script that indicate subagent dispatch (case-insensitive)
_SUBAGENT_RE = re.compile(
    r'subagent|sub_agent|llm_no\s*[=:]\s*[67]|delegate|from\s+memory\.subagent|'
    r'import\s+subagent|run_subagent|dispatch_to',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_WARN_PROMPT = (
    "\n\n⚠️ [DISPATCH GATE Level-1] 你已连续 {n} 轮使用执行类工具（code_run/file_write/file_patch）而未分发subagent。"
    "\n根据 model_dispatch_sop 分发门：编码任务→subagent(llm_no=7)，信息收集/数据处理→subagent(llm_no=6)。"
    "\n**请立即反思**：当前任务是否属于应分发的类型？如果是，下一轮必须调用subagent。"
    "\n如果确实需要大脑亲自执行（规划/协调/≤2轮探测/最终验证），请在回复中明确说明理由。"
)

_SEMI_BLOCK_PROMPT = (
    "\n\n🚫 [DISPATCH GATE Level-2] 连续 {n} 轮执行类操作未分发subagent，已触发半硬门禁。"
    "\n你**必须**在本轮回复开头包含以下反思（不可跳过）："
    "\n```"
    "\n[DISPATCH REFLECTION]"
    "\n当前任务类型: (编码/信息收集/数据处理/规划/验证)"
    "\n是否应分发: (是/否)"
    "\n不分发理由: (如选否，必须说明)"
    "\n下一步: (分发subagent / ask_user / 继续执行并说明理由)"
    "\n```"
    "\n若任务类型为编码/信息收集/数据处理，**下一轮将进入硬门禁，执行类工具将被拒绝**。"
)

_HARD_BLOCK_PROMPT = (
    "\n\n⛔ [DISPATCH GATE Level-3] 连续 {n} 轮执行类操作，硬门禁已激活。"
    "\n执行类工具（code_run/file_write/file_patch/web_execute_js）已被禁用。"
    "\n你现在只能："
    "\n1. 调用 subagent 分发任务（code_run 中包含 subagent/llm_no 调用）"
    "\n2. 调用 ask_user 请求用户指示"
    "\n3. 调用 file_read 读取文件"
    "\n4. 调用 update_working_checkpoint 保存上下文"
    "\n如需解除门禁，请分发subagent或ask_user。"
)

_TOOL_BLOCKED_MSG = (
    "⛔ [DISPATCH GATE] 硬门禁已激活（连续{n}轮执行未分发）。"
    "此工具当前被禁用。请改用 subagent 分发任务（编码→llm_no=7，信息收集→llm_no=6），"
    "或调用 ask_user 请求用户指示。"
)


# ---------------------------------------------------------------------------
# Gate class
# ---------------------------------------------------------------------------

class DispatchGate:
    """Tracks consecutive execution turns and enforces graduated dispatch policy."""

    def __init__(self):
        self.consecutive_exec = 0
        self.level = 0  # 0=open, 1=warn, 2=semi_block, 3=hard_block

    # ------ called at end of each turn from turn_end_callback ------
    def on_turn_end(self, tool_calls, response_text=''):
        """
        Evaluate this turn's tool usage and return (level, prompt_to_inject).
        Args:
            tool_calls: list of {'tool_name': str, 'args': dict}
            response_text: the assistant's response content
        Returns:
            (int, str) - gate level and prompt injection string
        """
        tool_names = {tc.get('tool_name', '') for tc in tool_calls}
        has_exec = bool(tool_names & EXEC_TOOLS)

        # Detect subagent dispatch in code_run scripts
        has_subagent = self._detect_subagent(tool_calls)

        # Detect ask_user (explicit user handoff)
        has_ask = 'ask_user' in tool_names

        # Reset conditions: subagent / ask_user → full reset
        if has_subagent or has_ask:
            self._reset()
            return self.level, ""

        if not has_exec:
            # Read-only / planning turn - don't increment, but don't reset either
            return self.level, ""

        # P1: Whitelisted turn patterns bypass gate entirely (no increment)
        if self._is_whitelisted(tool_calls, response_text):
            return self.level, ""

        # --- Determine exec weight ---
        # Light exec (running tests, small verification) counts as 0.5
        weight = 1.0
        if self._is_light_exec(tool_calls):
            weight = 0.5

        self.consecutive_exec += weight

        # --- Progress detection: halve counter if substantive progress found ---
        if response_text and _PROGRESS_RE.search(response_text):
            if self.consecutive_exec > 2:
                self.consecutive_exec = max(1, self.consecutive_exec / 2)

        # P2: partial reset/decay for explicit reasoning/verification signals.
        # This runs after increment/progress so the current exec turn still leaves
        # pressure unless the response contains a strong brain-duty signal.
        decay_signal = self._classify_decay_signal(response_text)
        if decay_signal:
            self.consecutive_exec = self._apply_partial_decay(
                decay_signal,
                self.consecutive_exec,
            )

        n = self.consecutive_exec

        if n >= 11:
            self.level = 3
            return 3, _HARD_BLOCK_PROMPT.format(n=int(n))
        elif n >= 8:
            self.level = 2
            return 2, _SEMI_BLOCK_PROMPT.format(n=int(n))
        elif n >= 5:
            self.level = 1
            return 1, _WARN_PROMPT.format(n=int(n))
        else:
            self.level = 0
            return 0, ""

    # ------ called when a new user message arrives ------
    def on_user_message(self):
        """Reset gate on new user input."""
        self._reset()

    # ------ called before executing a tool ------
    def check_tool(self, tool_name, tool_args=None):
        """
        Check if a tool is allowed at current gate level.
        Returns (allowed: bool, block_reason: str).
        At level 3, execution tools are blocked unless they contain subagent patterns.
        """
        if self.level < 3:
            return True, ""

        if tool_name in ALWAYS_ALLOWED:
            return True, ""

        if tool_name not in EXEC_TOOLS:
            return True, ""

        # At level 3: block exec tools UNLESS it's a subagent dispatch
        if tool_name == 'code_run' and tool_args:
            script = tool_args.get('script', '') or ''
            if _SUBAGENT_RE.search(script):
                return True, ""

        return False, _TOOL_BLOCKED_MSG.format(n=self.consecutive_exec)

    # ------ internal ------
    def _is_whitelisted(self, tool_calls, assistant_text):
        """Check if this turn matches a whitelisted task pattern (skip gate counting)."""
        for rule in _WHITELIST_RULES:
            tools = rule.get('tools')
            if tools is not None:
                candidates = [tc for tc in tool_calls if tc.get('tool_name') in tools]
                if not candidates:
                    continue
            else:
                candidates = tool_calls or [{'tool_name': 'no_tool', 'args': {}}]

            for tc in candidates:
                args = tc.get('args', {}) or {}
                path = args.get('path', '') or ''
                script = args.get('script', '') or ''
                text = assistant_text or ''

                if 'path_re' in rule and not rule['path_re'].search(path):
                    continue
                if 'script_re' in rule and not rule['script_re'].search(script):
                    continue
                if 'text_re' in rule and not rule['text_re'].search(text):
                    continue
                return True
        return False

    def _classify_decay_signal(self, response_text):
        """Classify partial decay signal from assistant response text."""
        text = response_text or ''
        if not text:
            return None
        for kind in ('strong', 'medium', 'weak'):
            if _DECAY_PATTERNS[kind].search(text):
                return kind
        return None

    def _apply_partial_decay(self, signal, before):
        """Apply P2 partial reset/decay while preserving pressure near high levels."""
        if signal == 'strong':
            if before < 8:
                return 0.0
            return max(5.0, before * 0.5)
        if signal == 'medium':
            if before >= 8:
                return max(before - 1.0, before * 0.7)
            return max(0.0, before * 0.5)
        if signal == 'weak':
            return max(0.0, before - 0.5)
        return before

    def _detect_subagent(self, tool_calls):
        for tc in tool_calls:
            if tc.get('tool_name') == 'code_run':
                script = tc.get('args', {}).get('script', '') or ''
                if _SUBAGENT_RE.search(script):
                    return True
        return False

    def _is_light_exec(self, tool_calls):
        """Check if ALL exec tools in this turn are lightweight (tests, verify, small patches)."""
        exec_calls = [tc for tc in tool_calls if tc.get('tool_name') in EXEC_TOOLS]
        if not exec_calls:
            return False
        for tc in exec_calls:
            args = tc.get('args', {})
            script = args.get('script', '') or ''
            # file_patch with small new_content is light
            if tc.get('tool_name') == 'file_patch':
                new_c = args.get('new_content', '') or ''
                if len(new_c) < 500:
                    continue  # light
            # code_run matching light patterns
            elif tc.get('tool_name') == 'code_run' and _LIGHT_EXEC_RE.search(script):
                continue  # light
            else:
                return False  # at least one heavy exec
        return True

    def _reset(self):
        self.consecutive_exec = 0.0
        self.level = 0

    def __repr__(self):
        return f"DispatchGate(level={self.level}, consecutive={int(self.consecutive_exec)})"