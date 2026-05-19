#!/usr/bin/env python3
"""Darwin 8-Dimension Evaluation Engine (100-point scale).

Rubric from vendor/darwin-skill/SKILL.md:
  D1 Frontmatter (8) | D2 Workflow (15) | D3 Boundary (10) | D4 Checkpoints (7)
  D5 Specificity (15) | D6 Resources (5) | D7 Architecture (15) | D8 TestExec (25)

D1-D6: static heuristic analysis.
D7-D8: LLM dry-run evaluation (outputs JSON prompt for LLM).
Total = sum(raw_score * weight) / 10, max 100.
"""
from __future__ import annotations
import re, json, sys, argparse, os, time, urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

WEIGHTS = {
    "D1_frontmatter": 8,
    "D2_workflow": 15,
    "D3_boundary": 10,
    "D4_checkpoints": 7,
    "D5_specificity": 15,
    "D6_resources": 5,
    "D7_architecture": 15,
    "D8_test_exec": 25,
}

# ── D1: Frontmatter Quality (8pts weight, raw 1-10) ──────────────
def score_d1(text: str) -> Tuple[int, List[str]]:
    raw = 0
    reasons = []
    # Has Struct Header section (2pts)
    if re.search(r"(?i)##\s*(struct\s*header|frontmatter)", text):
        raw += 2; reasons.append("has_header_section(+2)")
    else:
        reasons.append("no_header_section(+0)")
    # Has Trigger with content (2pts)
    if re.search(r"(?i)-\s*trigger\s*:", text):
        raw += 2; reasons.append("has_trigger(+2)")
    else:
        reasons.append("no_trigger(+0)")
    # Has Inputs/Outputs (2pts)
    has_io = 0
    if re.search(r"(?i)-\s*inputs?\s*:", text): has_io += 1
    if re.search(r"(?i)-\s*outputs?\s*:", text): has_io += 1
    raw += has_io
    reasons.append(f"io_fields({has_io}/2)(+{has_io})")
    # Has Tools/Risk/Side effects (2pts)
    aux = 0
    for f in ["tools", "side.?effects?", "risk"]:
        if re.search(rf"(?i)-\s*{f}\s*:", text): aux += 1
    pts = min(2, aux)
    raw += pts
    reasons.append(f"aux_fields({aux}/3)(+{pts})")
    return min(10, raw), reasons

# ── D2: Workflow Clarity (15pts weight, raw 1-10) ────────────────
def score_d2(text: str) -> Tuple[int, List[str]]:
    raw = 0
    reasons = []
    body = re.sub(r"(?s)^---.*?---\s*", "", text)  # strip frontmatter
    body = re.sub(r"(?i)##\s*struct\s*header.*?(?=##|\Z)", "", body, flags=re.S)
    
    # Has numbered/bulleted steps (2pts)
    numbered = len(re.findall(r"(?m)^\s*\d+[\.\)]\s+", body))
    bullets = len(re.findall(r"(?m)^\s*[-*]\s+", body))
    if numbered >= 3 or bullets >= 5:
        raw += 2; reasons.append(f"steps({numbered}num,{bullets}bul)(+2)")
    elif numbered >= 1 or bullets >= 2:
        raw += 1; reasons.append(f"few_steps({numbered}num,{bullets}bul)(+1)")
    else:
        reasons.append("no_steps(+0)")
    
    # Has action verbs (2pts)
    action_verbs = len(re.findall(r"(?i)\b(execute|run|verify|check|create|read|write|scan|fetch|parse|validate|confirm|generate|build|deploy|test|commit|push|install|configure)\b", body))
    if action_verbs >= 8:
        raw += 2; reasons.append(f"action_verbs({action_verbs})(+2)")
    elif action_verbs >= 3:
        raw += 1; reasons.append(f"few_verbs({action_verbs})(+1)")
    else:
        reasons.append(f"few_verbs({action_verbs})(+0)")
    
    # Has phases/stages structure (2pts)
    phases = len(re.findall(r"(?i)##\s*(phase|step|stage|阶段)", text))
    if phases >= 2:
        raw += 2; reasons.append(f"phases({phases})(+2)")
    elif phases >= 1:
        raw += 1; reasons.append(f"phases({phases})(+1)")
    else:
        reasons.append("no_phases(+0)")
    
    # Has clear input/output transitions (2pts)
    transitions = len(re.findall(r"(?i)\b(input|output|then|next|after|before|result|return|pass|fail)\b", body))
    if transitions >= 6:
        raw += 2; reasons.append(f"transitions({transitions})(+2)")
    elif transitions >= 3:
        raw += 1; reasons.append(f"transitions({transitions})(+1)")
    else:
        reasons.append(f"transitions({transitions})(+0)")
    
    # No vague instructions (2pts)
    vague = len(re.findall(r"(?i)\b(handle.?it|do.?the.?right.?thing|appropriately|as.?needed|etc\.?|and.?so.?on)\b", body))
    pts = max(0, 2 - vague)
    raw += pts
    reasons.append(f"vague({vague})(+{pts})")
    
    return min(10, raw), reasons

