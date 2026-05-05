from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from ._common import EXIT_FAIL, EXIT_OK, EXIT_SKIP, GA_ROOT, confirm_required, fail, info, ok, warn
except ImportError:
    from _common import EXIT_FAIL, EXIT_OK, EXIT_SKIP, GA_ROOT, confirm_required, fail, info, ok, warn  # type: ignore[no-redef]


RESTART_SCRIPT = GA_ROOT / "restart_ga.py"


class GAParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        fail(f"Argument error: {message}")
        raise ValueError(message)


def _run(cmd: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]}"


def _run_shell(command: str) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            errors="replace",
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "Shell is unavailable"


def _ast_str_keys(node: ast.AST) -> list[str]:
    if not isinstance(node, ast.Dict):
        return []
    keys: list[str] = []
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.append(key.value)
    return keys


def _ast_group_map(node: ast.AST) -> dict[str, list[str]]:
    if not isinstance(node, ast.Dict):
        return {}

    out: dict[str, list[str]] = {}
    for key_node, value_node in zip(node.keys, node.values):
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            continue
        if not isinstance(value_node, (ast.List, ast.Tuple)):
            continue
        vals: list[str] = []
        for elem in value_node.elts:
            if isinstance(elem, ast.Constant) and isinstance(elem.value, str):
                vals.append(elem.value)
        out[key_node.value] = vals
    return out


