from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
import sys
from typing import Any

try:
    from ._common import (
        EXIT_FAIL,
        EXIT_OK,
        EXIT_SKIP,
        PROTECTED_PORTS,
        confirm_required,
        fail,
        info,
        ok,
        warn,
    )
except ImportError:
    from _common import (  # type: ignore[no-redef]
        EXIT_FAIL,
        EXIT_OK,
        EXIT_SKIP,
        PROTECTED_PORTS,
        confirm_required,
        fail,
        info,
        ok,
        warn,
    )


class GAParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        fail(f"Argument error: {message}")
        raise ValueError(message)


def _run(cmd: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]}"


def _extract_port(endpoint: str) -> int | None:
    endpoint = endpoint.strip()
    if endpoint.startswith("["):
        match = re.search(r"\]:(\d+)$", endpoint)
    else:
        match = re.search(r":(\d+)$", endpoint)
    if not match:
        return None
    return int(match.group(1))


def _tasklist_name_map() -> dict[int, str]:
    rc, out, _ = _run(["tasklist", "/FO", "CSV", "/NH"])
    if rc != 0:
        return {}
    mapping: dict[int, str] = {}
    reader = csv.reader(io.StringIO(out))
    for row in reader:
        if len(row) < 2:
            continue
        pid_text = row[1].strip()
        if pid_text.isdigit():
            mapping[int(pid_text)] = row[0].strip()
    return mapping


def _listeners() -> list[dict[str, Any]]:
    rc, out, err = _run(["netstat", "-ano"])
    if rc != 0:
        raise RuntimeError(err.strip() or "netstat failed")

    entries: list[dict[str, Any]] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("TCP"):
            cols = re.split(r"\s+", line)
            if len(cols) < 5:
                continue
            local = cols[1]
            state = cols[3].upper()
            pid_text = cols[4]
            if state != "LISTENING":
                continue
        elif line.startswith("UDP"):
            cols = re.split(r"\s+", line)
            if len(cols) < 4:
                continue
            local = cols[1]
            pid_text = cols[-1]
            state = "LISTENING"
        else:
            continue

        if not pid_text.isdigit():
            continue
        port = _extract_port(local)
        if port is None:
            continue
        entries.append(
            {
                "proto": cols[0].upper(),
                "local": local,
                "state": state,
                "port": port,
                "pid": int(pid_text),
            }
        )

    name_map = _tasklist_name_map()
    for item in entries:
        item["name"] = name_map.get(item["pid"], "<unknown>")

    entries.sort(key=lambda x: (x["port"], x["pid"], x["proto"]))
    return entries


def cmd_list(args: argparse.Namespace) -> int:
    entries = _listeners()
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return EXIT_OK

    if not entries:
        info("No listening ports found.")
        return EXIT_OK

    print(f"{'PORT':<7}{'PID':<8}{'NAME':<30}{'PROTO':<7}LOCAL")
    for item in entries:
        print(
            f"{item['port']:<7}{item['pid']:<8}{item['name']:<30}"
            f"{item['proto']:<7}{item['local']}"
        )
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    entries = [item for item in _listeners() if item["port"] == args.port]
    if not entries:
        warn(f"Port {args.port} is not occupied in LISTENING state.")
        return EXIT_SKIP

    print(f"Port {args.port} is occupied:")
    print(f"{'PID':<8}{'NAME':<30}{'PROTO':<7}LOCAL")
    for item in entries:
        print(f"{item['pid']:<8}{item['name']:<30}{item['proto']:<7}{item['local']}")
    return EXIT_OK


@confirm_required
def cmd_kill(args: argparse.Namespace, dry_run: bool = False) -> int:
    port = args.port
    if port in PROTECTED_PORTS:
        fail(f"Port {port} is protected and cannot be killed.")
        return EXIT_FAIL

    entries = [item for item in _listeners() if item["port"] == port]
    if not entries:
        warn(f"No listener found on port {port}.")
        return EXIT_SKIP

    pids = sorted({item["pid"] for item in entries})
    if dry_run:
        info(f"Would kill PID(s) on port {port}: {', '.join(str(pid) for pid in pids)}")
        return EXIT_SKIP

    failed = False
    for pid in pids:
        rc, _, err = _run(["taskkill", "/PID", str(pid), "/F"])
        if rc == 0:
            ok(f"Killed PID {pid} on port {port}.")
        else:
            failed = True
            fail(f"Failed to kill PID {pid}: {err.strip() or 'taskkill failed'}")

    return EXIT_FAIL if failed else EXIT_OK


