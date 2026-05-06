#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Typed memory helper utilities.

This script is intentionally read-only. It helps classify memory candidates,
plan high-recall memory searches, compare conflicting statements, and flag
freshness-sensitive facts. It never writes L1/L2/L3 memory files.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List


TYPE_KEYWORDS: Dict[str, List[str]] = {
    "user_preference": ["用户", "偏好", "以后", "默认", "不要", "禁止", "喜欢", "协作", "疑问句", "吗", "？", "?"],
    "environment": ["路径", "安装", "版本", "端点", "API", "端口", "服务", "环境", "python", "pip", "依赖", "tabulate"],
    "procedure": ["SOP", "流程", "步骤", "规范", "清单", "工作流", "procedure"],
    "skill": ["工具", "脚本", ".py", "skill", "CLI", "函数", "命令"],
    "constraint": ["必须", "禁止", "红线", "约束", "触发", "规则", "不可"],
    "failure_lesson": ["失败", "报错", "429", "缺", "异常", "根因", "修复", "debug", "教训"],
    "project_state": ["项目", "进度", "待办", "交接", "状态", "里程碑", "验收"],
    "external_fact": ["论文", "网页", "arXiv", "外部", "研究", "数据", "报告"],
}

LAYER_HINTS = {
    "user_preference": "L2；L1只放极简索引",
    "environment": "L2",
    "procedure": "L3 SOP",
    "skill": "L3 .py/scripts；L1只索引",
    "constraint": "约束DSL/SOP；L1只放红线索引",
    "failure_lesson": "L2或相关L3 SOP",
    "project_state": "project_board/session_handoff",
    "external_fact": "任务产物；高复用才入长期记忆",
}

FRESHNESS_PATTERNS = [
    r"API|endpoint|端点|模型|model",
    r"版本|version|依赖|pip|npm|安装",
    r"路径|目录|端口|服务|进程",
    r"登录|cookie|验证码|反爬|限流|429",
    r"进度|部署|状态|待办",
]


@dataclass
class Classification:
    text: str
    memory_type: str
    score: int
    layer_hint: str
    matched_keywords: List[str]
    all_scores: Dict[str, int]


def classify_text(text: str) -> Classification:
    normalized = text.lower()
    scores: Dict[str, int] = {}
    matches: Dict[str, List[str]] = {}
    for memory_type, keywords in TYPE_KEYWORDS.items():
        hit = []
        score = 0
        for kw in keywords:
            if kw.lower() in normalized:
                hit.append(kw)
                score += 2 if len(kw) > 1 else 1
        scores[memory_type] = score
        matches[memory_type] = hit
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        best = "external_fact"
    return Classification(
        text=text,
        memory_type=best,
        score=scores[best],
        layer_hint=LAYER_HINTS[best],
        matched_keywords=matches[best],
        all_scores=scores,
    )


def make_recall_plan(query: str) -> Dict[str, object]:
    cls = classify_text(query)
    tokens = [t for t in re.split(r"[\s,，。；;:：/\\()（）\[\]{}<>]+", query) if t]
    expansions = set(tokens)
    synonyms = {
        "429": ["rate limit", "限流", "Too Many Requests", "arXiv API"],
        "tabulate": ["to_markdown", "pandas", "Markdown表格", "依赖缺失"],
        "疑问句": ["吗", "？", "?", "征询意见", "授权执行"],
        "记忆": ["memory", "L1", "L2", "L3", "global_mem", "SOP"],
        "冲突": ["conflict", "旧值", "新值", "覆盖", "不一致"],
    }
    for token in list(expansions):
        for key, vals in synonyms.items():
            if key.lower() in token.lower() or token.lower() in key.lower():
                expansions.update(vals)
    return {
        "query": query,
        "classification": asdict(cls),
        "search_order": [
            "Read L1 global_mem_insight for navigation",
            "Read relevant L2 global_mem sections",
            "Read target L3 SOP/script when indexed",
            "For code/file facts use Grep-first evidence verification",
        ],
        "high_recall_keywords": sorted(expansions),
        "freshness_check_required": is_freshness_sensitive(query),
    }


def compare_conflict(old: str, new: str) -> Dict[str, object]:
    old_cls = classify_text(old)
    new_cls = classify_text(new)
    same_type = old_cls.memory_type == new_cls.memory_type
    likely_conflict = same_type and normalize_statement(old) != normalize_statement(new)
    return {
        "old": old,
        "new": new,
        "old_type": old_cls.memory_type,
        "new_type": new_cls.memory_type,
        "same_type": same_type,
        "likely_conflict": likely_conflict,
        "resolution_rule": "latest explicit user preference > verified local state > old memory > model inference",
        "required_action": "verify evidence and patch minimally; do not overwrite blindly" if likely_conflict else "store separately or no conflict detected",
    }


def normalize_statement(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def is_freshness_sensitive(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in FRESHNESS_PATTERNS)


def freshness_report(text: str) -> Dict[str, object]:
    sensitive = is_freshness_sensitive(text)
    return {
        "text": text,
        "freshness_sensitive": sensitive,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "guidance": (
            "Verify with tools before using as current fact; record evidence/time if writing memory."
            if sensitive
            else "No default freshness check required, but evidence is still required for LTM writes."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Typed memory read-only helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("classify", help="classify a memory candidate")
    p.add_argument("text")

    p = sub.add_parser("plan", help="build a high-recall memory search plan")
    p.add_argument("query")

    p = sub.add_parser("conflict", help="compare old/new statements")
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)

    p = sub.add_parser("freshness", help="check whether a fact is freshness-sensitive")
    p.add_argument("text")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "classify":
        data = asdict(classify_text(args.text))
    elif args.cmd == "plan":
        data = make_recall_plan(args.query)
    elif args.cmd == "conflict":
        data = compare_conflict(args.old, args.new)
    elif args.cmd == "freshness":
        data = freshness_report(args.text)
    else:
        raise AssertionError(args.cmd)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())