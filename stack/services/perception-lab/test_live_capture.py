"""Interface A: real paired-capture entry, local journal HTTP and typed reads."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

import live_capture_fixtures as fixtures
from journal import Journal, PREFIX
from live_test_helpers import LiveHttp
from live_capture import LiveCaptureAdapter


class LiveCaptureTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-paired-capture-")
        self.addCleanup(self.work.cleanup)
        self.now = fixtures.NOW + .2
        self.config, self.policy, self.method, self.calibration, self.grant = fixtures.setup()
        self.server = self.journal = None
        self.adapter = LiveCaptureAdapter(self.config, source_id=fixtures.SOURCE, revision=fixtures.REVISION,
                                          policy=self.policy, method_ref=self.method, clock=lambda: self.now,
                                          publish=self.publish)
        self.journal = Journal(Path(self.work.name) / "synthetic.sqlite", grants=[self.grant], profile=fixtures.PROFILE,
                               live_config=self.config, clock=lambda: self.now,
                               live_receipt_resolver=lambda store, key: self.adapter.resolve_receipt(store, key))
        self.server = LiveHttp(self.journal)

    def tearDown(self):
        if self.server is not None:
            self.server.close()
        if self.journal is not None:
            self.journal.close()

    def publish(self, event):
        status, response = self.server.request("POST", PREFIX + "/events", event, token=fixtures.TOKEN)
        if status != 200:
            raise AssertionError({"http_status": status, "response": response})
        return response

    def snapshot(self):
        status, response = self.server.request("GET", PREFIX + "/snapshot?site_id=synthetic-site&room_id=synthetic-room", token=fixtures.TOKEN)
        self.assertEqual(status, 200, response)
        return response

    def control_clock(self):
        policy = self.config["manifest"]["sources"][fixtures.SOURCE]["control_clock"]
        return {"observed_at": self.now, "clock": {"status": "commissioned", "mapping_id": policy["mapping_id"],
                "mapping_digest": policy["mapping_digest"], "source_epoch": policy["source_epoch"],
                "original_unit": "unix_ns", "original_value": str(round(self.now * 1_000_000_000)), "uncertainty_s": .001}}

    def controlled_adapter(self, publish=None, **options):
        control_clock = options.pop("control_clock", self.control_clock)
        self.adapter = LiveCaptureAdapter(self.config, source_id=fixtures.SOURCE, revision=fixtures.REVISION,
                                          policy=self.policy, method_ref=self.method, clock=lambda: self.now,
                                          publish=self.publish if publish is None else publish,
                                          control_clock=control_clock, **options)

    def replace_grants(self, grants):
        self.server.close()
        self.journal.close()
        self.journal = Journal(Path(self.work.name) / "synthetic.sqlite", grants=grants, profile=fixtures.PROFILE,
                               live_config=self.config, clock=lambda: self.now,
                               live_receipt_resolver=lambda store, key: self.adapter.resolve_receipt(store, key))
        self.server = LiveHttp(self.journal)

    def history(self):
        status, response = self.server.request("GET", PREFIX + "/events?site_id=synthetic-site&room_id=synthetic-room&after=0", token=fixtures.TOKEN)
        self.assertEqual(status, 200, response)
        return response["items"]

    def offer(self, inputs=None, *, received_at=None):
        return self.adapter.offer(*(fixtures.inputs() if inputs is None else inputs),
                                  received_at=fixtures.NOW + .1 if received_at is None else received_at)

    def test_paired_capture_reaches_public_journal_without_inventing_human_geometry(self):
        result = self.offer()
        self.assertEqual(result["status"], "qualified", result)
        self.assertIsNotNone(result["receipt"])
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot["items"]), 1, snapshot)
        saved = snapshot["items"][0]
        self.assertEqual(saved["cursor"], result["receipt"]["cursor"])
        self.assertEqual(saved["committed_at"], fixtures.NOW + .2)
        event = result["publication"]
        payload = saved["payload"]
        self.assertEqual(event["payload"], payload)
        self.assertEqual(payload["schema"], "home-live-geometry-1")
        self.assertEqual(payload["status"], "unobservable")
        self.assertIsNone(payload["person_ref"])
        self.assertIsNone(payload["subject_ref"])
        self.assertTrue(all(part["status"] == "unobservable" for part in payload["parts"].values()))
        self.assertEqual(payload["clock"]["original_unit"], "unix_ns")
        self.assertEqual(payload["clock"]["original_value"], "1800000000000000000")
        self.assertEqual(payload["observed_at"], 1800000000)
        self.assertEqual(sorted(payload["observation_root_times"].values()), [1799999999.99, 1800000000])
        self.assertEqual(payload["sensor_roots"], ["camera:synthetic-oak"])
        self.assertTrue(all(roots == ["camera:synthetic-oak"] for roots in payload["sensor_root_bindings"].values()))
        self.assertEqual(payload["head"]["position"]["capture_seq"], 7)
        self.assertEqual(payload["available_at"], fixtures.NOW + .2)
        self.assertLess(payload["valid_until"], fixtures.NOW + 3)
        refs = payload["original_receipt_refs"]
        self.assertEqual({ref["component_id"] for ref in refs}, {"rgb", "depth"})
        for ref in refs:
            original = self.adapter.resolve_receipt(ref["store_id"], ref["receipt_id"])
            self.assertEqual(original["ref"], ref)
            self.assertEqual(original["capture_seq"], 7)
        point = self.adapter.point_at(result["capture"]["id"], [320, 200])
        self.assertEqual(len(point), 3)
        for actual, expected in zip(point, [1, 3.234, 1.5]):
            self.assertAlmostEqual(actual, expected, places=9)

    def test_exact_retry_and_local_original_receipts_never_refresh_or_mutate(self):
        inputs = fixtures.inputs()
        first = self.offer(inputs)
        self.assertEqual(first["status"], "qualified", first)
        refs = first["publication"]["payload"]["original_receipt_refs"]
        raw = {ref["component_id"]: self.adapter.original_receipt(ref["store_id"], ref["receipt_id"]) for ref in refs}
        self.assertEqual(raw["rgb"]["rgb_receipt"]["observed_at_unix_ns"], 1800000000000000000)
        self.assertEqual(raw["depth"]["raw_device_timestamp_seconds"], 50.19)
        self.assertTrue(all(original["available_at"] == fixtures.NOW + .1 for original in raw.values()))
        raw["depth"]["raw_device_timestamp_seconds"] = 999
        depth_ref = next(ref for ref in refs if ref["component_id"] == "depth")
        self.assertEqual(self.adapter.original_receipt(depth_ref["store_id"], depth_ref["receipt_id"])["raw_device_timestamp_seconds"], 50.19)
        self.now = fixtures.NOW + 5
        again = self.offer(inputs, received_at=self.now)
        self.assertEqual(again, first)
        self.assertEqual(self.snapshot()["cursor"], first["receipt"]["cursor"])
        self.assertEqual(self.snapshot()["items"], [])
        with self.assertRaises(ValueError):
            self.adapter.point_at(first["capture"]["id"], [320, 200])

    def test_changed_original_bytes_under_same_capture_identity_refuse(self):
        inputs = fixtures.inputs()
        first = self.offer(inputs)
        changed = (inputs[0], inputs[1], fixtures.bundle(value=1300), inputs[3])
        refused = self.offer(changed)
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["reason"], "capture_identity_conflict")
        self.assertEqual(refused["withdrawal"], "not_recorded")
        self.assertEqual(self.snapshot()["cursor"], first["receipt"]["cursor"])

    def test_wrong_pair_source_crop_mount_and_exposure_are_explicit_refusals(self):
        data, encoded, numeric, calibration = fixtures.inputs()
        wrong_sequence = fixtures.frame_metadata(sequence=8)
        wrong_stamp = fixtures.frame_metadata(rgb_device_s=50.3)
        wrong_epoch = fixtures.frame_metadata(epoch="different-camera-epoch")
        wrong_crop = fixtures.frame_metadata()
        wrong_crop["frames"]["rgb"]["intrinsics"][0][0] = 505
        wrong_transform = fixtures.frame_metadata()
        wrong_transform["frames"]["depth"]["transformation_valid"] = False
        wrong_calibration = copy.deepcopy(calibration)
        wrong_calibration["eeprom"]["synthetic_device"] = "changed-physical-calibration"
        wrong_receipt = fixtures.header(data, validation_id="different-clock-validation")
        future_receipt = fixtures.header(data, observed_ns=1800000001000000000)
        cases = {
            "rgb_sequence": (data, encoded, fixtures.bundle(wrong_sequence), calibration),
            "rgb_device_stamp": (data, encoded, fixtures.bundle(wrong_stamp), calibration),
            "capture_epoch": (data, encoded, fixtures.bundle(wrong_epoch), calibration),
            "rgb_crop": (data, encoded, fixtures.bundle(wrong_crop), calibration),
            "per_frame_transform": (data, encoded, fixtures.bundle(wrong_transform), calibration),
            "calibration_content": (data, encoded, numeric, wrong_calibration),
            "mount_moved": (data, encoded, fixtures.bundle(value=2200), calibration),
            "mount_unobservable": (data, encoded, fixtures.bundle(value=0), calibration),
            "clock_binding": (data, wrong_receipt, numeric, calibration),
            "future_exposure": (data, future_receipt, numeric, calibration),
            "depth_after_rgb": fixtures.inputs(depth_delta_s=.001),
            "excessive_lookback": fixtures.inputs(depth_delta_s=-.1),
        }
        for label, inputs in cases.items():
            with self.subTest(distinction=label):
                result = self.offer(inputs)
                self.assertEqual(result["status"], "refused", result)
                self.assertTrue(result["reason"])
                self.assertEqual(result["withdrawal"], "not_recorded")
                self.assertIsNone(result["capture"])
                self.assertEqual(self.snapshot()["cursor"], 0)

    def test_packaging_timestamp_cannot_replace_old_exposure(self):
        self.now = fixtures.NOW + 3
        result = self.offer(received_at=self.now)
        self.assertEqual(result["status"], "refused", result)
        self.assertEqual(self.snapshot()["cursor"], 0)
        self.assertEqual(self.snapshot()["items"], [])

    def test_recovered_capture_is_retired_by_a_second_source_failure(self):
        self.controlled_adapter()
        first = self.offer()
        self.assertEqual(first["status"], "qualified", first)
        self.now = fixtures.NOW + 1.2
        failed = self.offer(fixtures.inputs(sequence=8, observed_ns=1800000001000000000, depth_delta_s=.001), received_at=fixtures.NOW + 1.1)
        self.assertEqual(failed["withdrawal"], "recorded", failed)
        self.assertEqual(self.snapshot()["items"], [])
        self.assertEqual(failed["publication"]["payload"]["source_generation"], 2)
        self.now = fixtures.NOW + 2.2
        recovered = self.offer(fixtures.inputs(sequence=9, observed_ns=1800000002000000000), received_at=fixtures.NOW + 2.1)
        self.assertEqual(recovered["status"], "qualified", recovered)
        self.assertEqual(recovered["publication"]["payload"]["source_generation"], 2)
        self.assertEqual(len(self.snapshot()["items"]), 1)
        self.now = fixtures.NOW + 3.2
        failed_again = self.offer(fixtures.inputs(sequence=10, observed_ns=1800000003000000000, depth_delta_s=.001), received_at=fixtures.NOW + 3.1)
        self.assertEqual(failed_again["withdrawal"], "recorded", failed_again)
        self.assertEqual(len(self.snapshot()["items"]), 0)
        self.assertEqual(failed_again["publication"]["payload"]["source_generation"], 3)
        self.assertGreater(failed_again["receipt"]["cursor"], recovered["receipt"]["cursor"])

    def test_lost_control_ack_retries_the_original_barrier_without_new_generation(self):
        lost = []
        def publisher(event):
            receipt = self.publish(event)
            if event["payload"]["status"] == "withdrawn" and not lost:
                lost.append(copy.deepcopy(event))
                raise TimeoutError("synthetic ACK lost after durable journal commit")
            return receipt
        self.controlled_adapter(publisher)
        original = self.offer()
        self.assertEqual(original["status"], "qualified")
        self.now = fixtures.NOW + 1.2
        bad = fixtures.inputs(sequence=8, observed_ns=1800000001000000000, depth_delta_s=.001)
        first = self.offer(bad, received_at=fixtures.NOW + 1.1)
        self.assertEqual(first["withdrawal"], "not_recorded")
        self.assertEqual(self.snapshot()["items"], [])
        with self.assertRaises(ValueError):
            self.adapter.point_at(original["capture"]["id"], [320, 200])
        original_control = self.history()[-1]
        self.now = fixtures.NOW + 1.4
        confirmed = self.offer(bad, received_at=fixtures.NOW + 1.3)
        self.assertEqual(confirmed["withdrawal"], "recorded", confirmed)
        self.assertEqual(confirmed["publication"], lost[0])
        self.assertEqual(confirmed["receipt"]["cursor"], original_control["cursor"])
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(self.snapshot()["revocation_generation"], 1)

    def test_lost_positive_ack_blocks_new_capture_and_confirms_exact_original_after_expiry(self):
        sent = []
        def publisher(event):
            receipt = self.publish(event)
            sent.append(copy.deepcopy(event))
            if len(sent) == 1:
                raise TimeoutError("synthetic positive ACK lost after journal commit")
            return receipt
        self.controlled_adapter(publisher)
        original_inputs = fixtures.inputs()
        unconfirmed = self.offer(original_inputs)
        self.assertEqual(unconfirmed["status"], "refused", unconfirmed)
        self.assertEqual(unconfirmed["reason"], "publication_unconfirmed")
        self.assertEqual(len(self.snapshot()["items"]), 1)
        original_publication = self.history()[0]
        self.now = fixtures.NOW + 1.2
        blocked = self.offer(fixtures.inputs(sequence=8, observed_ns=1800000001000000000), received_at=fixtures.NOW + 1.1)
        self.assertEqual(blocked["reason"], "prior_publication_unconfirmed", blocked)
        self.assertEqual(len(sent), 1)
        self.now = fixtures.NOW + 5
        confirmed = self.offer(original_inputs, received_at=self.now)
        self.assertEqual(confirmed["status"], "qualified", confirmed)
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(confirmed["receipt"]["cursor"], original_publication["cursor"])
        self.assertEqual(confirmed["receipt"]["committed_at"], fixtures.NOW + .2)
        self.assertEqual(confirmed["publication"]["payload"]["available_at"], fixtures.NOW + .2)
        self.assertTrue(all(ref["available_at"] == fixtures.NOW + .1 for ref in confirmed["publication"]["payload"]["original_receipt_refs"]))
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.snapshot()["items"], [])
        with self.assertRaises(ValueError):
            self.adapter.point_at(confirmed["capture"]["id"], [320, 200])

    def test_expiry_equality_refuses_current_numeric_use_and_fresh_admission(self):
        first = self.offer()
        self.assertEqual(first["status"], "qualified", first)
        self.now = first["capture"]["effective_valid_until"]
        with self.assertRaises(ValueError):
            self.adapter.point_at(first["capture"]["id"], [320, 200])
        # A separate adapter has no retry receipt; the same original pair is
        # being offered for first admission at the exact usable boundary.
        self.controlled_adapter(control_clock=None)
        refused = self.offer(received_at=self.now)
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["reason"], "capture_expired_or_uncertain")
        self.assertEqual(self.snapshot()["cursor"], first["receipt"]["cursor"])

    def test_capacity_refuses_new_capture_without_evicting_original_receipts(self):
        self.controlled_adapter(control_clock=None, max_captures=1)
        first_inputs = fixtures.inputs()
        first = self.offer(first_inputs)
        self.assertEqual(first["status"], "qualified", first)
        self.now = fixtures.NOW + 1.2
        refused = self.offer(fixtures.inputs(sequence=8, observed_ns=1800000001000000000), received_at=fixtures.NOW + 1.1)
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["reason"], "capture_capacity_exhausted")
        for ref in first["publication"]["payload"]["original_receipt_refs"]:
            self.assertEqual(self.adapter.resolve_receipt(ref["store_id"], ref["receipt_id"])["ref"], ref)
        self.assertEqual(self.offer(first_inputs, received_at=self.now), first)
        self.assertEqual(self.snapshot()["cursor"], first["receipt"]["cursor"])

    def test_unqualified_control_clock_does_not_claim_a_recorded_withdrawal(self):
        self.controlled_adapter(control_clock=lambda: {"observed_at": self.now, "clock": {"status": "unverified"}})
        first = self.offer()
        self.assertEqual(first["status"], "qualified", first)
        self.now = fixtures.NOW + 1.2
        refused = self.offer(fixtures.inputs(sequence=8, observed_ns=1800000001000000000, depth_delta_s=.001), received_at=fixtures.NOW + 1.1)
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["withdrawal"], "not_recorded")
        self.assertIsNone(refused["publication"])
        self.assertEqual(self.snapshot()["cursor"], first["receipt"]["cursor"])
        self.assertEqual(len(self.snapshot()["items"]), 1)

    def test_denied_control_authority_reports_unconfirmed_and_leaves_natural_expiry(self):
        capture_only = copy.deepcopy(self.grant)
        capture_only["live_bindings"][0]["capabilities"] = ["capture", "read"]
        self.replace_grants([capture_only])
        self.controlled_adapter()
        first = self.offer()
        self.assertEqual(first["status"], "qualified", first)
        self.now = fixtures.NOW + 1.2
        refused = self.offer(fixtures.inputs(sequence=8, observed_ns=1800000001000000000, depth_delta_s=.001), received_at=fixtures.NOW + 1.1)
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["withdrawal"], "not_recorded")
        self.assertEqual(self.snapshot()["cursor"], first["receipt"]["cursor"])
        self.assertEqual(len(self.snapshot()["items"]), 1)
        self.now = fixtures.NOW + 4
        self.assertEqual(self.snapshot()["items"], [])

    def test_semantic_reader_cannot_expand_geometry_but_observes_source_generation(self):
        semantic_token = "synthetic-semantic-token"
        semantic = copy.deepcopy(self.grant)
        semantic["principal_id"] = "synthetic-semantic-principal"
        semantic["token_sha256"] = hashlib.sha256(semantic_token.encode()).hexdigest()
        semantic["operations"] = ["read"]
        semantic["payload_types"] = ["home-live-fact-1"]
        semantic["schema_pins"] = {"home-live-fact-1": semantic["schema_pins"]["home-live-fact-1"]}
        semantic["live_bindings"][0]["capabilities"] = ["read"]
        self.replace_grants([self.grant, semantic])
        self.controlled_adapter()
        self.assertEqual(self.offer()["status"], "qualified")
        query = "?site_id=synthetic-site&room_id=synthetic-room"
        status, snapshot = self.server.request("GET", PREFIX + "/snapshot" + query, token=semantic_token)
        self.assertEqual(status, 200, snapshot)
        self.assertEqual(snapshot["items"], [])
        self.assertEqual(snapshot["cursor"], 1)
        status, history = self.server.request("GET", PREFIX + "/events" + query + "&after=0", token=semantic_token)
        self.assertEqual(status, 200, history)
        self.assertEqual(history["items"], [])
        self.now = fixtures.NOW + 1.2
        refused = self.offer(fixtures.inputs(sequence=8, observed_ns=1800000001000000000, depth_delta_s=.001), received_at=fixtures.NOW + 1.1)
        self.assertEqual(refused["withdrawal"], "recorded", refused)
        status, after = self.server.request("GET", PREFIX + "/snapshot" + query, token=semantic_token)
        self.assertEqual(status, 200, after)
        self.assertEqual(after["items"], [])
        self.assertEqual(after["revocation_generation"], 1)

    def test_native_integer_camera_metadata_is_explicitly_refused_without_wire_exception(self):
        data, header, bundle, calibration = fixtures.inputs()
        calibration = copy.deepcopy(calibration)
        calibration["eeprom"]["serial"] = 2 ** 60
        refused = self.offer((data, header, bundle, calibration))
        self.assertEqual(refused["status"], "refused", refused)
        self.assertTrue(refused["reason"])
        self.assertIsNone(refused["capture"])
        self.assertEqual(self.snapshot()["cursor"], 0)


if __name__ == "__main__":
    unittest.main()
