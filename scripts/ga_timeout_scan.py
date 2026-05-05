"""GA Timeout/Retry 分布速查脚本（只读）

扫描 fsapp / GA 主侧的 stdout / daemon 日志，统计 P0 阶段加入的 4 类
结构化 tag 的频次和最近发生时间，用于事后定位飞书任务超时的真实归因。

Usage:
    python scripts/ga_timeout_scan.py                # 默认扫过去 7 天
    python scripts/ga_timeout_scan.py --days 1       # 只看过去 1 天
    python scripts/ga_timeout_scan.py --days 30 -v   # 30 天详细
    python scripts/ga_timeout_scan.py --tail 10      # 每类 tag 显示最近 10 条原文

仅读文件，不修改任何状态、不联网、不重启任何进程。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ---------- 路径解析 ----------
HERE = Path(__file__).resolve().parent
GA_ROOT = HERE.parent  # D:\AI\GenericAgent
LOG_DIRS = [GA_ROOT / "logs", GA_ROOT / "temp"]

# 候选日志文件名（glob 模式），覆盖 fsapp/wxapp/GA 主侧
LOG_GLOBS = [
    "fsapp_stdout.log",
    "fsapp_stderr.log",
    "fsapp_daemon.log",
    "wxapp_stdout.log",
    "wxapp_stderr.log",
    "wxapp_daemon.log",
    "agent_stdout.log",
    "agent_stderr.log",
]

# ---------- P0 引入的结构化 tag ----------
TAGS = [
    "FSAPP_TIMEOUT_900s",
    "REPLY_WAIT_TIMEOUT_600s",
    "NET_RETRY_HTTP",
    "NET_RETRY_EXC",
]
# 顺带统计的旧信号（无结构化 tag，用作对照）
LEGACY_SIGNALS = [
    "User aborted the task.",
    "[LLM Retry]",
    "HTTPSConnectionPool",
    "timed out",
]

TS_RE = re.compile(r"(20\d{2}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


def collect_log_files() -> list[Path]:
    files: list[Path] = []
    for d in LOG_DIRS:
        if not d.exists():
            continue
        for name in LOG_GLOBS:
            p = d / name
            if p.exists() and p.is_file():
                files.append(p)
    return files


def parse_recent_ts(line: str) -> datetime | None:
    m = TS_RE.search(line)
    if not m:
        return None
    s = m.group(1).replace("T", " ")
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def scan_file(path: Path, since: datetime | None) -> dict:
    """返回 {tag: [(ts_or_None, line), ...]}，已按 since 过滤。"""
    out: dict[str, list[tuple[datetime | None, str]]] = defaultdict(list)
    try:
        # 用二进制读避免编码异常，统一 errors=replace
        raw = path.read_bytes()
    except OSError as e:
        print(f"[WARN] read fail {path}: {e}", file=sys.stderr)
        return out
    text = raw.decode("utf-8", errors="replace")
    keys = TAGS + LEGACY_SIGNALS
    last_ts: datetime | None = None
    for line in text.splitlines():
        ts = parse_recent_ts(line)
        if ts is not None:
            last_ts = ts
        eff_ts = ts or last_ts
        if since is not None and eff_ts is not None and eff_ts < since:
            continue
        for k in keys:
            if k in line:
                out[k].append((eff_ts, line.rstrip()))
    return out


def fmt_ts(ts: datetime | None) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "??"


def main() -> int:
    ap = argparse.ArgumentParser(description="GA timeout/retry 分布速查（只读）")
    ap.add_argument("--days", type=int, default=7, help="只统计过去 N 天，默认 7")
    ap.add_argument("--tail", type=int, default=3, help="每类 tag 打印最近 N 条原文，默认 3")
    ap.add_argument("-v", "--verbose", action="store_true", help="按文件分别打印明细")
    args = ap.parse_args()

    since = None
    if args.days > 0:
        since = datetime.now() - timedelta(days=args.days)

    files = collect_log_files()
    if not files:
        print(f"[ERR] no log files found under {[str(d) for d in LOG_DIRS]}")
        return 2

    print(f"=== GA Timeout Scan ===")
    print(f"window : last {args.days} day(s) (since {fmt_ts(since) if since else 'beginning'})")
    print(f"files  : {len(files)}")
    for f in files:
        size_kb = f.stat().st_size / 1024
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        print(f"  - {f.relative_to(GA_ROOT)}  {size_kb:8.1f} KB  mtime={fmt_ts(mtime)}")
    print()

    # 汇总
    total: Counter[str] = Counter()
    by_file: dict[str, Counter[str]] = {}
    samples: dict[str, list[tuple[datetime | None, str, str]]] = defaultdict(list)
    for f in files:
        res = scan_file(f, since)
        c: Counter[str] = Counter()
        for k, lst in res.items():
            c[k] = len(lst)
            total[k] += len(lst)
            for ts, line in lst:
                samples[k].append((ts, str(f.name), line))
        by_file[str(f.relative_to(GA_ROOT))] = c

    # 主表（结构化 tag 在前，旧信号在后）
    print("--- Tag counts (structured P0 tags) ---")
    for k in TAGS:
        print(f"  {k:30s} : {total[k]}")
    print("--- Legacy signals (for reference) ---")
    for k in LEGACY_SIGNALS:
        print(f"  {k:30s} : {total[k]}")
    print()

    if args.verbose:
        print("--- Per-file breakdown ---")
        for fp, c in by_file.items():
            if not any(c.values()):
                continue
            print(f"  [{fp}]")
            for k in TAGS + LEGACY_SIGNALS:
                if c[k]:
                    print(f"      {k:30s} : {c[k]}")
        print()

    # 每类 tag 的最近 N 条
    if args.tail > 0:
        print(f"--- Latest {args.tail} sample(s) per tag ---")
        for k in TAGS:
            entries = samples.get(k, [])
            if not entries:
                print(f"  [{k}] (none)")
                continue
            entries.sort(key=lambda x: (x[0] or datetime.min), reverse=True)
            print(f"  [{k}] total={len(entries)}")
            for ts, fname, line in entries[: args.tail]:
                short = line if len(line) <= 240 else line[:240] + "..."
                print(f"    {fmt_ts(ts)}  ({fname})  {short}")
        print()

    # 一句话归因提示（纯 ASCII 标记，避免 GBK 终端崩）
    print("--- Hint ---")
    if total["FSAPP_TIMEOUT_900s"]:
        print("  [WARN] fsapp 15min 硬上限被触发，考虑 P3 异步化或评估上限合理性")
    if total["REPLY_WAIT_TIMEOUT_600s"]:
        print("  [WARN] agentmain 等 reply.txt 600s 超时，疑似 GA 主侧死循环或卡死")
    if total["NET_RETRY_HTTP"] + total["NET_RETRY_EXC"] >= 10:
        print("  [WARN] LLM 网络层重试频繁，可考虑 P1（统一收紧 timeout）")
    if not any(total[k] for k in TAGS):
        print("  [OK] 结构化 tag 暂无命中（可能 bot 尚未重启加载新代码，或窗口期内确实无超时）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())