# ── D3: Boundary Conditions (10pts weight, raw 1-10) ─────────────
def score_d3(text: str) -> Tuple[int, List[str]]:
    raw = 0
    reasons = []
    
    # Failure/error handling (3pts)
    if re.search(r"(?i)(failure|error|exception|catch|except)\s*(path|handling|recovery|fallback)?", text):
        raw += 3; reasons.append("error_handling(+3)")
    else:
        reasons.append("no_error_handling(+0)")
    
    # Fallback paths (2pts)
    if re.search(r"(?i)(fallback|alternative|backup|降级|备选)", text):
        raw += 2; reasons.append("fallback(+2)")
    else:
        reasons.append("no_fallback(+0)")
    
    # Edge cases (2pts)
    if re.search(r"(?i)(edge.?case|corner.?case|boundary|边界|极端|特殊情况)", text):
        raw += 2; reasons.append("edge_cases(+2)")
    else:
        reasons.append("no_edge_cases(+0)")
    
    # Recovery/rollback (2pts)
    if re.search(r"(?i)(rollback|revert|recovery|restore|回滚|恢复)", text):
        raw += 2; reasons.append("rollback(+2)")
    else:
        reasons.append("no_rollback(+0)")
    
    # Timeout/resource limits (1pt)
    if re.search(r"(?i)(timeout|limit|max|min|retry|重试|超时)", text):
        raw += 1; reasons.append("limits(+1)")
    else:
        reasons.append("no_limits(+0)")
    
    return min(10, raw), reasons

# ── D4: Checkpoints (7pts weight, raw 1-10) ──────────────────────
def score_d4(text: str) -> Tuple[int, List[str]]:
    raw = 0
    reasons = []
    
    # User confirmation points (3pts)
    confirmations = len(re.findall(r"(?i)(ask_user|confirm|pause|user.?confirm|请.*确认|暂停|ask)", text))
    if confirmations >= 3:
        raw += 3; reasons.append(f"confirmations({confirmations})(+3)")
    elif confirmations >= 1:
        raw += 2; reasons.append(f"confirmations({confirmations})(+2)")
    else:
        reasons.append("no_confirmations(+0)")
    
    # Decision gates (2pts)
    gates = len(re.findall(r"(?i)(if\s.*then|gate|门禁|checkpoint|检查点|decision)", text))
    if gates >= 2:
        raw += 2; reasons.append(f"gates({gates})(+2)")
    elif gates >= 1:
        raw += 1; reasons.append(f"gates({gates})(+1)")
    else:
        reasons.append("no_gates(+0)")
    
    # Progress reporting (2pts)
    progress = len(re.findall(r"(?i)(progress|status|report|log|print|echo|汇报|状态|进度)", text))
    if progress >= 3:
        raw += 2; reasons.append(f"progress({progress})(+2)")
    elif progress >= 1:
        raw += 1; reasons.append(f"progress({progress})(+1)")
    else:
        reasons.append("no_progress(+0)")
    
    return min(10, raw), reasons

