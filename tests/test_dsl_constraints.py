"""DSL Constraint Engine — 全量正例/反例自动化测试集
覆盖全部8条约束，每条至少1正例(触发fail/pending_llm) + 1反例(skip/pass)
"""
import sys, json, os
sys.path.insert(0, r'D:\AI\GenericAgent')
import ga_constraint_engine as eng

DSL_PATH = r'D:\AI\GenericAgent\assets\constraints_dsl.json'

def load_constraints():
    with open(DSL_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return {c['id']: c for c in data['constraints']}

ALL = load_constraints()
PASS = FAIL = 0

def check(name, cid, ctx, expected_status):
    global PASS, FAIL
    c = ALL[cid]
    r = eng.evaluate_constraint(c, ctx)
    ok = r['status'] == expected_status
    tag = 'PASS' if ok else 'FAIL'
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{tag}] {name}: got={r['status']}, expected={expected_status}, reason={r.get('reason','')}")

def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")

# ===== 1. MISSING-PS-CAT-TYPE (pattern_forbidden) =====
section("MISSING-PS-CAT-TYPE")

check("ps_cat_file", "MISSING-PS-CAT-TYPE",
    {"tool_calls": [], "scripts": ["cat config.yaml"], "response_text": "", "user_message": ""},
    "fail")

check("ps_type_file", "MISSING-PS-CAT-TYPE",
    {"tool_calls": [], "scripts": ["type settings.ini"], "response_text": "", "user_message": ""},
    "fail")

check("ps_get_content_ok", "MISSING-PS-CAT-TYPE",
    {"tool_calls": [], "scripts": ["Get-Content file.txt"], "response_text": "", "user_message": ""},
    "pass")

check("python_open_ok", "MISSING-PS-CAT-TYPE",
    {"tool_calls": [], "scripts": ["open('test.py').read()"], "response_text": "", "user_message": ""},
    "pass")

check("empty_ctx", "MISSING-PS-CAT-TYPE",
    {"tool_calls": [], "scripts": [], "response_text": "", "user_message": ""},
    "skip")

# ===== 2. MISSING-PLAN-WORKDIR (precondition) =====
section("MISSING-PLAN-WORKDIR")

# 正例: 响应含"规划"但历史无code_run/file_write → fail
check("plan_no_workdir", "MISSING-PLAN-WORKDIR",
    {"tool_calls": [], "scripts": [], "response_text": "我来做一个规划方案",
     "user_message": "", "history": []},
    "fail")

# 反例: 响应含"规划"且历史有code_run → pass
check("plan_with_workdir", "MISSING-PLAN-WORKDIR",
    {"tool_calls": [], "scripts": [], "response_text": "我来做一个规划方案",
     "user_message": "",
     "history": [{"tool_calls": [{"tool_name": "code_run", "args": {}}], "response_text": ""}]},
    "pass")

# 反例: 响应不含规划关键词 → skip
check("no_plan_keyword", "MISSING-PLAN-WORKDIR",
    {"tool_calls": [], "scripts": [], "response_text": "好的，我来读文件",
     "user_message": "", "history": []},
    "skip")

# ===== 3. MISSING-PLAN-REREAD (precondition) =====
section("MISSING-PLAN-REREAD")

# 正例: 响应含"下一步"但历史无file_read → fail
check("next_step_no_reread", "MISSING-PLAN-REREAD",
    {"tool_calls": [], "scripts": [], "response_text": "下一步我来实施",
     "user_message": "", "history": []},
    "fail")

# 反例: 响应含"继续"且历史有file_read → pass
check("continue_with_reread", "MISSING-PLAN-REREAD",
    {"tool_calls": [], "scripts": [], "response_text": "继续执行第三步",
     "user_message": "",
     "history": [{"tool_calls": [{"tool_name": "file_read", "args": {}}], "response_text": ""}]},
    "pass")

# 反例: 无触发词 → skip
check("no_trigger", "MISSING-PLAN-REREAD",
    {"tool_calls": [], "scripts": [], "response_text": "好的",
     "user_message": "", "history": []},
    "skip")

# ===== 4. MISSING-VISION-ENUM-WINDOW (precondition) =====
section("MISSING-VISION-ENUM-WINDOW")

