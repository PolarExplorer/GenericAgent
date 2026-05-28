#!/usr/bin/env python3
"""
GA Skill Search - AgentSkillsHub discovery engine for GA
Queries the AgentSkillsHub Supabase API and returns structured results.

Usage:
    python ga_skill_search.py "web scraping"
    python ga_skill_search.py "automation" --top 3
    python ga_skill_search.py "mcp" --category mcp-server
    python ga_skill_search.py "scraper" --lang Python --min-stars 100
    python ga_skill_search.py "agent" --safe-only --format json
    python ga_skill_search.py "automation" --top 3 --with-readme
"""
import argparse
import json
import subprocess
import sys

# AgentSkillsHub Supabase (public anon key)
SUPABASE_URL = "https://vknzzecmzsfmohglpfgm.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZrbnp6ZWNtenNmbW9oZ2xwZmdtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI4MDQ3MzIsImV4cCI6MjA4ODM4MDczMn0.zFAGZH-lDcL-GwyMkR-9sSV8pJToVzomsJ_fuXZIoDo"

# Column sets
LIGHT_COLS = (
    "repo_full_name,repo_url,description,category,language,"
    "stars,quality_score,security_grade,last_synced"
)
HEAVY_COLS = LIGHT_COLS + ",tags,topics,readme_content"


def _run_curl(args_list):
    """Run curl directly (no proxy - Supabase is accessible directly)."""
    cmd = ["curl", "-sL", "--noproxy", "*", "--max-time", "15"] + args_list
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20
        )
        return r.stdout
    except Exception as e:
        return json.dumps({"code": "curl_error", "message": str(e)})


def search_skills(keyword, category=None, lang=None, top=10,
                  min_stars=None, safe_only=False, with_readme=False):
    """Query AgentSkillsHub for skills matching keyword."""
    cols = HEAVY_COLS if with_readme else LIGHT_COLS
    url = SUPABASE_URL + "/rest/v1/skills?select=" + cols

    filters = []
    if keyword:
        # Use full-text search (plfts = plainto_tsquery) - much faster than ilike on 95K rows
        # plainto_tsquery treats words as AND automatically
        # URL-encode spaces as %20 to prevent curl argument splitting
        fts_query = keyword.replace(",", " ").strip().replace(" ", "%20")
        filters.append(f"search_vector=plfts.{fts_query}")
    if category:
        filters.append(f"category=eq.{category}")
    if lang:
        filters.append(f"language=eq.{lang}")
    if min_stars:
        filters.append(f"stars=gte.{min_stars}")
    if safe_only:
        filters.append("security_grade=eq.safe")

    if filters:
        url += "&" + "&".join(filters)

    url += "&order=quality_score.desc,stars.desc&limit=" + str(top)

    raw = _run_curl([
        url,
        "-H", "apikey: " + ANON_KEY,
        "-H", "Authorization: Bearer " + ANON_KEY,
    ])

    data = json.loads(raw)

    if isinstance(data, dict) and "code" in data:
        print(f"[ERROR] Supabase error: {data.get('message', data)}",
              file=sys.stderr)
        sys.exit(1)

    if not data:
        print(f"No results for '{keyword}'", file=sys.stderr)
        return []

    return data


def format_json(results):
    return json.dumps(results, indent=2, ensure_ascii=False)


def format_markdown(results, with_readme=False):
    lines = []
    for i, r in enumerate(results, 1):
        stars = r.get("stars", 0)
        qs = r.get("quality_score", 0) or 0
        grade = r.get("security_grade", "?") or "?"
        tag_str = ""
        tags = r.get("tags")
        if tags:
            tag_str = " | " + ", ".join(tags[:3])

        prefix = "**" if with_readme else ""
        suffix = "**" if with_readme else ""
        line = (f"{i}. {prefix}[{r['repo_full_name']}]({r['repo_url']}){suffix}"
                f" | Stars: {stars:,} | QS: {qs:.1f} | {grade}{tag_str}")
        lines.append(line)

        if r.get("description"):
            lines.append(f"   > {r['description'][:120]}")

        if with_readme and r.get("readme_content"):
            readme = r["readme_content"][:800]
            lines.append(f"   README: {readme}...")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Search AgentSkillsHub for agent skills")
    parser.add_argument("keyword", help="Search keyword")
    parser.add_argument("--top", type=int, default=10,
                        help="Number of results (default: 10)")
    parser.add_argument("--category", help="Filter by category")
    parser.add_argument("--lang", help="Filter by language")
    parser.add_argument("--min-stars", type=int,
                        help="Minimum star count")
    parser.add_argument("--safe-only", action="store_true",
                        help="Only safe-graded skills")
    parser.add_argument("--format", choices=["json", "markdown", "md"],
                        default="markdown", help="Output format")
    parser.add_argument("--with-readme", action="store_true",
                        help="Include README content (heavier)")

    args = parser.parse_args()

    results = search_skills(
        args.keyword, category=args.category, lang=args.lang,
        top=args.top, min_stars=args.min_stars,
        safe_only=args.safe_only, with_readme=args.with_readme
    )

    if not results:
        return

    if args.format == "json":
        print(format_json(results))
    else:
        print(format_markdown(results, with_readme=args.with_readme))


if __name__ == "__main__":
    main()
