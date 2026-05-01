"""GA Constraint Engine — 声明式约束检测引擎

所有约束用 JSON DSL 定义，引擎解释执行。
新增约束只需写 JSON，不需改引擎代码。

check_type 原语:
  pattern_forbidden  - 禁止模式（正则匹配工具参数/脚本）
  pattern_required   - 必须包含模式（响应/工具参数中必须出现）
  tool_routing       - 场景X必须用工具Y（触发条件+必须工具）
  precondition       - 做Y前必须做X（前置动作检查）
  output_check       - 响应必须满足格式/内容要求
  sequence_check     - 多轮行为序列检查
  duplicate_check    - 重复内容/工具调用检测
  llm_judge          - 需要LLM语义判断的复杂约束
  composite          - 组合多个子检查（AND/OR/NOT）
"""

import json, re, os
from typing import Any

# ---------------------------------------------------------------------------
# Context: 每轮审计传入的上下文字典
# {
#   "tool_calls": [{"tool_name": str, "args": dict}, ...],
#   "response_text": str,          # assistant 响应文本
#   "user_message": str,           # 用户消息
#   "history": [prior_ctx, ...],   # 历史轮次上下文
#   "scripts": [str, ...],         # 本轮执行的脚本内容
#   "model": str,                  # 当前模型
# }
# ---------------------------------------------------------------------------


def _get_all_text(ctx: dict, scope: str) -> str:
    """根据 scope 提取待检测文本"""
    parts = []
    if scope == "path_only":
        for tc in ctx.get("tool_calls") or []:
            p = tc.get("args", {}).get("path", "")
            if p:
                parts.append(p)
        return "\n".join(parts)
    if scope in ("all", "tools", "exec_only"):
        for tc in ctx.get("tool_calls") or []:
            parts.append(json.dumps(tc.get("args", {}), ensure_ascii=False))
        for s in ctx.get("scripts") or []:
            parts.append(s)
    if scope in ("all", "response"):
        parts.append(ctx.get("response_text") or "")
    if scope in ("all", "user"):
        parts.append(ctx.get("user_message") or "")
    return "\n".join(parts)


def _match_pattern(text: str, pattern: str, negative_context: str = None) -> bool:
    """正则匹配，支持 negative_context 排除前缀"""
    matches = list(re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE))
    if not matches:
        return False
    if negative_context:
        for m in matches:
            start = max(0, m.start() - 30)
            preceding = text[start:m.start()]
            if not re.search(negative_context, preceding, re.IGNORECASE):
                return True
        return False
    return True


def _tool_names(ctx: dict) -> set:
    return {tc.get("tool_name", "") for tc in ctx.get("tool_calls") or []}


def _history_tool_names(ctx: dict, lookback: int = None) -> set:
    """从历史轮次提取工具名集合"""
    names = set()
    history = ctx.get("history") or []
    if lookback:
        history = history[-lookback:]
    for h in history:
        for tc in h.get("tool_calls") or []:
            names.add(tc.get("tool_name", ""))
    return names


# ---------------------------------------------------------------------------
# Check type evaluators
# ---------------------------------------------------------------------------

def _check_pattern_forbidden(params: dict, ctx: dict) -> dict:
    """禁止模式：命中=fail，未命中=skip"""
    scope = params.get("scope", "exec_only")
    lookback = params.get("lookback", 0)
    # tool_names filter: only check args from specified tools (e.g. write-only)
    filter_tools = params.get("tool_names")
    if filter_tools:
        parts = []
        for tc in ctx.get("tool_calls") or []:
            if tc.get("tool_name", "") in filter_tools:
                parts.append(json.dumps(tc.get("args", {}), ensure_ascii=False))
        text = "\n".join(parts)
    else:
        text = _get_all_text(ctx, scope)
        # lookback: also include historical text for the same scope
        if lookback and scope == "user":
            history = (ctx.get("history") or [])[-lookback:]
            hist_parts = [h.get("user_message", "") for h in history if h.get("user_message")]
            if hist_parts:
                text = "\n".join(hist_parts) + "\n" + text
    if not text.strip():
        return {"status": "skip", "reason": "no relevant text"}
    pattern = params["pattern"]
    neg_ctx = params.get("negative_context")
    if _match_pattern(text, pattern, neg_ctx):
        return {"status": "fail", "reason": f"forbidden pattern matched: {pattern}"}
    return {"status": "skip", "reason": "pattern not found (not triggered)"}