def _wmic_process_rows() -> list[dict[str, Any]] | None:
    rc, out, err = _run(["wmic", "process", "get", "ProcessId,ParentProcessId,CommandLine", "/FORMAT:CSV"])
    if rc != 0:
        if "not recognized" in err.lower() or "not found" in err.lower() or rc == 127:
            return None
        if not out.strip():
            return None

    rows: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(out))
    for row in reader:
        if not row:
            continue
        if "ProcessId" in row and "ParentProcessId" in row:
            continue
        if len(row) < 4:
            continue
        pid_text = row[-1].strip()
        ppid_text = row[-2].strip()
        if not pid_text.isdigit() or not ppid_text.isdigit():
            continue
        cmd = ",".join(part.strip() for part in row[1:-2]).strip()
        if not cmd:
            cmd = "<empty>"
        rows.append({"pid": int(pid_text), "ppid": int(ppid_text), "cmd": cmd})
    return rows


def _tasklist_fallback(keyword: str) -> int:
    warn("wmic is unavailable. Falling back to tasklist (process name only).")
    rc, out, err = _run(["tasklist", "/FO", "CSV", "/NH"])
    if rc != 0:
        warn(err.strip() or "tasklist unavailable in current session")
        info("Cannot read process command line without wmic. Try running terminal as Administrator.")
        return EXIT_SKIP

    matches: list[tuple[str, str]] = []
    reader = csv.reader(io.StringIO(out))
    for row in reader:
        if len(row) < 2:
            continue
        name = row[0].strip()
        pid = row[1].strip()
        if keyword.lower() in name.lower():
            matches.append((pid, name))

    if not matches:
        info(f"No tasklist match for keyword: {keyword}")
        return EXIT_SKIP

    print(f"{'PID':<8}{'PPID':<8}CMD")
    for pid, name in matches:
        print(f"{pid:<8}{'-':<8}{name}")
    return EXIT_OK


def cmd_tree(args: argparse.Namespace) -> int:
    keyword = args.keyword.strip().lower()
    rows = _wmic_process_rows()
    if rows is None:
        return _tasklist_fallback(args.keyword)

    matches = [row for row in rows if keyword in row["cmd"].lower()]
    if not matches:
        info(f"No process command line match for keyword: {args.keyword}")
        return EXIT_SKIP

    print(f"{'PID':<8}{'PPID':<8}CMD")
    for row in matches:
        cmd = row["cmd"]
        if len(cmd) > 140:
            cmd = f"{cmd[:137]}..."
        print(f"{row['pid']:<8}{row['ppid']:<8}{cmd}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = GAParser(description="GA port helper for Windows")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list", help="List listening ports")
    p_list.add_argument("--json", action="store_true", help="Output JSON")
    p_list.set_defaults(func=cmd_list)

    p_check = subparsers.add_parser("check", help="Check one port")
    p_check.add_argument("port", type=int, help="Port number")
    p_check.set_defaults(func=cmd_check)

    p_kill = subparsers.add_parser("kill", help="Kill process on one port")
    p_kill.add_argument("port", type=int, help="Port number")
    p_kill.add_argument("--confirm", action="store_true", help="Actually kill process")
    p_kill.set_defaults(func=cmd_kill)

    p_tree = subparsers.add_parser("tree", help="Filter process tree by keyword")
    p_tree.add_argument("keyword", help="Keyword in process command line")
    p_tree.set_defaults(func=cmd_tree)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.func(args))
    except ValueError:
        return EXIT_FAIL
    except RuntimeError as exc:
        fail(str(exc))
        return EXIT_FAIL
    except KeyboardInterrupt:
        warn("Interrupted by user.")
        return EXIT_SKIP


if __name__ == "__main__":
    raise SystemExit(main())
