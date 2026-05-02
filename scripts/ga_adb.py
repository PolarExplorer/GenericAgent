import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory"))
from _common import *

ADB = "adb"


def check_device():
    """检查 ADB 设备连接"""
    r = subprocess.run([ADB, "devices"], capture_output=True, text=True)
    lines = [line for line in r.stdout.strip().splitlines()[1:] if line.strip()]
    devices = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1]})
    return devices


def cmd_status(args):
    devices = check_device()
    if not devices:
        fail("无 ADB 设备连接")
        return EXIT_FAIL
    for device in devices:
        status = "✅" if device["state"] == "device" else "⚠️"
        print(f"  {status} {device['serial']} ({device['state']})")
    return EXIT_OK


def cmd_screenshot(args):
    devices = check_device()
    if not devices:
        fail("无设备")
        return EXIT_FAIL

    out = args.out or os.path.join(str(GA_ROOT), "temp", "adb_screenshot.png")
    subprocess.run([ADB, "shell", "screencap", "-p", "/sdcard/screen.png"])
    subprocess.run([ADB, "pull", "/sdcard/screen.png", out])
    subprocess.run([ADB, "shell", "rm", "/sdcard/screen.png"])

    if os.path.exists(out):
        ok(f"截图已保存: {out} ({os.path.getsize(out)} bytes)")
        return EXIT_OK

    fail("截图失败")
    return EXIT_FAIL


@confirm_required
def cmd_tap(args, dry_run=True):
    if dry_run:
        info(f"[预览] 将点击坐标 ({args.x}, {args.y})")
        info("提示: 先截图确认坐标，再加 --confirm 执行")
        cmd_screenshot(type("Args", (), {"out": None})())
        return EXIT_SKIP

    subprocess.run([ADB, "shell", "input", "tap", str(args.x), str(args.y)])
    ok(f"已点击 ({args.x}, {args.y})")
    return EXIT_OK


@confirm_required
def cmd_text(args, dry_run=True):
    if dry_run:
        info(f"[预览] 将输入文本: \"{args.content}\"")
        return EXIT_SKIP

    escaped = args.content.replace(" ", "%s")
    subprocess.run([ADB, "shell", "input", "text", escaped])
    ok(f"已输入: {args.content}")
    return EXIT_OK


def main():
    parser = argparse.ArgumentParser(description="GA ADB 手机操作")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")

    p_ss = sub.add_parser("screenshot")
    p_ss.add_argument("--out")

    p_tap = sub.add_parser("tap")
    p_tap.add_argument("x", type=int)
    p_tap.add_argument("y", type=int)
    p_tap.add_argument("--confirm", action="store_true")

    p_text = sub.add_parser("text")
    p_text.add_argument("content")
    p_text.add_argument("--confirm", action="store_true")

    args = parser.parse_args()
    dispatch = {
        "status": cmd_status,
        "screenshot": cmd_screenshot,
        "tap": cmd_tap,
        "text": cmd_text,
    }
    if args.command in dispatch:
        sys.exit(dispatch[args.command](args))

    parser.print_help()
    sys.exit(EXIT_SKIP)


if __name__ == "__main__":
    main()
