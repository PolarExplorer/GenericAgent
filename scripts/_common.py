from __future__ import annotations

from functools import wraps
from pathlib import Path
from typing import Any, Callable


GA_ROOT = Path(__file__).resolve().parents[1]

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_SKIP = 2

PROTECTED_PORTS = {8765, 8766, 18513}


def _emit(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def ok(message: str) -> None:
    _emit("OK", message)


def warn(message: str) -> None:
    _emit("WARN", message)


def fail(message: str) -> None:
    _emit("FAIL", message)


def info(message: str) -> None:
    _emit("INFO", message)


def confirm_required(func: Callable[..., int]) -> Callable[[Any], int]:
    @wraps(func)
    def wrapper(args: Any) -> int:
        if not getattr(args, "confirm", False):
            warn("Dry-run only. Re-run with --confirm to apply changes.")
            return func(args, dry_run=True)
        return func(args, dry_run=False)

    return wrapper

