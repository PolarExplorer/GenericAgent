from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    from ._common import EXIT_FAIL, EXIT_OK, EXIT_SKIP, GA_ROOT, confirm_required, fail, info, ok, warn
except ImportError:
    from _common import EXIT_FAIL, EXIT_OK, EXIT_SKIP, GA_ROOT, confirm_required, fail, info, ok, warn  # type: ignore[no-redef]


SENSITIVE_EXACT = {
    ".env",
    ".env.local",
    ".env.production",
    "mykey.py",
}
SENSITIVE_PREFIXES = (
    "memory/",
)


class GAParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        fail(f"Argument error: {message}")
        raise ValueError(message)


def _run_git(args: list[str]) -> tuple[int, str, str]:
    cmd = ["git", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=GA_ROOT)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "git not found"


def _normalize_rel(path_text: str) -> str:
    return path_text.strip().replace("\\", "/").lstrip("./").lower()


def _is_sensitive_path(path_text: str) -> bool:
    rel = _normalize_rel(path_text)
    if not rel:
        return False
    if Path(rel).name in SENSITIVE_EXACT:
        return True
    return any(rel.startswith(prefix) for prefix in SENSITIVE_PREFIXES)


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _repo_root_ok() -> bool:
    rc, out, err = _run_git(["rev-parse", "--show-toplevel"])
    if rc != 0:
        fail(err.strip() or "Not a git repository")
        return False
    top = Path(out.strip()).resolve()
    if top != GA_ROOT.resolve():
        warn(f"Git root differs from GA_ROOT: {top}")
    return True


def _gather_sensitive_from_status(status_short: str) -> list[str]:
    sensitive: list[str] = []
    for line in status_short.splitlines():
        if len(line) < 4:
            continue
        path_text = line[3:]
        if _is_sensitive_path(path_text):
            sensitive.append(path_text)
    return sensitive


def _gather_staged_files() -> list[str] | None:
    rc, out, err = _run_git(["diff", "--cached", "--name-only"])
    if rc != 0:
        fail(err.strip() or "Failed to read staged files")
        return None
    return _split_lines(out)


def _has_unmerged_conflicts() -> bool:
    rc, out, err = _run_git(["diff", "--name-only", "--diff-filter=U"])
    if rc != 0:
        fail(err.strip() or "Failed to check conflicts")
        return True
    conflicted = _split_lines(out)
    if conflicted:
        fail("Unmerged conflicts exist:")
        for path in conflicted:
            print(f"  - {path}")
        return True
    return False


def cmd_status(_: argparse.Namespace) -> int:
    if not _repo_root_ok():
        return EXIT_FAIL

    rc, out, err = _run_git(["status", "--short"])
    if rc != 0:
        fail(err.strip() or "git status failed")
        return EXIT_FAIL

    status_short = out.strip()
    if status_short:
        print(status_short)
    else:
        info("Working tree clean.")

    sensitive = _gather_sensitive_from_status(out)
    if sensitive:
        warn("Sensitive files detected in git status:")
        for path in sensitive:
            print(f"  - {path}")
    return EXIT_OK


def cmd_stage(args: argparse.Namespace) -> int:
    if not _repo_root_ok():
        return EXIT_FAIL
    if not args.files:
        fail("No files provided for stage command.")
        return EXIT_FAIL

    resolved: list[str] = []
    for raw in args.files:
        p = Path(raw)
        abs_path = p if p.is_absolute() else (Path.cwd() / p)
        abs_path = abs_path.resolve()

        try:
            rel = abs_path.relative_to(GA_ROOT.resolve())
        except ValueError:
            fail(f"Path is outside repository: {raw}")
            return EXIT_FAIL

        rel_text = str(rel).replace("\\", "/")
        if _is_sensitive_path(rel_text):
            fail(f"Refusing to stage sensitive path: {rel_text}")
            return EXIT_FAIL

        if not abs_path.exists():
            fail(f"Path does not exist: {raw}")
            return EXIT_FAIL

        resolved.append(rel_text)

    rc, _, err = _run_git(["add", "--", *resolved])
    if rc != 0:
        fail(err.strip() or "git add failed")
        return EXIT_FAIL

    ok(f"Staged {len(resolved)} file(s).")
    return EXIT_OK


@confirm_required
def cmd_commit(args: argparse.Namespace, dry_run: bool = False) -> int:
    if not _repo_root_ok():
        return EXIT_FAIL
    if _has_unmerged_conflicts():
        return EXIT_FAIL

    staged = _gather_staged_files()
    if staged is None:
        return EXIT_FAIL
    if not staged:
        fail("No staged files to commit.")
        return EXIT_FAIL

    sensitive_staged = [path for path in staged if _is_sensitive_path(path)]
    if sensitive_staged:
        fail("Sensitive files are staged. Unstage them before commit:")
        for path in sensitive_staged:
            print(f"  - {path}")
        return EXIT_FAIL

    if dry_run:
        warn("Dry-run mode: commit not created.")
        info(f"Message: {args.message}")
        info("Staged files:")
        for path in staged:
            print(f"  - {path}")
        return EXIT_SKIP

    rc, out, err = _run_git(["commit", "-m", args.message])
    if rc != 0:
        fail(err.strip() or "git commit failed")
        return EXIT_FAIL

    if out.strip():
        print(out.rstrip())
    ok("Commit created.")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = GAParser(description="Safe git wrapper for GA workflows")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_status = subparsers.add_parser("status", help="Show git status --short and sensitive warnings")
    p_status.set_defaults(func=cmd_status)

    p_stage = subparsers.add_parser("stage", help="Stage files with sensitive-path checks")
    p_stage.add_argument("files", nargs="+", help="Files to stage")
    p_stage.set_defaults(func=cmd_stage)

    p_commit = subparsers.add_parser("commit", help="Commit staged files (dry-run unless --confirm)")
    p_commit.add_argument("message", help="Commit message")
    p_commit.add_argument("--confirm", action="store_true", help="Actually run git commit")
    p_commit.set_defaults(func=cmd_commit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
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