# ── D5: Instruction Specificity (15pts weight, raw 1-10) ─────────
def score_d5(text: str) -> Tuple[int, List[str]]:
    raw = 0
    reasons = []
    body = re.sub(r"(?s)^---.*?---\s*", "", text)
    
    # Concrete examples/commands (3pts)
    code_blocks = len(re.findall(r"```", text))
    examples = len(re.findall(r"(?i)(example|e\.g\.|for instance|示例|例如|比如)", body))
    if code_blocks >= 4 or examples >= 3:
        raw += 3; reasons.append(f"examples({code_blocks}code,{examples}eg)(+3)")
    elif code_blocks >= 2 or examples >= 1:
        raw += 2; reasons.append(f"examples({code_blocks}code,{examples}eg)(+2)")
    elif code_blocks >= 1:
        raw += 1; reasons.append(f"examples({code_blocks}code)(+1)")
    else:
        reasons.append("no_examples(+0)")
    
    # Specific file paths (2pts)
    paths = len(re.findall(r"(?:memory|\.\/|D:\\|C:\\|~\/|\/\w)[\w\/\\.-]+\.\w+", text))
    if paths >= 3:
        raw += 2; reasons.append(f"paths({paths})(+2)")
    elif paths >= 1:
        raw += 1; reasons.append(f"paths({paths})(+1)")
    else:
        reasons.append("no_paths(+0)")
    
    # Parameter specifications (2pts)
    params = len(re.findall(r"(?m)^\s*[-*]\s+\w+[=:：]", text))
    params += len(re.findall(r"(?i)(parameter|param|arg|argument|选项|参数)", body))
    if params >= 3:
        raw += 2; reasons.append(f"params({params})(+2)")
    elif params >= 1:
        raw += 1; reasons.append(f"params({params})(+1)")
    else:
        reasons.append("no_params(+0)")
    
    # Format specs (1pt)
    formats = len(re.findall(r"(?i)(format|schema|template|JSON|YAML|CSV|markdown|格式|模板)", body))
    if formats >= 2:
        raw += 1; reasons.append(f"formats({formats})(+1)")
    else:
        reasons.append(f"formats({formats})(+0)")
    
    # No placeholder language (2pts)
    placeholders = len(re.findall(r"(?i)\b(TODO|TBD|FIXME|FILL.?IN|PLACEHOLDER|待定|待补)\b", text))
    pts = max(0, 2 - placeholders)
    raw += pts
    reasons.append(f"placeholders({placeholders})(+{pts})")
    
    return min(10, raw), reasons

# ── D6: Resource Integration (5pts weight, raw 1-10) ─────────────
def score_d6(text: str) -> Tuple[int, List[str]]:
    raw = 0
    reasons = []
    
    # References to other files (2pts)
    refs = len(re.findall(r"(?:memory|vendor|skills|scripts)[/\\][\w./\\-]+", text))
    if refs >= 3:
        raw += 2; reasons.append(f"file_refs({refs})(+2)")
    elif refs >= 1:
        raw += 1; reasons.append(f"file_refs({refs})(+1)")
    else:
        reasons.append("no_file_refs(+0)")
    
    # Tool references (2pts)
    tools = len(re.findall(r"(?i)(ga_\w+|\.py|\.sh|\.exe|subprocess|curl|git|python)", text))
    if tools >= 3:
        raw += 2; reasons.append(f"tools({tools})(+2)")
    elif tools >= 1:
        raw += 1; reasons.append(f"tools({tools})(+1)")
    else:
        reasons.append("no_tools(+0)")
    
    # Dependencies declared (1pt)
    deps = len(re.findall(r"(?i)(depends|requires|prerequisite|依赖|需要|前置)", text))
    if deps >= 1:
        raw += 1; reasons.append(f"deps({deps})(+1)")
    else:
        reasons.append("no_deps(+0)")
    
    return min(10, raw), reasons

