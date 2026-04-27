import os
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ga_audit import _load_registry, _run_checks  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()