def _check_pattern_required(params: dict, ctx: dict) -> dict:
    """必须包含模式：有相关内容但缺少=fail，无相关内容=skip"""
    scope = params.get("scope", "response")
    text = _get_all_text(ctx, scope)
    if not text.strip():
        return {"status": "skip", "reason": "no relevant text"}
    # 先检查触发条件（如果有）
    trigger = params.get("trigger")
    if trigger and not _match_pattern(text, trigger):
        return {"status": "skip", "reason": "trigger not matched"}
    pattern = params["pattern"]
    if _match_pattern(text, pattern):
        return {"status": "pass", "reason": f"required pattern found"}
    return {"status": "fail", "reason": f"required pattern missing: {pattern}"}


def _check_tool_routing(params: dict, ctx: dict) -> dict:
    """场景X必须用工具Y"""
    # 先检查触发条件
    trigger = params.get("trigger", {})
    triggered = False

    # 触发方式1: 用户消息匹配
    if "user_pattern" in trigger:
        um = ctx.get("user_message") or ""
        if _match_pattern(um, trigger["user_pattern"]):
            triggered = True

    # 触发方式2: 工具调用匹配
    if "tool_pattern" in trigger:
        for tc in ctx.get("tool_calls") or []:
            args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)
            if _match_pattern(args_str, trigger["tool_pattern"]):
                triggered = True

    # 触发方式3: 脚本内容匹配
    if "script_pattern" in trigger:
        for s in ctx.get("scripts") or []:
            if _match_pattern(s, trigger["script_pattern"]):
                triggered = True

    if not triggered:
        return {"status": "skip", "reason": "scenario not triggered"}

    # 检查是否使用了要求的工具
    required_tools = set(params.get("required_tools", []))
    current_tools = _tool_names(ctx)
    if required_tools & current_tools:
        return {"status": "pass", "reason": f"correct tool used"}

    # 也检查历史（可能前一轮已经用了）
    lookback = params.get("lookback", 0)
    if lookback:
        hist_tools = _history_tool_names(ctx, lookback)
        if required_tools & hist_tools:
            return {"status": "pass", "reason": "correct tool used in recent history"}

    forbidden_tools = set(params.get("forbidden_tools", []))
    if forbidden_tools & current_tools:
        return {"status": "fail", "reason": f"wrong tool used, expected {required_tools}"}

    return {"status": "fail", "reason": f"required tool {required_tools} not used"}


def _check_precondition(params: dict, ctx: dict) -> dict:
    """做Y前必须做X"""
    # 检查动作Y是否在本轮发生
    action_y = params.get("action", {})
    y_triggered = False

    # tool_names and pattern are AND when both present, OR when only one exists
    tn_match = True  # default True so single-condition works
    pat_match = True
    if "tool_names" in action_y:
        tn_match = bool(_tool_names(ctx) & set(action_y["tool_names"]))
    if "pattern" in action_y:
        text = _get_all_text(ctx, action_y.get("scope", "all"))
        pat_match = bool(_match_pattern(text, action_y["pattern"]))
    # exclude_pattern: if text matches exclude, skip this constraint entirely
    exclude = action_y.get("exclude_pattern")
    if exclude and _match_pattern(_get_all_text(ctx, action_y.get("scope", "all")), exclude):
        return {"status": "skip", "reason": "action excluded by exclude_pattern"}
    if "tool_names" in action_y or "pattern" in action_y:
        y_triggered = tn_match and pat_match

    if not y_triggered:
        return {"status": "skip", "reason": "action not triggered"}

    # 检查前置条件X是否已满足
    precond = params.get("precondition", {})
    lookback = precond.get("lookback", 3)

    # 前置条件: 历史中有特定工具调用
    if "tool_names" in precond:
        hist_tools = _history_tool_names(ctx, lookback)
        current = _tool_names(ctx)
        all_tools = hist_tools | current
        if set(precond["tool_names"]) & all_tools:
            return {"status": "pass", "reason": "precondition met"}

    # 前置条件: 历史响应中有特定模式
    if "response_pattern" in precond:
        for h in (ctx.get("history") or [])[-lookback:]:
            resp = h.get("response_text") or ""
            if _match_pattern(resp, precond["response_pattern"]):
                return {"status": "pass", "reason": "precondition met in history"}
        # 也检查当前轮
        if _match_pattern(ctx.get("response_text") or "", precond["response_pattern"]):
            return {"status": "pass", "reason": "precondition met in current turn"}

    return {"status": "fail", "reason": "action performed without precondition"}