# ── D7/D8: LLM evaluation prompt generator ────────────────────────
def generate_llm_prompt(text: str, asset_name: str) -> dict:
    """Generate a structured prompt for LLM to score D7 and D8."""
    # Truncate if too long
    if len(text) > 8000:
        text = text[:8000] + "\n...[truncated]..."
    
    prompt = f"""You are a SOP/Skill quality evaluator. Score the following asset on two dimensions.

Asset: {asset_name}

CONTENT:
---
{text}
---

SCORING RUBRIC:

Dimension 7 - Architecture (weight=15, score 1-10):
- 9-10: Crystal clear hierarchy, no redundancy, consistent with ecosystem conventions
- 7-8: Good structure, minor gaps or slight redundancy
- 5-6: Acceptable but some confusion in organization
- 3-4: Disorganized, significant redundancy or gaps
- 1-2: Chaotic, no discernible structure

Dimension 8 - Test Execution Quality (weight=25, score 1-10):
Think of 2 typical user prompts for this SOP. Score based on:
- Would a user following this SOP get a correct, complete result?
- Is the SOP specific enough that two different users would produce similar outcomes?
- Are there ambiguities that would lead to different interpretations?
Scoring:
- 9-10: Any competent user would produce correct output
- 7-8: Most users would succeed, minor ambiguity
- 5-6: Mixed results, some ambiguity
- 3-4: Many users would fail, significant ambiguity
- 1-2: Unclear how to use this SOP at all

OUTPUT FORMAT (JSON only, no markdown):
{{
  "D7_architecture": {{"score": <1-10>, "reason": "<brief>"}},
  "D8_test_exec": {{"score": <1-10>, "reason": "<brief>", "dry_run": true}}
}}"""
    return {"asset": asset_name, "prompt": prompt}


# ── D7/D8: LLM API evaluator ─────────────────────────────────────
def evaluate_d7_d8_llm(text: str, asset_name: str, api_key: str = None,
                        api_base: str = "https://api.openai.com/v1",
                        model: str = "gpt-4o-mini") -> dict:
    """Call LLM API to score D7 and D8. Returns {"D7": int, "D8": int, "reasons": dict}."""
    import urllib.request, urllib.error
    _mykey_cfg = {}
    if api_key is None:
        try:
            sys.path.insert(0, r"D:\AI\GenericAgent")
            import mykey
            _mykey_cfg = getattr(mykey, 'native_oai_config', {})
            api_key = _mykey_cfg.get('apikey', os.environ.get("OPENAI_API_KEY", ""))
            if api_base == "https://api.openai.com/v1" and _mykey_cfg.get('apibase'):
                api_base = _mykey_cfg['apibase']
        except Exception:
            api_key = os.environ.get("OPENAI_API_KEY", "")
    if model == "gpt-4o-mini" and _mykey_cfg.get('model'):
        model = _mykey_cfg['model']
    prompt_data = generate_llm_prompt(text, asset_name)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_data["prompt"]}],
        "temperature": 0.1,
        "max_tokens": 300,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"].strip()
        # Extract JSON from response (handle markdown fences)
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return {"D7": None, "D8": None, "raw_response": content, "error": "no_json"}
        parsed = json.loads(m.group())
        d7 = parsed.get("D7_architecture", {}).get("score")
        d8 = parsed.get("D8_test_exec", {}).get("score")
        d7r = parsed.get("D7_architecture", {}).get("reason", "")
        d8r = parsed.get("D8_test_exec", {}).get("reason", "")
        return {"D7": d7, "D8": d8, "d7_reason": d7r, "d8_reason": d8r}
    except Exception as e:
        return {"D7": None, "D8": None, "error": str(e)}


# ── Asset classification ──────────────────────────────────────────
def classify_asset(filepath: Path) -> str:
    """Classify asset as 'sop' or 'tool' based on filename pattern."""
    name = filepath.name.lower()
    if name.endswith(".py"):
        return "tool"
    if "_sop" in name or "_skill" in name:
        return "sop"
    return "tool"