# 正例: 响应含"截图"+有vision工具调用 但历史无枚举窗口 → fail
check("screenshot_no_enum", "MISSING-VISION-ENUM-WINDOW",
    {"tool_calls": [{"tool_name": "code_run", "args": {}}],
     "scripts": [], "response_text": "我来截图看看",
     "user_message": "", "history": []},
    "fail")

# 反例: 响应含"截图"+有vision工具调用 且历史有"枚举窗口" → pass
check("screenshot_with_enum", "MISSING-VISION-ENUM-WINDOW",
    {"tool_calls": [{"tool_name": "code_run", "args": {}}],
     "scripts": [], "response_text": "我来截图看看",
     "user_message": "",
     "history": [{"tool_calls": [], "response_text": "先枚举窗口确认目标"}]},
    "pass")

# 反例: 无vision关键词 → skip
check("no_vision_keyword", "MISSING-VISION-ENUM-WINDOW",
    {"tool_calls": [], "scripts": [], "response_text": "读取文件内容",
     "user_message": "", "history": []},
    "skip")

# ===== 5. MISSING-LJQ-ACTIVATE (precondition) =====
section("MISSING-LJQ-ACTIVATE")

# 正例: 响应含"键鼠操作"+有code_run 但历史无activate → fail
check("click_no_activate", "MISSING-LJQ-ACTIVATE",
    {"tool_calls": [{"tool_name": "code_run", "args": {}}],
     "scripts": [], "response_text": "用键鼠操作点击确认按钮",
     "user_message": "", "history": []},
    "fail")

# 反例: 响应含"键鼠操作"+有code_run 且历史有"激活窗口" → pass
check("keyboard_with_activate", "MISSING-LJQ-ACTIVATE",
    {"tool_calls": [{"tool_name": "code_run", "args": {}}],
     "scripts": [], "response_text": "用键鼠操作",
     "user_message": "",
     "history": [{"tool_calls": [], "response_text": "已激活窗口到前台"}]},
    "pass")

# 反例: 无键鼠关键词 → skip
check("no_ljq_keyword", "MISSING-LJQ-ACTIVATE",
    {"tool_calls": [], "scripts": [], "response_text": "分析数据",
     "user_message": "", "history": []},
    "skip")

# ===== 6. MISSING-MEM-L0-READ (precondition) =====
section("MISSING-MEM-L0-READ")

# 正例: file_patch写memory路径 但历史无L0读取 → fail
check("mem_write_no_l0", "MISSING-MEM-L0-READ",
    {"tool_calls": [{"tool_name": "file_patch", "args": {"path": "memory/global_mem.txt"}}],
     "scripts": [],
     "response_text": "修改 memory/global_mem.txt file_patch写入新内容",
     "user_message": "", "history": []},
    "fail")

# 反例: file_patch写memory路径 且历史有META-SOP → pass
check("mem_write_with_l0", "MISSING-MEM-L0-READ",
    {"tool_calls": [{"tool_name": "file_patch", "args": {"path": "memory/global_mem.txt"}}],
     "scripts": [],
     "response_text": "修改 memory/global_mem.txt file_patch写入新内容",
     "user_message": "",
     "history": [{"tool_calls": [{"tool_name": "file_read", "args": {}}],
                  "response_text": "已读取META-SOP确认规则"}]},
    "pass")

# 反例: 无记忆写入动作 → skip
check("no_mem_action", "MISSING-MEM-L0-READ",
    {"tool_calls": [], "scripts": [], "response_text": "读取配置文件",
     "user_message": "", "history": []},
    "skip")

# ===== 7. REG-C018 / REG-C027 sequence false-positive regressions =====
section("REG-C018 / REG-C027 sequence regressions")

# 反例: .bat 脚本修改不是新项目编码，也不是 UI 开发，不应触发 C018/C027
bat_write_ctx = {
    "tool_calls": [{"tool_name": "file_write", "args": {"path": r"C:\\Users\\ZhuanZ（无密码）\\Desktop\\重启GA.bat"}}],
    "scripts": [], "response_text": "", "user_message": "", "history": []
}
check("c018_bat_write_not_new_project", "REG-C018", bat_write_ctx, "skip")
check("c027_bat_write_not_ui", "REG-C027", bat_write_ctx, "skip")

