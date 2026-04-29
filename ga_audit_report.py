#!/usr/bin/env python3
"""
GA Audit Report Exporter — 审计报告导出模块
生成 PDF 数据报告 + gpt-image-2 视觉摘要卡片

Usage (standalone test):
    python ga_audit_report.py [--dashboard-dir path]

As library:
    from ga_audit_report import export_report, get_export_status
"""
import json, os, sys, time, threading, subprocess, traceback
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
_EXPORT_STATUS = {}  # {job_id: {status, progress, pdf_path, card_path, error}}
_EXPORT_LOCK = threading.Lock()


def collect_report_data(dashboard_dir: Path) -> dict:
    """从 audit_log.json + constraints_snapshot.json 收集报告数据"""
    audit_log_path = dashboard_dir / "audit_log.json"
    snapshot_path = dashboard_dir / "constraints_snapshot.json"
    
    # Load events
    events = []
    if audit_log_path.exists():
        try:
            events = json.loads(audit_log_path.read_text(encoding="utf-8"))
        except Exception:
            events = []
    
    # Load constraints snapshot
    snapshot = {}
    if snapshot_path.exists():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            snapshot = {}
    
    # Filter out control events
    task_events = [e for e in events if e.get("turn") != "control"]
    
    # Aggregate worst-case per constraint
    worst_case = {}  # id -> {id, name, status, evidence, turn, timestamp}
    STATUS_RANK = {"fail": 3, "pass": 2, "skip": 1, "error": 4}
    
    for ev in task_events:
        for chk in ev.get("constraint_checks", []):
            cid = chk.get("id", "")
            if not cid:
                continue
            rank = STATUS_RANK.get(chk.get("status", "skip"), 0)
            existing = worst_case.get(cid)
            if not existing or rank > STATUS_RANK.get(existing["status"], 0):
                worst_case[cid] = {
                    "id": cid,
                    "name": chk.get("name", ""),
                    "status": chk.get("status", "skip"),
                    "evidence": chk.get("evidence", ""),
                    "source": chk.get("source", ""),
                    "turn": ev.get("turn", "?"),
                    "timestamp": ev.get("timestamp", ""),
                    "task_id": ev.get("task_id", ""),
                }
    
    # Collect all violations with context
    violations = []
    for ev in task_events:
        for v in ev.get("violations", []):
            violations.append({
                "id": v.get("id", ""),
                "name": v.get("name", ""),
                "evidence": v.get("evidence", ""),
                "source": v.get("source", ""),
                "turn": ev.get("turn", "?"),
                "timestamp": ev.get("timestamp", ""),
                "task_id": ev.get("task_id", ""),
                "summary": ev.get("summary", "")[:100],
                "model": ev.get("model", ""),
            })
    
    # Stats
    total_turns = len(task_events)
    total_constraints = len(worst_case)
    fail_count = sum(1 for c in worst_case.values() if c["status"] == "fail")
    pass_count = sum(1 for c in worst_case.values() if c["status"] == "pass")
    skip_count = sum(1 for c in worst_case.values() if c["status"] == "skip")
    
    # Token totals
    total_tokens_in = sum(ev.get("tokens", {}).get("input", 0) for ev in task_events)
    total_tokens_out = sum(ev.get("tokens", {}).get("output", 0) for ev in task_events)
    
    # Task IDs
    task_ids = list(dict.fromkeys(ev.get("task_id", "") for ev in task_events if ev.get("task_id")))
    
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_turns": total_turns,
        "total_constraints": total_constraints,
        "stats": {
            "fail": fail_count,
            "pass": pass_count,
            "skip": skip_count,
            "violation_events": len(violations),
        },
        "tokens": {"input": total_tokens_in, "output": total_tokens_out},
        "task_ids": task_ids,
        "worst_case": sorted(worst_case.values(), key=lambda x: -STATUS_RANK.get(x["status"], 0)),
        "violations": violations,
        "pass_rate": round(pass_count / max(total_constraints, 1) * 100, 1),
        "safety_score": round((1 - fail_count / max(total_constraints, 1)) * 100, 1),
    }


