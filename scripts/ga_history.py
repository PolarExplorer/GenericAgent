import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _common import *


def lint(messages):
    """返回 issues 列表，每项 {rule, index, detail}"""
    issues = []

    if messages and messages[0].get("role") != "user":
        issues.append({"rule": "NOT_USER_FIRST", "index": 0})

    for i in range(1, len(messages)):
        if messages[i].get("role") == messages[i - 1].get("role"):
            issues.append({"rule": "CONSECUTIVE_SAME_ROLE", "index": i})

    if messages and messages[-1].get("role") == "assistant":
        content = messages[-1].get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    issues.append({"rule": "TRAILING_TOOL_USE", "index": len(messages) - 1})
                    break

    pending_tool_uses = set()
    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "assistant" and (content is None or content == "" or content == []):
            issues.append({"rule": "EMPTY_CONTENT", "index": i})

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    pending_tool_uses.add(block.get("id"))
                elif block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if tool_use_id in pending_tool_uses:
                        pending_tool_uses.discard(tool_use_id)
                    else:
                        issues.append(
                            {
                                "rule": "ORPHAN_TOOL_RESULT",
                                "index": i,
                                "detail": f"tool_use_id={tool_use_id}",
                            }
                        )

        if role == "assistant" and "tool_calls" in msg:
            for tc in msg.get("tool_calls", []):
                if isinstance(tc, dict):
                    pending_tool_uses.add(tc.get("id"))
        if role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id in pending_tool_uses:
                pending_tool_uses.discard(tool_call_id)
            else:
                issues.append(
                    {
                        "rule": "ORPHAN_TOOL_RESULT",
                        "index": i,
                        "detail": f"tool_call_id={tool_call_id}",
                    }
                )

    for tid in sorted(x for x in pending_tool_uses if x):
        issues.append({"rule": "ORPHAN_TOOL_CALL", "detail": f"tool_use id={tid}"})

    return issues


def heal(messages, issues):
    """根据 issues 自动修复，返回新 messages 列表"""
    healed = list(messages)

    if any(item["rule"] == "NOT_USER_FIRST" for item in issues):
        healed.insert(0, {"role": "user", "content": "[auto-healed] session start"})

    if any(item["rule"] == "TRAILING_TOOL_USE" for item in issues):
        healed = healed[:-1]

    merged = [healed[0]] if healed else []
    for msg in healed[1:]:
        if msg.get("role") == merged[-1].get("role"):
            prev_content = merged[-1].get("content", "")
            curr_content = msg.get("content", "")
            if isinstance(prev_content, str) and isinstance(curr_content, str):
                merged[-1]["content"] = prev_content + "\n" + curr_content
            elif isinstance(prev_content, list) and isinstance(curr_content, list):
                merged[-1]["content"] = prev_content + curr_content
        else:
            merged.append(msg)

    return merged


def load_messages(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("messages", [])
    return []


def cmd_lint(args):
    if not os.path.exists(args.file):
        fail(f"文件不存在: {args.file}")
        return EXIT_FAIL

    try:
        messages = load_messages(args.file)
    except Exception as exc:
        fail(f"读取失败: {exc}")
        return EXIT_FAIL

    issues = lint(messages)
    if not issues:
        ok(f"0 issues ({len(messages)} messages)")
        return EXIT_OK

    fail(f"发现 {len(issues)} 个问题:")
    for item in issues:
        detail = item.get("detail", "")
        suffix = f" ({detail})" if detail else ""
        if "index" in item:
            print(f"  - {item['rule']} @ messages[{item['index']}]" + suffix)
        else:
            print(f"  - {item['rule']}" + suffix)
    return EXIT_FAIL


@confirm_required
def cmd_heal(args, dry_run=True):
    if not os.path.exists(args.file):
        fail(f"文件不存在: {args.file}")
        return EXIT_FAIL

    out_path = args.out
    if not out_path:
        base, ext = os.path.splitext(args.file)
        out_path = f"{base}.healed{ext or '.json'}"

    if os.path.abspath(out_path) == os.path.abspath(args.file):
        fail("heal 禁止覆盖原文件，请使用不同的 --out")
        return EXIT_FAIL

    try:
        messages = load_messages(args.file)
    except Exception as exc:
        fail(f"读取失败: {exc}")
        return EXIT_FAIL

    issues = lint(messages)
    if not issues:
        info("无问题，无需修复")
        return EXIT_SKIP

    fixed = heal(messages, issues)

    if dry_run:
        info(f"[预览] 将输出修复文件: {out_path}")
        info(f"[预览] messages: {len(messages)} -> {len(fixed)}")
        return EXIT_SKIP

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fixed, f, ensure_ascii=False, indent=2)
    ok(f"已写入: {out_path}")
    return EXIT_OK


def main():
    parser = argparse.ArgumentParser(description="GA History 合法性检查与清理")
    sub = parser.add_subparsers(dest="command")

    p_lint = sub.add_parser("lint", help="检查 history 文件")
    p_lint.add_argument("file")

    p_heal = sub.add_parser("heal", help="修复 history 文件")
    p_heal.add_argument("file")
    p_heal.add_argument("--out", help="输出文件路径")
    p_heal.add_argument("--confirm", action="store_true", help="实际写入修复文件")

    args = parser.parse_args()

    if args.command == "lint":
        sys.exit(cmd_lint(args))
    if args.command == "heal":
        sys.exit(cmd_heal(args))

    parser.print_help()
    sys.exit(EXIT_SKIP)


if __name__ == "__main__":
    main()
