import json
import os
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from semantic_audit import evaluate_event, evaluate_events, load_semantic_rules  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "audit_semantic")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return json.load(f)


class SemanticAuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_semantic_rules()

    def test_claim_without_evidence_warns(self):
        findings = evaluate_event(load_fixture("claim_without_evidence.json"), self.rules)
        self.assertEqual(["R007"], [f["rule_id"] for f in findings])
        self.assertEqual("warning", findings[0]["severity"])
        self.assertGreaterEqual(findings[0]["confidence"], 0.85)
        self.assertTrue(findings[0]["evidence_refs"])

    def test_claim_with_evidence_suppresses(self):
        findings = evaluate_event(load_fixture("claim_with_evidence.json"), self.rules)
        self.assertNotIn("R007", [f["rule_id"] for f in findings])

    def test_complex_task_without_plan_is_advisory(self):
        findings = evaluate_event(load_fixture("complex_without_plan.json"), self.rules)
        by_id = {f["rule_id"]: f for f in findings}
        self.assertIn("R012", by_id)
        self.assertEqual("advisory", by_id["R012"]["severity"])
        self.assertLess(by_id["R012"]["confidence"], 0.85)

    def test_repeated_failure_without_debug_warns(self):
        findings = evaluate_event(load_fixture("repeated_failure_without_debug.json"), self.rules)
        self.assertIn("R033", [f["rule_id"] for f in findings])

    def test_evaluate_events_adds_event_index(self):
        events = [
            load_fixture("claim_with_evidence.json"),
            load_fixture("claim_without_evidence.json"),
        ]
        findings = evaluate_events(events, self.rules)
        self.assertEqual(1, findings[0]["event_index"])

    def test_phase1_never_emits_fail(self):
        findings = evaluate_event(load_fixture("claim_without_evidence.json"), self.rules)
        self.assertNotIn("fail", [f["severity"] for f in findings])


if __name__ == "__main__":
    unittest.main()
