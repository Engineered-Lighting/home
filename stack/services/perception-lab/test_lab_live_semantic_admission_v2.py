"""Actual journal joins with authored PC/work/JEV originals; no external I/O."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "stack/services/home-context"))
sys.path.insert(0, str(ROOT / "tools/den-live-runtime"))
from journal import Journal, JournalError, digest
from live_capture import _original_digest
from live_test_helpers import NOW, SCOPE, TOKEN, ASSOCIATION_METHOD, METHOD, GEOMETRY_METHOD, reference, grant, geometry, event, receipt_resolver, person, subject
from live_conservative_test_helpers import conservative_fixture
from test_live_pc_support import authored_pc_fixture, PC_METHOD
from room_configuration import upgrade_configuration as room_configuration
from pc_support_configuration import upgrade_configuration as pc_configuration
from lab_live_semantic_admission import LabLiveSemanticAdmission, SemanticRefusal
import test_lab_live_semantic_admission as historical


def semantic_v2_fixture(*, include_worker=False):
    """Only authored qualifications. Real deployment must commission each body."""
    config, _, worker = conservative_fixture()
    room = json.loads(subprocess.check_output(["node", "-e", "process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"], cwd=ROOT, text=True, timeout=12))
    room["registration"] = deepcopy(worker["delivery_policy"]["registration"])
    config, worker = room_configuration(config, worker, room)
    seed = config["manifest"]["sources"]["synthetic-source"]
    policy = {"schema": "home-lab-semantic-admission-policy-2", "version": 2,
              "arm": "b", "generation": 1, "ha_source_id": "victoria-ha-lab",
              "ha_entities": {"active": "derived.computer_active", "recency": "derived.computer_input_recency"},
              "wire": "den-facts-7", "response_version": "jev-1.13.0", "max_events": 24}
    for family in ("ha", "jev"):
        source = deepcopy(seed)
        source.update(capture_epoch="lab-semantic-epoch", registration=None,
                      sensor_roots=["device:authored-pc", "camera:synthetic-oak"],
                      method_refs=[deepcopy(PC_METHOD if family == "ha" else METHOD)],
                      model_refs=[] if family == "ha" else [reference("jev-response-contract")],
                      receipt_component_ids=["pc-proof" if family == "ha" else "jev-original"],
                      part_policies={k: None for k in seed["part_policies"]})
        source["capture_clock"].update(original_unit="unix_s", source_epoch="lab-semantic-epoch")
        config["manifest"]["sources"][family + "-semantic"] = source
        policy[family] = {"source_id": family + "-semantic", "method_ref": deepcopy(source["method_refs"][0]),
            "association_method_ref": deepcopy(ASSOCIATION_METHOD),
            "model_ref": None if family == "ha" else deepcopy(source["model_refs"][0]),
            "receipt_store_id": "synthetic-receipts", "receipt_component_id": source["receipt_component_ids"][0],
            "clock_uncertainty_s": .001, "kind_sensor_roots": {"ha.observation": ["device:authored-pc"],
                **({"perception.output": ["camera:synthetic-oak"]} if family == "jev" else {})}}
    pc = authored_pc_fixture()["policy"]
    pc["bindings"][0].update(source_id="ha-semantic", geometry_source_id="synthetic-source")
    config, worker = pc_configuration(config, worker, pc)
    policy["pc_support_policy_digest"] = digest(pc)
    g = grant()
    g.update(source_ids=list(config["manifest"]["sources"]), sensor_roots=["device:authored-pc", "camera:synthetic-oak"])
    g["live_bindings"] = [{"manifest_ref": deepcopy(config["manifest_ref"]), "source_id": k,
                           "capabilities": ["capture", "control", "read"]} for k in g["source_ids"]]
    return (config, policy, g, worker) if include_worker else (config, policy, g)


class SemanticAdmissionV2Tests(unittest.TestCase):
    row = historical.LabLiveSemanticAdmissionTests.row
    chain = historical.LabLiveSemanticAdmissionTests.chain

    @classmethod
    def setUpClass(cls):
        cls.fixture = semantic_v2_fixture()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="authored-semantic-v2-")
        self.addCleanup(self.temp.cleanup)
        self.config, self.policy, self.grant = deepcopy(self.fixture)
        self.now = NOW + .1
        self.session = {"id": SCOPE["session_id"], "site_id": SCOPE["site_id"], "camera_id": "den", "started_at": NOW - 10}
        self.retained = []
        def retain(record):
            self.assertIsNone(self.journal.db.execute("SELECT 1 FROM publications WHERE json_extract(body,'$.id')=?", (self.join.prepared[(record["family"], self.event_id(record))]["event"]["id"],)).fetchone())
            self.retained.append(deepcopy(record))
            return {"status": "retained", "digest": _original_digest(record)}
        self.retainer = retain
        self.join = LabLiveSemanticAdmission(self.session, live_config=self.config, revision="synthetic-r1", policy=self.policy,
                                            original_retainer=lambda record: self.retainer(record))
        def resolve(store, receipt):
            try:
                return self.join.resolve_receipt(store, receipt)
            except KeyError:
                return receipt_resolver(store, receipt)
        self.journal = Journal(Path(self.temp.name) / "journal.sqlite", grants=[self.grant], profile="home-live-shadow-1",
            live_config=self.config, live_receipt_resolver=resolve, clock=lambda: self.now,
            live_wall_clock=lambda: self.now, live_monotonic=lambda: self.now - NOW)
        self.addCleanup(self.journal.close)
        self.principal = self.journal.authenticate(TOKEN)
        self.parent = self.publish_geometry()
        self.ref = self.join._work_ref(self.parent)
        self.now = NOW + .75

    @staticmethod
    def event_id(wrapper):
        original = wrapper["original"]
        return original["ha_event"]["id"] if original["schema"] == "home-original-pc-input-1" else original["events"][-1]["id"]

    def publish_geometry(self, *, seq=1, selected_person=None):
        p = geometry("semantic-v2-work-" + str(seq), seq=seq)
        p["manifest_ref"] = deepcopy(self.config["manifest_ref"])
        work = p["parts"]["work"]
        work["uncertainty"] = deepcopy(self.config["manifest"]["sources"]["synthetic-source"]["part_policies"]["work"])
        work["uncertainty"]["age"]["origin_at"] = work["observed_at"]
        work["valid_until"] = min(work["valid_until"], work["observed_at"] + work["uncertainty"]["age"]["max_age_s"])
        if selected_person is not None:
            p["id"] += "-person-" + str(selected_person["pid"])
            p["person_ref"] = deepcopy(selected_person)
            p["head"]["key"]["person_ref"] = deepcopy(selected_person)
        prior = self.journal.snapshot(self.principal, SCOPE["site_id"], SCOPE["room_id"])["items"]
        same = [q for q in prior if q["payload"].get("head", {}).get("key") == p["head"]["key"]]
        if same:
            p["head"]["supersedes"] = self.join._work_ref(same[0]) | {"component_id": None}
        self.journal.append_event(event(p), self.principal)
        return next(q for q in self.journal.snapshot(self.principal, SCOPE["site_id"], SCOPE["room_id"])["items"] if q["id"] == p["id"])

    def ha(self, eid=11, observed=NOW + .2, **_):
        f = authored_pc_fixture(seq=eid, snapshot_at=observed, input_at=observed - .5)
        row = f["original"]["ha_event"]
        row["id"] = eid
        return row

    def pc(self, rows=None, **kwargs):
        return self.join.publish_pc(rows or [self.ha()], received_at=self.now, journal=self.journal, grant=self.principal, **kwargs)

    def facts(self):
        return [json.loads(r[0])["payload"] for r in self.journal.db.execute("SELECT body FROM publications WHERE payload_type='home-live-fact-1'")]

    def jev_rows(self):
        rows = self.chain()
        camera = rows[0]["payload"]
        camera.update(observed_at=NOW, available_at=NOW + .05, evidence_roots=["synthetic-frame-1:rgb"])
        rows[-1]["payload"]["source_end_s"] = NOW - self.session["started_at"]
        return rows

    def test_selected_actual_parent_and_private_original_are_retained_before_admission(self):
        self.assertEqual(self.join.current_work_parent(self.journal, self.principal, self.now), self.ref)
        result = self.pc(work_parent_ref=self.ref)
        self.assertEqual(result["status"], "admitted_attributed")
        self.assertTrue(result["supports_engagement"])
        p = self.facts()[0]
        wrapper = self.retained[0]
        self.assertEqual(wrapper["original"]["schema"], "home-original-pc-input-1")
        self.assertEqual(wrapper["original_ref"]["digest"], digest(wrapper["original"]))
        self.assertEqual(p["parent_refs"], [self.ref])
        self.assertEqual(p["person_ref"], self.parent["payload"]["person_ref"])
        self.assertEqual(wrapper["original"]["ha_event"], self.ha())
        self.assertLess(wrapper["original"]["input_at"], p["observed_at"])

    def test_newer_current_work_parent_preserves_original_ha_clock(self):
        self.now = NOW + 1.2
        parent = self.publish_geometry(seq=2)
        self.pc([self.ha()], work_parent_ref=self.join._work_ref(parent))
        self.assertEqual(self.facts()[0]["observed_at"], NOW + 1)
        self.assertEqual(self.retained[0]["original"]["observed_at"], NOW + .2)
        self.now += .1
        self.pc([self.ha(eid=12, observed=NOW + .8)], work_parent_ref=self.join._work_ref(parent))
        self.assertEqual(len(self.facts()), 2)
        self.assertEqual(self.retained[1]["original"]["observed_at"], NOW + .8)

    def _pending_newer_parent(self):
        self.now = NOW + 1.2
        parent = self.publish_geometry(seq=2)
        def lost_retention(_):
            raise TimeoutError("authored_retainer_ack_lost")
        self.retainer = lost_retention
        with self.assertRaises(TimeoutError):
            self.pc(work_parent_ref=self.join._work_ref(parent))
        return deepcopy(self.join.prepared[("ha", 11)]["event"])

    def test_older_receipt_wrong_sequence_cannot_borrow_newer_parent_proof(self):
        prepared = self._pending_newer_parent()
        ref = prepared["payload"]["original_receipt_refs"][0]
        self.join.records[(ref["store_id"], ref["receipt_id"])]["capture_seq"] -= 1
        with self.assertRaises(JournalError) as error:
            self.journal.append_event(prepared, self.principal)
        self.assertEqual(error.exception.code, "receipt_mismatch")

    def test_older_receipt_non_supporting_fact_retains_historical_sequence_refusal(self):
        prepared = self._pending_newer_parent()
        p = prepared["payload"]
        p.update(value="active_unattributed", reason="qualified_per_observation_evidence")
        ref = p["original_receipt_refs"][0]
        self.join.records[(ref["store_id"], ref["receipt_id"])].pop("pc_input_original")
        with self.assertRaises(JournalError) as error:
            self.journal.append_event(prepared, self.principal)
        self.assertEqual(error.exception.code, "receipt_mismatch")

    def test_multiple_qualified_people_are_not_automatically_selected(self):
        other = person("other-track")
        other["pid"] = 2
        self.publish_geometry(seq=1, selected_person=other)
        self.assertIsNone(self.join.current_work_parent(self.journal, self.principal, self.now))
        self.assertFalse(self.pc()["supports_engagement"])
        self.assertIsNone(self.facts()[0]["person_ref"])

    def test_future_original_input_refuses_before_archive_or_admission(self):
        row = self.ha()
        p = row["payload"]
        from datetime import datetime, timezone
        from live_pc_support import _derived, PCSupportRefusal
        rule = self.config["manifest"]["worker"]["pc_support_policy"]["input_rules"][0]
        last = rule["derived"]["computer_active"]["last_active_entity"]
        p["ha"][last]["state"] = datetime.fromtimestamp(p["observed_at"] + 1, timezone.utc).isoformat(timespec="microseconds")
        p["ha"].update(_derived(rule, p["ha"], p["observed_at"]))
        p["snapshot_digest"] = digest({k: p[k] for k in ("observed_at", "ha", "acquisition_duration_s")})
        with self.assertRaisesRegex(PCSupportRefusal, "clock_not_applicable"):
            self.pc([row], work_parent_ref=self.ref)
        self.assertEqual(self.retained, [])
        self.assertEqual(self.facts(), [])

    def test_locked_device_is_inactive_not_person_absence_or_input_support(self):
        row = self.ha()
        p = row["payload"]
        from live_pc_support import _derived
        rule = self.config["manifest"]["worker"]["pc_support_policy"]["input_rules"][0]
        p["ha"][rule["derived"]["computer_active"]["session_entity"]]["state"] = "Locked"
        p["ha"].update(_derived(rule, p["ha"], p["observed_at"]))
        p["snapshot_digest"] = digest({k: p[k] for k in ("observed_at", "ha", "acquisition_duration_s")})
        result = self.pc([row], work_parent_ref=self.ref)
        self.assertFalse(result["supports_engagement"])
        self.assertEqual(self.facts()[0]["kind"], "pc_input_interaction")
        self.assertEqual(self.facts()[0]["value"], "observed_inactive")
        self.assertNotIn("pc_input_original", self.retained[0]["receipt_record"])

    def test_absent_selected_parent_stays_non_supporting(self):
        self.assertFalse(self.pc()["supports_engagement"])
        self.assertEqual(self.facts()[0]["value"], "active_unattributed")
        self.assertIsNone(self.facts()[0]["person_ref"])
        self.assertEqual(self.retained[0]["original"]["schema"], "home-original-lab-semantic-2")

    def test_same_input_polls_preserve_physical_input_root_and_horizon(self):
        first = self.ha()
        self.pc([first], work_parent_ref=self.ref)
        self.now += 1
        second = self.ha(eid=102, observed=NOW + 1.2)
        rule = self.config["manifest"]["worker"]["pc_support_policy"]["input_rules"][0]
        from live_pc_support import _derived
        last = rule["derived"]["computer_active"]["last_active_entity"]
        second["payload"]["ha"][last] = deepcopy(first["payload"]["ha"][last])
        second["payload"]["ha"].update(_derived(rule, second["payload"]["ha"], second["payload"]["observed_at"]))
        second["payload"]["snapshot_digest"] = digest({k: second["payload"][k] for k in ("observed_at", "ha", "acquisition_duration_s")})
        self.pc([second], work_parent_ref=self.ref)
        originals = [r["original"] for r in self.retained]
        self.assertEqual(originals[0]["input_root"], originals[1]["input_root"])
        self.assertEqual(originals[0]["input_valid_until"], originals[1]["input_valid_until"])

    def test_archive_loss_blocks_append_and_other_event_until_exact_retry(self):
        self.retainer = lambda _: {"status": "incomplete"}
        with self.assertRaisesRegex(SemanticRefusal, "durable_semantic"):
            self.pc(work_parent_ref=self.ref)
        self.assertEqual(self.facts(), [])
        with self.assertRaisesRegex(SemanticRefusal, "pending_exact_retry"):
            self.pc([self.ha(eid=102)], work_parent_ref=self.ref)

    def test_quiesce_during_retention_is_nonblocking_and_prevents_first_append(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        def retain(record):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test_release_missing")
            return {"status": "retained", "digest": _original_digest(record)}
        self.retainer = retain
        def publish():
            try:
                result.append(self.pc(work_parent_ref=self.ref))
            except Exception as error:
                result.append(error)
        thread = threading.Thread(target=publish, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.join.quiesce()["status"], "unconfirmed")
            self.assertFalse(release.is_set())
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [{"status": "quiesced", "supports_engagement": False, "receipts": []}])
        self.assertEqual(self.facts(), [])
        self.assertEqual(self.join.quiesce()["status"], "quiescent")

    def test_lost_ack_retries_frozen_body_after_parent_expiry_and_quiesce(self):
        append = self.journal.append_event
        def lose(envelope, principal):
            append(envelope, principal)
            raise TimeoutError("authored_lost_ack")
        self.journal.append_event = lose
        with self.assertRaises(TimeoutError):
            self.pc(work_parent_ref=self.ref)
        original = deepcopy(self.retained[0])
        self.retainer = lambda r: ({"status": "retained", "digest": _original_digest(r)}
                                  if r == original else self.fail("retry original changed"))
        self.journal.append_event = append
        self.now += 20
        self.assertEqual(self.join.quiesce()["status"], "unconfirmed")
        self.assertEqual(self.pc(work_parent_ref=self.ref)["status"], "admitted_attributed")
        self.assertEqual(len(self.facts()), 1)
        self.assertEqual(self.join.quiesce()["status"], "quiescent")
        with self.assertRaisesRegex(SemanticRefusal, "quiesced"):
            self.pc([self.ha(eid=102, observed=self.now)], work_parent_ref=None)

    def test_selected_forged_or_expired_reference_never_falls_back(self):
        forged = deepcopy(self.ref)
        forged["payload_digest"] = "0" * 64
        with self.assertRaisesRegex(SemanticRefusal, "selected_work_parent"):
            self.pc(work_parent_ref=forged)
        self.now += 11
        self.assertIsNone(self.join.current_work_parent(self.journal, self.principal, self.now))
        with self.assertRaisesRegex(SemanticRefusal, "selected_work_parent"):
            self.pc([self.ha(eid=102, observed=self.now - .1)], work_parent_ref=self.ref)

    def test_late_jev_does_not_bind_to_current_person_without_frozen_request(self):
        self.join.publish_jev(self.jev_rows(), output_id=14, received_at=self.now, journal=self.journal, grant=self.principal)
        self.assertIsNone(self.facts()[0]["person_ref"])
        self.assertFalse(self.facts()[0]["kind"] == "pc_input_interaction")

    def test_request_time_cutoff_matching_current_parent_allows_refinement_only(self):
        rows = self.jev_rows()
        frozen = self.join.remember_request(rows[2], received_at=self.now, journal=self.journal, grant=self.principal, work_parent_ref=self.ref)
        result = self.join.publish_jev(rows, output_id=14, received_at=self.now, journal=self.journal, grant=self.principal)
        self.assertEqual(result["status"], "admitted_attributed")
        self.assertFalse(result["supports_engagement"])
        self.assertEqual(self.retained[0]["original"]["request_binding"], frozen)
        self.assertEqual(self.facts()[0]["person_ref"], self.parent["payload"]["person_ref"])

    def test_changed_frozen_request_and_wrong_consumed_visual_refuse(self):
        rows = self.jev_rows()
        self.join.remember_request(rows[2], received_at=self.now, journal=self.journal, grant=self.principal, work_parent_ref=self.ref)
        with self.assertRaisesRegex(SemanticRefusal, "association_changed"):
            self.join.remember_request(rows[2], received_at=self.now, journal=self.journal, grant=self.principal)
        rows[0]["payload"]["evidence_roots"] = ["different-original-person-camera"]
        with self.assertRaisesRegex(SemanticRefusal, "cutoff_association"):
            self.join.publish_jev(rows, output_id=14, received_at=self.now, journal=self.journal, grant=self.principal)
        self.assertEqual(self.facts(), [])

    def test_old_exact_work_ref_is_not_rebound_to_later_work_head(self):
        rows = self.jev_rows()
        self.join.remember_request(rows[2], received_at=self.now, journal=self.journal, grant=self.principal, work_parent_ref=self.ref)
        self.now = NOW + 1.2
        self.publish_geometry(seq=2)
        with self.assertRaisesRegex(SemanticRefusal, "selected_work_parent"):
            self.join.publish_jev(rows, output_id=14, received_at=self.now, journal=self.journal, grant=self.principal)
        self.assertEqual(self.facts(), [])

    def test_v2_requires_fixed_retainer_and_exact_pc_policy_pin(self):
        with self.assertRaisesRegex(ValueError, "fixed_pc_successor"):
            LabLiveSemanticAdmission(self.session, live_config=self.config, revision="synthetic-r1", policy=self.policy)
        wrong = deepcopy(self.policy)
        wrong["pc_support_policy_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "fixed_pc_successor"):
            LabLiveSemanticAdmission(self.session, live_config=self.config, revision="synthetic-r1", policy=wrong,
                                     original_retainer=lambda _: None)


if __name__ == "__main__":
    unittest.main()
