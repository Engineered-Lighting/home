"""Authored actual-shaped Lab originals through real Journal admission; no I/O."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "stack/services/home-context"))
sys.path.insert(0, str(ROOT / "tools/den-live-runtime"))
from journal import Journal, digest
from live_capture import _original_digest
from live_test_helpers import NOW, SCOPE, TOKEN, event, geometry, receipt_resolver
from lab_live_semantic_admission import LabLiveSemanticAdmission, SemanticRefusal
from persisted_evidence import observation_record
import test_lab_live_semantic_admission_v2 as previous


class SemanticAdmissionV3Tests(unittest.TestCase):
    row = previous.SemanticAdmissionV2Tests.row
    chain = previous.SemanticAdmissionV2Tests.chain
    ha = previous.SemanticAdmissionV2Tests.ha
    facts = previous.SemanticAdmissionV2Tests.facts

    @classmethod
    def setUpClass(cls):
        cls.fixture = previous.semantic_v2_fixture()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="authored-semantic-v3-")
        self.addCleanup(self.temp.cleanup)
        self.config, self.policy2, self.grant = deepcopy(self.fixture)
        self.policy = {**deepcopy(self.policy2), "schema": "home-lab-semantic-admission-policy-3",
                       "version": 3, "root_lineage": "persisted-observation-record-1"}
        self.now = NOW + .1
        self.session = {"id": SCOPE["session_id"], "site_id": SCOPE["site_id"],
                        "camera_id": "den", "started_at": NOW - 10}
        self.retained = []

        def retain(record):
            self.assertEqual(self.facts(), [])
            self.retained.append(deepcopy(record))
            return {"status": "retained", "digest": _original_digest(record)}

        self.retainer = retain
        self.join = self.make_join(self.policy)

        def resolve(store, receipt):
            try:
                return self.join.resolve_receipt(store, receipt)
            except KeyError:
                record = receipt_resolver(store, receipt)
                # This independently authored geometry original names the
                # actual Store event-ID root consumed by incumbent JEV.
                if receipt == "synthetic-capture-1:pose":
                    record["observation_root_times"] = {"10": NOW}
                    record["sensor_root_bindings"] = {"10": ["camera:synthetic-oak"]}
                return record

        self.journal = Journal(Path(self.temp.name) / "journal.sqlite", grants=[self.grant],
            profile="home-live-shadow-1", live_config=self.config, live_receipt_resolver=resolve,
            clock=lambda: self.now, live_wall_clock=lambda: self.now,
            live_monotonic=lambda: self.now - NOW)
        self.addCleanup(self.journal.close)
        self.principal = self.journal.authenticate(TOKEN)
        body = geometry("semantic-v3-authored-work")
        body["manifest_ref"] = deepcopy(self.config["manifest_ref"])
        body["observation_roots"] = ["10", "synthetic-frame-1:depth"]
        body["observation_root_times"] = {root: NOW for root in body["observation_roots"]}
        body["sensor_root_bindings"] = {root: ["camera:synthetic-oak"] for root in body["observation_roots"]}
        work = body["parts"]["work"]
        work["uncertainty"] = deepcopy(self.config["manifest"]["sources"]["synthetic-source"]["part_policies"]["work"])
        work["uncertainty"]["age"]["origin_at"] = NOW
        work["valid_until"] = min(work["valid_until"], NOW + work["uncertainty"]["age"]["max_age_s"])
        self.journal.append_event(event(body), self.principal)
        self.parent = self.journal.snapshot(self.principal, SCOPE["site_id"], SCOPE["room_id"])["items"][0]
        self.ref = self.join._work_ref(self.parent)
        self.now = NOW + .75

    def make_join(self, policy):
        return LabLiveSemanticAdmission(self.session, live_config=self.config, revision="synthetic-r1",
            policy=policy, original_retainer=lambda record: self.retainer(record))

    def rows(self):
        rows = self.chain()
        payload = rows[0]["payload"]
        payload.update(observed_at=NOW, available_at=NOW + .05)
        payload.pop("evidence_roots")
        payload["result"].update(schema_version=1, coordinate_space="source_pixels",
                                 frame_id=payload["frame_id"])
        payload["arm_snapshot"] = {"frame": {"id": payload["frame_id"], "width": 640, "height": 360}}
        rows[-1]["payload"]["source_end_s"] = NOW - self.session["started_at"]
        return rows

    def publish(self, rows, *, attributed=False):
        if attributed:
            self.join.remember_request(rows[2], received_at=self.now, journal=self.journal,
                                       grant=self.principal, work_parent_ref=self.ref)
        return self.join.publish_jev(rows, output_id=14, received_at=self.now,
                                     journal=self.journal, grant=self.principal)

    def test_absent_envelope_roots_match_actual_persisted_record_and_current_work(self):
        rows = self.rows()
        frozen = deepcopy(rows)
        canonical = observation_record(10, "perception.output", rows[0]["payload"], self.session)
        self.assertEqual(canonical["evidence_roots"], ["10"])
        result = self.publish(rows, attributed=True)
        self.assertEqual(result["status"], "admitted_attributed")
        self.assertFalse(result["supports_engagement"])
        fact = self.facts()[0]
        self.assertEqual(fact["parent_refs"], [self.ref])
        self.assertEqual(fact["observation_root_times"]["10"], NOW)
        self.assertEqual(fact["sensor_root_bindings"]["10"], ["camera:synthetic-oak"])
        wrapper = self.retained[0]
        self.assertEqual(wrapper["original"]["schema"], "home-original-lab-semantic-2")
        self.assertEqual(wrapper["original"]["policy_digest"], digest(self.policy))
        self.assertEqual(wrapper["original"]["events"], frozen)
        self.assertNotIn("evidence_roots", wrapper["original"]["events"][0]["payload"])
        self.assertEqual(set(wrapper["receipt_record"]), {"ref", "capture_epoch", "capture_seq",
            "observation_root_times", "sensor_root_bindings"})
        self.assertEqual(rows, frozen)

    def test_explicit_roots_preserved_and_deduplicated_without_mutating_original(self):
        rows = self.rows()
        rows[0]["payload"]["evidence_roots"] = ["actual-original-visual", "actual-original-visual"]
        frozen = deepcopy(rows)
        self.assertEqual(self.publish(rows)["status"], "admitted_unattributed")
        fact = self.facts()[0]
        self.assertIn("actual-original-visual", fact["observation_roots"])
        self.assertNotIn("10", fact["observation_roots"])
        self.assertEqual(fact["observation_root_times"]["actual-original-visual"], NOW)
        self.assertEqual(self.retained[0]["original"]["events"], frozen)

    def test_different_original_root_cannot_borrow_current_work_association(self):
        rows = self.rows()
        rows[0]["payload"]["evidence_roots"] = ["different-original-person"]
        with self.assertRaisesRegex(SemanticRefusal, "cutoff_association"):
            self.publish(rows, attributed=True)
        self.assertEqual(self.retained, [])
        self.assertEqual(self.facts(), [])

    def test_one_matching_root_does_not_hide_an_additional_foreign_current_root(self):
        rows = self.rows()
        rows[0]["payload"]["evidence_roots"] = ["10", "different-original-person"]
        with self.assertRaisesRegex(SemanticRefusal, "consumed_visual_association_mismatch"):
            self.publish(rows, attributed=True)
        self.assertEqual(self.retained, [])
        self.assertEqual(self.facts(), [])

    def test_malformed_observation_roots_fail_before_retention_or_publication(self):
        for roots in ([], [""], [3], "not-a-list", ["x"] * 65):
            with self.subTest(roots=roots):
                rows = self.rows()
                rows[0]["payload"]["evidence_roots"] = roots
                with self.assertRaisesRegex(SemanticRefusal, "observation_record_required"):
                    self.publish(rows)
        self.assertEqual(self.retained, [])
        self.assertEqual(self.facts(), [])

    def test_missing_or_mismatched_original_frame_fails_closed(self):
        for missing in ("frame_id", "capture_epoch", "result_frame"):
            with self.subTest(missing=missing):
                rows = self.rows()
                payload = rows[0]["payload"]
                if missing == "result_frame":
                    payload["result"]["frame_id"] = "different-frame"
                else:
                    payload.pop(missing)
                with self.assertRaisesRegex(SemanticRefusal, "visual_frame_required"):
                    self.publish(rows)
        self.assertEqual(self.facts(), [])

    def test_same_event_changed_original_and_wrong_source_remain_refused(self):
        rows = self.rows()
        self.publish(rows)
        changed = deepcopy(rows)
        changed[0]["payload"]["result"]["objects"] = []
        with self.assertRaisesRegex(SemanticRefusal, "identity_conflict"):
            self.publish(changed)
        self.assertEqual(len(self.facts()), 1)

    def test_source_validation_precedes_canonical_root_projection(self):
        rows = self.rows()
        rows[0]["payload"]["status"] = "failed"
        rows[0]["payload"].pop("frame_id")
        with self.assertRaisesRegex(SemanticRefusal, "additional_source_contract_not_closed"):
            self.publish(rows)
        self.assertEqual(self.retained, [])

    def test_ha_snapshot_root_contract_is_not_replaced_by_event_id_fallback(self):
        rows = self.rows()
        rows[1]["payload"].pop("evidence_roots")
        with self.assertRaisesRegex(SemanticRefusal, "original_configured_pc_snapshot_required"):
            self.publish(rows)
        with self.assertRaisesRegex(SemanticRefusal, "original_configured_pc_snapshot_required"):
            self.join.publish_pc([rows[1]], received_at=self.now,
                                 journal=self.journal, grant=self.principal)
        self.assertEqual(self.retained, [])

    def test_v2_body_and_root_behavior_remain_explicit_and_unchanged(self):
        original_policy = deepcopy(self.policy2)
        self.join = self.make_join(self.policy2)
        rows = self.rows()
        with self.assertRaisesRegex(SemanticRefusal, "original_semantic_roots_required"):
            self.publish(rows)
        rows[0]["payload"]["evidence_roots"] = ["historical-original-root"]
        rows[0]["payload"]["result"].pop("frame_id")  # Historical v2 did not check this field.
        self.publish(rows)
        self.assertEqual(self.join.policy, original_policy)
        self.assertFalse(self.join.v3)
        self.assertEqual(self.retained[0]["original"]["policy_digest"], digest(original_policy))
        self.assertEqual(self.retained[0]["original"]["events"], rows)
        self.assertIn("historical-original-root", self.facts()[0]["observation_roots"])

    def test_v3_requires_exact_root_mode_and_preserves_v2_closed_shape(self):
        for change in ({"root_lineage": "other"}, {"version": 2}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "exact_lab_semantic_configuration_required"):
                self.make_join({**deepcopy(self.policy), **change})
        missing = deepcopy(self.policy)
        missing.pop("root_lineage")
        with self.assertRaisesRegex(ValueError, "exact_lab_semantic_configuration_required"):
            self.make_join(missing)
        with self.assertRaisesRegex(ValueError, "exact_lab_semantic_configuration_required"):
            self.make_join({**deepcopy(self.policy2), "root_lineage": "persisted-observation-record-1"})


if __name__ == "__main__":
    unittest.main()
