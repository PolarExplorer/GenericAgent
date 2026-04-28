"""Regression tests for llmcore history compaction."""
import json
import os
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _msg(role, text):
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _history_chars(history):
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in history)


class TestLLMCoreContextCompaction(unittest.TestCase):
    def _make_long_history(self):
        history = []
        for i in range(36):
            fact = f"FACT_{i:02d}"
            history.append(_msg("user", f"User turn {i}: remember {fact}. " + ("u" * 820)))
            history.append(_msg("assistant", f"Assistant turn {i}: acknowledged {fact}. " + ("a" * 820)))
        history.append(_msg("user", "FINAL_TASK: answer using retained facts."))
        return history

    def test_trim_compacts_old_history_into_digest_and_preserves_recent_context(self):
        from llmcore import _fix_messages, _msgs_claude2oai, trim_messages_history

        history = self._make_long_history()
        before = _history_chars(history)

        trim_messages_history(history, context_win=8000)

        combined = json.dumps(history, ensure_ascii=False)
        self.assertLess(_history_chars(history), before)
        self.assertEqual(history[0]["role"], "user")
        self.assertIn("[GA_CONTEXT_DIGEST]", combined)
        self.assertIn("FACT_00", combined)
        self.assertIn("FACT_18", combined)
        self.assertIn("FACT_35", combined)
        self.assertIn("FINAL_TASK", combined)

        fixed = _fix_messages(history)
        self.assertEqual(fixed[0]["role"], "user")
        converted = _msgs_claude2oai(fixed)
        self.assertTrue(converted)
        self.assertEqual(converted[0]["role"], "user")

    def test_fallback_trimming_keeps_digest_when_still_over_target(self):
        from llmcore import trim_messages_history

        history = self._make_long_history()
        trim_messages_history(history, context_win=3000)

        combined = json.dumps(history, ensure_ascii=False)
        self.assertEqual(history[0]["role"], "user")
        self.assertIn("[GA_CONTEXT_DIGEST]", combined)
        self.assertIn("FINAL_TASK", combined)


if __name__ == "__main__":
    unittest.main()