def _check_output(params: dict, ctx: dict) -> dict:
    """响应必须满足格式/内容要求"""
    resp = ctx.get("response_text") or ""
    if not resp.strip():
        return {"status": "skip", "reason": "no response"}

    # 触发条件（可选）
    trigger = params.get("trigger")
    if trigger and not _match_pattern(resp, trigger):
        um = ctx.get("user_message") or ""
        if not _match_pattern(um, trigger):
            return {"status": "skip", "reason": "trigger not matched"}

    pattern = params.get("required_pattern")
    if pattern:
        if _match_pattern(resp, pattern):
            return {"status": "pass", "reason": "output requirement met"}
        return {"status": "fail", "reason": f"output missing: {pattern}"}

    forbidden = params.get("forbidden_pattern")
    if forbidden:
        if _match_pattern(resp, forbidden):
            return {"status": "fail", "reason": f"output contains forbidden: {forbidden}"}
        return {"status": "pass", "reason": "forbidden pattern absent"}

    return {"status": "skip", "reason": "no check criteria"}


def _check_duplicate(params: dict, ctx: dict) -> dict:
    """重复内容/工具调用检测"""
    lookback = params.get("lookback", 3)
    history = (ctx.get("history") or [])[-lookback:]
    if not history:
        return {"status": "skip", "reason": "no history"}

    check_what = params.get("check", "tool_calls")  # tool_calls | response

    if check_what == "tool_calls":
        current_calls = [(tc.get("tool_name"), json.dumps(tc.get("args", {}), sort_keys=True))
                         for tc in ctx.get("tool_calls") or []]
        if not current_calls:
            return {"status": "skip", "reason": "no tool calls"}
        dup_count = 0
        for h in history:
            hist_calls = [(tc.get("tool_name"), json.dumps(tc.get("args", {}), sort_keys=True))
                          for tc in h.get("tool_calls") or []]
            if current_calls == hist_calls:
                dup_count += 1
        threshold = params.get("threshold", 2)
        if dup_count >= threshold:
            return {"status": "fail", "reason": f"identical tool calls repeated {dup_count} times"}
        return {"status": "pass", "reason": "no excessive duplication"}

    if check_what == "response":
        resp = ctx.get("response_text") or ""
        if not resp.strip():
            return {"status": "skip", "reason": "no response"}
        sim_threshold = params.get("similarity_threshold", 0.9)
        # 简单实现：完全相同检测
        for h in history:
            h_resp = h.get("response_text") or ""
            if resp == h_resp and resp.strip():
                return {"status": "fail", "reason": "identical response repeated"}
        return {"status": "pass", "reason": "responses differ"}

    return {"status": "skip", "reason": f"unknown check target: {check_what}"}


