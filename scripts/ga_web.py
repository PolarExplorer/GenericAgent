import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory"))
from _common import *


def get_browser_lock():
    """获取浏览器资源锁，防多Bot并发"""
    try:
        from ga_resource_lock import BrowserLock

        lock = BrowserLock()
        lock.acquire(timeout=30)
        return lock
    except ImportError:
        warn("ga_resource_lock 不可用，跳过锁")
        return None
    except Exception as exc:
        warn(f"获取浏览器锁失败，继续执行: {exc}")
        return None


def _new_cdp():
    from cdp_utils import CDPProxy

    cdp = CDPProxy()
    cdp.ensure_running()
    return cdp


def cmd_open(args):
    lock = get_browser_lock()
    tab_id = None
    try:
        cdp = _new_cdp()
        tab_id = cdp.new_tab(args.url, timeout=args.timeout)
        page_info = cdp.info(tab_id) if tab_id else {}
        ok(f"已打开: {args.url}")
        if isinstance(page_info, dict):
            info(f"Title: {page_info.get('title', 'N/A')}")
        return EXIT_OK
    except Exception as exc:
        fail(f"open 失败: {exc}")
        return EXIT_FAIL
    finally:
        try:
            if tab_id:
                cdp.close_tab(tab_id)
        except Exception:
            pass
        if lock:
            lock.release()


def cmd_extract(args):
    lock = get_browser_lock()
    tab_id = None
    try:
        cdp = _new_cdp()
        tab_id = cdp.new_tab(args.url, timeout=args.timeout)
        text = cdp.get_text(tab_id) or ""
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            ok(f"已保存: {args.out} ({len(text)} chars)")
        else:
            print(text)
        return EXIT_OK
    except Exception as exc:
        fail(f"extract 失败: {exc}")
        return EXIT_FAIL
    finally:
        try:
            if tab_id:
                cdp.close_tab(tab_id)
        except Exception:
            pass
        if lock:
            lock.release()


def cmd_screenshot(args):
    lock = get_browser_lock()
    tab_id = None
    try:
        out = args.out or "screenshot.png"
        cdp = _new_cdp()
        tab_id = cdp.new_tab(args.url, timeout=args.timeout)
        cdp.screenshot(tab_id, out, fmt="png")
        ok(f"截图已保存: {out}")
        return EXIT_OK
    except Exception as exc:
        fail(f"screenshot 失败: {exc}")
        return EXIT_FAIL
    finally:
        try:
            if tab_id:
                cdp.close_tab(tab_id)
        except Exception:
            pass
        if lock:
            lock.release()


def cmd_frames(args):
    lock = get_browser_lock()
    tab_id = None
    try:
        out_dir = args.out or "./frames"
        os.makedirs(out_dir, exist_ok=True)
        cdp = _new_cdp()
        tab_id = cdp.new_tab(args.url, timeout=args.timeout)

        count = max(1, args.count)
        for i in range(count):
            out_file = os.path.join(out_dir, f"frame_{i + 1:04d}.png")
            cdp.screenshot(tab_id, out_file, fmt="png")
            time.sleep(max(0.1, args.interval))

        ok(f"提取了 {count} 帧到 {out_dir}")
        return EXIT_OK
    except Exception as exc:
        fail(f"frames 失败: {exc}")
        return EXIT_FAIL
    finally:
        try:
            if tab_id:
                cdp.close_tab(tab_id)
        except Exception:
            pass
        if lock:
            lock.release()


def main():
    parser = argparse.ArgumentParser(description="GA 浏览器 CDP 操作")
    parser.add_argument("--timeout", type=int, default=30)
    sub = parser.add_subparsers(dest="command")

    p_open = sub.add_parser("open")
    p_open.add_argument("url")

    p_extract = sub.add_parser("extract")
    p_extract.add_argument("url")
    p_extract.add_argument("--out", help="输出路径")

    p_ss = sub.add_parser("screenshot")
    p_ss.add_argument("url")
    p_ss.add_argument("--out", help="输出路径")

    p_frames = sub.add_parser("frames")
    p_frames.add_argument("url")
    p_frames.add_argument("--out", help="输出目录")
    p_frames.add_argument("--count", type=int, default=10, help="提取帧数")
    p_frames.add_argument("--interval", type=float, default=1.0, help="每帧间隔秒")

    args = parser.parse_args()

    dispatch = {
        "open": cmd_open,
        "extract": cmd_extract,
        "screenshot": cmd_screenshot,
        "frames": cmd_frames,
    }
    if args.command in dispatch:
        sys.exit(dispatch[args.command](args))

    parser.print_help()
    sys.exit(EXIT_SKIP)


if __name__ == "__main__":
    main()
