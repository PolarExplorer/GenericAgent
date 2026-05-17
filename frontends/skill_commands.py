"""Auto-discover /trigger commands from memory skill files for TUI palette."""
import os
import re

# File name patterns that indicate a skill file
_SKILL_FILE_PAT = re.compile(r"(^SKILL\.md$|_skill.*\.md$)", re.IGNORECASE)

# Lines that likely contain trigger definitions
_TRIGGER_LINE = re.compile(
    r"(^-\s*Trigger|^trigger\s*:|ga_adapter|GA\s*Adapter)", re.IGNORECASE
)

# Valid command: /word with optional :sub or -sub, ASCII-only, 2-30 chars after /
_VALID_CMD = re.compile(r"^/[A-Za-z][A-Za-z0-9_:-]{1,29}$")
# False-positive tokens to exclude (paths, fragments)
_EXCLUDE = {"memory", "skills", "skill", "docs", "sync", "agent-paths"}


def _extract_cmds_from_line(line: str) -> list[str]:
    """Extract /command tokens from a single line."""
    results = []
    # 1) Backtick-wrapped: `/cmd`, `/cmd {arg}`, `/cmd ...`  -> extract first /word
    for m in re.finditer(r"`(/[A-Za-z][A-Za-z0-9_:-]+)[^`]*`", line):
        results.append(m.group(1))
    # 2) Bare /commands: preceded by any non-alnum or start-of-line
    for m in re.finditer(r"(?:^|[^A-Za-z0-9_/])(/[A-Za-z][A-Za-z0-9_:-]+)", line):
        results.append(m.group(1))
    return [c for c in results if _VALID_CMD.match(c) and c.lstrip("/") not in _EXCLUDE]


def discover(root_dir: str, builtin_names: set | None = None) -> list[tuple[str, str, str]]:
    """Return [(cmd, args_hint, description), ...] from skill .md files."""
    mem_root = os.path.join(root_dir, "memory")
    if not os.path.isdir(mem_root):
        return []
    if builtin_names is None:
        builtin_names = set()
    seen: set[str] = set()
    results: list[tuple[str, str, str]] = []

    for dirpath, dirs, files in os.walk(mem_root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "L4_raw_sessions")]
        for fname in files:
            if not _SKILL_FILE_PAT.search(fname):
                continue
            fp = os.path.join(dirpath, fname)
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read(6000)
            except OSError:
                continue

            # --- extract description ---
            desc = fname.replace(".md", "").replace("_", " ").strip()
            fm = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
            if fm:
                for ln in fm.group(1).splitlines():
                    stripped = ln.strip()
                    if stripped.startswith("description"):
                        d = re.sub(r"^description\s*:\s*>?\s*", "", stripped)
                        if d and len(d) > 5:
                            desc = d[:60]
                            break
            if desc == fname.replace(".md", "").replace("_", " ").strip():
                m = re.search(r"^#\s+(.+)", text, re.MULTILINE)
                if m:
                    desc = m.group(1).strip()[:60]

            # --- extract /trigger commands only from trigger-related lines ---
            triggers: list[str] = []
            for line in text.splitlines()[:60]:
                if _TRIGGER_LINE.search(line):
                    triggers.extend(_extract_cmds_from_line(line))

            for t in dict.fromkeys(triggers):
                if t in seen or t in builtin_names:
                    continue
                seen.add(t)
                results.append((t, "", f"[skill] {desc}"))
    return results