# ── Tool file scoring variants (D1/D4 differ; D2/D3/D5/D6 augmented) ──
def score_d1_tool(text: str) -> Tuple[int, List[str]]:
    """D1 for tool files: docstring quality instead of struct header."""
    raw = 0
    reasons = []
    
    # Module-level docstring (3pts)
    if re.search(r'^("""|\'\'\')', text.strip(), re.MULTILINE):
        raw += 3; reasons.append("module_docstring(+3)")
    elif re.search(r'""".+?"""', text[:500], re.DOTALL):
        raw += 2; reasons.append("inline_module_doc(+2)")
    else:
        reasons.append("no_module_docstring(+0)")
    
    # Function/class docstrings (4pts)
    func_defs = re.findall(r"^\s*def\s+\w+", text, re.MULTILINE)
    func_docs = re.findall(r'def\s+\w+\s*\([^)]*\)[^:]*:\s*\n\s+("""|\'\'\')', text)
    class_defs = re.findall(r"^\s*class\s+\w+", text, re.MULTILINE)
    class_docs = re.findall(r'class\s+\w+.*:\s*\n\s+("""|\'\'\')', text)
    
    total_defs = len(func_defs) + len(class_defs)
    total_docs = len(func_docs) + len(class_docs)
    
    if total_defs > 0 and total_docs >= total_defs * 0.7:
        raw += 4; reasons.append(f"docstrings({total_docs}/{total_defs})(+4)")
    elif total_defs > 0 and total_docs >= total_defs * 0.3:
        raw += 2; reasons.append(f"partial_docstrings({total_docs}/{total_defs})(+2)")
    elif total_defs > 0:
        reasons.append(f"few_docstrings({total_docs}/{total_defs})(+0)")
    else:
        raw += 2; reasons.append("script_no_funcs(+2)")
    
    # Type hints on parameters (3pts)
    typed_params = len(re.findall(r"def\s+\w+\s*\([^)]*:\s*\w+", text))
    if total_defs > 0 and typed_params >= total_defs * 0.5:
        raw += 3; reasons.append(f"type_hints({typed_params}/{total_defs})(+3)")
    elif total_defs > 0 and typed_params > 0:
        raw += 1; reasons.append(f"some_type_hints({typed_params})(+1)")
    else:
        reasons.append("no_type_hints(+0)")
    
    return raw, reasons


def score_d4_tool(text: str) -> Tuple[int, List[str]]:
    """D4 for tool files: logging/assert/test patterns as checkpoints."""
    raw = 0
    reasons = []
    
    # Logging patterns (3pts)
    if re.search(r"(?i)(import\s+logging|from\s+logging|getLogger)", text):
        raw += 3; reasons.append("has_logging(+3)")
    elif re.search(r"(?i)(print\s*\(|sys\.stderr\.write)", text):
        raw += 1; reasons.append("print_output(+1)")
    else:
        reasons.append("no_logging(+0)")
    
    # Assert/test patterns (3pts)
    if re.search(r"(?i)(assert\s|def\s+test_|unittest|pytest)", text):
        raw += 3; reasons.append("assert_or_test(+3)")
    elif re.search(r"(?i)(# ?test|# ?verify|# ?check|# ?validate)", text):
        raw += 1; reasons.append("test_comments(+1)")
    else:
        reasons.append("no_test_patterns(+0)")
    
    # Main guard (1pt)
    if re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', text):
        raw += 1; reasons.append("main_guard(+1)")
    else:
        reasons.append("no_main_guard(+0)")
    
    return raw, reasons


# ── Main scoring ──────────────────────────────────────────────────
def evaluate_asset(filepath: Path) -> dict:
    """Score asset with D1-D6 static analysis (category-aware)."""
    text = filepath.read_text(encoding="utf-8", errors="replace")
    name = filepath.name
    cat = classify_asset(filepath)
    
    # D1/D4 branch by category
    if cat == "sop":
        d1_raw, d1_reasons = score_d1(text)
        d4_raw, d4_reasons = score_d4(text)
    else:
        d1_raw, d1_reasons = score_d1_tool(text)
        d4_raw, d4_reasons = score_d4_tool(text)
    
    d2_raw, d2_reasons = score_d2(text)
    d3_raw, d3_reasons = score_d3(text)
    d5_raw, d5_reasons = score_d5(text)
    d6_raw, d6_reasons = score_d6(text)
    
    static_total = (
        d1_raw * WEIGHTS["D1_frontmatter"]
        + d2_raw * WEIGHTS["D2_workflow"]
        + d3_raw * WEIGHTS["D3_boundary"]
        + d4_raw * WEIGHTS["D4_checkpoints"]
        + d5_raw * WEIGHTS["D5_specificity"]
        + d6_raw * WEIGHTS["D6_resources"]
    ) / 10
    
    result = {
        "asset": name,
        "category": cat,
        "dimensions": {
            "D1_frontmatter": {"weight": 8, "raw": d1_raw, "weighted": d1_raw * 8 / 10, "reasons": d1_reasons},
            "D2_workflow":    {"weight": 15, "raw": d2_raw, "weighted": d2_raw * 15 / 10, "reasons": d2_reasons},
            "D3_boundary":    {"weight": 10, "raw": d3_raw, "weighted": d3_raw * 10 / 10, "reasons": d3_reasons},
            "D4_checkpoints": {"weight": 7, "raw": d4_raw, "weighted": d4_raw * 7 / 10, "reasons": d4_reasons},
            "D5_specificity": {"weight": 15, "raw": d5_raw, "weighted": d5_raw * 15 / 10, "reasons": d5_reasons},
            "D6_resources":   {"weight": 5, "raw": d6_raw, "weighted": d6_raw * 5 / 10, "reasons": d6_reasons},
            "D7_architecture": {"weight": 15, "raw": None, "weighted": None, "reasons": ["pending_llm"]},
            "D8_test_exec":   {"weight": 25, "raw": None, "weighted": None, "reasons": ["pending_llm"]},
        },
        "static_score": round(static_total, 1),
        "max_static": 60.0,
        "llm_prompt": generate_llm_prompt(text, name),
    }
    return result