def _check_sequence(params: dict, ctx: dict) -> dict:
    """多轮行为序列检查"""
    # 定义期望序列: [step1, step2, step3]
    # 每个step: {"tool_names": [...]} 或 {"pattern": "..."}
    steps = params.get("steps", [])
    if not steps:
        return {"status": "skip", "reason": "no steps defined"}

    # 检查触发条件
    trigger_step = params.get("trigger_step", len(steps) - 1)
    last_step = steps[trigger_step]

    # 检查最后一步是否在当前轮触发
    # 同一步同时声明 tool_names 与 pattern 时必须全部满足（AND），
    # 避免“任意 file_write”绕过后缀 pattern 造成误报。
    has_trigger_condition = False
    tool_match = True
    pattern_match = True
    if "tool_names" in last_step:
        has_trigger_condition = True
        tool_match = bool(_tool_names(ctx) & set(last_step["tool_names"]))
    if "pattern" in last_step:
        has_trigger_condition = True
        text = _get_all_text(ctx, last_step.get("scope", "all"))
        pattern_match = bool(_match_pattern(text, last_step["pattern"]))

    triggered = has_trigger_condition and tool_match and pattern_match
    if not triggered:
        return {"status": "skip", "reason": "sequence endpoint not triggered"}

    # exclude_pattern: if trigger step has exclude and current text matches, skip
    exclude = last_step.get("exclude_pattern")
    if exclude and _match_pattern(_get_all_text(ctx, last_step.get("scope", "all")), exclude):
        return {"status": "skip", "reason": "sequence excluded by exclude_pattern"}

    # 回溯检查前面的步骤是否按序出现 (with lookback limit)
    lookback = params.get("lookback", 50)
    history = (ctx.get("history") or [])[-lookback:]
    prior_steps = steps[:trigger_step]
    step_idx = 0
    for h in history:
        if step_idx >= len(prior_steps):
            break
        s = prior_steps[step_idx]
        matched = False
        if "tool_names" in s:
            h_tools = {tc.get("tool_name", "") for tc in h.get("tool_calls") or []}
            if set(s["tool_names"]) & h_tools:
                matched = True
        if "pattern" in s:
            h_text = json.dumps(h, ensure_ascii=False)
            # exclude_pattern: skip if history text matches exclude
            step_exclude = s.get("exclude_pattern")
            if step_exclude and _match_pattern(h_text, step_exclude):
                continue
            if _match_pattern(h_text, s["pattern"]):
                matched = True
        if matched:
            step_idx += 1

    if step_idx >= len(prior_steps):
        return {"status": "pass", "reason": "sequence followed correctly"}
    return {"status": "fail",
            "reason": f"sequence broken at step {step_idx}: {prior_steps[step_idx]}"}


def _check_llm_judge(params: dict, ctx: dict) -> dict:
    """LLM语义判断 — 返回 pending，由外部LLM评估后回填"""
    # 引擎本身不调用LLM，只准备prompt和判定标准
    criteria = params.get("criteria", "")
    scope = params.get("scope", "response")
    text = _get_all_text(ctx, scope)
    if not text.strip():
        return {"status": "skip", "reason": "no text to judge"}

    # 检查触发条件
    trigger = params.get("trigger")
    if trigger and not _match_pattern(text, trigger):
        return {"status": "skip", "reason": "trigger not matched"}

    return {
        "status": "pending_llm",
        "prompt": f"判断以下内容是否满足约束：{criteria}\n\n内容：{text[:2000]}",
        "criteria": criteria,
    }


def _check_composite(params: dict, ctx: dict) -> dict:
    """组合检查：AND/OR/NOT"""
    op = params.get("operator", "AND")  # AND | OR | NOT
    sub_checks = params.get("checks", [])
    results = []
    for sc in sub_checks:
        r = evaluate_constraint(sc, ctx)
        results.append(r)

    statuses = [r["status"] for r in results]

    if op == "AND":
        if all(s == "pass" for s in statuses):
            return {"status": "pass", "reason": "all sub-checks passed", "details": results}
        # skip means "condition not triggered" — propagate to avoid false positives
        if any(s == "skip" for s in statuses):
            return {"status": "skip", "reason": "some sub-checks not triggered", "details": results}
        if any(s == "fail" for s in statuses):
            failed = [r for r in results if r["status"] == "fail"]
            return {"status": "fail", "reason": f"{len(failed)} sub-check(s) failed", "details": results}
        return {"status": "skip", "reason": "inconclusive", "details": results}

    if op == "OR":
        if any(s == "pass" for s in statuses):
            return {"status": "pass", "reason": "at least one sub-check passed", "details": results}
        if all(s == "fail" for s in statuses):
            return {"status": "fail", "reason": "all sub-checks failed", "details": results}
        return {"status": "skip", "reason": "no pass, some skipped", "details": results}

    if op == "NOT":
        # NOT: 第一个子检查的结果取反
        if results and results[0]["status"] == "fail":
            return {"status": "pass", "reason": "NOT condition met (sub-check failed as expected)"}
        if results and results[0]["status"] == "pass":
            return {"status": "fail", "reason": "NOT condition violated (sub-check passed unexpectedly)"}
        return {"status": "skip", "reason": "sub-check skipped"}

    return {"status": "skip", "reason": f"unknown operator: {op}"}


