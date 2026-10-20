"""Fresh synthetic journal tests; no private ledgers, models or devices."""
import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dynamic_validation import INPUTS, OUTPUTS, content_digest, validate_dynamic
from journal import DYNAMIC_PROFILE, Journal, JournalError, digest
from schema_registry import schema_digest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXAMPLES = ROOT / "contracts" / "home-dynamic-context" / "examples"


def example(name):
    return json.loads((EXAMPLES / (name + ".json")).read_text(encoding="utf-8"))


SCOPE = {k: example("home-geometry-3")[k] for k in ("site_id", "room_id", "session_id", "domain_epoch")}


def grant_config(*, operations=None, types=None, token="synthetic-backend-token", worker="synthetic-worker"):
    types = sorted(INPUTS | OUTPUTS) if types is None else types
    geo = example("home-geometry-3")
    return {"principal_id": worker, "producer_id": worker,
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "scopes": [{"site_id": SCOPE["site_id"], "room_id": SCOPE["room_id"]}],
            "operations": operations if operations is not None else ["read", "event", "intent", "evidence_request", "evidence_reply", "worker_commit"],
            "payload_types": types, "schema_pins": {n: schema_digest(n) for n in types}, "revisions": ["1.0", "2.0", "3.0"],
            "source_ids": [geo["capture"]["source_id"]], "expected_worker_id": worker,
            "expected_actor_ref": example("home-intent-event-2")["actor_ref"],
            "registration_bindings": {"synthetic-registration": {"source_id": geo["capture"]["source_id"],
                "geometry_revision": "synthetic-geometry", "registry_digest": geo["registry_digest"], "coverage_refs": ["synthetic-pages"]}}}


def envelope(payload, producer="synthetic-worker"):
    t = payload.get("observed_at", payload.get("available_at", payload.get("t", 0)))
    return {"schema": "home-context-event-1", "schema_version": "1.0", "id": payload["id"],
            "producer_id": producer, "stream_epoch": "synthetic-source-epoch", **SCOPE,
            "observed_at": t, "available_at": payload.get("available_at", t), "valid_until": payload.get("valid_until", t + 3),
            "payload_type": payload["schema"], "payload_version": str(payload["version"]) + ".0",
            "revision": str(payload["version"]) + ".0", "payload": payload}


def normalized(publication):
    return {k: publication[k] for k in ("journal_epoch", "cursor", "id", "producer_id", "committed_at", "payload_type", "payload_version", "payload_digest", "payload")}


def bootstrap(journal, grant, *, t=0, received_at=None, limit=128):
    fence = journal.dynamic.input_cut(grant, SCOPE["site_id"], SCOPE["room_id"], consumer_id=grant.expected_worker_id, after=0, t=t, limit=limit)
    publications = fence["publications"]
    receipts = []
    for p in publications:
        received = t if received_at is None else received_at
        payload = p["payload"]
        receipts.append({"ref": {"journal_epoch": journal.stream_epoch, "cursor": p["cursor"], "payload_digest": p["payload_digest"]},
                         "received_at": received, "eligible_at": max(payload.get("observed_at", payload.get("available_at", p["committed_at"])), payload.get("available_at", p["committed_at"]), p["committed_at"], received)})
    cut = {"schema": "home-control-input-cut-1", "version": 1, **SCOPE, "runtime_epoch": "synthetic-runtime",
           "journal_epoch": journal.stream_epoch, "cut_id": fence["cut_id"], "input_cursor": fence["cut_cursor"], "as_of_t": t,
           "receipts": receipts, "eligible_refs": [r["ref"] for r in receipts if r["eligible_at"] <= t],
           "buffered_refs": [r["ref"] for r in receipts if r["eligible_at"] > t], "authority": fence["authority"],
           "complete": fence["complete"], "geometry_snapshot_digest": "a" * 64, "digest": "a" * 64}
    cut["digest"] = content_digest(cut)
    cp = example("home-context-worker-checkpoint-3")
    cp.update(**SCOPE, runtime_epoch="synthetic-runtime", journal_epoch=journal.stream_epoch,
              interface_digest=journal.schemas.interface_digest, commit_seq=1, semantic_seq=-1, control_seq=0,
              input_cursor=cut["input_cursor"], last_t=t, authority=fence["authority"], pending_inputs=[],
              geometry_inputs=[], retained_inputs=[], facts=[], intents=[], receipts=receipts, next_semantic_t=t + 1, next_control_t=t + .2, pipeline=None)
    cp["reducer"].update(last_t=None, semantic_seq=-1, roots=[], relations=[])
    cp["pending_inputs"] = [{"publication": normalized(p), "received_at": r["received_at"]}
                            for p, r in zip(publications, receipts) if r["eligible_at"] > t]
    cp["retained_inputs"] = [{"publication": normalized(p), "received_at": r["received_at"]}
                             for p, r in zip(publications, receipts) if r["eligible_at"] <= t]
    result = {"schema": "home-context-worker-commit-3", "version": 3, "id": "synthetic-bootstrap", **SCOPE,
              "runtime_epoch": cp["runtime_epoch"], "journal_epoch": journal.stream_epoch, "kind": "bootstrap",
              "expected_commit_seq": 0, "expected_input_cursor": 0, "expected_authority": fence["authority"],
              "input_cut": cut, "checkpoint": cp, "outputs": []}
    return result