def generate_pdf_report(data: dict, output_path: Path) -> Path:
    """生成 PDF 数据报告（weasyprint）"""
    from weasyprint import HTML as WeasyprintHTML

    stats = data["stats"]
    score = data["safety_score"]
    score_color = "#27ae60" if score >= 90 else "#f39c12" if score >= 70 else "#e74c3c"
    
    # Build violations HTML
    violations_html = ""
    if data["violations"]:
        rows = ""
        for v in data["violations"]:
            rows += f"""<tr>
                <td><code>{v['id']}</code></td>
                <td>{v['name'][:60]}</td>
                <td>Turn {v['turn']}</td>
                <td class="evidence">{v['evidence'][:120]}</td>
                <td>{v['timestamp']}</td>
            </tr>"""
        violations_html = f"""
        <h2>🚨 违规详情</h2>
        <table><thead><tr>
            <th>规则ID</th><th>规则名</th><th>触发轮次</th><th>证据</th><th>时间</th>
        </tr></thead><tbody>{rows}</tbody></table>"""
    else:
        violations_html = "<h2>🚨 违规详情</h2><p class='ok'>✅ 本次会话无违规记录</p>"

    # Build worst-case table
    wc_rows = ""
    for c in data["worst_case"][:50]:  # top 50
        st = c["status"]
        cls = "fail" if st == "fail" else "pass" if st == "pass" else "skip"
        label = {"fail": "❌ 违规", "pass": "✅ 通过", "skip": "⏭ 未触发"}.get(st, st)
        wc_rows += f"""<tr class="{cls}">
            <td><code>{c['id']}</code></td>
            <td>{c['name'][:50]}</td>
            <td>{label}</td>
            <td class="evidence">{c['evidence'][:80]}</td>
            <td>Turn {c['turn']}</td>
        </tr>"""

    html_content = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
