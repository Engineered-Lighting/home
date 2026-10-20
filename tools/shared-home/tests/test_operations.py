from pathlib import Path
import tempfile
import unittest

from home_shared.operations import Opportunity, compare_interactive, evaluate_pilot, preservation_gaps, restoration_readable, source_manifest


class OperationsTests(unittest.TestCase):
    def test_no_rejections_only_pass(self):
        opportunities = [Opportunity(str(i), "arrival", i * 30 + 5, i * 30, None, None, "rejected", True) for i in range(100)]
        result = evaluate_pilot(opportunities, stationary_minutes=[20, 20, 20], reviewed_samples=30,
                                materially_accurate_samples=30, wrong_site_disclosures=0, fabricated_identity_assertions=0)
        self.assertFalse(result["passed"])
        self.assertEqual(result["scheduled"], 100)
        self.assertEqual(result["valid"], 0)
        self.assertFalse(result["deployment_authorized"])

    def test_positive_pilot_and_failure_denominator(self):
        opportunities = [Opportunity(str(i), "arrival" if i < 20 else "refresh", i * 30 + 5, i * 30,
                                     i * 30 + 10, i * 30 + 6, "ok" if i < 95 else "timeout", i < 20) for i in range(100)]
        kwargs = dict(stationary_minutes=[20, 21, 22], reviewed_samples=30, materially_accurate_samples=27,
                      wrong_site_disclosures=0, fabricated_identity_assertions=0)
        self.assertTrue(evaluate_pilot(opportunities, **kwargs)["passed"])
        kwargs["wrong_site_disclosures"] = 1
        self.assertFalse(evaluate_pilot(opportunities, **kwargs)["passed"])
        with self.assertRaises(ValueError):
            evaluate_pilot(opportunities + opportunities[:1], **kwargs)

    def test_old_frames_and_all_failed_overlap_do_not_pass(self):
        opportunities = [Opportunity(str(i), "arrival" if i < 20 else "refresh", i * 30 + 5, i * 30,
                                     i * 30 + 10, i * 30 - 170, "ok", i < 20) for i in range(100)]
        kwargs = dict(stationary_minutes=[20, 20, 20], reviewed_samples=30, materially_accurate_samples=30,
                      wrong_site_disclosures=0, fabricated_identity_assertions=0)
        self.assertEqual(evaluate_pilot(opportunities, **kwargs)["valid"], 0)
        opportunities = [Opportunity(str(i), "arrival", i * 30 + 5, i * 30,
                                     i * 30 + 10, i * 30 + 6, "timeout" if i < 20 else "ok", i < 20) for i in range(1000)]
        self.assertFalse(evaluate_pilot(opportunities, **kwargs)["passed"])

    def test_restore_waits_for_current_deletion_history(self):
        self.assertFalse(restoration_readable(backup_deletion_generation=2, authoritative_deletion_generation=4,
                                             applied_deletion_generation=2, source_policy_revalidated=True, recovery_set_verified=True))
        self.assertTrue(restoration_readable(backup_deletion_generation=2, authoritative_deletion_generation=4,
                                            applied_deletion_generation=4, source_policy_revalidated=True, recovery_set_verified=True))
        self.assertGreater(len(preservation_gaps({})), 10)

    def test_hash_allowlist_and_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.py").write_text("print('fixture')", encoding="utf8")
            receipt = source_manifest(root, ["a.py"])
            self.assertEqual(len(receipt["files"][0]["sha256"]), 64)
            self.assertFalse(receipt["complete_deployed_baseline"])
            for invalid in ("../escape.py", ".env", ".git/config", "data.sqlite"):
                with self.assertRaises(ValueError):
                    source_manifest(root, [invalid])

    def test_p95_regression_and_error_rate(self):
        report = compare_interactive([1] * 100, [1.3] * 100, baseline_errors=0, pilot_errors=2,
                                     baseline_attempts=100, pilot_attempts=100)
        self.assertFalse(report["latency_passed"])
        self.assertFalse(report["error_rate_passed"])


if __name__ == "__main__":
    unittest.main()
