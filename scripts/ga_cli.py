#!/usr/bin/env python3
"""GA unified CLI dispatcher.

Usage:
    ga <tool> [subcommand] [args...]

Examples:
    ga ports list
    ga ports check 8765
    ga service status ga
    ga git status
    ga debug --target ga
    ga diag analyze
    ga mem scripts
    ga task init my_task
    ga history --help
    ga ocr run image.png
    ga doc read file.pdf
    ga web open https://example.com
    ga adb status
"""
import sys, os, subprocess

# Resolve paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GA_ROOT = os.path.dirname(SCRIPT_DIR)

# tool-name -> script filename (without .py)
TOOL_MAP = {
    "ports":   "ga_ports",
    "service": "ga_service",
    "git":     "ga_git_safe",
    "debug":   "ga_debug_collect",
    "diag":    "ga_diag_api",
    "mem":     "ga_mem_find",
    "task":    "ga_task_init",
    "history": "ga_history",
    "ocr":     "ga_ocr",
    "doc":     "ga_doc",
    "web":     "ga_web",
    "adb":     "ga_adb",
}

TOOL_DESC = {
    "ports":   "端口巡检与清理 (list/check/kill/tree)",
    "service": "服务管理 (status/restart/stop/start)",
    "git":     "安全Git操作 (status/stage/commit)",
    "debug":   "调试信息收集 (--target ga|system)",
    "diag":    "API诊断 (analyze/recent/hint)",
    "mem":     "记忆关键词搜索",
    "task":    "任务目录初始化 (init/clean)",
    "history": "对话历史维护 (lint/heal/export)",
    "ocr":     "本地OCR识别 (run)",
    "doc":     "文档处理 (read/images/merge)",
    "web":     "浏览器操作 (open/extract/screenshot/frames)",
    "adb":     "Android调试 (status/screenshot/tap/swipe/text)",
}


def print_help():
    print("GA CLI — GenericAgent 统一命令行工具")
    print(f"用法: ga <tool> [subcommand] [args...]\n")
    print("可用工具:")
    max_len = max(len(k) for k in TOOL_MAP)
    for name in TOOL_MAP:
        desc = TOOL_DESC.get(name, "")
        print(f"  {name:<{max_len+2}} {desc}")
    print(f"\n提示: ga <tool> --help  查看子命令详情")


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0

    tool = args[0].lower()
    if tool not in TOOL_MAP:
        print(f"[ERR] 未知工具: {tool}")
        print(f"  可用: {', '.join(sorted(TOOL_MAP.keys()))}")
        return 1

    script_path = os.path.join(SCRIPT_DIR, TOOL_MAP[tool] + ".py")
    if not os.path.isfile(script_path):
        print(f"[ERR] 脚本不存在: {script_path}")
        return 1

    # Forward remaining args to the target script
    cmd = [sys.executable, script_path] + args[1:]
    result = subprocess.run(cmd, cwd=GA_ROOT)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())