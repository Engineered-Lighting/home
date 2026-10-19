"""Original-frame expiry and cleanup regressions; authored HTTP/clock doubles."""
from copy import deepcopy
import unittest

import test_lab_whole_stack_trial as baseline

trial = baseline.trial


class CaptureExpiryTrialTests(unittest.TestCase):
    def delayed_descriptor(self, *, expired="rgb", corrupt_sample=None):
        lab = baseline.Lab()
        descriptors, samples = [], []
        waiting_sample = False

        def model_lease(event):
            if event["kind"] == "perception.output" and event["payload"].get("arm") == "b" and lab.clock.now == 100:
                event["payload"]["valid_until"] = 100 + (.912 if expired in {"model", "both"} else 1.412)
            return event

        lab.mutate = model_lease

        def api(method, suffix, body=None):
            nonlocal waiting_sample
            response = lab(method, suffix, body)
            if suffix.endswith("/live") and waiting_sample:
                waiting_sample = False
                samples.append(deepcopy(response))
                if corrupt_sample is not None:
                    corrupt_sample(response)
            if "/descriptor?" in suffix:
                if not descriptors:
                    response["metadata"]["valid_until"] = 100 + (.912 if expired in {"rgb", "both"} else 1.412)
                    # Integer-second delay keeps the incumbent fixture's JEV
                    # schedule intact. The selected lease expires88ms before
                    # receipt; the other lease can still have412ms remaining.
                    lab.clock.sleep(1)
                    waiting_sample = True
                descriptors.append(deepcopy(response))
            return response

        result = trial.run_trial(api, trial.Plan(baseline.REQUEST), lab.retain,
                                 monotonic=lambda: lab.clock.now, sleep=lab.clock.sleep)
        return lab, result, descriptors, samples

    def test_descriptor_delay_rechecks_both_original_exclusive_leases_at_fresh_server_time(self):
        for expired in ("rgb", "model", "both"):
            with self.subTest(expired=expired):
                lab, result, descriptors, samples = self.delayed_descriptor(expired=expired)
                self.assertTrue(result["passed"], result)
                self.assertNotEqual(result["representative_capture"]["media_id"], descriptors[0]["id"])
                self.assertEqual(result["expired_capture_candidates"], 1)
                self.assertEqual(len(descriptors), 2)
                self.assertEqual(samples[0]["server_time"], 101)
                expected = 100 + (.912 if expired in {"rgb", "both"} else 1.412)
                self.assertEqual(descriptors[0]["metadata"]["valid_until"], expected)
                self.assertEqual(descriptors[0]["metadata"]["source_timing_receipt"]["observed_at_unix_ns"], 1792208115779811123)
                self.assertTrue(result["cleanup"]["confirmed"])
                self.assertEqual(lab.stop_at, 131)

    def test_fresh_capture_clock_sample_cannot_change_media_generation(self):
        def changed(response):
            response["session"]["generation"] = 999
        _, result, descriptors, _ = self.delayed_descriptor(corrupt_sample=changed)
        self.assertFalse(result["passed"])
        self.assertIn("error", result)
        self.assertIsNone(result["representative_capture"])
        self.assertEqual(len(descriptors), 1)
        self.assertTrue(result["cleanup"]["confirmed"], result)

    def test_fresh_capture_clock_sample_requires_same_top_level_scope(self):
        for key, wrong in (("site_id", "other-site"), ("session_id", "other-session")):
            with self.subTest(key=key):
                def changed(response):
                    response[key] = wrong
                _, result, _, _ = self.delayed_descriptor(corrupt_sample=changed)
                self.assertFalse(result["passed"])
                self.assertIn("error", result)
                self.assertIsNone(result["representative_capture"])
                self.assertTrue(result["cleanup"]["confirmed"], result)

    def test_fresh_capture_clock_sample_rejects_missing_or_backward_server_time(self):
        for at in (None, 99.9):
            with self.subTest(server_time=at):
                def changed(response):
                    response["server_time"] = at
                _, result, _, _ = self.delayed_descriptor(corrupt_sample=changed)
                self.assertFalse(result["passed"])
                self.assertEqual(result["error"], "original_capture_clock_binding_mismatch")
                self.assertIsNone(result["representative_capture"])
                self.assertTrue(result["cleanup"]["confirmed"], result)

    def run_lab(self, *, expire_all=False, wrong_binding=False):
        lab = baseline.Lab()
        descriptors = []

        def short_model_lease(event):
            if event["kind"] == "perception.output" and event["payload"].get("arm") == "b":
                # The real failure had usable detector output after the RGB
                # descriptor's independent original lease had already expired.
                event["payload"]["valid_until"] = lab.clock.now + .412
            return event

        lab.mutate = short_model_lease

        def api(method, suffix, body=None):
            response = lab(method, suffix, body)
            if "/descriptor?" in suffix:
                if expire_all or not descriptors:
                    response["metadata"]["valid_until"] = lab.clock.now - .088
                if wrong_binding:
                    response["generation"] = 999
                descriptors.append(deepcopy(response))
            return response

        result = trial.run_trial(api, trial.Plan(baseline.REQUEST), lab.retain,
                                 monotonic=lambda: lab.clock.now, sleep=lab.clock.sleep)
        return lab, result, descriptors

    def test_eighty_eight_ms_expired_rgb_does_not_abort_current_detector_or_refresh_lease(self):
        lab, result, descriptors = self.run_lab()
        self.assertTrue(result["passed"], result)
        self.assertNotIn("error", result)
        self.assertEqual(lab.stop_at, 130)
        self.assertTrue(result["cleanup"]["confirmed"])
        self.assertTrue(result["event_window"]["complete"])
        self.assertEqual(len(descriptors), 2)
        self.assertEqual(result["expired_capture_candidates"], 1)
        self.assertEqual(descriptors[0]["metadata"]["valid_until"], 100 - .088)
        first = result["arm_b_outputs"][0]
        self.assertTrue(first["positive"])
        self.assertEqual(first["valid_until"], 100 + .412)
        self.assertEqual(result["representative_capture"]["media_id"], "rgb-101.0")
        self.assertEqual(result["representative_capture"]["effective_valid_until"], 101 + .412)
        self.assertEqual(result["representative_capture"]["source_observed_at_unix_ns"], 1792208115779811123)
        retained = [value["body"] for kind, value in lab.retained
                    if kind == "response" and "/descriptor?" in value["suffix"]]
        self.assertEqual(retained, descriptors)
        self.assertTrue(all("/media/" not in suffix or "/descriptor?" in suffix
                            for _, suffix, _ in lab.calls))

    def test_all_expired_rgb_descriptors_never_pass_and_stop_still_confirms(self):
        lab, result, descriptors = self.run_lab(expire_all=True)
        self.assertFalse(result["passed"])
        self.assertIsNone(result["representative_capture"])
        self.assertNotIn("error", result)
        self.assertTrue(any(row["positive"] for row in result["arm_b_outputs"]))
        self.assertTrue(any(row["fresh"] for row in result["pc_facts"]))
        self.assertTrue(result["cleanup"]["confirmed"], result)
        self.assertEqual(lab.stop_at, 130)
        self.assertEqual(len(descriptors), 30)
        self.assertEqual(result["expired_capture_candidates"], 30)
        self.assertEqual(result["cleanup"]["local_drain"], "drained")

    def test_real_binding_error_is_preserved_but_cleanup_never_reprojects_it(self):
        lab, result, descriptors = self.run_lab(wrong_binding=True)
        self.assertFalse(result["passed"])
        self.assertEqual(result["error"], "original_rgb_descriptor_binding_mismatch")
        self.assertIsNone(result["representative_capture"])
        self.assertEqual(lab.session["state"], "stopped")
        self.assertTrue(result["cleanup"]["confirmed"], result)
        self.assertEqual(result["cleanup"]["session_state"], "stopped")
        self.assertTrue(result["cleanup"]["both_workers_terminal"])
        self.assertEqual(result["cleanup"]["local_drain"], "drained")
        self.assertEqual(len(descriptors), 1, "cleanup fetched the known-bad representative again")
        self.assertEqual(len([call for call in lab.calls if call[0] == "POST" and call[1].endswith("/stop")]), 1)
        self.assertNotIn("cleanup_error", result)
        cleanup_originals = [row for kind, value in lab.retained
                            if kind == "response" and "/events?" in value["suffix"]
                            for row in value["body"].get("items", []) if row["kind"] == "workers.cleanup"]
        self.assertTrue(cleanup_originals, "terminal worker/drain originals were not retained")

    def test_expired_descriptor_cannot_hide_a_real_generation_mismatch(self):
        lab = baseline.Lab()
        lab.retain("plan", {"request": trial.Plan(baseline.REQUEST).body()})
        session = lab("POST", "/sessions", trial.Plan(baseline.REQUEST).body())
        lab("GET", "/sessions/" + session["id"] + "/live")
        original = next(e for e in lab.events if e["kind"] == "perception.output" and e["payload"].get("arm") == "b")
        row = trial.positive_projection(original, baseline.PROFILE, server_time=lab.clock.now)
        descriptor = lab("GET", "/sessions/" + session["id"] + "/media/" + row["media_ids"][0] + "/descriptor?generation=0")
        descriptor["generation"] = 999
        descriptor["metadata"]["valid_until"] = lab.clock.now - .088
        with self.assertRaisesRegex(ValueError, "original_rgb_descriptor_binding_mismatch"):
            trial.capture_projection(descriptor, row, session, lab.clock.now)


if __name__ == "__main__":
    unittest.main()