# 反例: GA 自身/temp 内实验写代码和 UI 文件不按外部新项目/UI mockup 规则误报
check("c018_temp_code_excluded", "REG-C018",
    {"tool_calls": [{"tool_name": "file_write", "args": {"path": r"D:\\AI\\GenericAgent\\temp\\NewTool\\main.py"}}],
     "scripts": [], "response_text": "", "user_message": "", "history": []},
    "skip")
check("c027_temp_ui_excluded", "REG-C027",
    {"tool_calls": [{"tool_name": "file_write", "args": {"path": r"D:\\AI\\GenericAgent\\temp\\NewUI\\index.html"}}],
     "scripts": [], "response_text": "", "user_message": "", "history": []},
    "skip")

# ===== 8. MISSING-TOOL-DISPATCH-TYPE (llm_judge) =====
section("MISSING-TOOL-DISPATCH-TYPE")

# 正例: 响应含"分发"触发词 → pending_llm
check("dispatch_triggered", "MISSING-TOOL-DISPATCH-TYPE",
    {"tool_calls": [], "scripts": [], "response_text": "使用subagent分发任务",
     "user_message": "", "history": []},
    "pending_llm")

# 反例: 无触发词 → skip
check("dispatch_not_triggered", "MISSING-TOOL-DISPATCH-TYPE",
    {"tool_calls": [], "scripts": [], "response_text": "好的，我来读文件",
     "user_message": "", "history": []},
    "skip")

# ===== 8. MISSING-CODE-HYPOTHESIS (llm_judge) =====
section("MISSING-CODE-HYPOTHESIS")

# 正例: 响应含"编码"触发词 → pending_llm
check("code_triggered", "MISSING-CODE-HYPOTHESIS",
    {"tool_calls": [], "scripts": [], "response_text": "现在开始编码实现功能",
     "user_message": "", "history": []},
    "pending_llm")

# 反例: 无触发词 → skip
check("code_not_triggered", "MISSING-CODE-HYPOTHESIS",
    {"tool_calls": [], "scripts": [], "response_text": "分析一下需求",
     "user_message": "", "history": []},
    "skip")


# ===== 9. REG-R044 celebrity visual prompt regressions =====
section("REG-R044 celebrity visual prompt regressions")

_nm1 = "\u9a6c\u65af\u514b"
_nm2 = "Taylor Swift"
_nm3 = "\u5965\u5df4\u9a6c"

def _r044_ctx(s):
    return {"tool_calls": [{"tool_name": "image_gen", "args": {"prompt": s}}],
            "scripts": [], "response_text": "", "user_message": "", "history": []}

# 正例：明确视觉产物请求 + 名人姓名，应拦截
check("r044_visual_named_person_cn", "REG-R044",
    _r044_ctx("\u751f\u6210\u4e00\u5f20" + _nm1 + "\u7684\u56fe\u7247"),
    "fail")
check("r044_visual_named_person_en", "REG-R044",
    _r044_ctx("draw a portrait of " + _nm2),
    "fail")

# 反例：文本精读/笔记/规则讨论中引用人物名，不应误拦截
check("r044_text_note_named_person_ok", "REG-R044",
    _r044_ctx("\u7cbe\u8bfb\u6587\u7ae0\uff0c\u5236\u4f5c Markdown \u7b14\u8bb0\uff0c\u539f\u6587\u63d0\u5230" + _nm1 + " perspective skill"),
    "pass")
check("r044_article_named_person_ok", "REG-R044",
    _r044_ctx("\u5199\u4e00\u7bc7\u5173\u4e8e" + _nm3 + "\u7ba1\u7406\u98ce\u683c\u7684\u6587\u7ae0"),
    "pass")
check("r044_rule_discussion_ok", "REG-R044",
    _r044_ctx("\u8ba8\u8bba\u89c4\u5219 REG-R044 \u4e3a\u4ec0\u4e48\u4e0d\u5e94\u76f4\u63a5\u4f7f\u7528\u516c\u4f17\u4eba\u7269\u59d3\u540d"),
    "pass")

print(f"\n{'='*60}")
print(f"  TOTAL: {PASS} passed, {FAIL} failed out of {PASS+FAIL}")
print(f"{'='*60}")

if __name__ == "__main__":
    sys.exit(1 if FAIL else 0)
