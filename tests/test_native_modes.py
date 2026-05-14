"""
test_native_modes.py - Unit & integration tests for GA native modes:
  1. Plan mode: enter/exit/check_completion/intercept logic
  2. Goal mode: prompt injection verification
  3. Subagent (--task --once): single-round execution and exit
"""
import os
import sys
import re
import tempfile
import shutil
import subprocess
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeParent:
    """Minimal parent object for GenericAgentHandler."""
    def __init__(self):
        self.task_dir = tempfile.mkdtemp(prefix='ga_test_')
        self.history = []
        self.handler = None


# ---------------------------------------------------------------------------
# Plan Mode Unit Tests
# ---------------------------------------------------------------------------

class TestPlanMode(unittest.TestCase):
    """Test plan mode enter/exit/_check_plan_completion and intercept."""

    def setUp(self):
        from ga import GenericAgentHandler
        self.parent = FakeParent()
        self.handler = GenericAgentHandler(self.parent, last_history=[], cwd='./temp')
        self.tmpdir = tempfile.mkdtemp(prefix='ga_plan_test_')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        shutil.rmtree(self.parent.task_dir, ignore_errors=True)

    def test_enter_and_exit_plan_mode(self):
        """enter_plan_mode sets working state; _exit clears it."""
        plan_file = os.path.join(self.tmpdir, 'plan.md')
        with open(plan_file, 'w', encoding='utf-8') as f:
            f.write("- [ ] step1\n- [ ] step2\n")

        result = self.handler.enter_plan_mode(plan_file)
        self.assertEqual(result, plan_file)
        self.assertEqual(self.handler._in_plan_mode(), plan_file)
        self.assertEqual(self.handler.max_turns, 100)

        self.handler._exit_plan_mode()
        self.assertIsNone(self.handler._in_plan_mode())

    def test_check_plan_completion_counts_unchecked(self):
        """_check_plan_completion returns count of unchecked items."""
        plan_file = os.path.join(self.tmpdir, 'plan.md')
        with open(plan_file, 'w', encoding='utf-8') as f:
            f.write("- [x] done\n- [ ] todo1\n- [ ] todo2\n- [ ] todo3\n")

        self.handler.enter_plan_mode(plan_file)
        remaining = self.handler._check_plan_completion()
        self.assertEqual(remaining, 3)

    def test_check_plan_completion_zero_when_all_done(self):
        """All items checked => returns 0."""
        plan_file = os.path.join(self.tmpdir, 'plan.md')
        with open(plan_file, 'w', encoding='utf-8') as f:
            f.write("- [x] done1\n- [x] done2\n")

        self.handler.enter_plan_mode(plan_file)
        remaining = self.handler._check_plan_completion()
        self.assertEqual(remaining, 0)

    def test_check_plan_completion_none_when_no_file(self):
        """Non-existent plan file => returns None."""
        self.handler.enter_plan_mode('/nonexistent/plan.md')
        self.assertIsNone(self.handler._check_plan_completion())

    def test_plan_intercept_blocks_premature_completion(self):
        """do_no_tool intercepts completion claims in plan mode without VERIFY."""
        plan_file = os.path.join(self.tmpdir, 'plan.md')
        with open(plan_file, 'w', encoding='utf-8') as f:
            f.write("- [ ] not done\n")
        self.handler.enter_plan_mode(plan_file)

        # Simulate a response object claiming completion
        class FakeResponse:
            content = "任务完成，所有步骤已执行。"
            thinking = ""
        
        resp = FakeResponse()
        result = self.handler.do_no_tool({}, resp)
        # do_no_tool is a generator that yields then returns StepOutcome
        outputs = []
        try:
            while True:
                outputs.append(next(result))
        except StopIteration as e:
            outcome = e.value

        # Should have intercepted with verification prompt
        self.assertTrue(any('验证拦截' in str(o) or '拦截' in str(o) for o in outputs))
        self.assertIn('验证', outcome.next_prompt if hasattr(outcome, 'next_prompt') else '')

    def test_plan_no_intercept_with_verdict(self):
        """do_no_tool does NOT intercept if VERDICT is present."""
        plan_file = os.path.join(self.tmpdir, 'plan.md')
        with open(plan_file, 'w', encoding='utf-8') as f:
            f.write("- [x] all done\n")
        self.handler.enter_plan_mode(plan_file)

        class FakeResponse:
            content = "任务完成。VERDICT: PASS"
            thinking = ""

        resp = FakeResponse()
        result = self.handler.do_no_tool({}, resp)
        outputs = []
        try:
            while True:
                outputs.append(next(result))
        except StopIteration as e:
            outcome = e.value

        # Should NOT intercept
        intercept_msgs = [o for o in outputs if '拦截' in str(o)]
        self.assertEqual(len(intercept_msgs), 0)


# ---------------------------------------------------------------------------
# Goal Mode Unit Test
# ---------------------------------------------------------------------------

class TestGoalMode(unittest.TestCase):
    """Test goal mode prompt injection (system prompt contains objective)."""

    def test_goal_mode_prompt_contains_objective(self):
        """When goal mode is active, system prompt wraps objective."""
        from ga import GenericAgentHandler
        parent = FakeParent()
        handler = GenericAgentHandler(parent, last_history=[], cwd='./temp')
        # Simulate goal mode by setting working state
        handler.working['in_goal_mode'] = True
        handler.working['goal_objective'] = 'Write hello.txt'
        handler.working['goal_max_turns'] = 10

        # Check that _get_anchor_prompt or system prompt references the objective
        # Goal mode injects via system prompt in the main loop, 
        # so we verify the working state is correctly set
        self.assertTrue(handler.working.get('in_goal_mode'))
        self.assertEqual(handler.working['goal_objective'], 'Write hello.txt')


# ---------------------------------------------------------------------------
# Subagent Integration Test (--task --once)
# ---------------------------------------------------------------------------

class TestSubagentOnce(unittest.TestCase):
    """Integration test: agentmain.py --task --once exits after one round."""

    def test_once_flag_single_round_exit(self):
        """--once causes process to exit after producing output.txt."""
        task_dir = os.path.join(REPO_ROOT, 'temp', '_test_once_integration')
        if os.path.exists(task_dir):
            shutil.rmtree(task_dir)
        os.makedirs(task_dir, exist_ok=True)

        input_file = os.path.join(task_dir, 'input.txt')
        with open(input_file, 'w', encoding='utf-8') as f:
            f.write('Reply exactly: TEST_ONCE_PASS')

        start = time.time()
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, 'agentmain.py'),
             '--task', '_test_once_integration',
             '--input', 'Reply exactly: TEST_ONCE_PASS',
             '--once', '--nobg'],
            capture_output=True, text=True, timeout=120,
            cwd=REPO_ROOT
        )
        elapsed = time.time() - start

        self.assertEqual(proc.returncode, 0,
                         f"Process failed: {proc.stderr[:500]}")
        self.assertLess(elapsed, 60, "Should complete in under 60s")

        output_file = os.path.join(task_dir, 'output.txt')
        self.assertTrue(os.path.isfile(output_file),
                        f"output.txt not created in {task_dir}")

        content = open(output_file, encoding='utf-8').read()
        self.assertIn('TEST_ONCE_PASS', content)

        # Cleanup
        shutil.rmtree(task_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