def next_commit(journal, grant, *, t, kind, intent_results=None):
    old = journal.checkpoint(grant.expected_worker_id, grant, SCOPE["site_id"], SCOPE["room_id"])["checkpoint"]
    fence = journal.dynamic.input_cut(grant, SCOPE["site_id"], SCOPE["room_id"], consumer_id=grant.expected_worker_id, after=old["input_cursor"], t=t)
    receipts = copy.deepcopy(old["receipts"])
    for p in fence["publications"]:
        payload = p["payload"]
        receipts.append({"ref": {"journal_epoch": journal.stream_epoch, "cursor": p["cursor"], "payload_digest": p["payload_digest"]}, "received_at": t,
                         "eligible_at": max(payload.get("observed_at", payload.get("available_at", p["committed_at"])), payload.get("available_at", p["committed_at"]), p["committed_at"], t)})
    snapshot = {"schema": "home-dynamic-geometry-snapshot-1", "version": 1, **SCOPE, "as_of_t": t,
                "journal_epoch": journal.stream_epoch, "input_cursor": fence["cut_cursor"], "authority": fence["authority"],
                "registry_digest": example("home-geometry-3")["registry_digest"], "object_heads": [], "digest": "a" * 64}
    snapshot["digest"] = content_digest(snapshot)
    cut = {"schema": "home-control-input-cut-1", "version": 1, **SCOPE, "runtime_epoch": old["runtime_epoch"],
           "journal_epoch": journal.stream_epoch, "cut_id": fence["cut_id"], "input_cursor": fence["cut_cursor"], "as_of_t": t,
           "receipts": receipts, "eligible_refs": [r["ref"] for r in receipts], "buffered_refs": [], "authority": fence["authority"],
           "complete": True, "geometry_snapshot_digest": snapshot["digest"], "digest": "a" * 64}
    cut["digest"] = content_digest(cut)
    cp = copy.deepcopy(old)
    cp.update(commit_seq=old["commit_seq"] + 1, input_cursor=fence["cut_cursor"], last_t=t,
              authority=fence["authority"], receipts=receipts, geometry_inputs=[], pending_inputs=[], retained_inputs=[])
    cp["retained_inputs"] = [{"publication": normalized(json.loads(journal.db.execute("SELECT body FROM publications WHERE cursor=?",
                                                   (r["ref"]["cursor"],)).fetchone()[0])), "received_at": r["received_at"]}
                             for r in receipts]
    outputs = []
    if kind == "semantic":
        cp["semantic_seq"] += 1
        engagement = example("home-engagement-state-3"); engagement.update(id="engagement:" + str(cp["commit_seq"]), t=t, semantic_seq=cp["semantic_seq"], relations=[])
        policy = example("home-lighting-policy-decision-2"); policy.update(id="policy:" + str(cp["commit_seq"]), t=t, semantic_seq=cp["semantic_seq"], requests=[], intent_results=intent_results or [])
        for result in policy["intent_results"]:
            saved = journal.lookup_intent(result["id"], grant, SCOPE["site_id"], SCOPE["room_id"])["application_result"]
            result.update(decided_at=saved["available_at"] if saved else t, decision_ref=saved["decision_ref"] if saved else policy["id"])
        old_ledger = old["pipeline"]["intent_outcomes"] if old["pipeline"] else []
        outcomes = {o["intent_id"]: copy.deepcopy(o) for o in old_ledger}
        intents = []
        for result in policy["intent_results"]:
            declaration = journal.lookup_intent(result["id"], grant, SCOPE["site_id"], SCOPE["room_id"])["intent"]
            intents.append(declaration)
            outcomes[result["id"]] = {"intent_id": result["id"], "intent_digest": digest(declaration),
                                      **{k: result[k] for k in ("status", "reason", "decided_at", "decision_ref")}}
        cp["pipeline"] = {"t": t, "commands": [], "policy": policy, "leases": [], "frozen_heads": [],
                          "intent_ids": [i["id"] for i in intents], "intents": intents, "intent_outcomes": list(outcomes.values()),
                          "fixtures": [], "config_digest": cp["config_digest"]}
        outputs = [engagement, policy]
        cp["reducer"]["semantic_seq"] = cp["semantic_seq"]
    if kind == "control":
        cp["control_seq"] += 1
        allocation = example("home-lighting-allocation-3"); allocation.update(id="allocation:" + str(cp["control_seq"]), t=t, control_seq=cp["control_seq"], targets=[], commands=[], assessments=[])
        execution = example("home-lighting-execution-1"); execution.update(id="execution:" + str(cp["control_seq"]), from_t=t - .2, t=t, control_seq=cp["control_seq"], samples=[{"t": t - .2 + (i + 1) * .02, "fixtures": []} for i in range(10)], boundary_state=[])
        control = example("home-lighting-control-decision-1"); control.update(id="control:" + str(cp["control_seq"]), t=t, semantic_seq=cp["semantic_seq"], control_seq=cp["control_seq"], input_cut_digest=cut["digest"], geometry_snapshot=snapshot, allocation=allocation, execution=execution, consumed_refs=[])
        outputs = [control]
    return {"schema": "home-context-worker-commit-3", "version": 3, "id": "step:" + str(cp["commit_seq"]), **SCOPE,
            "runtime_epoch": cp["runtime_epoch"], "journal_epoch": journal.stream_epoch, "kind": kind,
            "expected_commit_seq": old["commit_seq"], "expected_input_cursor": old["input_cursor"], "expected_authority": fence["authority"],
            "input_cut": cut, "checkpoint": cp, "outputs": outputs}


class DynamicServiceTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-dynamic-test-", dir=HERE)
        self.now = 0
        self.path = Path(self.work.name) / "fresh.db"
        self.j = Journal(self.path, grants=[grant_config()], profile=DYNAMIC_PROFILE, clock=lambda: self.now)
        self.grant = self.j.authenticate("synthetic-backend-token")

    def tearDown(self):
        self.j.close()
        self.work.cleanup()

    def code(self, expected, fn):
        with self.assertRaises(JournalError) as raised:
            fn()
        self.assertEqual(raised.exception.code, expected)

    def intent(self, kind="association_correction"):
        p = example("home-intent-event-2")
        p.update(id="synthetic-correction", available_at=self.now, valid_until=self.now + 30, kind=kind)
        return p

    def geometry(self, seq=1):
        p = example("home-geometry-3")
        p.update(id="synthetic-geometry:" + str(seq), observed_at=self.now, available_at=self.now, valid_until=self.now + 1)
        p["capture"].update(capture_seq=seq, observed_at=self.now)
        return p

    def test_authority_race_rejects_whole_transaction(self):
        prepared = bootstrap(self.j, self.grant)
        self.j.intent(self.intent(), self.grant)
        self.code("authority_conflict", lambda: self.j.worker_commit(prepared, self.grant))
        self.assertEqual(self.j.checkpoint(self.grant.expected_worker_id, self.grant, SCOPE["site_id"], SCOPE["room_id"])["commit_seq"], 0)
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM publications").fetchone()[0], 1)

    def test_lost_ack_exact_retry_precedes_new_authority_cas(self):
        prepared = bootstrap(self.j, self.grant)
        receipt = self.j.worker_commit(prepared, self.grant)
        self.j.intent(self.intent(), self.grant)
        self.assertEqual(self.j.worker_commit(prepared, self.grant), receipt)
        changed = copy.deepcopy(prepared)
        changed["checkpoint"]["next_control_t"] += .2
        self.code("identity_conflict", lambda: self.j.worker_commit(changed, self.grant))
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM workers").fetchone()[0], 1)

    def test_fresh_profile_and_legacy_guard_do_not_migrate(self):
        with self.assertRaisesRegex(ValueError, "fresh journal"):
            Journal(self.path, grants=[grant_config()], profile=DYNAMIC_PROFILE)
        with self.assertRaisesRegex(ValueError, "profile mismatch"):
            Journal(self.path, grants=[])

    def test_closed_schema_versions_and_geometry_basis(self):
        self.now = .19
        geo = self.geometry()
        wrong = envelope(geo); wrong["payload_version"] = "1.0"
        self.code("invalid_payload", lambda: self.j.append_event(wrong, self.grant))
        geo["objects"][0]["patches"][0]["u"] = [2, 0, 0]
        self.code("invalid_payload", lambda: self.j.append_event(envelope(geo), self.grant))

    def test_cut_pagination_freezes_tail_and_authority(self):
        self.now = .19
        self.j.append_event(envelope(self.geometry()), self.grant)
        self.j.intent(self.intent("too_bright"), self.grant)
        cut = self.j.dynamic.input_cut(self.grant, SCOPE["site_id"], SCOPE["room_id"], consumer_id=self.grant.expected_worker_id, after=0, t=.19, limit=1)
        self.assertFalse(cut["complete"])
        self.now = .2
        later = self.intent(); later["id"] = "later-correction"
        self.j.intent(later, self.grant)
        end = self.j.dynamic.input_cut(self.grant, SCOPE["site_id"], SCOPE["room_id"], consumer_id=self.grant.expected_worker_id, after=cut["next_cursor"], t=.19, limit=1, cut_id=cut["cut_id"])
        self.assertTrue(end["complete"])
        self.assertEqual(end["cut_cursor"], 2)
        self.assertEqual(end["authority"], cut["authority"])
        self.assertEqual([p["cursor"] for p in end["publications"]], [2])
        self.code("incomplete_cut", lambda: self.j.dynamic.input_cut(self.grant, SCOPE["site_id"], SCOPE["room_id"], consumer_id=self.grant.expected_worker_id, after=3, t=.19, cut_id=cut["cut_id"]))

    def test_partial_fence_cannot_commit(self):
        self.now = .19
        self.j.append_event(envelope(self.geometry()), self.grant)
        self.j.intent(self.intent("too_bright"), self.grant)
        prepared = bootstrap(self.j, self.grant, t=.19, limit=1)
        self.code("incomplete_cut", lambda: self.j.worker_commit(prepared, self.grant))

    def test_future_receipt_does_not_block_later_eligible_input(self):
        self.now = .19
        self.j.append_event(envelope(self.geometry(1)), self.grant)
        self.now = .39
        self.j.append_event(envelope(self.geometry(2)), self.grant)
        self.now = .4
        prepared = bootstrap(self.j, self.grant, t=.4)
        cut = prepared["input_cut"]
        future, current = cut["receipts"]
        future.update(received_at=.41, eligible_at=.41)
        cut.update(eligible_refs=[current["ref"]], buffered_refs=[future["ref"]])
        cut["digest"] = content_digest(cut)
        original = json.loads(self.j.db.execute("SELECT body FROM publications WHERE cursor=?",
                                               (future["ref"]["cursor"],)).fetchone()[0])
        prepared["checkpoint"]["pending_inputs"] = [{"publication": normalized(original), "received_at": .41}]
        prepared["checkpoint"]["retained_inputs"] = [item for item in prepared["checkpoint"]["retained_inputs"]
                                                     if item["publication"]["cursor"] != future["ref"]["cursor"]]
        self.now = .41
        backdated = copy.deepcopy(prepared)
        backdated["input_cut"].update(eligible_refs=[future["ref"], current["ref"]], buffered_refs=[])
        backdated["input_cut"]["digest"] = content_digest(backdated["input_cut"])
        self.code("receipt_order", lambda: self.j.worker_commit(backdated, self.grant))
        omitted_body = copy.deepcopy(prepared)
        omitted_body["checkpoint"]["pending_inputs"] = []
        self.code("invalid_input_receipt", lambda: self.j.worker_commit(omitted_body, self.grant))
        omitted_receipt = copy.deepcopy(prepared)
        omitted_receipt["checkpoint"]["receipts"] = [current]
        self.code("invalid_input_receipt", lambda: self.j.worker_commit(omitted_receipt, self.grant))
        committed = self.j.worker_commit(prepared, self.grant)
        self.assertEqual(committed["input_cursor"], current["ref"]["cursor"])

    def test_notification_geometry_and_cut_denied(self):
        from journal import Grant
        notification = Grant.from_dict(grant_config(operations=["read"], types=["home-engagement-state-3"], token="notification-only", worker="notification"))
        self.now = .19
        self.j.append_event(envelope(self.geometry()), self.grant)
        self.assertEqual(self.j.snapshot(notification, SCOPE["site_id"], SCOPE["room_id"])["items"], [])
        self.code("forbidden", lambda: self.j.dynamic.input_cut(notification, SCOPE["site_id"], SCOPE["room_id"], consumer_id="notification", after=0, t=.19))

    def test_revoked_receipt_proof_allows_stopped_control(self):
        from journal import Grant
        self.j.worker_commit(bootstrap(self.j, self.grant), self.grant)
        self.now = .19
        geo = self.geometry()
        self.j.append_event(envelope(geo), self.grant)
        self.now = .2
        self.j.worker_commit(next_commit(self.j, self.grant, t=.2, kind="semantic"), self.grant)
        revocation = {"schema": "home-context-revocation-1", "version": 1, "id": "synthetic-revoke", **SCOPE,
                      "source_id": geo["capture"]["source_id"], "revision": "*", "reason": "owner_test", "available_at": self.now}
        revgrant = Grant.from_dict(grant_config(operations=["revoke"], types=["home-context-revocation-1"]))
        # Add a pinned legacy revocation validator only to this test-owned profile.
        from schema_registry import SchemaRegistry
        self.j.schemas = SchemaRegistry([self.grant, revgrant], profile=DYNAMIC_PROFILE)
        self.j.revoke(envelope(revocation), revgrant)
        self.now = .4
        result = self.j.worker_commit(next_commit(self.j, self.grant, t=.4, kind="control"), self.grant)
        self.assertEqual(result["control_seq"], 1)
        self.assertFalse(self.j.dynamic.authority(SCOPE["site_id"], SCOPE["room_id"])["motion_allowed"])

    def test_typed_intent_application_result_is_atomic_and_immutable(self):
        self.j.worker_commit(bootstrap(self.j, self.grant), self.grant)
        intent = self.intent("too_bright")
        self.j.intent(intent, self.grant)
        step = next_commit(self.j, self.grant, t=0, kind="semantic", intent_results=[{"id": intent["id"], "status": "applied", "reason": "declared_output"}])
        self.j.worker_commit(step, self.grant)
        result = self.j.lookup_intent(intent["id"], self.grant, SCOPE["site_id"], SCOPE["room_id"])["application_result"]
        self.assertEqual(result["schema"], "home-intent-application-result-2")
        self.assertEqual(result["status"], "applied")
        self.assertTrue(validate_dynamic(result)["ok"])
        self.now = 1
        later = next_commit(self.j, self.grant, t=1, kind="semantic", intent_results=[{"id": intent["id"], "status": "expired", "reason": "scope_finished"}])
        self.code("intent_result_conflict", lambda: self.j.worker_commit(later, self.grant))
        self.assertEqual(self.j.lookup_intent(intent["id"], self.grant, SCOPE["site_id"], SCOPE["room_id"])["application_result"], result)

    def test_cached_evidence_and_all_stub_terminal_states(self):
        self.now = .19
        geo = self.geometry()
        self.j.append_event(envelope(geo), self.grant)
        request = example("home-evidence-request-2")
        request.update(id="cached-request", available_at=self.now, deadline=self.now + 3)
        request["person_ref"] = geo["objects"][0]["person_ref"]
        request["object_ref"] = geo["objects"][0]["object_ref"]
        self.j.request_evidence(request, self.grant)
        terminal = self.j.lookup_request(request["id"], self.grant, SCOPE["site_id"], SCOPE["room_id"])["terminal_reply"]
        self.assertEqual(terminal["status"], "supported")
        self.assertEqual((terminal["actual_frames"], terminal["actual_model_calls"]), (0, 0))
        self.j.evidence_router = "synthetic_stub"
        for index, status in enumerate(("abstained", "unavailable", "denied", "expired", "cancelled")):
            p = copy.deepcopy(request); p["id"] = "stub-request:" + str(index)
            self.j.request_evidence(p, self.grant)
            reply = example("home-evidence-reply-2")
            reply.update(id="stub-reply:" + str(index), request_id=p["id"], available_at=self.now, status=status, estimate_ref=None)
            self.j.reply_evidence(reply, self.grant)
            self.assertEqual(self.j.lookup_request(p["id"], self.grant, SCOPE["site_id"], SCOPE["room_id"])["terminal_reply"]["status"], status)

    def test_target_pages_and_frozen_declaration_bind_durable_inputs(self):
        self.now = .19
        payload = self.geometry()
        receipt = self.j.append_event(envelope(payload), self.grant)
        prepared = bootstrap(self.j, self.grant, t=.19)
        target = example("home-resolved-target-1")
        target["estimate_ref"] = {"journal_epoch": self.j.stream_epoch, "cursor": receipt["cursor"],
                                  "payload_digest": receipt["payload_digest"], "component_id": payload["objects"][0]["object_ref"]["oid"]}
        for key in ("person_ref", "object_ref", "association_generation", "patches", "uncertainty"):
            target[key] = copy.deepcopy(payload["objects"][0][key])
        request = example("home-lighting-request-3")
        request.update(id=target["request_id"], person_ref=target["person_ref"], object_ref=target["object_ref"],
                       association_generation=target["association_generation"], requirements=target["requirements"])
        request["lease"]["authority_generation"] = prepared["input_cut"]["authority"]["generation"]
        prepared["checkpoint"]["pipeline"] = {"policy": {"requests": [request]}, "intents": []}
        output = {"geometry_snapshot": {"object_heads": [{"object_ref": target["object_ref"], "estimate": payload["objects"][0],
                    "estimate_ref": target["estimate_ref"], "status": "fresh"}]}}
        self.j.dynamic._target(target, output, prepared, self.grant)
        altered = copy.deepcopy(target)
        altered["patches"][0]["centre_m"][0] += .001
        self.code("invalid_reference", lambda: self.j.dynamic._target(altered, output, prepared, self.grant))
        target["status"] = "frozen"
        request["target_kind"] = "declared_frozen_pages"
        request["lease"]["id"] = "declared-lease:forged-intent"
        self.code("invalid_reference", lambda: self.j.dynamic._target(target, output, prepared, self.grant))

    def test_freeze_captures_fresh_head_then_rejects_nonopen_authority(self):
        self.now = .19
        old_geometry = self.geometry(1)
        old_receipt = self.j.append_event(envelope(old_geometry), self.grant)
        self.now = .39
        geometry = self.geometry(2)
        geometry["objects"][0]["patches"][0]["centre_m"][0] += .05
        geometry_receipt = self.j.append_event(envelope(geometry), self.grant)
        intent = self.intent("keep_here")
        self.j.intent(intent, self.grant)
        prepared = bootstrap(self.j, self.grant, t=.39)
        original = geometry["objects"][0]
        target = example("home-resolved-target-1")
        target.update(status="frozen", estimate_ref={"journal_epoch": self.j.stream_epoch,
                      "cursor": geometry_receipt["cursor"], "payload_digest": geometry_receipt["payload_digest"],
                      "component_id": original["object_ref"]["oid"]})
        fields = ("person_ref", "object_ref", "association_generation", "patches", "uncertainty")
        for key in fields:
            target[key] = copy.deepcopy(original[key])
        request = example("home-lighting-request-3")
        request.update(id=target["request_id"], target_kind="declared_frozen_pages",
                       person_ref=target["person_ref"], object_ref=target["object_ref"],
                       association_generation=target["association_generation"], requirements=target["requirements"], valid_until=30)
        request["lease"].update(id="declared-lease:" + intent["id"], issued_at=intent["available_at"], valid_until=30,
                                authority_generation=prepared["input_cut"]["authority"]["generation"])
        head = {"object_ref": target["object_ref"], "estimate": copy.deepcopy(original),
                "estimate_ref": copy.deepcopy(target["estimate_ref"]), "status": "fresh"}
        pipeline = {"policy": {"requests": [request]}, "intents": [intent], "frozen_heads": [copy.deepcopy(head)],
                    "intent_ids": [intent["id"]], "intent_outcomes": [{"intent_id": intent["id"], "intent_digest": digest(intent), "status": "applied"}]}
        prepared["checkpoint"]["pipeline"] = pipeline
        output = {"geometry_snapshot": {"object_heads": [copy.deepcopy(head)]}}
        self.j.dynamic._target(target, output, prepared, self.grant)
        stale = copy.deepcopy(target)
        stale["estimate_ref"].update(cursor=old_receipt["cursor"], payload_digest=old_receipt["payload_digest"])
        for key in fields:
            stale[key] = copy.deepcopy(old_geometry["objects"][0][key])
        self.code("invalid_reference", lambda: self.j.dynamic._target(stale, output, prepared, self.grant))
        # Store only a test-owned prior checkpoint to exercise durable frozen continuity.
        self.j.db.execute("INSERT INTO workers(worker_id,site_id,room_id,input_cursor,context_seq,checkpoint) VALUES(?,?,?,?,?,?)",
                          (self.grant.expected_worker_id, SCOPE["site_id"], SCOPE["room_id"], prepared["checkpoint"]["input_cursor"],
                           1, json.dumps(prepared["checkpoint"])))
        output["geometry_snapshot"]["object_heads"][0]["status"] = "held"
        output["geometry_snapshot"]["object_heads"][0]["estimate"]["state"] = "occluded"
        output["geometry_snapshot"]["object_heads"][0]["estimate"]["patches"] = []
        self.code("invalid_reference", lambda: self.j.dynamic._target(target, output, prepared, self.grant))
        output["geometry_snapshot"]["object_heads"][0]["estimate"]["state"] = "closed"
        self.code("invalid_reference", lambda: self.j.dynamic._target(target, output, prepared, self.grant))

    def test_geometry_head_uses_observed_time_before_journal_order(self):
        self.now = .39
        newer = self.geometry(1)
        receipt = self.j.append_event(envelope(newer), self.grant)
        self.now = .4
        delayed = self.geometry(2)
        delayed["observed_at"] = delayed["capture"]["observed_at"] = .19
        delayed["valid_until"] = 1.19
        self.j.append_event(envelope(delayed), self.grant)
        prepared = bootstrap(self.j, self.grant, t=.4)
        snapshot = example("home-dynamic-geometry-snapshot-1")
        snapshot.update(as_of_t=.4, journal_epoch=self.j.stream_epoch, input_cursor=prepared["input_cut"]["input_cursor"],
                        authority=prepared["input_cut"]["authority"])
        head = snapshot["object_heads"][0]
        head.update(estimate=copy.deepcopy(newer["objects"][0]), object_ref=newer["objects"][0]["object_ref"],
                    estimate_ref={"journal_epoch": self.j.stream_epoch, "cursor": receipt["cursor"],
                                  "payload_digest": receipt["payload_digest"], "component_id": newer["objects"][0]["object_ref"]["oid"]},
                    observed_at=newer["observed_at"], available_at=newer["available_at"], valid_until=newer["valid_until"], capture=newer["capture"])
        snapshot["digest"] = content_digest(snapshot)
        prepared["input_cut"]["geometry_snapshot_digest"] = snapshot["digest"]
        self.j.dynamic._geometry(snapshot, prepared["input_cut"], prepared, self.grant,
                                 {r["ref"]["cursor"]: r for r in prepared["input_cut"]["receipts"]})

    def test_js_python_valid_and_invalid_example_parity(self):
        values = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES.glob("*.json"))]
        invalid = copy.deepcopy(values[0]); invalid["unexpected"] = True
        bad_geo = example("home-geometry-3"); bad_geo["objects"][0]["patches"][0]["u"] = [2, 0, 0]
        values.extend([invalid, bad_geo])
        script = "const C=require('./app/src/home-dynamic-context-contract.js');let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s).map(x=>C.validate(x).ok))));"
        result = subprocess.run(["node", "-e", script], input=json.dumps(values), cwd=ROOT, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([validate_dynamic(v)["ok"] for v in values], json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
