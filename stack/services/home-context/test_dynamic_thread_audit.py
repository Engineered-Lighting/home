"""Public synthetic service regressions for thread audit findings 3, 4 and 5."""
import copy
import json
import subprocess
import unittest

from dynamic_validation import component_id, content_digest, validate_dynamic
from journal import digest
import test_dynamic_context_service as fixtures
from test_dynamic_context_service import (ROOT, SCOPE, bootstrap,
                                         envelope, example, next_commit)


def recovery_parity(snapshot, publications, t):
    """Compare actual snapshot ingestion with uninterrupted original receipts."""
    script = r"""
const W=require('./tools/home-dynamic-context-shadow-worker.cjs');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{
const x=JSON.parse(input),w=W.createWorker({identity:x.identity});w.ingestSnapshot(x.snapshot,x.t);
const cut={journal_epoch:x.snapshot.stream_epoch,cut_cursor:x.snapshot.cursor,authority:x.snapshot.authority};
function state(rows){return {geometry:W.buildGeometrySnapshot({identity:x.identity,registry:{digest:x.registry},t:x.t,cut,rows}).object_heads,
facts:W.currentFacts(rows,x.t,cut).filter(f=>f.available_at<=x.t&&f.valid_until>x.t).sort((a,b)=>a.id.localeCompare(b.id))};}
process.stdout.write(JSON.stringify({recovered:state(w.receiptLog()),uninterrupted:state(x.publications.map(p=>({publication:W.normalizePublication(p),received_at:p.committed_at})))}));
});
"""
    run = subprocess.run(["node", "-e", script], input=json.dumps({"identity": SCOPE, "registry": example("home-geometry-3")["registry_digest"], "snapshot": snapshot, "publications": publications, "t": t}), cwd=ROOT, text=True, capture_output=True, timeout=10)
    if run.returncode:
        raise AssertionError(run.stdout + run.stderr)
    return json.loads(run.stdout)


