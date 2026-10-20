"""Authored original-shaped HA/JEV admission; no model, host or camera I/O."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "home-context"))
from journal import Journal, JournalError, digest
from live_capture import _original_digest
from live_test_helpers import configuration, grant, reference, METHOD, ASSOCIATION_METHOD, NOW, PROFILE, SCOPE, TOKEN, barrier, event
from lab_live_semantic_admission import LabLiveSemanticAdmission, SemanticRefusal


class LabLiveSemanticAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="authored-semantic-")
        self.addCleanup(self.temp.cleanup)
        self.now = NOW + .75
        self.session = {"id": SCOPE["session_id"], "site_id": SCOPE["site_id"],
                        "camera_id": "den", "started_at": NOW - 10}
        self.config = configuration()
        seed = deepcopy(self.config["manifest"]["sources"]["synthetic-source"])
        self.config["manifest"]["sources"] = {}
        self.policy = {"schema": "home-lab-semantic-admission-policy-1", "version": 1,
                       "arm": "b", "generation": 1, "ha_source_id": "victoria-ha-lab",
                       "ha_entities": {"active": "derived.computer_active", "recency": "derived.computer_input_recency"},
                       "wire": "den-facts-7", "response_version": "jev-1.13.0", "max_events": 24}
        for family in ("ha", "jev"):
            source = deepcopy(seed)
            source.update(capture_epoch="lab-semantic-epoch", registration=None,
                          sensor_roots=["device:pc"] if family == "ha" else ["device:pc", "camera:oak"],
                          model_refs=[] if family == "ha" else [reference("jev-1.13.0-response-contract")],
                          receipt_component_ids=[family + "-original"],
                          part_policies={k: None for k in source["part_policies"]})
            source["capture_clock"].update(original_unit="unix_s", source_epoch="lab-semantic-epoch")
            self.config["manifest"]["sources"][family + "-semantic"] = source
            self.policy[family] = {
                "source_id": family + "-semantic", "method_ref": deepcopy(METHOD),
                "association_method_ref": deepcopy(ASSOCIATION_METHOD),
                "model_ref": None if family == "ha" else deepcopy(source["model_refs"][0]),
                "receipt_store_id": "synthetic-receipts", "receipt_component_id": family + "-original",
                "clock_uncertainty_s": .001,
                "kind_sensor_roots": {"ha.observation": ["device:pc"],
                    **({"perception.output": ["camera:oak"]} if family == "jev" else {})}}
        self.config["manifest_ref"]["digest"] = digest(self.config["manifest"])
        self.grant = grant()
        self.grant.update(source_ids=list(self.config["manifest"]["sources"]), sensor_roots=["device:pc", "camera:oak"])
        self.grant["live_bindings"] = [{"manifest_ref": deepcopy(self.config["manifest_ref"]), "source_id": k,
                                      "capabilities": ["capture", "control", "read"]}
                                     for k in self.config["manifest"]["sources"]]
        self.join = self.make_join()
        self.read_grant = deepcopy(self.grant)
        self.read_grant.update(principal_id="semantic-read-principal", operations=["read"],
                               token_sha256=hashlib.sha256(b"semantic-read-token").hexdigest())
        for binding in self.read_grant["live_bindings"]:
            binding["capabilities"] = ["read"]
        self.journal = Journal(Path(self.temp.name) / "journal.sqlite", grants=[self.grant, self.read_grant], profile=PROFILE,
                               live_config=self.config, live_receipt_resolver=self.join.resolve_receipt, clock=lambda: self.now)
        self.addCleanup(self.journal.close)
        self.principal = self.journal.authenticate(TOKEN)

    def make_join(self, **changes):
        return LabLiveSemanticAdmission(self.session, live_config=self.config, revision="synthetic-r1",
                                        policy={**deepcopy(self.policy), **changes})

    def row(self, eid, kind, payload):
        return {"id": eid, "kind": kind, "session_id": self.session["id"], "created_at": self.now, "payload": payload}

    def ha(self, eid=11, observed=NOW + .2, active="on", recent="under_15s", source="victoria-ha-lab"):
        states = {"sensor.formd_t1_sessionstate": {"state": "unlocked", "status": "known"},
                  "sensor.formd_t1_lastactive": {"state": "2027-01-15T08:00:00Z", "status": "known"},
                  "derived.computer_active": {"state": active, "status": "known", "provenance": {
                      "rule": "computer_active_v1", "inputs": ["sensor.formd_t1_sessionstate", "sensor.formd_t1_lastactive"],
                      "max_idle_seconds": 60}},
                  "derived.computer_input_recency": {"state": recent, "status": "known", "provenance": {
                      "rule": "computer_active_v3", "inputs": ["sensor.formd_t1_sessionstate", "sensor.formd_t1_lastactive"]}}}
        content = {"observed_at": observed, "ha": states, "acquisition_duration_s": .01}
        return self.row(eid, "ha.observation", {**content, "site_id": self.session["site_id"], "camera_id": "den",
            "session_id": self.session["id"], "available_at": observed + .05, "valid_until": observed + 3,
            "evidence_roots": [f"ha:{source}:snapshot-{eid}"], "snapshot_key": f"ha:{source}:snapshot-{eid}",
            "snapshot_digest": digest(content), "quality": {"source_id": source, "timing_basis": "reconciled_snapshot_acquisition"}})

    def chain(self, *, people="one", activity="computer_work", status="ready"):
        """Use the real incumbent producer's relative-clock output method."""
        from live_reasoning import LiveReasoning
        from reasoning import ACTIVITIES
        camera = self.row(10, "perception.output", {"site_id": self.session["site_id"], "camera_id": "den",
            "arm": "b", "generation": 1, "status": "completed", "capture_epoch": "original-camera-epoch",
            "observed_at": NOW + .1, "available_at": NOW + .15, "valid_until": NOW + 3,
            "frame_id": "original-frame-10", "evidence_roots": ["original-camera-root-10"],
            "result": {"objects": [{"label": "person"}]}})
        ha = self.ha()
        packet = {"model": "jev-1.13.0", "state": {"schema": "den-facts-7", "now": {
            "people_in_view": people, "camera_evidence": "current"},
            "devices": {"pc_input": "under_15s", "pc_active": "on"}}, "questions": {"activity": {"instructions": "activity?"}}}
        prov = {"request_id": "jev-original-1", "session_id": self.session["id"], "site_id": self.session["site_id"],
                "arm": "b", "generation": 1, "observed_at": NOW + .4, "deadline": NOW + 15,
                "wire_version": "den-facts-7", "response_version": "jev-1.13.0", "input_digest": digest(packet),
                "evidence_ids": ["10", "11"], "timeline_ids": ["10"], "evidence_digest": "1" * 64}
        request = self.row(12, "reasoning.request", {"request_id": prov["request_id"], "arm": "b", "generation": 1,
            "available_at": NOW + .45, "packet_sha256": digest(packet), "request": packet,
            "status": "admitted_before_transport", "wire_version": "den-facts-7", "evidence_ids": ["10", "11"],
            "timeline_ids": ["10"], "evidence_digest": "1" * 64, "timeline_verified": "full"})
        delivery = self.row(13, "reasoning.delivery", {"request_id": prov["request_id"], "arm": "b", "generation": 1,
            "available_at": NOW + .6, "state": "completed", "status": status, "current": True})
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE events(id INTEGER, session_id TEXT, kind TEXT, payload TEXT)")
        runner = LiveReasoning.__new__(LiveReasoning)
        runner.session, runner.sid, runner.policy = self.session, self.session["id"], {}
        runner.store = SimpleNamespace(db=connection)
        provider = {"status": status, "judgment": {"activity": activity, "confidence": .8,
                    "probabilities": {k: 1.0 if k == activity else 0.0 for k in ACTIVITIES}, "model_version": "jev-1.13.0"},
                    "diagnostics": {"request": packet}, "provenance": prov}
        out = runner._output({"request_id": prov["request_id"], "site_id": self.session["site_id"], "arm": "b",
            "generation": 1, "cutoff": NOW + .4, "deadline": NOW + 15,
            "records": [{**camera["payload"], "source_kind": "perception.output", "objects": [{"label": "person"}]}]},
            provider, NOW + .6)
        return [camera, ha, request, delivery, self.row(14, "reasoning.output", out)]

    def pc(self, events, **kw):
        return self.join.publish_pc(events, received_at=self.now, journal=self.journal, grant=self.principal, **kw)

    def jev(self, events, **kw):
        return self.join.publish_jev(events, output_id=14, received_at=self.now, journal=self.journal, grant=self.principal, **kw)

    def publications(self):
        return [json.loads(r[0]) for r in self.journal.db.execute("SELECT body FROM publications ORDER BY cursor")]

    def test_actual_journal_pc_is_unattributed_and_original_snapshot_and_lastactive_retained(self):
        original = self.ha()
        result = self.pc([original])
        self.assertEqual(result["status"], "admitted_unattributed")
        self.assertFalse(result["supports_engagement"])
        p = self.publications()[0]["payload"]
        self.assertEqual(p["value"], "active_unattributed")
        self.assertIsNone(p["person_ref"])
        self.assertIsNone(p["subject_ref"])
        self.assertEqual(p["observed_at"], original["payload"]["observed_at"])
        self.assertEqual(p["observation_roots"], original["payload"]["evidence_roots"])
        ref = p["original_receipt_refs"][0]
        saved = self.join.original_receipt(ref["store_id"], ref["receipt_id"])
        self.assertEqual(saved["events"], [original])
        self.assertEqual(ref["digest"], _original_digest(saved))
        # Private returns cannot mutate the fixed original resolver.
        saved["events"][0]["payload"]["ha"].clear()
        self.assertEqual(self.join.original_receipt(ref["store_id"], ref["receipt_id"])["events"], [original])

    def test_latest_owner_presence_does_not_hide_primary_pc_source(self):
        unrelated = self.ha(20, NOW + .4, active="off", source="la-owner-presence")
        self.pc([self.ha(), unrelated])
        self.assertEqual(self.publications()[0]["payload"]["value"], "active_unattributed")

    def test_mixed_camera_derived_ha_snapshot_cannot_masquerade_as_independent_pc_root(self):
        row = self.ha()
        p = row["payload"]
        p["ha"]["binary_sensor.den_oak_person_occupancy"] = {
            "state": "on", "status": "known", "origin": "same_camera_visual"}
        p["snapshot_digest"] = digest({k: p[k] for k in ("observed_at", "ha", "acquisition_duration_s")})
        with self.assertRaisesRegex(SemanticRefusal, "mixed_ha_snapshot_camera_binding_required"):
            self.pc([row])
        self.assertEqual(self.publications(), [])

    def test_commissioned_mixed_snapshot_preserves_same_camera_binding(self):
        config, policy, g = deepcopy(self.config), deepcopy(self.policy), deepcopy(self.grant)
        config["manifest"]["sources"]["ha-semantic"]["sensor_roots"] = ["camera:oak", "device:pc"]
        for family in ("ha", "jev"):
            policy[family]["kind_sensor_roots"]["ha.observation"] = ["camera:oak", "device:pc"]
        config["manifest_ref"]["digest"] = digest(config["manifest"])
        for binding in g["live_bindings"]:
            binding["manifest_ref"] = deepcopy(config["manifest_ref"])
        join = LabLiveSemanticAdmission(self.session, live_config=config, revision="synthetic-r1", policy=policy)
        j = Journal(Path(self.temp.name) / "mixed.sqlite", grants=[g], profile=PROFILE, live_config=config,
                    live_receipt_resolver=join.resolve_receipt, clock=lambda: self.now)
        self.addCleanup(j.close)
        row = self.ha()
        p = row["payload"]
        p["ha"]["binary_sensor.den_oak_person_occupancy"] = {
            "state": "on", "status": "known", "origin": "same_camera_visual"}
        p["snapshot_digest"] = digest({k: p[k] for k in ("observed_at", "ha", "acquisition_duration_s")})
        join.publish_pc([row], received_at=self.now, journal=j, grant=j.authenticate(TOKEN))
        public = json.loads(j.db.execute("SELECT body FROM publications").fetchone()[0])["payload"]
        self.assertEqual(public["observation_roots"], p["evidence_roots"])
        self.assertEqual(public["sensor_root_bindings"][p["snapshot_key"]], ["camera:oak", "device:pc"])
        self.assertEqual(public["value"], "active_unattributed")

    def test_read_only_grant_cannot_publish_or_retrieve_cached_write_ack(self):
        row = self.ha()
        self.pc([row])
        read_only = self.journal.authenticate("semantic-read-token")
        with self.assertRaises(JournalError) as caught:
            self.join.publish_pc([row], received_at=self.now, journal=self.journal, grant=read_only)
        self.assertEqual(caught.exception.code, "forbidden")
        self.assertEqual(len(self.publications()), 1)

    def test_exact_retry_after_expiry_and_mutated_original_identity_refused(self):
        row = self.ha()
        initial = self.pc([row])
        self.now += 20
        self.assertEqual(initial, self.pc([row]))
        changed = deepcopy(row)
        changed["payload"]["valid_until"] += 1
        with self.assertRaisesRegex(SemanticRefusal, "identity_conflict"):
            self.pc([changed])
        self.assertEqual(len(self.publications()), 1)

    def test_expired_primary_snapshot_never_falls_back_or_refreshes(self):
        row = self.ha()
        self.now = row["payload"]["valid_until"]
        self.assertEqual(self.pc([row])["status"], "expired")
        self.assertEqual(self.publications(), [])

    def test_late_inputs_refuse_without_wedging_later_fresh_source(self):
        self.pc([self.ha(20, NOW + .3)])
        with self.assertRaisesRegex(SemanticRefusal, "progression_regressed"):
            self.pc([self.ha(19, NOW + .2)])
        self.now += 1
        self.pc([self.ha(21, NOW + 1)])
        self.assertEqual(len(self.publications()), 2)

    def test_lost_ack_requires_exact_retry_and_retains_first_consumer_time(self):
        real = self.journal.append_event
        def lose(envelope, principal):
            real(envelope, principal)
            raise TimeoutError("authored_lost_ack")
        self.journal.append_event = lose
        row = self.ha()
        with self.assertRaises(TimeoutError):
            self.pc([row])
        self.now += .1
        with self.assertRaisesRegex(SemanticRefusal, "pending_exact_retry"):
            self.pc([self.ha(12, NOW + .3)])
        self.journal.append_event = real
        self.pc([row])
        self.assertEqual(len(self.publications()), 1)
        ref = self.publications()[0]["payload"]["original_receipt_refs"][0]
        self.assertEqual(self.join.original_receipt(ref["store_id"], ref["receipt_id"])["first_consumer_received_at"], NOW + .75)

    def test_actual_jev_output_shape_admitted_with_all_originals_and_distinct_modalities(self):
        rows = self.chain()
        self.assertNotIn("observed_at", rows[-1]["payload"])
        result = self.jev(rows)
        self.assertFalse(result["supports_engagement"])
        p = self.publications()[0]["payload"]
        self.assertEqual(p["value"], "computer_work")
        self.assertEqual(p["observed_at"], rows[1]["payload"]["observed_at"])
        self.assertEqual(p["available_at"], NOW + .6)
        self.assertEqual(p["valid_until"], NOW + 3)
        self.assertEqual(p["sensor_root_bindings"]["original-camera-root-10"], ["camera:oak"])
        self.assertEqual(p["sensor_root_bindings"][rows[1]["payload"]["snapshot_key"]], ["device:pc"])
        ref = p["original_receipt_refs"][0]
        original = self.join.original_receipt(ref["store_id"], ref["receipt_id"])
        self.assertEqual(original["events"], rows)
        self.assertEqual(ref["digest"], _original_digest(original))
        self.assertIsNone(p["person_ref"])

    def test_jev_missing_request_delivery_or_original_evidence_refuses(self):
        for missing in (10, 11, 12, 13):
            with self.subTest(missing=missing):
                with self.assertRaises(SemanticRefusal):
                    self.jev([r for r in self.chain() if r["id"] != missing])
        self.assertEqual(self.publications(), [])

    def test_unretained_timeline_and_wrong_original_pc_word_refuse(self):
        for mutation in ("timeline", "pc_word"):
            with self.subTest(mutation=mutation):
                rows = self.chain()
                if mutation == "timeline":
                    rows[2]["payload"]["timeline_ids"] = ["9", "10"]
                    rows[-1]["payload"]["provenance"]["timeline_ids"] = ["9", "10"]
                else:
                    p = rows[1]["payload"]
                    p["ha"]["derived.computer_active"]["state"] = "off"
                    p["snapshot_digest"] = digest({k: p[k] for k in ("observed_at", "ha", "acquisition_duration_s")})
                with self.assertRaisesRegex(SemanticRefusal, "complete_jev_original_evidence|pc_packet_original_mismatch"):
                    self.jev(rows)

    def test_jev_exclusive_expiry_cannot_be_extended_by_late_receipt(self):
        rows = self.chain()
        self.now = rows[-1]["payload"]["valid_until"]
        self.assertEqual(self.jev(rows)["status"], "expired")
        self.assertEqual(self.publications(), [])

    def test_jev_wrong_generation_hash_version_and_future_cutoff_refuse(self):
        for mutation in ("generation", "packet", "version", "cutoff", "delivery", "clock"):
            with self.subTest(mutation=mutation):
                rows = self.chain()
                if mutation == "generation": rows[-1]["payload"]["generation"] = 2
                if mutation == "packet": rows[2]["payload"]["request"]["state"]["devices"]["pc_active"] = "off"
                if mutation == "version": rows[-1]["payload"]["judgment"]["model_version"] = "other"
                if mutation == "cutoff": rows[-1]["payload"]["provenance"]["observed_at"] = NOW + .1
                if mutation == "delivery": rows[3]["payload"]["current"] = False
                if mutation == "clock": rows[-1]["payload"]["available_at_s"] += 1
                with self.assertRaises(SemanticRefusal): self.jev(rows)
        self.assertEqual(self.publications(), [])

    def test_no_person_multiple_person_and_abstention_never_support_activity(self):
        for people, activity, status in (("none", "computer_work", "ready"), ("multiple", "computer_work", "ready"),
                                         ("one", "insufficient_evidence", "abstain")):
            with self.subTest(people=people, status=status):
                rows = self.chain(people=people, activity=activity, status=status)
                join = self.make_join()
                # Separate isolated actual journal for each immutable source history.
                j = Journal(Path(self.temp.name) / (people + status + ".sqlite"), grants=[self.grant], profile=PROFILE,
                            live_config=self.config, live_receipt_resolver=join.resolve_receipt, clock=lambda: self.now)
                self.addCleanup(j.close)
                result = join.publish_jev(rows, output_id=14, received_at=self.now, journal=j, grant=j.authenticate(TOKEN))
                self.assertFalse(result["supports_engagement"])
                payload = json.loads(j.db.execute("SELECT body FROM publications").fetchone()[0])["payload"]
                self.assertEqual(payload["status"], "unobservable")
                self.assertIsNone(payload["value"])
                self.assertIsNone(payload["person_ref"])

    def test_unclosed_qualified_ambiguous_or_reset_association_never_grants_person_identity(self):
        for proposal in ({"status": "qualified", "person_ref": {"pid": 1}},
                         {"status": "ambiguous"}, {"status": "reset"}):
            with self.subTest(proposal=proposal):
                with self.assertRaisesRegex(SemanticRefusal, "consumer_contract_not_closed"):
                    self.pc([self.ha()], association=proposal)
                with self.assertRaisesRegex(SemanticRefusal, "consumer_contract_not_closed"):
                    self.jev(self.chain(), association=proposal)
        self.assertEqual(self.publications(), [])

    def test_source_barrier_does_not_allow_old_adapter_to_resume_new_generation(self):
        self.pc([self.ha()])
        self.now = NOW + 1
        b = barrier(self.publications(), event_id="authored-ha-reset", at=self.now, schema="home-live-fact-1")
        b.update(manifest_ref=deepcopy(self.config["manifest_ref"]), source_id="ha-semantic")
        envelope = event(b)
        self.journal.append_event(envelope, self.principal)
        self.now += 1
        with self.assertRaises(JournalError) as caught:
            self.pc([self.ha(30, NOW + 1)])
        self.assertEqual(caught.exception.code, "source_generation_conflict")

    def test_grant_and_original_resolver_are_enforced_by_actual_journal(self):
        original = self.journal.live_receipt_resolver
        self.journal.live_receipt_resolver = lambda *_: (_ for _ in ()).throw(KeyError("missing"))
        with self.assertRaises(JournalError) as caught:
            self.pc([self.ha()])
        self.assertEqual(caught.exception.code, "receipt_unavailable")
        self.journal.live_receipt_resolver = original
        self.pc([self.ha()])
        self.assertEqual(len(self.publications()), 1)

    def test_repeated_pc_snapshots_do_not_mint_input_events_or_engagement(self):
        self.pc([self.ha()])
        self.now += 1
        self.pc([self.ha(21, NOW + 1)])
        facts = [p["payload"] for p in self.publications()]
        self.assertEqual({p["value"] for p in facts}, {"active_unattributed"})
        raw_lastactive = []
        for p in facts:
            ref = p["original_receipt_refs"][0]
            e = self.join.original_receipt(ref["store_id"], ref["receipt_id"])["events"][0]
            raw_lastactive.append(e["payload"]["ha"]["sensor.formd_t1_lastactive"])
        self.assertEqual(raw_lastactive[0], raw_lastactive[1])
        # The actual shared JS reducer sees schema-admitted facts and cannot
        # turn repeat device snapshots into a person-bound use episode.
        script = """const fs=require('fs'); const x=JSON.parse(fs.readFileSync(0,'utf8'));
const R=require('./app/src/home-engagement-reducer.js');
const r=R.createReducer({identity:x.scope,profile:'home-live-shadow-1',validateFact:()=>({ok:true}),
 live_policy:{support_methods:[x.method],refinement_methods:[],negative_methods:[],allow_experimental:true}});
const out=x.facts.map((f,i)=>r.step({t:f.available_at+.1,seq:i,facts:[f]}));
process.stdout.write(JSON.stringify(out));"""
        completed = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[3],
            input=json.dumps({"scope": SCOPE, "method": METHOD, "facts": facts}), text=True,
            capture_output=True, timeout=10, check=True)
        self.assertTrue(all(not row["relations"] for row in json.loads(completed.stdout)))

    def test_binding_mutation_and_undeclared_camera_device_correlation_refuse(self):
        self.join.policy["ha_source_id"] = "other"
        with self.assertRaisesRegex(SemanticRefusal, "configuration_changed"):
            self.pc([self.ha()])
        policy = deepcopy(self.policy)
        policy["jev"]["kind_sensor_roots"]["perception.output"] = ["device:pc"]
        with self.assertRaisesRegex(ValueError, "independent_device_root_required"):
            self.make_join(**policy)


if __name__ == "__main__":
    unittest.main()
