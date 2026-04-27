#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Repeatable isolated smoke test for GA audit dashboard.

This script intentionally does NOT use the real dashboard/control ports (8765/8766).
It runs ga_audit against a temporary dashboard directory and dynamic localhost ports,
creates synthetic audit turns, verifies HTTP/data/UI-template signals, exercises the
isolated /api/stop endpoint, writes a JSON report, then shuts its own servers down.

Usage:
    python tests/dashboard_smoke_test.py
    python tests/dashboard_smoke_test.py --report temp/dashboard_smoke_report.json
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import shutil
import socket
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http_get(url: str, timeout: float = 5.0) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            return {
                "ok": 200 <= r.status < 300,
                "status": r.status,
                "len": len(body),
                "text": body.decode("utf-8", errors="replace"),
            }
    except Exception as e:
        return {"ok": False, "error": repr(e), "text": ""}


def http_post_json(url: str, payload: Dict[str, Any] | None = None, timeout: float = 5.0) -> Dict[str, Any]:
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            parsed = None
            try:
                parsed = json.loads(body)
            except Exception:
                pass
            return {"ok": 200 <= r.status < 300, "status": r.status, "text": body, "json": parsed}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": e.code, "text": body, "error": repr(e)}
    except Exception as e:
        return {"ok": False, "error": repr(e), "text": ""}


def wait_until(check, timeout: float = 5.0, interval: float = 0.1):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = check()
        if last:
            return last
        time.sleep(interval)
    return last