class DynamicThreadAuditTests(unittest.TestCase):
    setUp = fixtures.DynamicServiceTests.setUp
    tearDown = fixtures.DynamicServiceTests.tearDown
    geometry = fixtures.DynamicServiceTests.geometry
    intent = fixtures.DynamicServiceTests.intent
    code = fixtures.DynamicServiceTests.code

    def snapshot(self):
        return self.j.snapshot(self.grant, SCOPE["site_id"], SCOPE["room_id"], consumer_id=self.grant.expected_worker_id)

    def recovery_proofs(self):
        return {table: [tuple(row) for row in self.j.db.execute("SELECT * FROM " + table + " ORDER BY 1,2,3").fetchall()]
                for table in ("dynamic_snapshot_masks", "dynamic_deliveries", "consumers")}

    def assert_recovery_parity(self, snapshot):
        publications = self.j.page(self.grant, SCOPE["site_id"], SCOPE["room_id"])["items"]
        states = recovery_parity(snapshot, publications, self.now)
        self.assertEqual(states["recovered"], states["uninterrupted"])

    def batch(self, *, t=0, seq=100, facts=None):
        batch = example("home-fact-batch-2")
        fact = batch["facts"][0]
        fact.update(id="fact:" + str(seq), observed_at=t, available_at=t, valid_until=t + 1)
        fact["capture"].update(capture_seq=seq, observed_at=t)
        batch.update(id="batch:" + str(seq), observed_at=t, available_at=t, valid_until=t + 1, facts=facts or [fact])
        batch["observed_at"] = min(f["observed_at"] for f in batch["facts"])
        batch["available_at"] = max(f["available_at"] for f in batch["facts"])
        batch["valid_until"] = max(f["valid_until"] for f in batch["facts"])
        return batch

    def request(self, *, obj=None, ident="cached-audit"):
        request = example("home-evidence-request-2")
        request.update(id=ident, available_at=self.now, deadline=self.now + .5)
        if obj:
            request.update(object_ref=obj["object_ref"], person_ref=obj["person_ref"])
        self.j.request_evidence(request, self.grant)
        return self.j.lookup_request(ident, self.grant, SCOPE["site_id"], SCOPE["room_id"])["terminal_reply"]

    def test_partial_snapshot_keeps_other_object_and_unmodified_original(self):
        initial = self.geometry()
        second = copy.deepcopy(initial["objects"][0])
        second["object_ref"]["oid"] = "book-2"
        second["person_ref"]["pid"] = 2
        initial["objects"].append(second)
        receipt = self.j.append_event(envelope(initial), self.grant)
        self.now = .2
        partial = self.geometry(2)
        partial["objects"] = [copy.deepcopy(second)]
        partial["objects"][0]["patches"][0]["centre_m"][0] += .02
        self.j.append_event(envelope(partial), self.grant)
        snapshot = self.snapshot()
        self.assertEqual((snapshot["schema"], snapshot["version"]), ("home-context-snapshot-2", 2))
        self.assertEqual([(h["object_ref"]["oid"], h["publication_ref"]["cursor"]) for h in snapshot["component_heads"]], [("book-1", 1), ("book-2", 2)])
        original = snapshot["items"][0]
        self.assertEqual(original["payload"], initial)
        self.assertEqual(original["payload_digest"], receipt["payload_digest"])
        self.assertEqual(len(original["payload"]["objects"]), 2)
        # The actual worker installs selectors, not a reconstructed publication.
        script = "const W=require('./tools/home-dynamic-context-shadow-worker.cjs');let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>{const x=JSON.parse(s),w=W.createWorker({identity:x.identity});w.ingestSnapshot(x.snapshot,.2);process.stdout.write(JSON.stringify(w.receiptLog()));});"
        run = subprocess.run(["node", "-e", script], input=json.dumps({"identity": SCOPE, "snapshot": snapshot}), cwd=ROOT, text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        rows = json.loads(run.stdout)
        self.assertEqual(rows[0]["component_ids"], [component_id(initial["objects"][0], True)])
        self.assertEqual(rows[0]["publication"]["payload"], initial)
        self.assertEqual(rows[1]["component_ids"], [component_id(partial["objects"][0], True)])

    def test_mixed_child_expiry_selects_only_live_fact_from_original_batch(self):
        base = self.batch()
        short, live = copy.deepcopy(base["facts"][0]), copy.deepcopy(base["facts"][0])
        short.update(id="short", valid_until=.1)
        live.update(id="live", valid_until=1)
        live["object_ref"]["oid"] = "book-2"
        live["person_ref"]["pid"] = 2
        base["facts"] = [short, live]
        self.j.append_event(envelope(base), self.grant)
        self.now = .2
        snapshot = self.snapshot()
        self.assertEqual([h["publication_ref"]["component_id"] for h in snapshot["component_heads"]], ["live"])
        self.assertEqual(snapshot["items"][0]["payload"], base)
        wrong = copy.deepcopy(base); wrong["valid_until"] = .1
        self.assertFalse(validate_dynamic(wrong)["ok"])

    def test_same_observation_competing_fact_ids_survive_delivery_order(self):
        first = self.batch()
        self.j.append_event(envelope(first), self.grant)
        self.now = .1
        second = copy.deepcopy(first)
        second.update(id="interpretation-later", available_at=.1)
        second["facts"][0].update(id="closed-interpretation", kind="closed", value=True, available_at=.1)
        self.j.append_event(envelope(second), self.grant)
        heads = self.snapshot()["component_heads"]
        self.assertEqual({h["publication_ref"]["component_id"] for h in heads}, {"fact:100", "closed-interpretation"})
        self.now = .2
        newer = self.batch(t=.2, seq=101)
        self.j.append_event(envelope(newer), self.grant)
        self.assertEqual([h["publication_ref"]["component_id"] for h in self.snapshot()["component_heads"]], ["fact:101"])

    def test_epoch_handoff_retires_only_named_object_lineage(self):
        initial = self.geometry()
        other = copy.deepcopy(initial["objects"][0]); other["object_ref"]["oid"] = "book-2"
        initial["objects"].append(other)
        self.j.append_event(envelope(initial), self.grant)
        self.now = .2
        new = self.geometry(2)
        new["objects"][0]["object_ref"]["object_epoch"] = "books-2"
        new["objects"][0]["person_ref"]["track_epoch"] = "people-2"
        new["objects"][0]["association_generation"] = 1
        self.j.append_event(envelope(new), self.grant)
        heads = self.snapshot()["component_heads"]
        self.assertEqual(len(heads), 2)
        self.assertEqual({(h["object_ref"]["oid"], h["object_ref"]["object_epoch"]) for h in heads}, {("book-1", "books-2"), ("book-2", "books-1")})

    def test_closed_withdrawn_occluded_heads_forbid_older_open_cache(self):
        for index, state in enumerate(("closed", "withdrawn", "occluded")):
            with self.subTest(state=state):
                self.now = index * .4
                open_head = self.geometry(index * 2 + 1)
                self.j.append_event(envelope(open_head), self.grant)
                self.now += .2
                negative = self.geometry(index * 2 + 2)
                negative["objects"][0].update(state=state, patches=[])
                self.j.append_event(envelope(negative), self.grant)
                result = self.request(ident="cache-" + state)
                self.assertEqual((result["status"], result["estimate_ref"]), ("unavailable", None))
                self.assertEqual(self.snapshot()["component_heads"][0]["publication_ref"]["cursor"], self.j.db.execute("SELECT max(cursor) FROM publications WHERE payload_type='home-geometry-3'").fetchone()[0])

    def test_expired_current_geometry_is_snapshot_tombstone_and_cache_fence(self):
        initial = self.geometry()
        self.j.append_event(envelope(initial), self.grant)
        self.now = .2
        expiring = self.geometry(2); expiring["valid_until"] = .3
        self.j.append_event(envelope(expiring), self.grant)
        self.now = .4
        snapshot = self.snapshot()
        self.assertEqual(snapshot["component_heads"][0]["valid_until"], .3)
        self.assertEqual(snapshot["component_heads"][0]["publication_ref"]["cursor"], 2)
        self.assertEqual(self.request()["status"], "unavailable")

    def test_current_scoped_person_and_object_epochs_fence_old_cache(self):
        initial = self.geometry()
        self.j.append_event(envelope(initial), self.grant)
        self.now = .2
        new = self.geometry(2)
        new["objects"][0]["person_ref"]["track_epoch"] = "new-person-track"
        new["objects"][0]["object_ref"]["object_epoch"] = "new-object-track"
        new["objects"][0]["association_generation"] = 1
        self.j.append_event(envelope(new), self.grant)
        self.assertEqual(self.request(obj=initial["objects"][0], ident="old-identity")["status"], "unavailable")
        reply = self.request(obj=new["objects"][0], ident="new-identity")
        self.assertEqual(reply["status"], "supported")
        self.assertEqual(reply["estimate_ref"]["cursor"], 2)

    def test_association_correction_fences_object_until_new_observation(self):
        initial = self.geometry()
        other = copy.deepcopy(initial["objects"][0]); other["object_ref"]["oid"] = "book-2"; other["person_ref"]["pid"] = 2
        initial["objects"].append(other)
        self.j.append_event(envelope(initial), self.grant)
        self.now = .1
        self.j.intent(self.intent("association_correction"), self.grant)
        self.assertEqual(self.request(ident="corrected-object")["status"], "unavailable")
        self.assertEqual(self.request(obj=other, ident="unrelated-reader")["status"], "supported")
        self.now = .2
        refreshed = self.geometry(2); refreshed["objects"][0]["association_generation"] = 1
        self.j.append_event(envelope(refreshed), self.grant)
        self.assertEqual(self.request(ident="fresh-after-correction")["status"], "supported")

    def test_same_capture_closed_conflict_denies_cache_despite_later_open_delivery(self):
        facts = self.batch(seq=1)
        facts["facts"][0].update(kind="closed", value=True)
        self.j.append_event(envelope(facts), self.grant)
        self.now = .1
        geometry = self.geometry(1)
        geometry["observed_at"] = geometry["capture"]["observed_at"] = 0
        self.j.append_event(envelope(geometry), self.grant)
        self.assertEqual(self.request()["status"], "unavailable")

    def test_registration_change_fences_other_objects_old_geometry(self):
        self.j.append_event(envelope(self.geometry()), self.grant)
        self.now = .2
        updated = self.geometry(2); updated["registration_generation"] = 2
        updated["objects"][0]["object_ref"]["oid"] = "book-2"
        self.j.append_event(envelope(updated), self.grant)
        self.assertEqual(self.request()["status"], "unavailable")

    def test_pending_future_geometry_refuses_snapshot_without_proofs_then_recovers(self):
        old = self.geometry()
        self.j.append_event(envelope(old), self.grant)
        self.snapshot()
        self.now = .2
        future = self.geometry(2)
        future["available_at"] = .4
        future["objects"][0].update(state="closed", patches=[])
        self.j.append_event(envelope(future), self.grant)
        before = self.recovery_proofs()
        self.code("snapshot_pending_inputs", self.snapshot)
        self.assertEqual(self.recovery_proofs(), before)
        self.assertEqual(self.request(ident="before-availability")["status"], "supported")
        self.now = .4
        snapshot = self.snapshot()
        self.assertEqual(snapshot["component_heads"][0]["publication_ref"]["cursor"], 2)
        self.assert_recovery_parity(snapshot)
        self.assertEqual(self.request(ident="after-availability")["status"], "unavailable")

    def test_future_fact_interpretation_only_conflicts_at_original_availability(self):
        geometry = self.geometry()
        self.j.append_event(envelope(geometry), self.grant)
        first = self.batch()
        self.j.append_event(envelope(first), self.grant)
        self.now = .2
        future = copy.deepcopy(first)
        future.update(id="future-interpretation", available_at=.4)
        future["facts"][0].update(id="future-closed", kind="closed", value=True, available_at=.4)
        self.j.append_event(envelope(future), self.grant)
        before = self.recovery_proofs()
        self.code("snapshot_pending_inputs", self.snapshot)
        self.assertEqual(self.recovery_proofs(), before)
        self.assertEqual(self.request(ident="before-fact-availability")["status"], "supported")
        self.now = .4
        self.assertEqual({h["publication_ref"]["component_id"] for h in self.snapshot()["component_heads"] if h["kind"] != "geometry"}, {"fact:100", "future-closed"})
        self.assertEqual(self.request(ident="after-fact-availability")["status"], "unavailable")

    def test_pending_fact_child_refuses_snapshot_until_whole_original_is_eligible(self):
        batch = self.batch()
        positive = batch["facts"][0]
        delayed = copy.deepcopy(positive)
        delayed.update(id="later-closed-child", kind="closed", value=True, available_at=.1)
        batch.update(available_at=.1, facts=[positive, delayed])
        self.j.append_event(envelope(batch), self.grant)
        before = self.recovery_proofs()
        self.code("snapshot_pending_inputs", self.snapshot)
        self.assertEqual(self.recovery_proofs(), before)
        self.assertFalse(before["dynamic_snapshot_masks"])
        self.now = .1
        snapshot = self.snapshot()
        self.assertEqual({h["publication_ref"]["component_id"] for h in snapshot["component_heads"]}, {positive["id"], delayed["id"]})
        self.assertEqual(snapshot["items"][0]["payload"], batch)
        self.assert_recovery_parity(snapshot)

    def test_expired_opposing_same_capture_assertion_fences_cache_until_new_capture(self):
        self.now = .2
        geometry = self.geometry(1)
        self.j.append_event(envelope(geometry), self.grant)
        batch = self.batch(t=.2)
        negative = copy.deepcopy(batch["facts"][0])
        negative.update(id="short-conflict", kind="closed", value=True, valid_until=.3)
        batch["facts"].append(negative)
        self.j.append_event(envelope(batch), self.grant)
        self.now = .4
        self.assertEqual(self.request(ident="expired-conflict")["status"], "unavailable")
        self.assertEqual({h["publication_ref"]["component_id"] for h in self.snapshot()["component_heads"] if h["kind"] != "geometry"}, {"fact:100"})
        refreshed = self.geometry(2)
        self.j.append_event(envelope(refreshed), self.grant)
        resolved = self.batch(t=.4, seq=101)
        self.j.append_event(envelope(resolved), self.grant)
        self.assertEqual(self.request(ident="new-original-resolved")["status"], "supported")

    def test_same_observed_negative_geometry_has_stable_service_worker_priority(self):
        closed = self.geometry(1)
        closed["objects"][0].update(state="closed", patches=[])
        self.j.append_event(envelope(closed), self.grant)
        self.now = .1
        interpretation = self.geometry(2)
        interpretation["observed_at"] = interpretation["capture"]["observed_at"] = 0
        self.j.append_event(envelope(interpretation), self.grant)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["component_heads"][0]["publication_ref"]["cursor"], 1)
        self.assertEqual(self.request()["status"], "unavailable")
        page = self.j.page(self.grant, SCOPE["site_id"], SCOPE["room_id"])
        script = "const W=require('./tools/home-dynamic-context-shadow-worker.cjs');let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>{const x=JSON.parse(s),v=W.buildGeometrySnapshot({identity:x.identity,registry:{digest:x.digest},t:.1,cut:x.cut,rows:x.publications.filter(p=>p.payload_type==='home-geometry-3').map(p=>({publication:W.normalizePublication(p),received_at:p.committed_at}))});process.stdout.write(JSON.stringify(v));});"
        run = subprocess.run(["node", "-e", script], input=json.dumps({"identity": SCOPE, "digest": closed["registry_digest"], "cut": {"cut_cursor": page["next_cursor"], "authority": snapshot["authority"], "journal_epoch": snapshot["stream_epoch"]}, "publications": page["items"]}), cwd=ROOT, text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["object_heads"][0]["estimate_ref"]["cursor"], 1)

    def test_source_envelope_cannot_hide_original_availability(self):
        geometry = self.geometry()
        geometry["available_at"] = 1
        geometry["valid_until"] = 2
        event = envelope(geometry); event["available_at"] = 0
        self.code("invalid_payload", lambda: self.j.append_event(event, self.grant))
        batch = self.batch()
        batch["available_at"] = batch["facts"][0]["available_at"] = 1
        batch["valid_until"] = batch["facts"][0]["valid_until"] = 2
        event = envelope(batch); event["available_at"] = 0
        self.code("invalid_payload", lambda: self.j.append_event(event, self.grant))

    def test_unselected_fact_cannot_enter_materialized_checkpoint(self):
        batch = self.batch()
        extra = copy.deepcopy(batch["facts"][0]); extra.update(id="unselected-fact", valid_until=.1)
        batch["facts"].append(extra)
        self.j.append_event(envelope(batch), self.grant)
        self.now = .2
        self.snapshot()
        commit = bootstrap(self.j, self.grant, t=.2)
        row = commit["checkpoint"]["retained_inputs"][0]
        row["component_ids"] = [batch["facts"][0]["id"]]
        commit["checkpoint"]["facts"] = [extra]
        self.code("invalid_reference", lambda: self.j.worker_commit(commit, self.grant))

    def test_late_eligible_obsolete_capture_does_not_regress_authority(self):
        self.j.append_event(envelope(self.geometry(1)), self.grant)
        original = self.j.dynamic.authority(SCOPE["site_id"], SCOPE["room_id"])
        self.now = .2
        future = self.geometry(2)
        future.update(available_at=.6, registration_generation=2)
        future["objects"][0].update(state="closed", patches=[])
        self.j.append_event(envelope(future), self.grant)
        self.assertEqual(self.j.dynamic.authority(SCOPE["site_id"], SCOPE["room_id"]), original)
        self.now = .3
        self.j.append_event(envelope(self.geometry(3)), self.grant)
        self.now = .6
        snapshot = self.snapshot()
        self.assertEqual(snapshot["component_heads"][0]["publication_ref"]["cursor"], 3)
        self.assertEqual(snapshot["authority"], original)
        self.assertEqual(self.request(ident="obsolete-future")["status"], "supported")
        self.assertEqual(self.snapshot()["authority"], original)

    def test_future_registration_applies_once_when_eligible_checkpoint_read(self):
        self.j.append_event(envelope(self.geometry(1)), self.grant)
        authority = self.j.dynamic.authority(SCOPE["site_id"], SCOPE["room_id"])
        self.now = .2
        future = self.geometry(2)
        future.update(available_at=.4, registration_generation=2)
        self.j.append_event(envelope(future), self.grant)
        self.code("snapshot_pending_inputs", self.snapshot)
        self.assertEqual(self.j.dynamic.authority(SCOPE["site_id"], SCOPE["room_id"]), authority)
        self.now = .4
        checkpoint = self.j.checkpoint(self.grant.expected_worker_id, self.grant, SCOPE["site_id"], SCOPE["room_id"])
        self.assertEqual(checkpoint["schema"], "home-context-worker-checkpoint-response-3")
        self.assertEqual(checkpoint["authority"]["registration_generation"], 2)
        self.assertEqual(checkpoint["authority"]["generation"], authority["generation"] + 1)
        self.assertEqual(self.snapshot()["authority"], checkpoint["authority"])

    def test_same_observed_different_associations_remain_original_fact_competitors(self):
        first = self.batch()
        self.j.append_event(envelope(first), self.grant)
        self.now = .1
        competing = copy.deepcopy(first)
        competing.update(id="new-association-interpretation", available_at=.1)
        competing["facts"][0].update(id="different-association", association_generation=1, available_at=.1)
        self.j.append_event(envelope(competing), self.grant)
        self.assertEqual({(h["publication_ref"]["component_id"], h["association_generation"]) for h in self.snapshot()["component_heads"]}, {("fact:100", 0), ("different-association", 1)})
        self.assertEqual(self.snapshot()["authority"]["association_generation"], 1)
        self.now = .2
        newer = self.batch(t=.2, seq=101)
        newer["facts"][0]["association_generation"] = 1
        self.j.append_event(envelope(newer), self.grant)
        self.assertEqual([h["publication_ref"]["component_id"] for h in self.snapshot()["component_heads"]], ["fact:101"])

    def test_old_dynamic_live_dialects_are_explicitly_rejected(self):
        for name in ("home-fact-batch-1", "home-geometry-2", "home-engagement-state-2", "home-lighting-policy-decision-1", "home-context-worker-checkpoint-2", "home-context-worker-commit-2", "home-context-replay-3"):
            with self.subTest(schema=name):
                self.assertFalse(validate_dynamic(example(name))["ok"])

    def test_checkpoint_selected_components_bind_original_and_remain_immutable(self):
        geometry = self.geometry()
        self.j.append_event(envelope(geometry), self.grant)
        commit = bootstrap(self.j, self.grant)
        row = commit["checkpoint"]["retained_inputs"][0]
        row["component_ids"] = ["geometry:" + "0" * 64]
        self.code("invalid_reference", lambda: self.j.worker_commit(commit, self.grant))
        self.snapshot()
        row["component_ids"] = [component_id(geometry["objects"][0], True)]
        self.j.worker_commit(commit, self.grant)
        self.now = 1
        later = next_commit(self.j, self.grant, t=1, kind="semantic")
        self.code("invalid_reference", lambda: self.j.worker_commit(later, self.grant))
        later["checkpoint"]["retained_inputs"][0]["component_ids"] = row["component_ids"]
        self.j.worker_commit(later, self.grant)

    def test_rejected_first_intent_cannot_apply_later_and_conflict_is_atomic(self):
        self.j.worker_commit(bootstrap(self.j, self.grant), self.grant)
        intent = self.intent("too_bright")
        self.j.intent(intent, self.grant)
        first = next_commit(self.j, self.grant, t=0, kind="semantic", intent_results=[{"id": intent["id"], "status": "rejected", "reason": "no_qualified_relation"}])
        self.j.worker_commit(first, self.grant)
        original = self.j.lookup_intent(intent["id"], self.grant, SCOPE["site_id"], SCOPE["room_id"])["application_result"]
        cursor = self.j._meta("cursor")
        self.now = 1
        changed = next_commit(self.j, self.grant, t=1, kind="semantic", intent_results=[{"id": intent["id"], "status": "applied", "reason": "declared_output"}])
        self.code("intent_result_conflict", lambda: self.j.worker_commit(changed, self.grant))
        self.assertEqual(self.j._meta("cursor"), cursor)
        self.assertEqual(self.j.checkpoint(self.grant.expected_worker_id, self.grant, SCOPE["site_id"], SCOPE["room_id"])["commit_seq"], 2)
        repeat = next_commit(self.j, self.grant, t=1, kind="semantic", intent_results=[{"id": intent["id"], "status": original["status"], "reason": original["reason"]}])
        self.j.worker_commit(repeat, self.grant)
        self.assertEqual(self.j.lookup_intent(intent["id"], self.grant, SCOPE["site_id"], SCOPE["room_id"])["application_result"], original)
        policy = repeat["outputs"][1]
        self.assertEqual((policy["intent_results"][0]["decided_at"], policy["intent_results"][0]["decision_ref"]), (0, first["outputs"][1]["id"]))


if __name__ == "__main__":
    unittest.main()
