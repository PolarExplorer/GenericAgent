import os
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import json
import queue
import tempfile
import shutil

from ga_audit import (  # noqa: E402
    _CONSEC_EXEC_HISTORY,
    _load_registry,
    _on_turn_end,
    _run_checks,
    preview_r061_pre_tool_guard,
)


CASES = [
    (
        "R032",
        "code_run",
        {
            "script": "file_write(path='D:/AI/GenericAgent/memory/global_mem.txt', mode='overwrite')",
        },
        "fail",
    ),
    (
        "R032",
        "file_patch",
        {"path": r"D:\AI\GenericAgent\memory\global_mem.txt"},
        "pass",
    ),
    (
        "R042",
        "code_run",
        {"script": 'git commit -m "修复审计"'},
        "fail",
    ),
    (
        "R042",
        "code_run",
        {"script": 'git commit -m "Improve audit registry checks"'},
        "pass",
    ),
    (
        "R021",
        "web_execute_js",
        {"script": 'document.querySelector("textarea").value = "abc";'},
        "fail",
    ),
    (
        "R021",
        "web_execute_js",
        {
            "script": (
                'const el=document.querySelector("textarea");'
                'el.value="abc";'
                'el.dispatchEvent(new Event("input",{bubbles:true}));'
            )
        },
        "pass",
    ),
    (
        "C023",
        "code_run",
        {"script": 'prompt="midjourney style mockup"'},
        "fail",
    ),
    (
        "C026",
        "code_run",
        {"script": 'image_gen("x", size="1536x1024", quality="high")'},
        "fail",
    ),
    (
        "C026",
        "code_run",
        {"script": 'start_background_image_job(size="1536x1024", quality="high")'},
        "pass",
    ),
    (
        "C044",
        "web_execute_js",
        {"script": "await chrome.debugger.sendCommand(target, 'Runtime.evaluate', {})"},
        "fail",
    ),
    (
        "C044",
        "web_execute_js",
        {"script": "const tabId=123; await chrome.debugger.sendCommand({tabId}, 'Runtime.evaluate', {})"},
        "pass",
    ),
    (
        "C045",
        "web_execute_js",
        {"script": "await chrome.autofillPrivate.saveAddress({});"},
        "fail",
    ),
    (
        "C045",
        "web_execute_js",
        {"script": "await Page.bringToFront(); await chrome.autofillPrivate.saveAddress({});"},
        "pass",
    ),
    (
        "R020",
        "code_run",
        {"script": "click_button('OK')"},
        "fail",
    ),
    (
        "R020",
        "code_run",
        {"script": "import win32gui\nwindows=[]\nwin32gui.EnumWindows(lambda h,p: windows.append(h), None)\nclick_button('OK')"},
        "pass",
    ),
    (
        "R023",
        "code_run",
        {"script": "follow verify_sop carefully"},
        "fail",
    ),
    (
        "R023",
        "file_read",
        {"path": r"D:\AI\GenericAgent\memory\verify_sop.md"},
        "pass",
    ),
]


class SubagentDelegationGuardTest(unittest.TestCase):
    def setUp(self):
        import ga_audit
        ga_audit._CONSEC_EXEC_HISTORY[:] = []
        self.registry = _load_registry()

    def test_r061_fails_after_three_direct_task_exec_turns(self):
        import ga_audit
        # Simulate 3 consecutive turns of task_exec without subagent
        for _ in range(3):
            ga_audit._CONSEC_EXEC_HISTORY.append({"exec": True, "task_exec": True, "subagent": False})
        checks = _run_checks(self.registry, [{"name": "code_run", "args": {}}], {})
        matched = [check for check in checks if check["id"] == "R061"]
        self.assertTrue(matched, "R061 was not evaluated")
        self.assertEqual("fail", matched[0]["status"])

    def test_r061_passes_when_subagent_used_within_window(self):
        import ga_audit
        # 2 direct turns then a subagent turn → window broken, should pass
        ga_audit._CONSEC_EXEC_HISTORY.append({"exec": True, "task_exec": True, "subagent": False})
        ga_audit._CONSEC_EXEC_HISTORY.append({"exec": True, "task_exec": True, "subagent": False})
        ga_audit._CONSEC_EXEC_HISTORY.append({"exec": True, "task_exec": True, "subagent": True})
        checks = _run_checks(self.registry, [{"name": "web_scan", "args": {}}], {})
        matched = [check for check in checks if check["id"] == "R061"]
        self.assertTrue(matched, "R061 was not evaluated")
        self.assertEqual("pass", matched[0]["status"])


class AuditRegistryDetectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = _load_registry()

    def test_detection_examples(self):
        for rule_id, tool_name, args, expected in CASES:
            with self.subTest(rule_id=rule_id, expected=expected):
                checks = _run_checks(
                    self.registry,
                    [{"name": tool_name, "args": args}],
                    {},
                )
                matched = [check for check in checks if check["id"] == rule_id]
                self.assertTrue(matched, f"{rule_id} was not evaluated")
                self.assertEqual(expected, matched[0]["status"])

    def test_malformed_detection_string_is_skipped(self):
        registry = {
            "constraints": [{"id": "C999", "active": True, "detection": "engine-only-string"}],
            "rules": [],
        }
        checks = _run_checks(registry, [{"name": "code_run", "args": {"script": "print(1)"}}], {})
        matched = [check for check in checks if check["id"] == "C999"]
        self.assertFalse(matched)


class OnTurnEndR061Test(unittest.TestCase):
    """Verify R061 through real _on_turn_end → audit_log chain."""

    def setUp(self):
        import ga_audit
        ga_audit._CONSEC_EXEC_HISTORY[:] = []
        ga_audit._BUDGET_SESSION_TASK_ID = None
        ga_audit._BUDGET_SESSION_LAST_TURN = None
        try:
            import budget_tracker
            budget_tracker.tracker.reset()
        except Exception:
            pass
        self._orig_dir = ga_audit._DASHBOARD_DIR
        self._orig_log = ga_audit._AUDIT_LOG_PATH
        self._orig_agent_ref = ga_audit._agent_ref
        self._orig_subagent_available = ga_audit._SUBAGENT_AVAILABLE
        self._tmp = tempfile.mkdtemp()
        import pathlib
        ga_audit._DASHBOARD_DIR = pathlib.Path(self._tmp)
        ga_audit._AUDIT_LOG_PATH = pathlib.Path(self._tmp) / "audit_log.json"

    def tearDown(self):
        import ga_audit
        ga_audit._DASHBOARD_DIR = self._orig_dir
        ga_audit._AUDIT_LOG_PATH = self._orig_log
        ga_audit._agent_ref = self._orig_agent_ref
        ga_audit._SUBAGENT_AVAILABLE = self._orig_subagent_available
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _last_event(self):
        import ga_audit
        log_path = ga_audit._AUDIT_LOG_PATH
        if not log_path.exists():
            return None
        events = json.loads(log_path.read_text(encoding="utf-8"))
        return events[-1] if events else None

    def test_install_assigns_task_id_to_turn_events(self):
        """install() wires put_task/get hooks so turn events carry the active task_id."""
        import ga_audit

        class DummyAgent:
            def __init__(self):
                self.task_queue = queue.Queue()

            def put_task(self, text, source="user", images=None):
                display_queue = queue.Queue()
                self.task_queue.put({"text": text, "source": source, "images": images, "output": display_queue})
                return display_queue

        agent = DummyAgent()
        self.assertTrue(ga_audit.install(agent))
        agent.put_task("check monitor panel", source="user")
        item = agent.task_queue.get()
        self.assertIn("task_id", item)
        self.assertTrue(item["task_id"])

        agent._turn_end_hooks["ga_audit"]({"turn": 1, "summary": "", "tool_calls": []})
        event = self._last_event()
        self.assertIsNotNone(event, "No event in audit log")
        self.assertEqual(item["task_id"], event.get("task_id"))

    def test_r061_soft_block_via_on_turn_end(self):
        """3 consecutive task_exec turns without subagent → R061 soft_block in audit log."""
        for turn_idx in range(1, 4):
            ctx = {"turn": turn_idx, "summary": "", "tool_calls": [{"name": "code_run", "args": {"script": "pass"}}]}
            _on_turn_end(ctx)
        event = self._last_event()
        self.assertIsNotNone(event, "No event in audit log")
        r061 = [c for c in event.get("constraint_checks", []) if c["id"] == "R061"]
        self.assertTrue(r061, "R061 not in constraint_checks")
        self.assertEqual("fail", r061[0]["status"], f"Expected fail but got {r061[0]['status']}")
        self.assertEqual("soft_block", r061[0].get("severity"))
        self.assertEqual(3, r061[0].get("consecutive_exec_turns"))

    def test_r061_hard_block_via_on_turn_end(self):
        """4 consecutive task_exec turns without subagent → R061 hard_block metadata."""
        for turn_idx in range(1, 5):
            ctx = {"turn": turn_idx, "summary": "", "tool_calls": [{"name": "code_run", "args": {"script": "pass"}}]}
            _on_turn_end(ctx)
        event = self._last_event()
        self.assertIsNotNone(event, "No event in audit log")
        r061 = [c for c in event.get("constraint_checks", []) if c["id"] == "R061"]
        self.assertTrue(r061, "R061 not in constraint_checks")
        self.assertEqual("fail", r061[0]["status"], f"Expected fail but got {r061[0]['status']}")
        self.assertEqual("hard_block", r061[0].get("severity"))
        self.assertEqual(4, r061[0].get("consecutive_exec_turns"))

    def test_r061_pass_when_subagent_breaks_streak(self):
        """Subagent call within window → R061 pass in audit log."""
        # 2 task_exec turns, then a subagent turn, then 1 more task_exec
        for turn_idx, tc in enumerate([
            [{"name": "code_run", "args": {"script": "pass"}}],
            [{"name": "web_execute_js", "args": {"script": "document.body.click()"}}],
            [{"name": "subagent", "args": {}}],
            [{"name": "file_patch", "args": {"path": "x", "old_content": "a", "new_content": "b"}}],
        ], start=1):
            ctx = {"turn": turn_idx, "summary": "", "tool_calls": tc}
            _on_turn_end(ctx)
        event = self._last_event()
        self.assertIsNotNone(event, "No event in audit log")
        r061 = [c for c in event.get("constraint_checks", []) if c["id"] == "R061"]
        self.assertTrue(r061, "R061 not in constraint_checks")
        self.assertEqual("pass", r061[0]["status"], f"Expected pass but got {r061[0]['status']}")

    def test_r061_ignores_read_only_tools(self):
        """Read-only file_read/web_scan do not count as R061 direct execution turns."""
        for turn_idx, tc in enumerate([
            [{"name": "file_read", "args": {"path": "x"}}],
            [{"name": "web_scan", "args": {}}],
            [{"name": "file_read", "args": {"path": "y"}}],
            [{"name": "web_scan", "args": {}}],
        ], start=1):
            ctx = {"turn": turn_idx, "summary": "", "tool_calls": tc}
            _on_turn_end(ctx)
        event = self._last_event()
        self.assertIsNotNone(event, "No event in audit log")
        r061 = [c for c in event.get("constraint_checks", []) if c["id"] == "R061"]
        self.assertTrue(r061, "R061 not in constraint_checks")
        self.assertEqual("pass", r061[0]["status"], f"Expected pass but got {r061[0]['status']}")

    def test_r061_pre_tool_guard_dry_run_soft_and_hard(self):
        _CONSEC_EXEC_HISTORY.clear()
        _CONSEC_EXEC_HISTORY.extend([
            {"exec": True, "task_exec": True, "subagent": False},
            {"exec": True, "task_exec": True, "subagent": False},
        ])
        soft = preview_r061_pre_tool_guard([{"tool_name": "code_run", "args": {}}])
        self.assertIsNotNone(soft)
        self.assertEqual("dry_run", soft["mode"])
        self.assertEqual("soft_block", soft["severity"])
        self.assertEqual(3, soft["consecutive_exec_turns"])

        _CONSEC_EXEC_HISTORY.append({"exec": True, "task_exec": True, "subagent": False})
        hard = preview_r061_pre_tool_guard([{"tool_name": "file_patch", "args": {}}])
        self.assertIsNotNone(hard)
        self.assertEqual("hard_block", hard["severity"])
        self.assertEqual(4, hard["consecutive_exec_turns"])

    def test_r061_pre_tool_guard_no_warning_for_subagent(self):
        _CONSEC_EXEC_HISTORY.clear()
        _CONSEC_EXEC_HISTORY.extend([
            {"exec": True, "task_exec": True, "subagent": False},
            {"exec": True, "task_exec": True, "subagent": False},
        ])
        warning = preview_r061_pre_tool_guard([{"tool_name": "subagent", "args": {}}])
        self.assertIsNone(warning)

    def test_budget_fields_are_complete_via_on_turn_end(self):
        _on_turn_end({
            "turn": 1,
            "summary": "budget regression probe",
            "tool_calls": [{"name": "code_run", "args": {"script": "print(1)"}}],
            "tokens": {"input": 120, "output": 30, "cached": 10},
        })
        event = self._last_event()
        self.assertIsNotNone(event, "No event in audit log")
        self.assertEqual({"input": 120, "output": 30, "cached": 10}, event.get("tokens"))
        budget = event.get("budget") or {}
        for key in [
            "used_pct", "token_pct", "turn_pct", "tier", "signal",
            "total_tokens", "effective_cost", "max_tokens", "turns", "max_turns",
            "remaining_tokens", "remaining_turns",
        ]:
            self.assertIn(key, budget)
        self.assertNotEqual("error", budget.get("signal"))
        self.assertNotEqual("unknown", budget.get("tier"))

    def test_budget_resets_when_turn_counter_restarts(self):
        _on_turn_end({
            "turn": 1,
            "summary": "old task",
            "tool_calls": [],
            "tokens": {"input": 30000, "output": 30000, "cached": 0},
        })
        _on_turn_end({
            "turn": 2,
            "summary": "old task",
            "tool_calls": [],
            "tokens": {"input": 30000, "output": 30000, "cached": 0},
        })
        _on_turn_end({
            "turn": 1,
            "summary": "new task",
            "tool_calls": [],
            "tokens": {"input": 1000, "output": 0, "cached": 0},
        })
        event = self._last_event()
        budget = event.get("budget", {})
        self.assertEqual(1000, budget.get("total_tokens"))
        self.assertEqual(1, budget.get("turns"))
        self.assertLess(budget.get("used_pct", 999), 100)

    def test_budget_resets_pre_session_singleton_accumulation(self):
        import ga_audit
        import budget_tracker
        budget_tracker.tracker.start_session("previous in-memory task")
        budget_tracker.tracker.record_turn()
        budget_tracker.tracker.record(90000, 0, 0)
        ga_audit._BUDGET_SESSION_TASK_ID = "previous in-memory task"
        ga_audit._BUDGET_SESSION_LAST_TURN = 9
        _on_turn_end({
            "turn": 1,
            "summary": "new task after process restart boundary",
            "tool_calls": [],
            "tokens": {"input": 1000, "output": 0, "cached": 0},
        })
        event = self._last_event()
        budget = event.get("budget", {})
        self.assertEqual(1000, budget.get("total_tokens"))
        self.assertEqual(1, budget.get("turns"))
        self.assertLess(budget.get("used_pct", 999), 100)

    def test_r004_r061_fail_without_exemption_via_on_turn_end(self):
        for turn_idx in range(1, 4):
            _on_turn_end({
                "turn": turn_idx,
                "summary": "direct execution regression probe",
                "tool_calls": [{"name": "code_run", "args": {"script": "print(1)"}}],
            })
        event = self._last_event()
        checks = {c["id"]: c for c in event.get("constraint_checks", [])}
        self.assertEqual("fail", checks["R004"]["status"])
        self.assertEqual("fail", checks["R061"]["status"])

    def test_r004_r061_pass_with_explicit_minimal_verification_exemption(self):
        for turn_idx in range(1, 4):
            _on_turn_end({
                "turn": turn_idx,
                "summary": "最小验证豁免：隔离验证 R004/R061",
                "tool_calls": [{"name": "code_run", "args": {"script": "print(1)"}}],
            })
        event = self._last_event()
        checks = {c["id"]: c for c in event.get("constraint_checks", [])}
        self.assertEqual("pass", checks["R004"]["status"])
        self.assertEqual("pass", checks["R061"]["status"])

    def test_exemption_keyword_in_negated_test_label_does_not_exempt(self):
        for turn_idx in range(1, 4):
            _on_turn_end({
                "turn": turn_idx,
                "summary": "R004_R061_no_exemption_case",
                "tool_calls": [{"name": "code_run", "args": {"script": "print(1)"}}],
            })
        event = self._last_event()
        checks = {c["id"]: c for c in event.get("constraint_checks", [])}
        self.assertEqual("fail", checks["R004"]["status"])
        self.assertEqual("fail", checks["R061"]["status"])


if __name__ == "__main__":
    unittest.main()