def _check_consecutive_execution(params: dict, ctx: dict) -> dict:
    """连续多轮纯执行无反思检测 (R004/R061)
    
    检查最近N轮是否全部是纯工具调用(无反思/规划/checkpoint)。
    params:
      lookback: 回看轮数 (default 3)
      reflection_signals: 反思信号pattern列表
      execution_tools: 执行类工具名列表 (可选，默认所有tool_call)
    """
    lookback = params.get("lookback", 3)
    history = (ctx.get("history") or [])
    if len(history) < lookback:
        return {"status": "skip", "reason": f"history < {lookback} turns"}

    recent = history[-lookback:]
    reflection_signals = params.get("reflection_signals", [
        r"(?:反思|回顾|review|rethink|重新考虑|换方案|换个思路)",
        r"update_working_checkpoint",
        r"(?:阶段|phase|step)\s*\d",
        r"(?:方案|plan|策略|strategy)",
    ])

    consecutive_exec = 0
    for h in recent:
        h_text = (h.get("response_text") or "") + " " + json.dumps(
            h.get("tool_calls") or [], ensure_ascii=False)
        has_reflection = False
        for sig in reflection_signals:
            if _match_pattern(h_text, sig):
                has_reflection = True
                break
        has_tools = bool(h.get("tool_calls"))
        if has_tools and not has_reflection:
            consecutive_exec += 1
        else:
            consecutive_exec = 0  # 链断裂

    threshold = params.get("threshold", lookback)
    if consecutive_exec >= threshold:
        return {"status": "fail",
                "reason": f"连续{consecutive_exec}轮纯执行无反思(阈值{threshold})"}
    return {"status": "pass",
            "reason": f"连续执行{consecutive_exec}轮，未超阈值{threshold}"}


# ---------------------------------------------------------------------------
# Evaluator dispatch
# ---------------------------------------------------------------------------

_EVALUATORS = {
    "consecutive_execution": _check_consecutive_execution,
    "pattern_forbidden": _check_pattern_forbidden,
    "pattern_required": _check_pattern_required,
    "tool_routing": _check_tool_routing,
    "precondition": _check_precondition,
    "output_check": _check_output,
    "duplicate_check": _check_duplicate,
    "sequence_check": _check_sequence,
    "llm_judge": _check_llm_judge,
    "composite": _check_composite,
}


def evaluate_constraint(constraint: dict, ctx: dict) -> dict:
    """评估单条约束，返回 {status, reason, ...}"""
    check_type = constraint.get("check_type", "")
    params = constraint.get("params", {})
    evaluator = _EVALUATORS.get(check_type)
    if not evaluator:
        return {"status": "error", "reason": f"unknown check_type: {check_type}"}
    try:
        result = evaluator(params, ctx)
        result["constraint_id"] = constraint.get("id", "?")
        result["constraint_name"] = constraint.get("name", "?")
        return result
    except Exception as e:
        return {
            "status": "error",
            "reason": str(e),
            "constraint_id": constraint.get("id", "?"),
        }


def evaluate_all(constraints: list, ctx: dict) -> list:
    """评估所有约束，返回结果列表"""
    return [evaluate_constraint(c, ctx) for c in constraints]


def load_constraints(path: str) -> list:
    """从JSON文件加载约束定义"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return data.get("constraints", [])


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def summary(results: list) -> dict:
    """统计结果摘要"""
    s = {"pass": 0, "fail": 0, "skip": 0, "error": 0, "pending_llm": 0}
    for r in results:
        st = r.get("status", "error")
        s[st] = s.get(st, 0) + 1
    s["total"] = len(results)
    return s