def cmd_eval(args):
    fp = Path(args.asset)
    if not fp.exists():
        print(f"ERROR: file not found: {fp}")
        sys.exit(1)
    result = evaluate_asset(fp)
    
    print(f"asset: {result['asset']}  [{result.get('category', '?')}]")
    print(f"static_score (D1-D6): {result['static_score']}/{result['max_static']}")
    print()
    for dim, info in result["dimensions"].items():
        if info["raw"] is not None:
            print(f"  {dim}: raw={info['raw']}/10  weighted={info['weighted']:.1f}/{info['weight']}  {', '.join(info['reasons'])}")
        else:
            print(f"  {dim}: [LLM needed] weight={info['weight']}")
    
    if args.llm_prompt:
        print("\n--- LLM PROMPT (for D7/D8) ---")
        print(result["llm_prompt"]["prompt"])


def cmd_batch(args):
    mem = Path(args.memory_dir)
    files = sorted(mem.glob("*.md"))
    files += sorted(mem.glob("*.py"))
    # filter out non-sop files
    skip = {"global_mem.txt", "global_mem_insight.txt", "memory_management_sop.md"}
    files = [f for f in files if f.name not in skip and not f.name.startswith(".")]
    
    results = []
    for f in files:
        try:
            r = evaluate_asset(f)
            results.append(r)
        except Exception as e:
            results.append({"asset": f.name, "error": str(e)})
    
    # Print summary table
    print(f"{'Asset':<40} {'Cat':<5} {'D1':>3} {'D2':>3} {'D3':>3} {'D4':>3} {'D5':>3} {'D6':>3} {'Static':>7}")
    print("-" * 85)
    for r in results:
        if "error" in r:
            print(f"{r['asset']:<40} {'?':<5} ERROR: {r['error']}")
            continue
        d = r["dimensions"]
        cat = r.get("category", "?")
        print(f"{r['asset']:<40} {cat:<5} {d['D1_frontmatter']['raw']:>3} {d['D2_workflow']['raw']:>3} {d['D3_boundary']['raw']:>3} {d['D4_checkpoints']['raw']:>3} {d['D5_specificity']['raw']:>3} {d['D6_resources']['raw']:>3} {r['static_score']:>6.1f}")
    
    # Save JSON
    out = Path(args.output) if args.output else mem.parent / "temp" / "darwin_eval_v2_baseline.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {len(results)} results to {out}")


