#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
import re

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"

DOCS = {
    "spec.md": "spec-template.md",
    "plan.md": "plan-template.md",
    "tasks.md": "tasks-template.md",
}


def init_project(project_root: Path, force: bool = False) -> int:
    project_root = project_root.resolve()
    project_root.mkdir(parents=True, exist_ok=True)
    created = []
    skipped = []
    for name, tmpl in DOCS.items():
        target = project_root / name
        if target.exists() and not force:
            skipped.append(str(target))
            continue
        shutil.copy2(TEMPLATES / tmpl, target)
        created.append(str(target))
    checklist_dir = project_root / "checklists"
    checklist_dir.mkdir(exist_ok=True)
    checklist = checklist_dir / "sdd-checklist.md"
    if force or not checklist.exists():
        shutil.copy2(TEMPLATES / "checklist-template.md", checklist)
        created.append(str(checklist))
    print("CREATED")
    for x in created:
        print("  " + x)
    print("SKIPPED")
    for x in skipped:
        print("  " + x)
    return 0


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def check_project(project_root: Path) -> int:
    project_root = project_root.resolve()
    issues = []
    warnings = []
    for doc in ["spec.md", "plan.md", "tasks.md"]:
        if not (project_root / doc).exists():
            issues.append(f"missing {doc}")
    spec = _read(project_root / "spec.md")
    plan = _read(project_root / "plan.md")
    tasks = _read(project_root / "tasks.md")
    agents = _read(project_root / "AGENTS.md")

    if "NEEDS CLARIFICATION" in spec or "NEEDS CLARIFICATION" in plan:
        warnings.append("unresolved NEEDS CLARIFICATION marker")
    if spec and not re.search(r"Success Criteria|验收|Acceptance", spec, re.I):
        issues.append("spec lacks success/acceptance criteria section")
    if plan and not re.search(r"Verification|验证|Gate", plan, re.I):
        issues.append("plan lacks verification/gate section")
    # Accept either checklist style ("- [ ] T001") or markdown table rows ("| T001 | TODO | ...").
    # Brownfield projects often maintain task ledgers as tables; check should verify IDs, not force one layout.
    has_checklist_task_id = bool(re.search(r"- \[ [ xX] \] T\d{3}\b", tasks))
    has_table_task_id = bool(re.search(r"^\s*\|\s*T\d{3}\s*\|", tasks, re.M))
    if tasks and not (has_checklist_task_id or has_table_task_id):
        issues.append("tasks do not use task ids like T001 in checklist or table format")
    if tasks and "`" not in tasks:
        warnings.append("tasks may lack exact file paths")
    if not agents:
        warnings.append("AGENTS.md missing; project_context_sop may be needed")

    print("GA_SDD_CHECK")
    print(f"project={project_root}")
    print(f"issues={len(issues)} warnings={len(warnings)}")
    for x in issues:
        print("ISSUE: " + x)
    for x in warnings:
        print("WARN: " + x)
    return 1 if issues else 0


def sync_project(project_root: Path) -> int:
    project_root = project_root.resolve()
    print("GA_SDD_SYNC_DRY_RUN")
    print("No file is modified by sync in MVP.")
    print("Required manual/agent checks:")
    print("- spec changes -> check plan.md, tasks.md, AGENTS.md, contracts/, validation.md")
    print("- plan changes -> check tasks.md and AGENTS.md if architecture/commands changed")
    print("- implementation done -> run check and verify, then reverse-sync docs")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="ga_sdd", description="Lightweight GA SDD overlay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("project_root")
    p_init.add_argument("--force", action="store_true")
    p_check = sub.add_parser("check")
    p_check.add_argument("project_root")
    p_sync = sub.add_parser("sync")
    p_sync.add_argument("project_root")
    args = ap.parse_args()
    if args.cmd == "init":
        return init_project(Path(args.project_root), args.force)
    if args.cmd == "check":
        return check_project(Path(args.project_root))
    if args.cmd == "sync":
        return sync_project(Path(args.project_root))
    raise AssertionError(args.cmd)

if __name__ == "__main__":
    raise SystemExit(main())