def _load_registry_import(path: Path) -> tuple[dict[str, Any], dict[str, list[str]]] | None:
    spec = importlib.util.spec_from_file_location("_ga_restart_registry", str(path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    components = getattr(module, "COMPONENTS", None)
    groups = getattr(module, "TARGET_GROUPS", None)
    if not isinstance(components, dict) or not isinstance(groups, dict):
        return None
    normalized_groups: dict[str, list[str]] = {}
    for name, targets in groups.items():
        if not isinstance(name, str):
            continue
        if isinstance(targets, (list, tuple)):
            normalized_groups[name] = [x for x in targets if isinstance(x, str)]
    return components, normalized_groups


def _load_registry_ast(path: Path) -> tuple[dict[str, Any], dict[str, list[str]]]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))

    component_names: list[str] = []
    groups: dict[str, list[str]] = {}

    for node in tree.body:
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "COMPONENTS" for t in node.targets):
                value = node.value
                component_names = _ast_str_keys(value)
            if any(isinstance(t, ast.Name) and t.id == "TARGET_GROUPS" for t in node.targets):
                value = node.value
                groups = _ast_group_map(value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "COMPONENTS" and node.value is not None:
                component_names = _ast_str_keys(node.value)
            if node.target.id == "TARGET_GROUPS" and node.value is not None:
                groups = _ast_group_map(node.value)

    components = {name: {} for name in component_names}
    return components, groups


def _load_registry(path: Path) -> tuple[dict[str, Any], dict[str, list[str]]]:
    try:
        imported = _load_registry_import(path)
        if imported is not None:
            return imported
    except Exception as exc:
        warn(f"Import parse failed, fallback to AST: {exc}")

    try:
        return _load_registry_ast(path)
    except Exception as exc:
        fail(f"Unable to parse {path.name}: {exc}")
        return {}, {}


COMPONENTS, TARGET_GROUPS = _load_registry(RESTART_SCRIPT)


def _resolve_targets(target: str) -> list[str] | None:
    if target in TARGET_GROUPS:
        return list(TARGET_GROUPS[target])
    if target in COMPONENTS:
        return [target]
    return None


def _target_arg_invalid(target: str) -> int:
    fail(f"Unknown service target: {target}")
    info("Use 'list' to see available services and groups.")
    return EXIT_FAIL


def _read_int(path: str | None) -> int | None:
    if not path:
        return None
    file = Path(path)
    if not file.exists():
        return None
    try:
        return int(file.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    rc, out, _ = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
    if rc != 0:
        return False
    return str(pid) in out and "No tasks are running" not in out


def _listening_ports() -> set[int]:
    rc, out, err = _run(["netstat", "-ano"])
    if rc != 0:
        warn(err.strip() or "netstat failed")
        return set()
    ports: set[int] = set()
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        match = re.search(r":(\d+)\s+.*LISTENING", line)
        if match:
            ports.add(int(match.group(1)))
    return ports


def _wmic_blob() -> str:
    rc, out, _ = _run_shell('wmic process where "Name like \'python%\'" get CommandLine /format:list')
    if rc != 0:
        return ""
    return out.lower()


def _component_status(name: str, cfg: dict[str, Any], listening: set[int], wmic_text: str) -> tuple[str, str]:
    signals: list[bool] = []
    notes: list[str] = []

    lock_pid = _read_int(cfg.get("lock_file"))
    if lock_pid is not None:
        alive = _pid_alive(lock_pid)
        signals.append(alive)
        notes.append(f"lock_pid={lock_pid} ({'alive' if alive else 'dead'})")

    keywords = [str(x).lower() for x in cfg.get("match_keywords", []) if isinstance(x, str)]
    if keywords and wmic_text:
        matched = any(k in wmic_text for k in keywords)
        signals.append(matched)
        notes.append(f"keyword_match={matched}")

    # ports are only valid runtime signals when the component owns them
    # exclusively. Shared/reference ports are reported as notes only and must
    # not make a dead bot look RUNNING.
    ports = [int(p) for p in cfg.get("ports", []) if isinstance(p, int)]
    if ports:
        open_ports = [p for p in ports if p in listening]
        all_open = len(open_ports) == len(ports)
        signals.append(all_open)
        notes.append(f"ports={ports}, open={open_ports}")

    shared_ports = [int(p) for p in cfg.get("shared_ports", []) if isinstance(p, int)]
    if shared_ports:
        open_shared = [p for p in shared_ports if p in listening]
        notes.append(f"shared_ports={shared_ports}, open={open_shared}")

    if not signals:
        return "UNKNOWN", "no runtime probes available" if not notes else "; ".join(notes)
    if any(signals):
        return "RUNNING", "; ".join(notes)
    return "STOPPED", "; ".join(notes)


def cmd_list(_: argparse.Namespace) -> int:
    if not COMPONENTS:
        fail(f"Cannot load components from {RESTART_SCRIPT}")
        return EXIT_FAIL

    info("Components:")
    for name in COMPONENTS.keys():
        print(f"  - {name}")

    info("Target groups:")
    if not TARGET_GROUPS:
        print("  (none)")
        return EXIT_OK
    for group, members in TARGET_GROUPS.items():
        print(f"  - {group}: {', '.join(members)}")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    targets = _resolve_targets(args.target)
    if targets is None:
        return _target_arg_invalid(args.target)

    listening = _listening_ports()
    wmic_text = _wmic_blob()
    for name in targets:
        cfg_obj = COMPONENTS.get(name, {})
        cfg = cfg_obj if isinstance(cfg_obj, dict) else {}
        state, detail = _component_status(name, cfg, listening, wmic_text)
        print(f"{name:<10} {state:<8} {detail}")
    return EXIT_OK


def _guard_core(targets: list[str], allow_core: bool) -> bool:
    if "ga" in targets and not allow_core:
        fail("Refusing operation on core service 'ga'. Re-run with --allow-core.")
        return False
    return True


def _dispatch_restart(target: str, dry_run: bool, kill_only: bool) -> int:
    cmd = [sys.executable, str(RESTART_SCRIPT), target]
    if kill_only:
        cmd.append("--kill-only")

    if dry_run:
        info(f"Dry-run: would execute -> {' '.join(cmd)}")
        return EXIT_SKIP

    rc, out, err = _run(cmd)

    def _safe_print_block(text: str) -> None:
        if not text.strip():
            return
        try:
            print(text.rstrip())
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "utf-8"
            cleaned = text.encode(enc, errors="replace").decode(enc, errors="replace")
            print(cleaned.rstrip())

    _safe_print_block(out)
    if err.strip():
        _safe_print_block(f"[WARN] restart_ga stderr:\n{err}")
    if rc == 0:
        ok("Command completed successfully.")
        return EXIT_OK
    fail(f"Command failed with exit code {rc}.")
    return EXIT_FAIL


@confirm_required
def cmd_restart(args: argparse.Namespace, dry_run: bool = False) -> int:
    targets = _resolve_targets(args.target)
    if targets is None:
        return _target_arg_invalid(args.target)
    if not _guard_core(targets, args.allow_core):
        return EXIT_FAIL
    return _dispatch_restart(args.target, dry_run=dry_run, kill_only=False)


@confirm_required
def cmd_stop(args: argparse.Namespace, dry_run: bool = False) -> int:
    targets = _resolve_targets(args.target)
    if targets is None:
        return _target_arg_invalid(args.target)
    if not _guard_core(targets, args.allow_core):
        return EXIT_FAIL
    return _dispatch_restart(args.target, dry_run=dry_run, kill_only=True)


def build_parser() -> argparse.ArgumentParser:
    parser = GAParser(description="GA service manager wrapper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list", help="List available services and groups")
    p_list.set_defaults(func=cmd_list)

    p_status = subparsers.add_parser("status", help="Show service status")
    p_status.add_argument("target", help="Service or group target (name|all|full)")
    p_status.set_defaults(func=cmd_status)

    p_restart = subparsers.add_parser("restart", help="Restart service(s) via restart_ga.py")
    p_restart.add_argument("target", help="Service or group target (name|all|full)")
    p_restart.add_argument("--confirm", action="store_true", help="Actually execute")
    p_restart.add_argument("--allow-core", action="store_true", help="Allow operations on 'ga'")
    p_restart.set_defaults(func=cmd_restart)

    p_stop = subparsers.add_parser("stop", help="Stop service(s) via restart_ga.py --kill-only")
    p_stop.add_argument("target", help="Service or group target (name|all|full)")
    p_stop.add_argument("--confirm", action="store_true", help="Actually execute")
    p_stop.add_argument("--allow-core", action="store_true", help="Allow operations on 'ga'")
    p_stop.set_defaults(func=cmd_stop)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if not RESTART_SCRIPT.exists():
        fail(f"Missing required script: {RESTART_SCRIPT}")
        return EXIT_FAIL
    try:
        args = parser.parse_args(argv)
        return int(args.func(args))
    except ValueError:
        return EXIT_FAIL
    except KeyboardInterrupt:
        warn("Interrupted by user.")
        return EXIT_SKIP


if __name__ == "__main__":
    raise SystemExit(main())
