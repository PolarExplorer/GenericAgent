#!/usr/bin/env python3
"""Read-only health report for GA constraint assets.

This script does not modify any asset. It checks structure, identifiers,
detection metadata, regex compilation, and optional regression tests for:
- assets/constraints_dsl.json
- assets/constraints_registry.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_ASSETS = [
    REPO_ROOT / "assets" / "constraints_dsl.json",
    REPO_ROOT / "assets" / "constraints_registry.json",
]
DEFAULT_TESTS = [
    REPO_ROOT / "tests" / "test_dsl_constraints.py",
    REPO_ROOT / "tests" / "test_ga_audit_registry.py",
]

CORE_FIELDS = ["id", "name", "description", "category", "source", "severity", "enabled", "active", "detection"]


def load_json(path: pathlib.Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_items(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        items: List[Dict[str, Any]] = []
        for key in ("constraints", "rules", "dsl_constraints"):
            arr = data.get(key, [])
            if isinstance(arr, list):
                items.extend(x for x in arr if isinstance(x, dict))
        if not items and all(isinstance(k, str) for k in data.keys()):
            return [data]
        return items
    return []


def section_counts(data: Any) -> Dict[str, int]:
    if isinstance(data, list):
        return {"list": len(data)}
    if not isinstance(data, dict):
        return {"unknown": 0}
    counts: Dict[str, int] = {}
    for key in ("constraints", "rules", "dsl_constraints"):
        value = data.get(key)
        if isinstance(value, list):
            counts[key] = len(value)
    return counts


def get_detection_kind(det: Any) -> str:
    if isinstance(det, dict):
        return "dict"
    if isinstance(det, str):
        return "str"
    if det is None:
        return "missing"
    return type(det).__name__


def regex_patterns(item: Dict[str, Any]) -> Iterable[Tuple[str, str]]:
    det = item.get("detection")
    if isinstance(det, dict):
        pat = det.get("pattern")
        if isinstance(pat, str) and pat:
            yield "detection.pattern", pat
        params = det.get("params")
        if isinstance(params, dict):
            for key in ("pattern", "required_pattern", "exclude_pattern"):
                p = params.get(key)
                if isinstance(p, str) and p:
                    yield f"detection.params.{key}", p
    params = item.get("params")
    if isinstance(params, dict):
        for key in ("pattern", "required_pattern", "exclude_pattern"):
            p = params.get(key)
            if isinstance(p, str) and p:
                yield f"params.{key}", p


def analyze_asset(path: pathlib.Path) -> Dict[str, Any]:
    data = load_json(path)
    items = iter_items(data)
    ids = [str(x.get("id", "")) for x in items]
    id_counter = collections.Counter(ids)
    duplicate_ids = sorted([k for k, v in id_counter.items() if k and v > 1])
    empty_ids = sum(1 for x in ids if not x)

    missing_fields = {field: 0 for field in CORE_FIELDS}
    for item in items:
        for field in CORE_FIELDS:
            if field not in item:
                missing_fields[field] += 1

    detection_kind_top = collections.Counter(get_detection_kind(x.get("detection")) for x in items).most_common()
    detection_type_top = collections.Counter(
        (x.get("detection") or {}).get("type", "") if isinstance(x.get("detection"), dict) else ""
        for x in items
    ).most_common()
    check_type_top = collections.Counter(str(x.get("check_type", "")) for x in items).most_common()
    category_top = collections.Counter(str(x.get("category", "")) for x in items).most_common()
    severity_top = collections.Counter(str(x.get("severity", "")) for x in items).most_common()

    regex_bad: List[Dict[str, str]] = []
    regex_count = 0
    longest_patterns: List[Tuple[str, int, str]] = []
    for item in items:
        item_id = str(item.get("id", ""))
        for source, pat in regex_patterns(item):
            regex_count += 1
            longest_patterns.append((item_id, len(pat), source))
            try:
                re.compile(pat)
            except Exception as exc:  # pragma: no cover - report path
                regex_bad.append({"id": item_id, "source": source, "error": str(exc), "pattern": pat[:160]})
    longest_patterns.sort(key=lambda x: x[1], reverse=True)

    return {
        "path": str(path.relative_to(REPO_ROOT)),
        "sections": section_counts(data),
        "total_items": len(items),
        "unique_ids": len(set(x for x in ids if x)) == len([x for x in ids if x]) and empty_ids == 0,
        "empty_ids": empty_ids,
        "duplicate_ids": duplicate_ids,
        "missing_fields": missing_fields,
        "detection_kind_top": detection_kind_top,
        "detection_type_top": detection_type_top,
        "check_type_top": check_type_top,
        "category_top": category_top[:12],
        "severity_top": severity_top[:12],
        "regex_count": regex_count,
        "regex_bad_count": len(regex_bad),
        "regex_bad": regex_bad,
        "longest_patterns": longest_patterns[:8],
    }


def run_test(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        return {"path": str(path.relative_to(REPO_ROOT)), "exists": False, "returncode": None}
    proc = subprocess.run(
        [sys.executable, str(path.relative_to(REPO_ROOT))],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    tail = ((proc.stdout or "") + ("\nSTDERR:\n" + proc.stderr if proc.stderr else "")).strip()[-1200:]
    return {"path": str(path.relative_to(REPO_ROOT)), "exists": True, "returncode": proc.returncode, "tail": tail}


def build_report(include_tests: bool = False) -> Dict[str, Any]:
    assets = [analyze_asset(p) for p in DEFAULT_ASSETS]
    report: Dict[str, Any] = {
        "repo_root": str(REPO_ROOT),
        "mode": "read_only",
        "assets": assets,
    }
    if include_tests:
        report["tests"] = [run_test(p) for p in DEFAULT_TESTS]
    return report


def safe_print(value: Any = "") -> None:
    text = str(value)
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def print_text(report: Dict[str, Any]) -> None:
    safe_print("GA constraint health report")
    safe_print(f"repo_root: {report['repo_root']}")
    safe_print(f"mode: {report['mode']}")
    for asset in report["assets"]:
        safe_print("\n== " + asset["path"] + " ==")
        safe_print("sections: " + str(asset["sections"]))
        safe_print("total_items: " + str(asset["total_items"]))
        safe_print("unique_ids: " + str(asset["unique_ids"]) + " empty_ids: " + str(asset["empty_ids"]) + " duplicate_ids: " + str(asset["duplicate_ids"]))
        safe_print("missing_fields: " + str(asset["missing_fields"]))
        safe_print("detection_kind_top: " + str(asset["detection_kind_top"]))
        safe_print("detection_type_top: " + str(asset["detection_type_top"]))
        safe_print("check_type_top: " + str(asset["check_type_top"][:12]))
        safe_print("category_top: " + str(asset["category_top"]))
        safe_print("severity_top: " + str(asset["severity_top"]))
        safe_print("regex_count: " + str(asset["regex_count"]) + " regex_bad_count: " + str(asset["regex_bad_count"]))
        if asset["regex_bad"]:
            safe_print("regex_bad: " + str(asset["regex_bad"]))
        safe_print("longest_patterns: " + str(asset["longest_patterns"]))
    if "tests" in report:
        safe_print("\n== tests ==")
        for test in report["tests"]:
            safe_print(f"{test['path']}: exists={test['exists']} returncode={test['returncode']}")
            if test.get("tail"):
                safe_print(test["tail"])


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only GA constraint asset health report")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument("--run-tests", action="store_true", help="Also run related regression tests")
    parser.add_argument("--fail-on-regex-error", action="store_true", help="Return non-zero if any regex fails to compile")
    args = parser.parse_args(argv)

    report = build_report(include_tests=args.run_tests)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report)

    if args.fail_on_regex_error:
        if any(asset["regex_bad_count"] for asset in report["assets"]):
            return 2
    if args.run_tests and any(test.get("returncode") not in (0, None) for test in report.get("tests", [])):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