def cmd_llm_score(args):
    """Record LLM-provided D7/D8 scores and compute final total."""
    fp = Path(args.json_file)
    data = json.loads(fp.read_text(encoding="utf-8"))
    
    d7_raw = args.d7
    d8_raw = args.d8
    asset_name = args.asset
    
    for r in data:
        if r.get("asset") == asset_name:
            d = r["dimensions"]
            d["D7_architecture"]["raw"] = d7_raw
            d["D7_architecture"]["weighted"] = d7_raw * 15 / 10
            d["D8_test_exec"]["raw"] = d8_raw
            d["D8_test_exec"]["weighted"] = d8_raw * 25 / 10
            total = r["static_score"] + d["D7_architecture"]["weighted"] + d["D8_test_exec"]["weighted"]
            r["total_score"] = round(total, 1)
            print(f"{asset_name}: static={r['static_score']} D7={d7_raw}*1.5={d['D7_architecture']['weighted']:.1f} D8={d8_raw}*2.5={d['D8_test_exec']['weighted']:.1f} TOTAL={r['total_score']}/100")
            break
    else:
        print(f"Asset '{asset_name}' not found in {fp}")
        return
    
    fp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _apply_llm_scores(result: dict, llm_result: dict) -> dict:
    """Merge LLM D7/D8 scores into evaluate_asset result and compute total."""
    d7 = llm_result.get("D7")
    d8 = llm_result.get("D8")
    d = result["dimensions"]
    if d7 is not None:
        d["D7_architecture"]["raw"] = d7
        d["D7_architecture"]["weighted"] = d7 * 15 / 10
        d["D7_architecture"]["reasons"] = [llm_result.get("d7_reason", "")]
    if d8 is not None:
        d["D8_test_exec"]["raw"] = d8
        d["D8_test_exec"]["weighted"] = d8 * 25 / 10
        d["D8_test_exec"]["reasons"] = [llm_result.get("d8_reason", "")]
    total = result["static_score"]
    total += d["D7_architecture"]["weighted"] if d["D7_architecture"]["weighted"] else 0
    total += d["D8_test_exec"]["weighted"] if d["D8_test_exec"]["weighted"] else 0
    result["total_score"] = round(total, 1)
    result["llm_d7_d8"] = llm_result
    return result


def cmd_full_eval(args):
    """Evaluate single asset with D1-D6 static + D7-D8 LLM (100-point)."""
    fp = Path(args.asset)
    if not fp.exists():
        print(f"ERROR: file not found: {fp}")
        sys.exit(1)
    
    result = evaluate_asset(fp)
    text = fp.read_text(encoding="utf-8", errors="replace")
    
    print(f"asset: {result['asset']}  [{result.get('category', '?')}]")
    print(f"static_score (D1-D6): {result['static_score']}/{result['max_static']}")
    for dim, info in result["dimensions"].items():
        if info["raw"] is not None:
            print(f"  {dim}: raw={info['raw']}/10  weighted={info['weighted']:.1f}/{info['weight']}  {', '.join(info['reasons'])}")
    
    print("\nCalling LLM for D7/D8...")
    llm_result = evaluate_d7_d8_llm(
        text, result["asset"],
        api_base=args.api_base, model=args.model
    )
    
    if llm_result.get("error"):
        print(f"  LLM ERROR: {llm_result['error']}")
    else:
        result = _apply_llm_scores(result, llm_result)
        print(f"  D7_architecture: {llm_result['D7']}/10 — {llm_result.get('d7_reason','')}")
        print(f"  D8_test_exec: {llm_result['D8']}/10 — {llm_result.get('d8_reason','')}")
        print(f"\n  TOTAL: {result['total_score']}/100")
    
    out = Path(args.output) if args.output else None
    if out:
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved to {out}")


