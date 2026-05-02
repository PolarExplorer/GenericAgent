from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

try:
    from ._common import EXIT_FAIL, EXIT_OK, EXIT_SKIP, GA_ROOT, fail, info, ok, warn
except ImportError:
    from _common import EXIT_FAIL, EXIT_OK, EXIT_SKIP, GA_ROOT, fail, info, ok, warn  # type: ignore[no-redef]


TARGETS = ("ga", "audit", "feishu", "wechat", "all")

SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "mykey.py",
}
SENSITIVE_DIRS = {
    ".git",
    "memory",
    "__pycache__",
}


def _lower_parts(path: Path) -> tuple[str, ...]:
    return tuple(part.lower() for part in path.parts)


def _is_sensitive(rel_path: Path) -> bool:
    parts = _lower_parts(rel_path)
    if any(part in SENSITIVE_DIRS for part in parts):
        return True
    if rel_path.name.lower() in SENSITIVE_NAMES:
        return True
    if rel_path.suffix.lower() in {".pyc", ".pyo"}:
        return True
    return False


def _collect_ga_files() -> set[Path]:
    files: set[Path] = set()
    for rel in ("restart_ga.py", "ga.py"):
        p = GA_ROOT / rel
        if p.is_file():
            files.add(p)
    scripts_dir = GA_ROOT / "scripts"
    if scripts_dir.exists():
        files.update(p for p in scripts_dir.glob("*.py") if p.is_file())
    return files


def _collect_audit_files() -> set[Path]:
    files: set[Path] = set()
    for rel_dir in ("temp/dashboard", "temp/test_audit"):
        base = GA_ROOT / rel_dir
        if base.exists():
            files.update(p for p in base.rglob("*") if p.is_file())
    files.update(p for p in GA_ROOT.rglob("*audit*.log") if p.is_file())
    return files


def _collect_temp_keyword_files(keywords: tuple[str, ...]) -> set[Path]:
    temp_dir = GA_ROOT / "temp"
    if not temp_dir.exists():
        return set()

    files: set[Path] = set()
    lower_keywords = tuple(k.lower() for k in keywords)
    for p in temp_dir.rglob("*"):
        if not p.is_file():
            continue
        rel_text = str(p.relative_to(GA_ROOT)).replace("\\", "/").lower()
        if any(k in rel_text for k in lower_keywords):
            files.add(p)
    return files


def _collect_target_files(target: str) -> set[Path]:
    target = target.lower()
    if target == "ga":
        return _collect_ga_files()
    if target == "audit":
        return _collect_audit_files()
    if target == "feishu":
        return _collect_temp_keyword_files(("feishu", "fsapp"))
    if target == "wechat":
        return _collect_temp_keyword_files(("wechat", "wxapp"))
    if target == "all":
        return (
            _collect_ga_files()
            | _collect_audit_files()
            | _collect_temp_keyword_files(("feishu", "fsapp"))
            | _collect_temp_keyword_files(("wechat", "wxapp"))
        )
    raise ValueError(f"Unsupported target: {target}")


def _default_out_path(target: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return GA_ROOT / "temp" / f"ga_debug_{target}_{ts}.zip"


def _resolve_out_path(out_arg: str | None, target: str) -> Path:
    if not out_arg:
        return _default_out_path(target)

    out_path = Path(out_arg)
    if not out_path.suffix:
        out_path = out_path.with_suffix(".zip")
    if not out_path.is_absolute():
        out_path = (Path.cwd() / out_path).resolve()
    return out_path


def _zip_files(out_zip: Path, candidates: set[Path]) -> tuple[int, int]:
    added = 0
    skipped_sensitive = 0
    out_zip.parent.mkdir(parents=True, exist_ok=True)

    with ZipFile(out_zip, mode="w", compression=ZIP_DEFLATED) as zf:
        for file_path in sorted(candidates):
            if not file_path.exists() or not file_path.is_file():
                continue
            rel = file_path.resolve().relative_to(GA_ROOT.resolve())
            if _is_sensitive(rel):
                skipped_sensitive += 1
                continue
            zf.write(file_path, arcname=str(rel).replace("\\", "/"))
            added += 1
    return added, skipped_sensitive


class GAParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        fail(f"Argument error: {message}")
        raise ValueError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = GAParser(description="Collect GA debug materials into zip")
    parser.add_argument("--target", required=True, choices=TARGETS, help="Target scope")
    parser.add_argument("--out", help="Output zip path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except ValueError:
        return EXIT_FAIL

    try:
        files = _collect_target_files(args.target)
    except ValueError as exc:
        fail(str(exc))
        return EXIT_FAIL

    if not files:
        fail(f"No files matched target '{args.target}'.")
        return EXIT_FAIL

    out_path = _resolve_out_path(args.out, args.target)
    if out_path.exists():
        fail(f"Output already exists: {out_path}")
        return EXIT_FAIL

    try:
        added, skipped_sensitive = _zip_files(out_path, files)
    except Exception as exc:
        fail(f"Failed to create zip: {exc}")
        return EXIT_FAIL

    if added == 0:
        out_path.unlink(missing_ok=True)
        fail("No safe files to package after filtering sensitive entries.")
        return EXIT_FAIL

    if skipped_sensitive > 0:
        warn(f"Skipped sensitive files: {skipped_sensitive}")

    ok(f"Collected {added} file(s) for target '{args.target}'.")
    info(f"ZIP: {out_path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
