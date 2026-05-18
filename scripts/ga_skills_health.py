#!/usr/bin/env python3
"""Read-only health report for GA skill assets.

This script does not modify any asset. It scans callable skill markdown files,
keeps reference/index/vendor/deprecated material out of the main score, and
reports a lightweight Darwin-style structure baseline.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
from typing import Any, Dict, Iterable, List, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MEMORY_ROOT = REPO_ROOT / "memory"
SKILLS_ROOT = MEMORY_ROOT / "skills"

EXCLUDE_PARTS = {"vendor", "_deprecated", "references", "research", "__pycache__"}
EXCLUDE_SUFFIXES = ("_index.md",)

DIMENSIONS: List[Tuple[str, List[str]]] = [
    ("trigger", ["trigger", "when to use", "activation", "invoke", "command", "触发", "调用", "何时", "/"]),
    ("io", ["inputs", "outputs", "input", "output", "输入", "输出", "deliverable", "artifact", "schema"]),
    ("executable", ["steps", "workflow", "procedure", "run", "execute", "流程", "步骤", "执行", "命令", "python"]),
    ("acceptance", ["acceptance", "verify", "validation", "done when", "完成定义", "验收", "验证", "pass", "fail"]),
    ("safety", ["risk", "side effect", "side effects", "permission", "secret", "安全", "风险", "副作用", "禁止", "权限"]),
    ("boundary", ["conflict", "boundary", "priority", "fallback", "do not", "边界", "冲突", "优先", "回退", "不要"]),
    ("index", ["l1", "l2", "l3", "global_mem", "insight", "router", "routing", "索引", "路由", "召回"]),
    ("practical", ["example", "sample", "case", "prompt", "failure", "示例", "案例", "实战", "失败", "样例"]),
]


def is_excluded(path: pathlib.Path) -> Tuple[bool, str]:
    rel_parts = path.relative_to(REPO_ROOT).parts
    lower_parts = {part.lower() for part in rel_parts}
    hit = sorted(EXCLUDE_PARTS.intersection(lower_parts))
    if hit:
        return True, "excluded_part:" + hit[0]
    name = path.name.lower()
    for suffix in EXCLUDE_SUFFIXES:
        if name.endswith(suffix):
            return True, "excluded_suffix:" + suffix
    return False, ""


def iter_candidate_paths(include_excluded: bool = False) -> Iterable[pathlib.Path]:
    seen = set()
    roots = []
    roots.extend(MEMORY_ROOT.glob("*_skill.md"))
    if SKILLS_ROOT.exists():
        roots.extend(SKILLS_ROOT.rglob("*.md"))
    for path in sorted(roots):
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        excluded, _reason = is_excluded(path)
        if excluded and not include_excluded:
            continue
        yield path


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def title_of(text: str, path: pathlib.Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
    return path.stem


def slash_triggers(text: str) -> List[str]:
    found = re.findall(r"(?<!\w)/(?:[A-Za-z][\w-]{1,40})", text[:12000])
    noise = {"/api", "/assets", "/div", "/github", "/img", "/path", "/raw", "/www"}
    return sorted({item for item in found if item not in noise})


def score_dimension(text: str, keywords: List[str]) -> int:
    low = text.lower()
    hits = sum(1 for word in keywords if word.lower() in low)
    header_re = r"(?im)^#{1,4}\s*.*(?:" + "|".join(re.escape(word) for word in keywords[:5]) + r")"
    has_header = bool(re.search(header_re, text))
    if hits >= 4 and has_header:
        return 5
    if hits >= 4:
        return 4
    if hits >= 2:
        return 3
    if hits >= 1:
        return 2
    return 1


def analyze_skill(path: pathlib.Path) -> Dict[str, Any]:
    text = read_text(path)
    triggers = slash_triggers(text)
    scores = {name: score_dimension(text, words) for name, words in DIMENSIONS}
    if not triggers and not re.search(r"(?im)(trigger|when to use|activation|invoke|触发|调用)", text):
        scores["trigger"] = min(scores["trigger"], 2)
    missing = [name for name, value in scores.items() if value <= 2]
    excluded, reason = is_excluded(path)
    return {
        "path": str(path.relative_to(REPO_ROOT)),
        "title": title_of(text, path),
        "excluded": excluded,
        "exclude_reason": reason,
        "lines": len(text.splitlines()),
        "bytes": len(text),
        "triggers": triggers,
        "scores": scores,
        "total_score": sum(scores.values()),
        "missing_or_weak": missing,
    }


def build_report(include_excluded: bool = False) -> Dict[str, Any]:
    skills = [analyze_skill(path) for path in iter_candidate_paths(include_excluded=include_excluded)]
    active = [item for item in skills if not item["excluded"]]
    excluded = [item for item in skills if item["excluded"]]
    totals = [item["total_score"] for item in active]
    dimension_weak_counts: Dict[str, int] = {}
    for name, _words in DIMENSIONS:
        dimension_weak_counts[name] = sum(1 for item in active if item["scores"][name] <= 2)
    by_dir: Dict[str, Dict[str, Any]] = {}
    grouped: Dict[str, List[int]] = collections.defaultdict(list)
    for item in active:
        parts = pathlib.PurePath(item["path"]).parts
        key = "/".join(parts[:3]) if len(parts) >= 3 else item["path"]
        grouped[key].append(item["total_score"])
    for key, values in sorted(grouped.items()):
        by_dir[key] = {"count": len(values), "min": min(values), "max": max(values), "avg": round(sum(values) / len(values), 1)}
    return {
        "repo_root": str(REPO_ROOT),
        "mode": "read_only",
        "score_scale": "8 dimensions, each 1-5, max 40",
        "exclude_rules": {
            "parts": sorted(EXCLUDE_PARTS),
            "suffixes": list(EXCLUDE_SUFFIXES),
        },
        "summary": {
            "active_skill_count": len(active),
            "excluded_candidate_count": len(excluded),
            "min_score": min(totals) if totals else None,
            "max_score": max(totals) if totals else None,
            "avg_score": round(sum(totals) / len(totals), 1) if totals else None,
            "weak_dimension_counts": dimension_weak_counts,
        },
        "by_dir": by_dir,
        "lowest": sorted(active, key=lambda item: (item["total_score"], -item["bytes"]))[:20],
        "highest": sorted(active, key=lambda item: item["total_score"], reverse=True)[:10],
        "skills": active,
        "excluded_candidates": excluded,
    }


def safe_print(value: Any = "") -> None:
    text = str(value)
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def print_text(report: Dict[str, Any]) -> None:
    safe_print("GA skills health report")
    safe_print(f"repo_root: {report['repo_root']}")
    safe_print(f"mode: {report['mode']}")
    safe_print(f"score_scale: {report['score_scale']}")
    safe_print("exclude_rules: " + str(report["exclude_rules"]))
    safe_print("summary: " + str(report["summary"]))
    safe_print("\n== by_dir ==")
    for key, value in report["by_dir"].items():
        safe_print(f"{key}: {value}")
    safe_print("\n== lowest ==")
    for item in report["lowest"]:
        safe_print(f"{item['total_score']:2d} {item['path']} weak={item['missing_or_weak']} triggers={item['triggers']}")
    safe_print("\n== highest ==")
    for item in report["highest"]:
        safe_print(f"{item['total_score']:2d} {item['path']} triggers={item['triggers']}")
    if report["excluded_candidates"]:
        safe_print("\n== excluded_candidates ==")
        for item in report["excluded_candidates"][:30]:
            safe_print(f"{item['exclude_reason']} {item['path']}")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only GA skill asset health report")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument("--include-excluded", action="store_true", help="Include vendor/deprecated/reference/index candidates in report")
    parser.add_argument("--fail-under", type=int, default=None, help="Return non-zero if any active skill scores below this total")
    args = parser.parse_args(argv)

    report = build_report(include_excluded=args.include_excluded)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report)

    if args.fail_under is not None:
        if any(item["total_score"] < args.fail_under for item in report["skills"]):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
