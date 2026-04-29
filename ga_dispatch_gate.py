"""
ga_dispatch_gate.py - 分级硬门禁 for model_dispatch_sop
Graduated enforcement: open → warning → forced reflection → hard block

Thresholds (consecutive execution-tool turns without subagent dispatch):
  Level 0 (0-2): open
  Level 1 (3-4): soft warning - remind to reflect on dispatch
  Level 2 (5-6): semi-block - forced reflection template, still allows execution
  Level 3 (7+):  hard block - refuse execution tools unless dispatching subagent

Reset conditions: user message / subagent call / ask_user
"""

import re

# Tools that count as "execution" (doing work the muscle model should do)
EXEC_TOOLS = frozenset({'code_run', 'file_write', 'file_patch', 'web_execute_js'})

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

        # Reset conditions
        if has_subagent or has_ask:
            self._reset()
            return self.level, ""

        if not has_exec:
            # Read-only / planning turn - don't increment, but don't reset either
            # (agent might be reading files between exec turns to avoid the gate)
            return self.level, ""

        # Increment consecutive execution counter
        self.consecutive_exec += 1
        n = self.consecutive_exec

        if n >= 7:
            self.level = 3
            return 3, _HARD_BLOCK_PROMPT.format(n=n)
        elif n >= 5:
            self.level = 2
            return 2, _SEMI_BLOCK_PROMPT.format(n=n)
        elif n >= 3:
            self.level = 1
            return 1, _WARN_PROMPT.format(n=n)
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
    def _detect_subagent(self, tool_calls):
        for tc in tool_calls:
            if tc.get('tool_name') == 'code_run':
                script = tc.get('args', {}).get('script', '') or ''
                if _SUBAGENT_RE.search(script):
                    return True
        return False

    def _reset(self):
        self.consecutive_exec = 0
        self.level = 0

    def __repr__(self):
        return f"DispatchGate(level={self.level}, consecutive={self.consecutive_exec})"