def cmd_full_batch(args):
    """Batch full 100-point evaluation (D1-D6 static + D7-D8 LLM)."""
    mem = Path(args.memory_dir)
    files = sorted(mem.glob("*.md"))
    files += sorted(mem.glob("*.py"))
    skip = {"global_mem.txt", "global_mem_insight.txt", "memory_management_sop.md"}
    files = [f for f in files if f.name not in skip and not f.name.startswith(".")]
    
    if args.limit:
        files = files[:args.limit]
    
    results = []
    errors = []
    for i, f in enumerate(files):
        print(f"[{i+1}/{len(files)}] {f.name}...", end=" ", flush=True)
        try:
            result = evaluate_asset(f)
            text = f.read_text(encoding="utf-8", errors="replace")
            llm_result = evaluate_d7_d8_llm(
                text, result["asset"],
                api_base=args.api_base, model=args.model
            )
            if llm_result.get("error"):
                result["llm_error"] = llm_result["error"]
                errors.append(f.name)
                print(f"LLM error: {llm_result['error'][:50]}")
            else:
                result = _apply_llm_scores(result, llm_result)
                print(f"D7={llm_result['D7']} D8={llm_result['D8']} total={result['total_score']}")
            results.append(result)
        except Exception as e:
            results.append({"asset": f.name, "error": str(e)})
            errors.append(f.name)
            print(f"ERROR: {e}")
        # Rate limit
        time.sleep(args.delay)
    
    # Save results
    out = Path(args.output) if args.output else mem.parent / "temp" / "darwin_full_100.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # Summary
    ok = [r for r in results if "total_score" in r]
    if ok:
        scores = [r["total_score"] for r in ok]
        avg = sum(scores) / len(scores)
        statics = [r["static_score"] for r in ok]
        avg_static = sum(statics) / len(statics)
        d7s = [r["dimensions"]["D7_architecture"]["raw"] for r in ok if r["dimensions"]["D7_architecture"]["raw"] is not None]
        d8s = [r["dimensions"]["D8_test_exec"]["raw"] for r in ok if r["dimensions"]["D8_test_exec"]["raw"] is not None]
        avg_d7 = sum(d7s) / len(d7s) if d7s else 0
        avg_d8 = sum(d8s) / len(d8s) if d8s else 0
        
        print(f"\n{'='*80}")
        print(f"{'SOP':<43} {'Cat':<5} {'Static':>6} {'D7':>4} {'D8':>4} {'Total':>6}")
        print(f"{'-'*85}")
        for r in ok:
            d = r["dimensions"]
            d7 = d["D7_architecture"]["raw"] or "-"
            d8 = d["D8_test_exec"]["raw"] or "-"
            cat = r.get("category", "?")
            print(f"{r['asset']:<43} {cat:<5} {r['static_score']:>6.1f} {str(d7):>4} {str(d8):>4} {r['total_score']:>6.1f}")
        print(f"{'-'*85}")
        print(f"{'AVERAGE':<48} {avg_static:>6.1f} {avg_d7:>4.1f} {avg_d8:>4.1f} {avg:>6.1f}")
        print(f"\nSuccess: {len(ok)}/{len(results)} | Errors: {len(errors)}")
    print(f"Results saved: {out}")


def build_parser():
    p = argparse.ArgumentParser(description="Darwin 8-Dimension Eval Engine")
    sub = p.add_subparsers(dest="cmd")
    
    s = sub.add_parser("eval", help="Evaluate single asset (D1-D6 static)")
    s.add_argument("--asset", required=True)
    s.add_argument("--llm-prompt", action="store_true", help="Print LLM prompt for D7/D8")
    s.set_defaults(func=cmd_eval)
    
    s = sub.add_parser("batch", help="Batch evaluate all SOPs (D1-D6 static)")
    s.add_argument("--memory-dir", default=str(Path.home() / "AppData/Roaming/GA/memory"))
    s.add_argument("--output", default=None)
    s.set_defaults(func=cmd_batch)
    
    s = sub.add_parser("llm-score", help="Record LLM D7/D8 scores into existing JSON")
    s.add_argument("--json-file", required=True)
    s.add_argument("--asset", required=True)
    s.add_argument("--d7", type=int, required=True)
    s.add_argument("--d8", type=int, required=True)
    s.set_defaults(func=cmd_llm_score)
    
    s = sub.add_parser("full-eval", help="Full 100-point eval: D1-D6 static + D7-D8 LLM")
    s.add_argument("--asset", required=True)
    s.add_argument("--output", default=None)
    s.add_argument("--api-base", default="https://api.openai.com/v1")
    s.add_argument("--model", default="gpt-4o-mini")
    s.set_defaults(func=cmd_full_eval)
    
    s = sub.add_parser("full-batch", help="Full 100-point batch: all SOPs with LLM D7/D8")
    s.add_argument("--memory-dir", default=str(Path.home() / "AppData/Roaming/GA/memory"))
    s.add_argument("--output", default=None)
    s.add_argument("--api-base", default="https://api.openai.com/v1")
    s.add_argument("--model", default="gpt-4o-mini")
    s.add_argument("--limit", type=int, default=None, help="Limit number of assets")
    s.add_argument("--delay", type=float, default=1.0, help="Seconds between API calls")
    s.set_defaults(func=cmd_full_batch)
    
    return p

if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
    else:
        args.func(args)
