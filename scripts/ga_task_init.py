from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from ._common import EXIT_FAIL, EXIT_OK, EXIT_SKIP, GA_ROOT, confirm_required, fail, info, ok, warn
except ImportError:
    from _common import EXIT_FAIL, EXIT_OK, EXIT_SKIP, GA_ROOT, confirm_required, fail, info, ok, warn  # type: ignore[no-redef]


TEMPLATES = ("default", "research", "experiment")
TASK_DIR_PATTERN = re.compile(r"^task_(\d{8})_(.+)$")


class GAParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        fail(f"Argument error: {message}")
        raise ValueError(message)


def _sanitize_name(name: str) -> str:
    text = re.sub(r"\s+", "_", name.strip())
    text = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return "task"
    return text


def _task_md_content(name: str, template: str) -> str:
    if template == "research":
        body = (
            "# Research Task\n\n"
            f"- Name: {name}\n"
            f"- Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "## Goal\n"
            "-\n\n"
            "## Questions\n"
            "1. \n"
            "2. \n"
            "3. \n\n"
            "## Sources\n"
            "-\n\n"
            "## Findings\n"
            "-\n\n"
            "## Next Steps\n"
            "-\n"
        )
        return body

    if template == "experiment":
        body = (
            "# Experiment Task\n\n"
            f"- Name: {name}\n"
            f"- Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "## Hypothesis\n"
            "-\n\n"
            "## Setup\n"
            "-\n\n"
            "## Variables\n"
            "- Independent:\n"
            "- Dependent:\n"
            "- Controlled:\n\n"
            "## Execution Log\n"
            "-\n\n"
            "## Result\n"
            "-\n\n"
            "## Conclusion\n"
            "-\n"
        )
        return body

    body = (
        "# Task\n\n"
        f"- Name: {name}\n"
        f"- Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "## Objective\n"
        "-\n\n"
        "## Plan\n"
        "1. \n"
        "2. \n"
        "3. \n\n"
        "## Progress\n"
        "-\n\n"
        "## Notes\n"
        "-\n\n"
        "## Acceptance Criteria\n"
        "-\n"
    )
    return body


def _next_task_dir(temp_dir: Path, name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d")
    base = f"task_{stamp}_{_sanitize_name(name)}"
    candidate = temp_dir / base
    if not candidate.exists():
        return candidate

    index = 2
    while True:
        candidate = temp_dir / f"{base}_{index}"
        if not candidate.exists():
            return candidate
        index += 1


def _is_within(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _task_age_days(task_date: date) -> int:
    return (date.today() - task_date).days


def _parse_task_date(dir_name: str) -> date | None:
    match = TASK_DIR_PATTERN.match(dir_name)
    if not match:
        return None
    date_text = match.group(1)
    try:
        return datetime.strptime(date_text, "%Y%m%d").date()
    except ValueError:
        return None


def _collect_old_tasks(temp_dir: Path, older_than_days: int) -> list[tuple[Path, int]]:
    cutoff = date.today() - timedelta(days=older_than_days)
    tasks: list[tuple[Path, int]] = []
    for path in temp_dir.iterdir():
        if not path.is_dir():
            continue
        parsed = _parse_task_date(path.name)
        if parsed is None:
            continue
        if parsed > cutoff:
            continue
        if not (path / "task.md").is_file():
            continue
        if not (path / "logs").is_dir():
            continue
        if not (path / "output").is_dir():
            continue
        tasks.append((path, _task_age_days(parsed)))
    tasks.sort(key=lambda item: item[0].name)
    return tasks


def cmd_init(args: argparse.Namespace) -> int:
    temp_dir = GA_ROOT / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    task_dir = _next_task_dir(temp_dir, args.name)
    logs_dir = task_dir / "logs"
    output_dir = task_dir / "output"
    task_md = task_dir / "task.md"

    try:
        logs_dir.mkdir(parents=True, exist_ok=False)
        output_dir.mkdir(parents=True, exist_ok=False)
        task_md.write_text(_task_md_content(args.name, args.template), encoding="utf-8")
    except Exception as exc:
        fail(f"Failed to initialize task directory: {exc}")
        return EXIT_FAIL

    ok(f"Task initialized: {task_dir}")
    info(f"Template: {args.template}")
    return EXIT_OK


@confirm_required
def cmd_clean(args: argparse.Namespace, dry_run: bool = False) -> int:
    temp_dir = GA_ROOT / "temp"
    if not temp_dir.exists():
        warn(f"Temp directory does not exist: {temp_dir}")
        return EXIT_SKIP

    tasks = _collect_old_tasks(temp_dir, args.older_than)
    if not tasks:
        info("No old task directories to clean.")
        return EXIT_SKIP

    if dry_run:
        warn("Dry-run mode. The following directories would be removed:")
        for path, age in tasks:
            print(f"  - {path.name} ({age} day(s) old)")
        return EXIT_SKIP

    failed = False
    removed = 0
    for path, age in tasks:
        if not _is_within(temp_dir, path):
            fail(f"Refusing to remove path outside temp: {path}")
            failed = True
            continue
        try:
            shutil.rmtree(path)
            ok(f"Removed: {path.name} ({age} day(s) old)")
            removed += 1
        except Exception as exc:
            fail(f"Failed to remove {path.name}: {exc}")
            failed = True

    info(f"Removed {removed} task directories.")
    return EXIT_FAIL if failed else EXIT_OK


def _non_negative_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {raw}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = GAParser(description="Task workspace initializer and cleaner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init", help="Initialize a task directory under temp")
    p_init.add_argument("name", help="Task name")
    p_init.add_argument("--template", choices=TEMPLATES, default="default", help="task.md template")
    p_init.set_defaults(func=cmd_init)

    p_clean = subparsers.add_parser("clean", help="Clean old task directories under temp")
    p_clean.add_argument("--older-than", type=_non_negative_int, default=7, help="Delete tasks older than N days")
    p_clean.add_argument("--confirm", action="store_true", help="Actually delete directories")
    p_clean.set_defaults(func=cmd_clean)

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