def tool_call(tool_name: str, args: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {"tool_name": tool_name, "args": args or {}}


class DummyAgent:
    def __init__(self, abort_file: Path):
        self.current_query = "Dashboard smoke isolated test"
        self.last_query = ""
        self._turn_end_hooks: Dict[str, Any] = {}
        self.task_queue: queue.Queue = queue.Queue()
        self.abort_called = False
        self.abort_count = 0
        self.abort_file = abort_file

    def put_task(self, query, source="user", images=None):
        display_queue: List[Any] = []
        self.current_query = query
        self.task_queue.put({"query": query, "source": source, "output": display_queue})
        return display_queue

    def abort(self):
        self.abort_called = True
        self.abort_count += 1
        self.abort_file.write_text(
            f"abort_called={self.abort_called}; abort_count={self.abort_count}\n",
            encoding="utf-8",
        )


def make_cases() -> List[Dict[str, Any]]:
    return [
        {
            "turn": 101,
            "summary": "<summary>smoke turn101 verified read</summary> PASS with evidence",
            "tool_calls": [
                tool_call("file_read", {"path": str(ROOT / "ga_audit.py")}),
                tool_call("code_run", {"script": "print('PASS')"}),
            ],
            "response": {
                "model": "dashboard-smoke-model",
                "usage": {"input_tokens": 12, "output_tokens": 5},
                "text": "done verified PASS",
            },
        },
        {
            "turn": 102,
            "summary": "smoke turn102 intentional memory write without META-SOP",
            "tool_calls": [
                tool_call(
                    "file_patch",
                    {
                        "path": str(ROOT / "memory" / "global_mem.txt"),
                        "old_content": "x",
                        "new_content": "y",
                    },
                )
            ],
            "response": {
                "model": "dashboard-smoke-model",
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                "text": "intentional fail case",
            },
        },
        {
            "turn": 103,
            "summary": "<summary>smoke turn103 subagent evidence</summary> DOM verification target",
            "tool_calls": [
                tool_call("subagent_run", {"goal": "smoke"}),
                tool_call("web_scan", {"url": "http://127.0.0.1/dashboard.html"}),
            ],
            "response": {
                "model": "dashboard-smoke-model",
                "usage": {"input": 20, "output": 10},
                "choices": [{"message": {"content": "verified"}}],
            },
        },
    ]


def check(condition: bool, name: str, details: Any = None) -> Dict[str, Any]:
    return {"name": name, "ok": bool(condition), "details": details}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(ROOT / "temp" / "dashboard_smoke_report.json"))
    ap.add_argument("--keep-dir", action="store_true", help="Do not delete isolated runtime dashboard directory.")
    args = ap.parse_args()

    report_path = Path(args.report).resolve()
    runtime_dir = Path(tempfile.mkdtemp(prefix="ga_dashboard_smoke_"))
    abort_file = runtime_dir / "abort_seen.txt"
    checks: List[Dict[str, Any]] = []
    report: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "runtime_dir": str(runtime_dir),
        "report_path": str(report_path),
        "checks": checks,
    }

    try:
        import ga_audit

        dashboard_port = free_port()
        control_port = free_port()
        while control_port == dashboard_port:
            control_port = free_port()

        # Fully isolate ga_audit runtime state from the real dashboard.
        ga_audit._CONTROL_PORT = control_port
        ga_audit._DASHBOARD_PORT = dashboard_port
        ga_audit._DASHBOARD_DIR = runtime_dir
        ga_audit._DASHBOARD_HTML_PATH = runtime_dir / "dashboard.html"
        ga_audit._AUDIT_LOG_PATH = runtime_dir / "audit_log.json"
        ga_audit._control_server = None
        ga_audit._dashboard_server = None
        ga_audit._agent_ref = None
        if hasattr(ga_audit, "_CONSEC_EXEC_HISTORY"):
            ga_audit._CONSEC_EXEC_HISTORY[:] = []

        report["runtime_ports"] = {"dashboard": dashboard_port, "control": control_port}
        checks.append(check(dashboard_port not in (8765, 8766) and control_port not in (8765, 8766),
                            "uses_dynamic_non_default_ports",
                            report["runtime_ports"]))

        agent = DummyAgent(abort_file)
        install_ok = ga_audit.install(agent)
        hook = agent._turn_end_hooks.get("ga_audit")
        checks.append(check(install_ok and hook is not None, "ga_audit_install_and_hook", {"install_ok": install_ok, "hook": bool(hook)}))

        agent.put_task("Dashboard smoke isolated test: filters, fail display, stop control", source="smoke")
        # Simulate queue consumption so current task metadata is populated exactly as the real loop does.
        try:
            agent.task_queue.get_nowait()
        except queue.Empty:
            pass

        for case in make_cases():
            ctx = dict(case)
            ctx["self"] = agent
            hook(ctx)
            time.sleep(0.05)

        base = f"http://127.0.0.1:{dashboard_port}"
        control_base = f"http://127.0.0.1:{control_port}"

        dashboard_resp = wait_until(lambda: http_get(f"{base}/dashboard.html") if http_get(f"{base}/dashboard.html").get("ok") else None, timeout=5)
        audit_resp = wait_until(lambda: http_get(f"{base}/audit_log.json") if http_get(f"{base}/audit_log.json").get("ok") else None, timeout=5)
        constraints_resp = wait_until(lambda: http_get(f"{base}/constraints_snapshot.json") if http_get(f"{base}/constraints_snapshot.json").get("ok") else None, timeout=5)

        checks.append(check(bool(dashboard_resp and dashboard_resp.get("ok")), "dashboard_html_http_200",
                            {"status": dashboard_resp.get("status") if dashboard_resp else None, "len": dashboard_resp.get("len") if dashboard_resp else None}))
        checks.append(check(bool(audit_resp and audit_resp.get("ok")), "audit_log_http_200",
                            {"status": audit_resp.get("status") if audit_resp else None, "len": audit_resp.get("len") if audit_resp else None}))
        checks.append(check(bool(constraints_resp and constraints_resp.get("ok")), "constraints_snapshot_http_200",
                            {"status": constraints_resp.get("status") if constraints_resp else None, "len": constraints_resp.get("len") if constraints_resp else None}))

        dashboard_text = dashboard_resp.get("text", "") if dashboard_resp else ""
        checks.append(check("view-mode" in dashboard_text and "task-select" in dashboard_text and "task-summary" in dashboard_text,
                            "dashboard_template_has_task_filter_controls"))
        checks.append(check("btn-stop-ga" in dashboard_text, "dashboard_template_has_stop_button"))
        checks.append(check("constraint" in dashboard_text.lower(), "dashboard_template_has_constraint_rendering"))

        events = json.loads(audit_resp.get("text", "[]")) if audit_resp else []
        constraints = json.loads(constraints_resp.get("text", "{}")) if constraints_resp else {}
        task_ids = sorted({e.get("task_id") for e in events if isinstance(e, dict) and e.get("task_id")})
        fail_events = [
            e for e in events
            if any(c.get("status") == "fail" for c in e.get("constraint_checks", []))
        ]
        c005_hits = [
            c for e in events for c in e.get("constraint_checks", [])
            if c.get("id") == "C005"
        ]
        checks.append(check(len(events) >= 3, "synthetic_turn_events_written", {"event_count": len(events)}))
        checks.append(check(len(task_ids) == 1 and task_ids[0], "single_task_id_filterable", {"task_ids": task_ids}))
        checks.append(check(any(e.get("turn") == 101 for e in events) and any(e.get("turn") == 103 for e in events),
                            "turn_101_and_103_present"))
        checks.append(check(any(e.get("subagent") for e in events), "subagent_event_present"))
        checks.append(check(bool(fail_events), "intentional_fail_visible_in_data",
                            {"fail_turns": [e.get("turn") for e in fail_events]}))
        checks.append(check(any(c.get("status") == "fail" for c in c005_hits), "c005_memory_write_fail_detected",
                            {"c005_statuses": [c.get("status") for c in c005_hits]}))
        checks.append(check(len(constraints.get("constraints", [])) >= 1, "constraints_snapshot_has_items",
                            {"constraint_count": len(constraints.get("constraints", []))}))

        # Exercise ONLY the isolated dynamic control port.
        stop_resp = http_post_json(f"{control_base}/api/stop", {"source": "dashboard_smoke"})
        checks.append(check(stop_resp.get("ok") and (stop_resp.get("json") or {}).get("ok"),
                            "isolated_stop_api_200", stop_resp))
        checks.append(check(agent.abort_called and agent.abort_count == 1 and abort_file.exists(),
                            "isolated_abort_called_once",
                            {"abort_called": agent.abort_called, "abort_count": agent.abort_count, "abort_file": str(abort_file)}))

        # Re-read data after stop control event is appended.
        audit_after = http_get(f"{base}/audit_log.json")
        events_after = json.loads(audit_after.get("text", "[]")) if audit_after.get("ok") else events
        control_events = [e for e in events_after if e.get("turn") == "control" and e.get("control", {}).get("action") == "stop"]
        checks.append(check(bool(control_events), "control_stop_event_recorded",
                            {"control_events": control_events[-2:]}))

        report["event_count_after_stop"] = len(events_after)
        report["task_ids"] = task_ids
        report["dashboard_url"] = f"{base}/dashboard.html"
        report["control_url"] = f"{control_base}/api/stop"
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")

        ok = all(c["ok"] for c in checks)
        report["ok"] = ok
        return_code = 0 if ok else 1

    except Exception as e:
        report["ok"] = False
        report["error"] = repr(e)
        report["traceback"] = traceback.format_exc()
        return_code = 1

    finally:
        try:
            import ga_audit
            for attr in ("_control_server", "_dashboard_server"):
                srv = getattr(ga_audit, attr, None)
                if srv is not None:
                    try:
                        srv.shutdown()
                        srv.server_close()
                    except Exception:
                        pass
                    setattr(ga_audit, attr, None)
        except Exception:
            pass

        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "ok": report.get("ok"),
            "report": str(report_path),
            "runtime_ports": report.get("runtime_ports"),
            "checks_total": len(report.get("checks", [])),
            "checks_failed": [c["name"] for c in report.get("checks", []) if not c.get("ok")],
        }, ensure_ascii=False, indent=2))

        if not args.keep_dir:
            shutil.rmtree(runtime_dir, ignore_errors=True)

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())