from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from ._common import EXIT_FAIL, EXIT_OK, EXIT_SKIP, GA_ROOT, fail, info, warn
except ImportError:
    from _common import EXIT_FAIL, EXIT_OK, EXIT_SKIP, GA_ROOT, fail, info, warn  # type: ignore[no-redef]


LEVELS = ("L1", "L2", "L3", "all")
TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml", ".ini"}
SKIP_DIR_NAMES = {".git", "__pycache__", "L4_raw_sessions"}


class GAParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        fail(f"Argument error: {message}")
        raise ValueError(message)


def _line_preview(line: str, limit: int = 180) -> str:
    normalized = re.sub(r"\s+", " ", line.strip())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


def _safe_console_text(text: str) -> str:
    enc = sys.stdout.encoding or "utf-8"
    return text.encode(enc, errors="replace").decode(enc, errors="replace")


def _print_console(text: str) -> None:
    print(_safe_console_text(text))


def _is_text_candidate(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name == ".gitignore"


def _l1_files(memory_dir: Path) -> list[Path]:
    files = [
        memory_dir / "global_mem_insight.txt",
    ]
    return [p for p in files if p.is_file()]


def _l2_files(memory_dir: Path) -> list[Path]:
    files = [
        memory_dir / "global_mem.txt",
    ]
    return [p for p in files if p.is_file()]


def _l3_files(memory_dir: Path) -> list[Path]:
    exclude = {memory_dir / "global_mem_insight.txt", memory_dir / "global_mem.txt"}
    out: list[Path] = []
    for path in memory_dir.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = set(part.lower() for part in path.relative_to(memory_dir).parts[:-1])
        if rel_parts & {name.lower() for name in SKIP_DIR_NAMES}:
            continue
        if path in exclude:
            continue
        if _is_text_candidate(path):
            out.append(path)
    return out


def _issue_files(memory_dir: Path) -> list[Path]:
    issues_dir = memory_dir / "issues"
    if not issues_dir.exists():
        return []
    return sorted(p for p in issues_dir.glob("*.md") if p.is_file())


def _target_files(memory_dir: Path, level: str, issues_only: bool) -> list[Path]:
    if issues_only:
        return _issue_files(memory_dir)

    if level == "L1":
        return _l1_files(memory_dir)
    if level == "L2":
        return _l2_files(memory_dir)
    if level == "L3":
        return _l3_files(memory_dir)

    merged = _l1_files(memory_dir) + _l2_files(memory_dir) + _l3_files(memory_dir)
    unique: dict[Path, None] = {}
    for p in merged:
        unique[p] = None
    return sorted(unique.keys())


def _search_file(path: Path, keyword: str) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    needle = keyword.casefold()
    matches: list[tuple[int, str]] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if needle in line.casefold():
            matches.append((idx, _line_preview(line)))
    return matches


def cmd_find(args: argparse.Namespace) -> int:
    memory_dir = GA_ROOT / "memory"
    if not memory_dir.exists():
        fail(f"Missing memory directory: {memory_dir}")
        return EXIT_FAIL

    files = _target_files(memory_dir, args.level, args.issues)
    if not files:
        warn("No target files found for the selected scope.")
        return EXIT_SKIP

    info(f"Searching {len(files)} file(s)...")
    total_hits = 0
    for path in files:
        try:
            matches = _search_file(path, args.query)
        except OSError as exc:
            warn(f"Skip unreadable file: {path} ({exc})")
            continue
        if not matches:
            continue

        rel = path.relative_to(GA_ROOT).as_posix()
        _print_console(rel)
        for line_no, preview in matches:
            _print_console(f"  {line_no}: {preview}")
        total_hits += len(matches)

    if total_hits == 0:
        warn(f"No match for keyword: {args.query}")
        return EXIT_SKIP

    info(f"Matched {total_hits} line(s).")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = GAParser(description="Search GA memory files by keyword")
    parser.add_argument("query", help="Keyword to search")
    parser.add_argument("--level", choices=LEVELS, default="all", help="Memory level scope")
    parser.add_argument("--issues", action="store_true", help="Search only memory/issues/*.md")
    parser.set_defaults(func=cmd_find)
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
