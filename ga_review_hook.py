"""
GA Review Hook - Task completion review with isolated Opus context.
KISS: track project_root from file paths -> on CURRENT_TASK_DONE -> fast_ask Opus -> print + save.
"""
import os, re, traceback
from datetime import datetime


def track_project_root(tool_calls, working):
    """Scan tool_calls file paths each turn; if spec.md found, store its parent as project_root."""
    if working.get('project_root'):
        return  # already detected this session

    paths = []
    for tc in tool_calls:
        args = tc.get('args', {})
        for key in ('path', 'cwd', 'file_path'):
            v = args.get(key)
            if v:
                paths.append(v)
        script = args.get('script', '')
        if script:
            for m in re.finditer(r'["\']([^"\']*(?:spec|tasks)\.md[^"\']*)["\']', script):
                paths.append(m.group(1))

    for p in paths:
        try:
            p = os.path.abspath(p)
        except Exception:
            continue
        d = os.path.dirname(p) if not os.path.isdir(p) else p
        for _ in range(6):
            if os.path.isfile(os.path.join(d, 'spec.md')):
                working['project_root'] = d
                print(f"[ReviewHook] Project root detected: {d}")
                return
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent


def run_review(working, parent):
    """On task completion: read spec.md+tasks.md, call Opus in isolated context, print verdict."""
    project_root = working.get('project_root')
    if not project_root:
        return None

    spec_path = os.path.join(project_root, 'spec.md')
    tasks_path = os.path.join(project_root, 'tasks.md')
    plan_path = os.path.join(project_root, 'plan.md')
    trace_path = os.path.join(project_root, 'traceability_matrix.md')

    if not os.path.isfile(spec_path):
        print(f"[ReviewHook] spec.md not found at {spec_path}, skip.")
        return None

    with open(spec_path, 'r', encoding='utf-8', errors='replace') as f:
        spec_content = f.read()[:60000]
    tasks_content = ''
    if os.path.isfile(tasks_path):
        with open(tasks_path, 'r', encoding='utf-8', errors='replace') as f:
            tasks_content = f.read()[:15000]
    plan_content = ''
    if os.path.isfile(plan_path):
        with open(plan_path, 'r', encoding='utf-8', errors='replace') as f:
            plan_content = f.read()[:15000]
    trace_content = ''
    if os.path.isfile(trace_path):
        with open(trace_path, 'r', encoding='utf-8', errors='replace') as f:
            trace_content = f.read()[:15000]

    # Get Opus session (llm_no=2) for isolated call
    try:
        llmclients = getattr(parent, 'llmclients', None)
        if not llmclients or len(llmclients) <= 2:
            print("[ReviewHook] Opus client unavailable (llmclients[2] missing).")
            return None
        opus_session = getattr(llmclients[2], 'backend', None)
        if not opus_session:
            print("[ReviewHook] Opus backend session not found.")
            return None
    except Exception as e:
        print(f"[ReviewHook] Error getting Opus session: {e}")
        return None

    review_prompt = (
        "You are an independent SDD document reviewer. You have NO conversation history.\n"
        "Your job: audit spec/tasks/plan/traceability completeness and cross-document alignment.\n\n"
        "## spec.md:\n" + spec_content + "\n\n"
        "## tasks.md:\n" + (tasks_content or "(not found)") + "\n\n"
        "## plan.md:\n" + (plan_content or "(not found)") + "\n\n"
        "## traceability_matrix.md:\n" + (trace_content or "(not found)") + "\n\n"
        "## 1. Spec Structure (7 elements) - check each:\n"
        "- Problem Statement: clear, unambiguous, single interpretation?\n"
        "- Success Metrics: quantifiable, testable?\n"
        "- User Stories: cover core scenarios?\n"
        "- Acceptance Criteria: each item verifiable?\n"
        "- Non-Goals: explicitly stated what is OUT of scope?\n"
        "- Constraints: technical/resource limits listed?\n"
        "- Granularity test: would this spec still be valid if you swapped the tech stack?\n\n"
        "## 2. Tasks alignment:\n"
        "- Do tasks map to spec objectives?\n"
        "- Progress markers accurate?\n"
        "- Any spec requirements missing from tasks?\n\n"
        "## 3. Plan alignment:\n"
        "- Does plan.md phases/milestones match spec scope?\n"
        "- Are phase dependencies logical?\n"
        "- Does task ordering in tasks.md follow plan phases?\n\n"
        "## 4. Traceability check:\n"
        "- Does traceability_matrix.md cover ALL spec requirements?\n"
        "- Any spec requirement with no corresponding task?\n"
        "- Any task with no spec requirement (orphan work)?\n\n"
        "## 5. Domain blind-spot detection:\n"
        "Based on the project goals and context described in spec.md, identify key dimensions\n"
        "that SHOULD be defined but are NOT — things whose absence forces the implementer to guess.\n\n"
        "## 6. Drift check:\n"
        "- Any completed work that doesn't serve stated objectives?\n"
        "- Task ordering sensible?\n\n"
        "## Output format (concise):\n"
        "VERDICT: PASS | INCOMPLETE | REALIGN_NEEDED\n"
        "SPEC_GAPS (spec.md\u6587\u6863\u7f3a\u5931/\u8584\u5f31\u9879): (from section 1, or None)\n"
        "PLAN_ALIGNMENT (plan\u4e0espec/tasks\u7684\u5bf9\u9f50\u95ee\u9898): (from section 3, or None)\n"
        "TRACEABILITY (\u9700\u6c42\u8ffd\u6eaf\u7f3a\u53e3\uff0cspec\u9700\u6c42\u672a\u88ab\u4efb\u52a1\u8986\u76d6): (from section 4, or None)\n"
        "BLIND_SPOTS (\u9886\u57df\u76f2\u533a\uff0c\u672a\u5b9a\u4e49\u5bfc\u81f4\u5b9e\u65bd\u8005\u9700\u731c\u6d4b): (from section 5, or None)\n"
        "DRIFT (\u6267\u884c\u504f\u79bb\uff0ctasks\u5b9e\u9645\u8fdb\u5ea6\u4e0espec\u76ee\u6807\u4e0d\u4e00\u81f4): (from section 6, or None)\n"
        "RECOMMENDATION: (max 3 actionable bullets to improve the documents)\n"
    )

    try:
        print("[ReviewHook] Calling Opus for isolated review...")
        msgs = [{"role": "user", "content": [{"type": "text", "text": review_prompt}]}]
        review_result = "".join(opus_session.raw_ask(msgs))

        # Save
        review_path = os.path.join(project_root, 'review_latest.md')
        with open(review_path, 'w', encoding='utf-8') as f:
            f.write(f"# Review ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n")
            f.write(review_result)

        print(f"\n{'='*60}")
        print("[REVIEW HOOK] Task Completion Review")
        print(f"{'='*60}")
        print(review_result)
        print(f"{'='*60}")
        print(f"Saved: {review_path}\n")
        return review_result
    except Exception as e:
        print(f"[ReviewHook] Review failed: {e}")
        traceback.print_exc()
        return None