@page {{ size: A4; margin: 20mm 15mm; }}
body {{ font-family: "Microsoft YaHei","Droid Sans Fallback",Helvetica,sans-serif;
  font-size: 10pt; color: #2c3e50; line-height: 1.6; }}
h1 {{ color: #1a5276; border-bottom: 2px solid #1a5276; padding-bottom: 8px; font-size: 18pt; }}
h2 {{ color: #2c3e50; margin-top: 20px; font-size: 13pt; }}
.header {{ display: flex; justify-content: space-between; align-items: center; }}
.score-box {{ text-align: center; padding: 15px 25px; border-radius: 12px;
  background: {score_color}15; border: 2px solid {score_color}; }}
.score-num {{ font-size: 36pt; font-weight: bold; color: {score_color}; }}
.score-label {{ font-size: 9pt; color: #666; }}
.stats {{ display: flex; gap: 20px; margin: 15px 0; }}
.stat {{ padding: 10px 18px; border-radius: 8px; background: #f8f9fa; text-align: center; }}
.stat .num {{ font-size: 20pt; font-weight: bold; }}
.stat .label {{ font-size: 8pt; color: #888; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 8.5pt; }}
th {{ background: #1a5276; color: white; padding: 6px 8px; text-align: left; }}
td {{ padding: 5px 8px; border-bottom: 1px solid #eee; }}
tr.fail td {{ background: #fdf2f2; }}
tr.pass td {{ background: #f0fdf4; }}
code {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-size: 8pt; }}
.evidence {{ font-size: 7.5pt; color: #666; max-width: 200px; word-break: break-all; }}
.ok {{ color: #27ae60; font-weight: bold; }}
.footer {{ margin-top: 30px; text-align: center; font-size: 8pt; color: #aaa;
  border-top: 1px solid #eee; padding-top: 10px; }}
</style></head><body>
<h1>📋 GA 审计报告</h1>
<div class="stats">
  <div class="score-box"><div class="score-num">{score}</div><div class="score-label">安全评分</div></div>
  <div class="stat"><div class="num" style="color:#e74c3c">{stats['fail']}</div><div class="label">违规</div></div>
  <div class="stat"><div class="num" style="color:#27ae60">{stats['pass']}</div><div class="label">通过</div></div>
  <div class="stat"><div class="num" style="color:#888">{stats['skip']}</div><div class="label">未触发</div></div>
  <div class="stat"><div class="num">{data['total_turns']}</div><div class="label">总轮次</div></div>
</div>
<p>生成时间: {data['generated_at']} · 任务数: {len(data['task_ids'])} · 
  Token: {data['tokens']['input']:,} in / {data['tokens']['output']:,} out</p>
{violations_html}
<h2>📊 约束检查总览 (Worst-Case)</h2>
<table><thead><tr><th>ID</th><th>名称</th><th>状态</th><th>证据</th><th>轮次</th></tr></thead>
<tbody>{wc_rows}</tbody></table>
<div class="footer">GA Audit System · {data['generated_at']}</div>
</body></html>"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    WeasyprintHTML(string=html_content).write_pdf(str(output_path))
    return output_path


def generate_summary_card(data: dict, output_path: Path) -> Path:
    """用 gpt-image-2 生成视觉摘要卡片（同步调用，耗时~1-3min）"""
    sys.path.insert(0, str(_SCRIPT_DIR / "memory"))
    from image_gen_utils import generate_image

    stats = data["stats"]
    score = data["safety_score"]
    grade = "A+" if score >= 95 else "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D"
    viol_summary = ""
    if data["violations"]:
        top3 = data["violations"][:3]
        viol_lines = [f"- {v['id']}: {v['name'][:30]}" for v in top3]
        viol_summary = "Top violations:\\n" + "\\n".join(viol_lines)
    else:
        viol_summary = "No violations detected ✓"

    prompt = f"""Design a professional dark-themed audit report summary card (landscape).

Header: "GA Audit Report" with a shield icon
Large score display: {score}/100 (grade {grade}) with {'green' if score>=90 else 'orange' if score>=70 else 'red'} accent
Stats row: {stats['fail']} violations (red) | {stats['pass']} passed (green) | {stats['skip']} skipped (gray) | {data['total_turns']} turns
{viol_summary}
Footer: Generated {data['generated_at']}

Style: Modern dashboard card, dark background (#1a1a2e), rounded corners, subtle gradients, 
clean typography, data visualization feel. Professional and minimal."""

    output_path = Path(output_path)
    generate_image(prompt=prompt, output_path=str(output_path), size="1536x1024", quality="high")
    return output_path


def _run_export(job_id: str, dashboard_dir: Path):
    """后台线程执行导出任务"""
    try:
        with _EXPORT_LOCK:
            _EXPORT_STATUS[job_id] = {"status": "collecting", "progress": 10}

        data = collect_report_data(dashboard_dir)

        # Generate PDF
        with _EXPORT_LOCK:
            _EXPORT_STATUS[job_id]["status"] = "generating_pdf"
            _EXPORT_STATUS[job_id]["progress"] = 30

        report_dir = dashboard_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_path = report_dir / f"audit_report_{ts}.pdf"

        try:
            generate_pdf_report(data, pdf_path)
            with _EXPORT_LOCK:
                _EXPORT_STATUS[job_id]["pdf_path"] = str(pdf_path)
                _EXPORT_STATUS[job_id]["progress"] = 50
        except Exception as e:
            with _EXPORT_LOCK:
                _EXPORT_STATUS[job_id]["pdf_error"] = str(e)[:300]
                _EXPORT_STATUS[job_id]["progress"] = 50

        # Generate image card via subprocess (long running)
        with _EXPORT_LOCK:
            _EXPORT_STATUS[job_id]["status"] = "generating_card"
            _EXPORT_STATUS[job_id]["progress"] = 60

        card_path = report_dir / f"audit_card_{ts}.png"
        try:
            # Use subprocess to avoid blocking timeout
            card_script = str(_SCRIPT_DIR / "ga_audit_report.py")
            proc = subprocess.Popen(
                [sys.executable, card_script, "--generate-card",
                 "--dashboard-dir", str(dashboard_dir),
                 "--output", str(card_path),
                 "--data-json", json.dumps(data, ensure_ascii=False)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(_SCRIPT_DIR)
            )
            stdout, stderr = proc.communicate(timeout=300)
            if proc.returncode == 0 and card_path.exists():
                with _EXPORT_LOCK:
                    _EXPORT_STATUS[job_id]["card_path"] = str(card_path)
            else:
                with _EXPORT_LOCK:
                    _EXPORT_STATUS[job_id]["card_error"] = (stderr.decode("utf-8", errors="replace"))[:300]
        except subprocess.TimeoutExpired:
            proc.kill()
            with _EXPORT_LOCK:
                _EXPORT_STATUS[job_id]["card_error"] = "Image generation timed out (5min)"
        except Exception as e:
            with _EXPORT_LOCK:
                _EXPORT_STATUS[job_id]["card_error"] = str(e)[:300]

        with _EXPORT_LOCK:
            _EXPORT_STATUS[job_id]["status"] = "done"
            _EXPORT_STATUS[job_id]["progress"] = 100

    except Exception as e:
        with _EXPORT_LOCK:
            _EXPORT_STATUS[job_id] = {
                "status": "error", "progress": 0,
                "error": traceback.format_exc()[:500]
            }


def export_report(dashboard_dir: Path) -> str:
    """启动异步导出，返回 job_id"""
    job_id = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    t = threading.Thread(target=_run_export, args=(job_id, dashboard_dir), daemon=True)
    t.start()
    return job_id


def get_export_status(job_id: str) -> dict:
    """查询导出状态"""
    with _EXPORT_LOCK:
        return dict(_EXPORT_STATUS.get(job_id, {"status": "not_found"}))


# ── CLI entry for subprocess card generation ──
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate-card", action="store_true")
    parser.add_argument("--dashboard-dir", default=".")
    parser.add_argument("--output", default="card.png")
    parser.add_argument("--data-json", default=None)
    args = parser.parse_args()

    if args.generate_card:
        if args.data_json:
            data = json.loads(args.data_json)
        else:
            data = collect_report_data(Path(args.dashboard_dir))
        generate_summary_card(data, Path(args.output))
        print(f"Card saved: {args.output}")
    else:
        # Default: full export (blocking)
        data = collect_report_data(Path(args.dashboard_dir))
        out = Path(args.dashboard_dir) / "reports"
        out.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf = generate_pdf_report(data, out / f"audit_report_{ts}.pdf")
        print(f"PDF: {pdf}")