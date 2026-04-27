import os
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import json
import tempfile
import shutil

from ga_audit import _load_registry, _run_checks, _on_turn_end, _CONSEC_EXEC_HISTORY  # noqa: E402


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


class OnTurnEndR061Test(unittest.TestCase):
    """Verify R061 through real _on_turn_end → audit_log chain."""

    def setUp(self):
        import ga_audit
        ga_audit._CONSEC_EXEC_HISTORY[:] = []
        self._orig_dir = ga_audit._DASHBOARD_DIR
        self._orig_log = ga_audit._AUDIT_LOG_PATH
        self._tmp = tempfile.mkdtemp()
        import pathlib
        ga_audit._DASHBOARD_DIR = pathlib.Path(self._tmp)
        ga_audit._AUDIT_LOG_PATH = pathlib.Path(self._tmp) / "audit_log.json"

    def tearDown(self):
        import ga_audit
        ga_audit._DASHBOARD_DIR = self._orig_dir
        ga_audit._AUDIT_LOG_PATH = self._orig_log
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _last_event(self):
        import ga_audit
        log_path = ga_audit._AUDIT_LOG_PATH
        if not log_path.exists():
            return None
        events = json.loads(log_path.read_text(encoding="utf-8"))
        return events[-1] if events else None

    def test_r061_fail_via_on_turn_end(self):
        """3 consecutive task_exec turns without subagent → R061 fail in audit log."""
        for turn_idx in range(1, 5):
            ctx = {"turn": turn_idx, "summary": "", "tool_calls": [{"name": "code_run", "args": {"script": "pass"}}]}
            _on_turn_end(ctx)
        event = self._last_event()
        self.assertIsNotNone(event, "No event in audit log")
        r061 = [c for c in event.get("constraint_checks", []) if c["id"] == "R061"]
        self.assertTrue(r061, "R061 not in constraint_checks")
        self.assertEqual("fail", r061[0]["status"], f"Expected fail but got {r061[0]['status']}")

    def test_r061_pass_when_subagent_breaks_streak(self):
        """Subagent call within window → R061 pass in audit log."""
        # 2 task_exec turns, then a subagent turn, then 1 more task_exec
        for turn_idx, tc in enumerate([
            [{"name": "code_run", "args": {"script": "pass"}}],
            [{"name": "web_scan", "args": {}}],
            [{"name": "subagent", "args": {}}],
            [{"name": "file_read", "args": {"path": "x"}}],
        ], start=1):
            ctx = {"turn": turn_idx, "summary": "", "tool_calls": tc}
            _on_turn_end(ctx)
        event = self._last_event()
        self.assertIsNotNone(event, "No event in audit log")
        r061 = [c for c in event.get("constraint_checks", []) if c["id"] == "R061"]
        self.assertTrue(r061, "R061 not in constraint_checks")
        self.assertEqual("pass", r061[0]["status"], f"Expected pass but got {r061[0]['status']}")


if __name__ == "__main__":
    unittest.main()