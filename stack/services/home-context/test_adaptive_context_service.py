"""Fresh adaptive public-service tests; no devices, models or private journals."""
import copy
import hashlib
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from adaptive_validation import (ADAPTIVE_INPUTS, ADAPTIVE_OUTPUTS, RevisionStore,
                                 SCOPE_KEYS, validate_adaptive)
from dynamic_validation import content_digest, component_id
from journal import ADAPTIVE_PROFILE, Journal, JournalError, PREFIX, digest
from schema_registry import ADAPTIVE_PUBLICATIONS, schema_digest
from server import LocalServer


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXAMPLES = ROOT / "contracts/home-adaptive-experience/examples"


def example(name):
    path = EXAMPLES / (name + ".json")
    if not path.is_file():
        path = ROOT / "contracts/home-dynamic-context/examples" / (name + ".json")
    return json.loads(path.read_text())


def synthetic_fixture():
    script = "const f=require('./tools/fixtures/home-adaptive-experience-synthetic.cjs').makeFixture({variant_id:'P-A-D-adequate-protected-yes',duration_s:10});process.stdout.write(JSON.stringify({identity:f.identity,clock_profile:f.clock_profile,materials:f.materials,config_digest:f.config_digest,config:f.config,registered_surfaces:f.registered_surfaces,rows:f.inputsAt(0),later:f.inputsAt(.2),one:f.inputsAt(1),declarations:f.inputsAt(5)}));"
    return json.loads(subprocess.check_output(["node", "-e", script], cwd=ROOT, text=True, timeout=10))


def normalized(publication):
    return {key: publication[key] for key in ("journal_epoch", "cursor", "id", "producer_id", "committed_at", "payload_type", "payload_version", "payload_digest", "payload")}


def grant_config(fixture, *, token="adaptive-test", principal="adaptive-worker", read_only=False):
    types = sorted(ADAPTIVE_PUBLICATIONS | {"home-source-health-1", "home-context-revocation-1"})
    registrations = {row["registration_ref"]: {"source_id": row["source_id"], "geometry_revision": "synthetic-geometry",
                       "registry_digest": row["registry_digest"], "coverage_refs": ["synthetic-pages"]} for row in fixture["materials"]}
    registered = next(row for row in fixture["rows"] if row["schema"] == "home-registered-surface-1")
    registrations[registered["registration_ref"]] = {"source_id": registered["source_id"], "geometry_revision": "synthetic-geometry",
                                                  "registry_digest": registered["registry_digest"], "coverage_refs": ["synthetic-pages"]}
    identity = fixture["identity"]
    return {"principal_id": principal, "producer_id": principal, "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "scopes": [{key: identity[key] for key in ("site_id", "room_id")}], "operations": ["read"] if read_only else
            ["read", "event", "intent", "evidence_request", "evidence_reply", "worker_commit", "revoke"],
            "payload_types": types, "schema_pins": {name: schema_digest(name) for name in types},
            "revisions": ["1.0", "2.0", "3.0", "4.0"], "source_ids": ["scripted-camera", "scripted-daylight"],
            "expected_worker_id": principal, "expected_actor_ref": "synthetic-owner", "registration_bindings": registrations,
            "adaptive_subjects": [{"owner_id": "synthetic-owner", "scope": identity, "subject_rights": [{"subject": {"kind": "session", "id": identity["session_id"]},
               "chain_ids": ["reading:person1:book-1", "reading:person2:book-1", "reading:person1:book-2", "protect:synthetic-protected-1"],
               "rights": ["read"] if read_only else ["read", "write", "derive"], "derived_goal_namespace": "goal:" + principal + ":"}]}]}


def envelope(journal, grant, payload):
    t = payload.get("issued_at", payload.get("observed_at", payload.get("available_at", payload.get("t", 0))))
    return {"schema": "home-context-event-1", "schema_version": "1.0", "id": payload["id"], "producer_id": grant.producer_id,
            "stream_epoch": journal.stream_epoch, **journal.adaptive_config["identity"], "observed_at": t,
            "available_at": payload.get("available_at", t), "valid_until": min(120, payload.get("valid_until", t + 3)),
            "payload_type": payload["schema"], "payload_version": str(payload["version"]) + ".0", "revision": str(payload["version"]) + ".0", "payload": payload}


def prepared(journal, grant, *, t=0, kind="bootstrap", outcomes=None, fence=None):
    identity = journal.adaptive_config["identity"]; clock = journal.adaptive_config["clock_profile"]["digest"]
    old = journal.checkpoint(grant.expected_worker_id, grant, identity["site_id"], identity["room_id"])["checkpoint"]
    if fence is None:
        fence = journal.dynamic.input_cut(grant, identity["site_id"], identity["room_id"], consumer_id=grant.expected_worker_id, after=old["input_cursor"] if old else 0, t=t)
    receipts = copy.deepcopy(old["receipts"]) if old else []
    for publication in fence["publications"]:
        receipts.append({"ref": {"journal_epoch": journal.stream_epoch, "cursor": publication["cursor"], "payload_digest": publication["payload_digest"]},
                         "received_at": t, "eligible_at": journal.dynamic._eligible(publication, t)})
    checkpoint = copy.deepcopy(old) if old else example("home-context-worker-checkpoint-4")
    checkpoint.update(**identity, clock_profile_digest=clock, runtime_epoch="adaptive-test-runtime", journal_epoch=journal.stream_epoch,
                      config_digest=journal.adaptive_config["config_digest"], interface_digest=journal.schemas.interface_digest,
                      commit_seq=(old["commit_seq"] if old else 0) + 1, input_cursor=fence["cut_cursor"], last_t=t,
                      authority=fence["authority"], receipts=receipts, pending_inputs=[], geometry_inputs=[], retained_inputs=[], facts=[], intents=[],
                      next_semantic_t=min(120, t + 1), next_control_t=min(120, t + .2))
    checkpoint["retained_inputs"] = [{"publication": normalized(json.loads(journal.db.execute("SELECT body FROM publications WHERE cursor=?", (row["ref"]["cursor"],)).fetchone()[0])), "received_at": row["received_at"]} for row in receipts if row["eligible_at"] <= t]
    checkpoint["pending_inputs"] = [{"publication": normalized(json.loads(journal.db.execute("SELECT body FROM publications WHERE cursor=?", (row["ref"]["cursor"],)).fetchone()[0])), "received_at": row["received_at"]} for row in receipts if row["eligible_at"] > t]
    if not old:
        checkpoint.update(semantic_seq=-1, control_seq=0, pipeline=None, goal_heads=[], goal_revision_count=0, reservations=[], pending_feedback=None, acknowledged_feedback=None, committed_output_inputs=[], intent_authority_bindings=[])
        checkpoint["reducer"].update(last_t=None, semantic_seq=-1, roots=[], relations=[])
    policy_proofs = {row["publication"]["cursor"]: row for row in checkpoint["committed_output_inputs"]}
    for publication in fence["committed_outputs"]:
        if publication["payload_type"] == "home-lighting-policy-decision-3" and publication["cursor"] not in policy_proofs:
            policy_proofs[publication["cursor"]] = {"publication": publication, "received_at": t}
    checkpoint["committed_output_inputs"] = [policy_proofs[key] for key in sorted(policy_proofs)]
    store = RevisionStore(identity=identity, clock_profile=journal.adaptive_config["clock_profile"], consumer_receipts=True, configured_worker_id=grant.producer_id)
    for row in checkpoint["retained_inputs"] + checkpoint["pending_inputs"]:
        if row["publication"]["payload_type"] in {"home-preference-snapshot-1", "home-goal-declaration-1", "home-experience-goal-1"}:
            store.admit(row, journal.dynamic._original_authorization(row))
    checkpoint["revisions"] = store.snapshot()
    outputs = []
    if kind == "semantic":
        checkpoint["semantic_seq"] += 1
        engagement = example("home-engagement-state-3"); engagement.update(**identity, id="engagement:" + str(checkpoint["commit_seq"]), t=t, semantic_seq=checkpoint["semantic_seq"], relations=[])
        policy = example("home-lighting-policy-decision-3"); policy.update(**identity, clock_profile_digest=clock, id="policy:" + str(checkpoint["commit_seq"]), t=t, semantic_seq=checkpoint["semantic_seq"], requests=[], dispositions=[], intent_results=[], consumed_refs=[])
        prior = copy.deepcopy(old["pipeline"]["intent_outcomes"]) if old and old["pipeline"] else []
        intents = [row["publication"]["payload"] for row in checkpoint["retained_inputs"] if row["publication"]["payload_type"] == "home-intent-event-3"]
        for intent_id, status, reason in outcomes or []:
            intent = next(body for body in intents if body["id"] == intent_id)
            prior.append({"intent_id": intent_id, "intent_digest": digest(intent), "status": status, "reason": reason, "decided_at": t, "decision_ref": policy["id"]})
            if status == "applied" and intent["kind"] == "dont_follow":
                follow = next(row for row in fence["authority"]["follows"] if row["person_ref"] == intent["person_ref"] and row["object_ref"] == intent["object_ref"])
                checkpoint["intent_authority_bindings"].append({"intent_id": intent_id, **{key: follow[key] for key in ("person_ref", "object_ref", "association_generation", "source_id", "registration_ref")}, "applied_at": t, "baseline_follow_generation": follow["follow_generation"]})
                engagement["relations"].append({"person_ref": follow["person_ref"], "object_ref": follow["object_ref"], "association_generation": follow["association_generation"], "status": "engaged", "activity": "reading", "supported_at": t, "last_progress_at": t, "original_roots": [], "conflict_ids": [], "closed": False})
        policy["intent_results"] = prior
        checkpoint["pipeline"] = {"t": t, "commands": [], "policy": policy, "leases": [], "frozen_heads": [], "intent_ids": copy.deepcopy(old["pipeline"]["intent_ids"]) if old and old["pipeline"] else [],
                                  "intents": intents, "intent_outcomes": prior, "fixtures": [], "config_digest": checkpoint["config_digest"], "exposure_context": None, "reservations": []}
        checkpoint["reducer"]["semantic_seq"] = checkpoint["semantic_seq"]
        outputs = [engagement, policy]
    cut = {"schema": "home-control-input-cut-2", "version": 2, **identity, "clock_profile_digest": clock, "runtime_epoch": checkpoint["runtime_epoch"],
           "journal_epoch": journal.stream_epoch, "cut_id": fence["cut_id"], "input_cursor": fence["cut_cursor"], "as_of_t": t, "receipts": receipts,
           "eligible_refs": [row["ref"] for row in receipts if row["eligible_at"] <= t], "buffered_refs": [row["ref"] for row in receipts if row["eligible_at"] > t],
           "authority": fence["authority"], "complete": True, "geometry_snapshot_digest": "a" * 64}
    cut["digest"] = content_digest(cut)
    commit = {"schema": "home-context-worker-commit-4", "version": 4, "id": "adaptive-step:" + str(checkpoint["commit_seq"]), **identity, "clock_profile_digest": clock,
              "runtime_epoch": checkpoint["runtime_epoch"], "journal_epoch": journal.stream_epoch, "kind": kind,
              "expected_commit_seq": old["commit_seq"] if old else 0, "expected_input_cursor": old["input_cursor"] if old else 0,
              "expected_authority": fence["authority"], "input_cut": cut, "checkpoint": checkpoint, "outputs": outputs}
    if kind == "semantic":
        checkpoint["authority"] = journal.dynamic._post_authority(commit, {row["intent_id"]: row for row in policy["intent_results"]})
    return commit


def goal_output(test, commit, ident="book-1"):
    goal = example("home-experience-goal-1"); geometry = test.row("home-geometry-3")["objects"][0]
    goal.update(**test.identity, id="goal-output:" + ident, clock_profile_digest=test.fixture["clock_profile"]["digest"],
                goal_id="goal:adaptive-worker:" + ident, owner_id="synthetic-owner", subject={"kind": "session", "id": test.identity["session_id"]},
                beneficiary={"kind": "person", "person_ref": geometry["person_ref"]}, target={"kind": "object", "object_ref": geometry["object_ref"], "registration_ref": None},
                association_generation=0, issued_at=test.now, effective_at=test.now, available_at=test.now, valid_until=10,
                preference_refs=[], declaration_ref=None)
    commit["checkpoint"]["goal_heads"].append(goal); commit["checkpoint"]["goal_revision_count"] += 1
    commit["outputs"].insert(1, goal)
    return goal


def control_output(test, commit, t):
    checkpoint, cut = commit["checkpoint"], commit["input_cut"]; clock = test.fixture["clock_profile"]["digest"]
    checkpoint["control_seq"] += 1; commit["kind"] = "control"
    snapshot = example("home-dynamic-geometry-snapshot-2")
    snapshot.update(**test.identity, clock_profile_digest=clock, as_of_t=t, journal_epoch=test.j.stream_epoch,
                    input_cursor=cut["input_cursor"], authority=cut["authority"], registry_digest=test.fixture["materials"][0]["registry_digest"], object_heads=[], surface_heads=[])
    snapshot["digest"] = content_digest(snapshot); cut["geometry_snapshot_digest"] = snapshot["digest"]; cut["digest"] = content_digest(cut)
    allocation = example("home-lighting-allocation-4"); allocation.update(**test.identity, clock_profile_digest=clock, id="allocation:" + str(checkpoint["control_seq"]),
              t=t, control_seq=checkpoint["control_seq"], targets=[], assessments=[], registry_digest=snapshot["registry_digest"])
    execution = example("home-lighting-execution-2"); execution.update(**test.identity, clock_profile_digest=clock, id="execution:" + str(checkpoint["control_seq"]),
              t=t, from_t=round(t - .2, 10), control_seq=checkpoint["control_seq"],
              fixture_ids=[fixture["id"] for fixture in test.fixture["config"]["registry"]["fixtures"]], target_ids=[],
              samples=[{"t": round(t - .2 + (i + 1) * .02, 10), "states": [[0, 0, 0, 0, 0, 0, 0, "dark", -1] for _ in range(4)]} for i in range(10)])
    feedback = execution["feedback"]; profile = test.fixture["config"]["profile"]
    feedback.update(**test.identity, clock_profile_digest=clock, id="feedback:" + str(checkpoint["control_seq"]), t=t, from_t=execution["from_t"],
              control_seq=checkpoint["control_seq"], model_ref=profile["profile_ref"], model_digest=digest(profile), sample_digest=digest(execution["samples"]),
              execution_digest=digest({key: body for key, body in execution.items() if key != "feedback"}))
    control = example("home-lighting-control-decision-2"); control.update(**test.identity, clock_profile_digest=clock, id="control:" + str(checkpoint["control_seq"]),
              t=t, semantic_seq=checkpoint["semantic_seq"], control_seq=checkpoint["control_seq"], input_cut_digest=cut["digest"], geometry_snapshot=snapshot,
              allocation=allocation, execution=execution, consumed_refs=[])
    checkpoint["pending_feedback"] = {"commit_id": commit["id"], "control_seq": checkpoint["control_seq"], "component_digest": digest(feedback)}
    commit["outputs"] = [control]
    return control


def exposure_request(test, goal, geometry, geometry_receipt, environment, environment_receipt, authority):
    """Exact original bodies for admission proofs; this helper makes no optics claim."""
    obj = geometry["objects"][0]; material = test.fixture["materials"][0]
    component = lambda body, receipt: {"component_id": body["id"], "component_digest": digest(body),
        "publication_ref": {"journal_epoch": test.j.stream_epoch, "cursor": receipt["cursor"], "payload_digest": digest(body)}}
    fences = test.j.dynamic._fences(authority, obj["person_ref"], obj["object_ref"], obj["association_generation"], material["source_id"], material["registration_ref"])
    request = example("home-lighting-request-4")
    request.update(**test.identity, id="request:" + goal["id"], clock_profile_digest=test.fixture["clock_profile"]["digest"],
        goal_ref={"component_id": goal["id"], "component_digest": digest(goal), "publication_ref": None},
        preference_refs=goal["preference_refs"], capability_refs=[], instruction_refs=[], beneficiary=goal["beneficiary"], target=goal["target"],
        association_generation=obj["association_generation"], valid_until=2)
    request["lease"].update(**test.identity, clock_profile_digest=test.fixture["clock_profile"]["digest"], id="lease:" + goal["id"],
        person_ref=obj["person_ref"], object_ref=obj["object_ref"], association_generation=obj["association_generation"], issued_at=0, valid_until=2, fences=fences)
    midpoint = [sum(patch["centre_m"][i] for patch in obj["patches"]) / 2 for i in range(3)]
    target = example("home-resolved-target-2")
    target.update(**test.identity, id="target:unit-proof", clock_profile_digest=test.fixture["clock_profile"]["digest"], request_id=request["id"],
        **{key: request[key] for key in ("goal_ref", "beneficiary", "target", "association_generation", "objectives")},
        source_id=material["source_id"], registration_ref=material["registration_ref"],
        geometry_ref={**component(geometry, geometry_receipt)["publication_ref"], "component_id": obj["object_ref"]["oid"]},
        patches=obj["patches"], uncertainty=obj["uncertainty"], uncertainty_correlation_ref=material["uncertainty_correlation_ref"],
        transform_origin_m=midpoint, aim_anchor_m=midpoint, environment_refs=[component(environment, environment_receipt)], fences=fences)
    context = {"installed_at": .2, "requests": [request], "targets": [target],
        "environment_heads": [{"body": environment, "ref": component(environment, environment_receipt)}],
        "support_bounds": [{"target_id": target["id"], "valid_until": .5}]}
    return request, context


def past_exposure(test, commit, control, context):
    request = context["requests"][0]
    proof = next(row["publication"] for row in commit["checkpoint"]["committed_output_inputs"]
                 if request in row["publication"]["payload"]["requests"])
    environment_refs, environment_until = test.j.dynamic._exposure_environment(context["targets"][0], context)
    feedback = control["execution"]["feedback"]
    feedback["exposures"] = [{"goal_ref": request["goal_ref"], "request_ref": {"component_id": request["id"], "component_digest": digest(request),
        "publication_ref": {"journal_epoch": proof["journal_epoch"], "cursor": proof["cursor"], "payload_digest": proof["payload_digest"], "component_id": request["id"]}},
        "preference_refs": request["preference_refs"], "environment_refs": environment_refs, "from_t": feedback["from_t"], "t": feedback["t"],
        "status": "compute_refused", "reason": "unit_admission_proof_without_optical_model", "weighted_coverage": None, "worst13_coverage": None,
        "protected_max_lux": None, "support_valid_until": .5, "environment_valid_until": environment_until}]
    commit["checkpoint"]["pending_feedback"]["component_digest"] = digest(feedback)
    return feedback


def install_geometry_head(test, commit, control, geometry, receipt):
    obj = geometry["objects"][0]
    head = {"object_ref": obj["object_ref"], "status": "fresh", "reason": "original_fresh",
        "estimate_ref": {"journal_epoch": test.j.stream_epoch, "cursor": receipt["cursor"], "payload_digest": digest(geometry), "component_id": obj["object_ref"]["oid"]},
        **{key: geometry[key] for key in ("observed_at", "available_at", "valid_until", "capture")}, "estimate": obj}
    snapshot = control["geometry_snapshot"]; snapshot["object_heads"] = [head]
    snapshot["digest"] = content_digest(snapshot); commit["input_cut"]["geometry_snapshot_digest"] = snapshot["digest"]
    commit["input_cut"]["digest"] = content_digest(commit["input_cut"]); control["input_cut_digest"] = commit["input_cut"]["digest"]
    return head


def install_surface_head(test, commit, control, surface, receipt, *, status="fresh"):
    head = {"registration_ref": surface["registration_ref"], "status": status, "reason": "unit_original_surface",
            "publication_ref": {"journal_epoch": test.j.stream_epoch, "cursor": receipt["cursor"], "payload_digest": digest(surface)},
            "surface": copy.deepcopy(surface)}
    snapshot = control["geometry_snapshot"]; snapshot["surface_heads"] = [head]
    snapshot["digest"] = content_digest(snapshot); commit["input_cut"]["geometry_snapshot_digest"] = snapshot["digest"]
    commit["input_cut"]["digest"] = content_digest(commit["input_cut"]); control["input_cut_digest"] = commit["input_cut"]["digest"]
    return head


class AdaptiveServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = synthetic_fixture()

    def setUp(self):
        self.now = 0
        self.work = tempfile.TemporaryDirectory(prefix="adaptive-fresh-", dir=HERE)
        config = {key: self.fixture[key] for key in ("identity", "clock_profile", "materials", "config_digest", "config", "registered_surfaces")}
        self.j = Journal(Path(self.work.name) / "fresh.db", grants=[grant_config(self.fixture)], profile=ADAPTIVE_PROFILE, clock=lambda: self.now, adaptive_config=config)
        self.grant = self.j.authenticate("adaptive-test")
        self.identity = self.fixture["identity"]

    def tearDown(self):
        self.j.close(); self.work.cleanup()

    def code(self, expected, action):
        with self.assertRaises(JournalError) as caught:
            action()
        self.assertEqual(caught.exception.code, expected)

    def row(self, family):
        return copy.deepcopy(next(row for row in self.fixture["rows"] if row["schema"] == family))

    def append(self, payload):
        if payload["schema"] in {"home-preference-snapshot-1", "home-goal-declaration-1"}:
            return self.j.dynamic.declaration(payload, self.grant, family=payload["schema"])
        if payload["schema"] == "home-intent-event-3":
            return self.j.intent(payload, self.grant)
        return self.j.append_event(envelope(self.j, self.grant, payload), self.grant)

    def snapshot(self):
        return self.j.snapshot(self.grant, self.identity["site_id"], self.identity["room_id"], self.grant.expected_worker_id)

    def intent(self, kind="dont_follow", expiry=2):
        body = example("home-intent-event-3")
        geometry = self.row("home-geometry-3")["objects"][0]
        body.update(**self.identity, id="intent:" + kind, clock_profile_digest=self.fixture["clock_profile"]["digest"],
                    issued_at=self.now, effective_at=self.now, available_at=self.now, valid_until=expiry,
                    subject={"kind": "session", "id": self.identity["session_id"]}, person_ref=geometry["person_ref"], object_ref=geometry["object_ref"], kind=kind, actor_ref="synthetic-owner")
        return body

    def test_current_public_examples_python_js_parity(self):
        values = [json.loads(path.read_text()) for path in EXAMPLES.glob("*.json") if path.name != "clock-profile.json"]
        self.assertTrue(all(validate_adaptive(body)["ok"] for body in values))
        invalid = [{**body, "unexpected": True} for body in values]
        execution = example("home-lighting-execution-2")
        for fault in ("duplicate_fixture", "duplicate_target", "bad_index", "short_tuple", "boolean_number", "nonfinite_number"):
            body = copy.deepcopy(execution)
            if fault == "duplicate_fixture": body["fixture_ids"][1] = body["fixture_ids"][0]
            elif fault == "duplicate_target": body["target_ids"] = ["target:a", "target:a"]
            elif fault == "bad_index": body["boundary_state"][0][8] = 0
            elif fault == "short_tuple": body["boundary_state"][0].pop()
            elif fault == "boolean_number": body["boundary_state"][0][0] = True
            else: body["boundary_state"][0][0] = "NaN"
            body["feedback"]["execution_digest"] = digest({key: item for key, item in body.items() if key != "feedback"})
            invalid.append(body)
        self.assertTrue(all(not validate_adaptive(body)["ok"] for body in invalid))
        script = "const A=require('./app/src/home-adaptive-experience-contract.js');let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s).map(x=>A.validate(x).ok))));"
        run = subprocess.run(["node", "-e", script], cwd=ROOT, input=json.dumps(values + invalid), text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), [validate_adaptive(body)["ok"] for body in values + invalid])

    def test_bootstrap_exact_retry_commit_lookup_and_cas(self):
        commit = prepared(self.j, self.grant)
        receipt = self.j.worker_commit(commit, self.grant)
        self.assertEqual(self.j.worker_commit(commit, self.grant), receipt)
        result = self.j.dynamic.lookup_commit(commit["id"], self.grant, self.identity["site_id"], self.identity["room_id"])
        self.assertEqual(result["receipt"], receipt); self.assertEqual(result["publications"], [])
        changed = copy.deepcopy(commit); changed["id"] = "stale-other-id"
        self.code("checkpoint_conflict", lambda: self.j.worker_commit(changed, self.grant))
        changed = copy.deepcopy(commit); changed["checkpoint"]["config_digest"] = "f" * 64
        self.code("identity_conflict", lambda: self.j.worker_commit(changed, self.grant))

    def test_trusted_config_and_old_live_dialects_rejected(self):
        commit = prepared(self.j, self.grant); commit["checkpoint"]["config_digest"] = "f" * 64
        self.code("reset_required", lambda: self.j.worker_commit(commit, self.grant))
        old = example("home-intent-event-3"); old.update(schema="home-intent-event-2", version=2)
        self.code("invalid_payload", lambda: self.j.intent(old, self.grant))

    def test_subject_owner_actor_and_exact_chain_filter(self):
        preference = self.row("home-preference-snapshot-1")
        for field, value in (("owner_id", "another-owner"), ("preference_set_id", "ungranted-chain"), ("subject", {"kind": "room", "id": self.identity["room_id"]})):
            wrong = copy.deepcopy(preference); wrong[field] = value
            self.code("forbidden", lambda: self.append(wrong))
        wrong = self.intent(); wrong["actor_ref"] = "payload-is-not-authentication"
        self.code("forbidden", lambda: self.append(wrong))
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM publications").fetchone()[0], 0)

    def test_future_revision_cas_and_expired_tombstone_no_resurrection(self):
        first = self.row("home-preference-snapshot-1"); receipt = self.append(first)
        next_body = copy.deepcopy(first); next_body.update(id="preference:revision1", revision=1, expected_predecessor={"revision": 0, "digest": digest(first)}, effective_at=1, available_at=1, valid_until=2)
        second = self.append(next_body)
        self.code("snapshot_pending_inputs", self.snapshot)
        self.assertEqual(self.append(next_body), second)
        fork = copy.deepcopy(next_body); fork["id"] = "fork"
        self.code("adaptive_revision_cas", lambda: self.append(fork))
        self.now = 1
        snapshot = self.snapshot(); self.assertEqual(snapshot["revision_heads"][0]["revision"], 1)
        self.now = 2
        snapshot = self.snapshot(); self.assertEqual(snapshot["revision_heads"][0]["disposition"], "expired")
        self.assertEqual(snapshot["revision_heads"][0]["publication_ref"]["cursor"], second["cursor"])
        self.assertNotEqual(second["cursor"], receipt["cursor"])

    def test_preference_and_receipt_intent_never_change_motion(self):
        self.append(self.row("home-geometry-3")); authority = self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])
        self.append(self.row("home-preference-snapshot-1")); self.append(self.intent("too_bright")); self.append(self.intent())
        self.assertEqual(self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"]), authority)

    def test_first_applied_dont_follow_atomic_scoped_and_semantic_expiry(self):
        geometry = self.row("home-geometry-3"); second = copy.deepcopy(geometry["objects"][0]); second["object_ref"]["oid"] = "book-2"; second["person_ref"]["pid"] = 2; geometry["objects"].append(second)
        self.append(geometry); intent = self.intent(); self.append(intent)
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        commit = prepared(self.j, self.grant, kind="semantic", outcomes=[(intent["id"], "applied", "declared_follow_stop")])
        receipt = self.j.worker_commit(commit, self.grant)
        authority = self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])
        self.assertEqual([(row["object_ref"]["oid"], row["follow_allowed"], row["follow_generation"]) for row in authority["follows"]], [("book-1", False, 1), ("book-2", True, 0)])
        lookup = self.j.lookup_intent(intent["id"], self.grant, self.identity["site_id"], self.identity["room_id"])
        self.assertEqual(lookup["application_result"]["decision_ref"], commit["outputs"][1]["id"])
        self.assertEqual(self.j.worker_commit(commit, self.grant), receipt)
        self.now = 2
        self.assertFalse(self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])["follows"][0]["follow_allowed"])
        restore = prepared(self.j, self.grant, t=2, kind="semantic")
        self.j.worker_commit(restore, self.grant)
        self.assertEqual(self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])["follows"][0]["follow_generation"], 2)
        self.assertTrue(self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])["follows"][0]["follow_allowed"])
        self.assertEqual(self.j.lookup_intent(intent["id"], self.grant, self.identity["site_id"], self.identity["room_id"])["application_result"], lookup["application_result"])

    def test_rejected_dont_follow_and_illegal_delta_do_not_close_permission(self):
        self.append(self.row("home-geometry-3")); intent = self.intent(); self.append(intent)
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        commit = prepared(self.j, self.grant, kind="semantic", outcomes=[(intent["id"], "rejected", "no_exact_current_relation")])
        wrong = copy.deepcopy(commit); wrong["checkpoint"]["authority"]["room"]["motion_allowed"] = False
        self.code("authority_conflict", lambda: self.j.worker_commit(wrong, self.grant))
        self.assertIsNone(self.j.lookup_intent(intent["id"], self.grant, self.identity["site_id"], self.identity["room_id"])["application_result"])
        self.j.worker_commit(commit, self.grant)
        self.assertTrue(self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])["follows"][0]["follow_allowed"])
        self.now = 1; changed = prepared(self.j, self.grant, t=1, kind="semantic")
        changed["checkpoint"]["pipeline"]["intent_outcomes"][0]["status"] = "applied"
        self.code("intent_result_conflict", lambda: self.j.worker_commit(changed, self.grant))

    def test_partial_components_future_fence_and_original_masks(self):
        geometry = self.row("home-geometry-3"); second = copy.deepcopy(geometry["objects"][0]); second["object_ref"]["oid"] = "book-2"; second["person_ref"]["pid"] = 2; geometry["objects"].append(second)
        first = self.append(geometry); self.now = .2
        newer = copy.deepcopy(self.fixture["later"][0]); newer["objects"] = [second]; newer.update(available_at=.4, valid_until=1.4)
        self.append(newer)
        before = {table: self.j.db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ("dynamic_snapshot_masks", "dynamic_deliveries", "consumers")}
        self.code("snapshot_pending_inputs", self.snapshot)
        self.assertEqual(before, {table: self.j.db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in before})
        self.now = .4; snapshot = self.snapshot()
        self.assertEqual([(row["object_ref"]["oid"], row["publication_ref"]["cursor"]) for row in snapshot["component_heads"]], [("book-1", first["cursor"]), ("book-2", 2)])
        self.assertEqual(snapshot["items"][0]["payload"], geometry)

    def test_complete_environment_empty_withdraws_and_tombstone_preserved(self):
        environment = self.row("home-lighting-environment-1"); self.append(environment)
        self.now = .2; empty = copy.deepcopy(environment); empty.update(id="environment:empty", observed_at=.2, effective_at=.2, available_at=.2, valid_until=.4, contributions=[])
        receipt = self.append(empty); self.now = .4
        snapshot = self.snapshot(); head = next(row for row in snapshot["family_heads"] if row["payload_type"] == empty["schema"])
        self.assertEqual(head["disposition"], "expired"); self.assertEqual(head["publication_ref"]["cursor"], receipt["cursor"])
        self.assertEqual(snapshot["items"][0]["payload"]["contributions"], [])

    def test_authority_race_rejects_whole_transaction(self):
        self.append(self.row("home-geometry-3")); commit = prepared(self.j, self.grant)
        self.now = .2; self.append(self.fixture["later"][0])
        # New geometry does not cause a room-global permission generation; a
        # scoped changed association does change the complete fenced map.
        changed = copy.deepcopy(self.fixture["later"][0]); changed["id"] = "new-association"; changed["objects"][0]["association_generation"] = 1
        self.append(changed)
        self.code("authority_conflict", lambda: self.j.worker_commit(commit, self.grant))
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM workers").fetchone()[0], 0)

    def test_pipeline_projection_cannot_reopen_an_active_applied_instruction(self):
        self.append(self.row("home-geometry-3")); instruction = self.intent(expiry=3); self.append(instruction)
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        self.j.worker_commit(prepared(self.j, self.grant, kind="semantic", outcomes=[(instruction["id"], "applied", "stop")]), self.grant)
        self.now = 1; wrong = prepared(self.j, self.grant, t=1, kind="semantic")
        wrong["checkpoint"]["pipeline"]["intents"] = []
        wrong["checkpoint"]["authority"]["follows"][0].update(follow_allowed=True, follow_generation=2)
        self.code("authority_conflict", lambda: self.j.worker_commit(wrong, self.grant))
        self.assertFalse(self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])["follows"][0]["follow_allowed"])

    def test_first_partial_mask_requires_authentic_snapshot_selection(self):
        geometry = self.row("home-geometry-3"); second = copy.deepcopy(geometry["objects"][0]); second["object_ref"]["oid"] = "book-2"; second["person_ref"]["pid"] = 2; geometry["objects"].append(second)
        self.append(geometry)
        wrong = prepared(self.j, self.grant)
        wrong["checkpoint"]["retained_inputs"][0]["component_ids"] = [component_id(second, True)]
        self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
        snapshot = self.snapshot()
        correct = prepared(self.j, self.grant)
        correct["checkpoint"]["retained_inputs"][0]["component_ids"] = sorted(component_id(item, True) for item in geometry["objects"])
        self.assertEqual(snapshot["items"][0]["payload"], geometry)
        self.j.worker_commit(correct, self.grant)
        self.now = 1; changed = prepared(self.j, self.grant, t=1, kind="semantic")
        changed["checkpoint"]["retained_inputs"][0]["component_ids"] = [component_id(second, True)]
        self.code("invalid_reference", lambda: self.j.worker_commit(changed, self.grant))

    def test_whole_future_fact_batch_snapshot_fence_and_same_capture_conflict(self):
        batch = self.row("home-fact-batch-2"); other = copy.deepcopy(batch["facts"][0])
        other.update(id="future-negative", kind="closed", value=True, available_at=.2)
        batch["facts"].append(other); batch["available_at"] = .2
        self.append(batch)
        self.code("snapshot_pending_inputs", self.snapshot)
        self.now = .2; snapshot = self.snapshot()
        self.assertEqual({head["publication_ref"]["component_id"] for head in snapshot["component_heads"]}, {batch["facts"][0]["id"], "future-negative"})
        self.assertEqual(snapshot["items"][0]["payload"], batch)

    def test_cached_evidence_uses_current_head_and_four_clock_eligibility(self):
        geometry = self.row("home-geometry-3"); self.append(geometry)
        request = example("home-evidence-request-2"); request.update(**self.identity, id="cached:current", person_ref=geometry["objects"][0]["person_ref"],
              object_ref=geometry["objects"][0]["object_ref"], available_at=0, deadline=.5, precision_m=.02, orientation_rad=.1)
        self.j.request_evidence(request, self.grant)
        reply = self.j.lookup_request(request["id"], self.grant, self.identity["site_id"], self.identity["room_id"])["terminal_reply"]
        self.assertEqual(reply["status"], "supported")
        self.now = .2; closed = copy.deepcopy(self.fixture["later"][0]); closed.update(available_at=.4, valid_until=1.4)
        closed["objects"][0]["state"] = "withdrawn"; self.append(closed)
        self.assertIsNotNone(self.j.dynamic._cached_estimate(request))
        self.now = .4; self.assertIsNone(self.j.dynamic._cached_estimate(request))

    def test_public_revision_pages_filter_subject_and_derived_namespace(self):
        preference = self.row("home-preference-snapshot-1"); self.append(preference)
        narrow = copy.deepcopy(grant_config(self.fixture, read_only=True)); narrow["adaptive_subjects"][0]["subject_rights"][0]["chain_ids"] = ["another-chain"]
        from journal import Grant
        reader = Grant.from_dict(narrow)
        self.assertEqual(self.j.page(reader, self.identity["site_id"], self.identity["room_id"])["items"], [])
        self.assertEqual(self.j.snapshot(reader, self.identity["site_id"], self.identity["room_id"])["revision_heads"], [])

    def test_source_revocation_fences_only_its_configured_authority(self):
        self.append(self.row("home-geometry-3"))
        revocation = json.loads((ROOT / "contracts/home-context-service/examples/home-context-revocation-1.json").read_text()) if (ROOT / "contracts/home-context-service/examples/home-context-revocation-1.json").is_file() else {
            "schema": "home-context-revocation-1", "version": 1, "id": "revoke-camera", **self.identity,
            "source_id": "scripted-camera", "revision": "*", "available_at": 0, "reason": "synthetic_test"}
        revocation.update(**self.identity, id="revoke-camera", source_id="scripted-camera", revision="*", available_at=0)
        event = envelope(self.j, self.grant, revocation); event["payload_version"] = "1.0"; event["revision"] = "1.0"
        self.j.revoke(event, self.grant)
        authority = self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])
        self.assertTrue(authority["room"]["motion_allowed"])
        self.assertTrue(next(row for row in authority["sources"] if row["source_id"] == "scripted-camera")["revoked"])
        self.assertFalse(next(row for row in authority["sources"] if row["source_id"] == "scripted-daylight")["revoked"])

    def test_registered_original_requires_its_exact_authenticated_source(self):
        from dataclasses import replace
        surface = self.row("home-registered-surface-1")
        original = self.append(surface)
        row = self.j.db.execute("SELECT source_id FROM publications WHERE cursor=?", (original["cursor"],)).fetchone()
        self.assertEqual(row["source_id"], surface["source_id"])
        bad = copy.deepcopy(surface); bad.update(id="wrong-registered-source", source_id="scripted-daylight")
        self.code("forbidden", lambda: self.append(bad))
        restricted = replace(self.grant, source_ids=("scripted-daylight",))
        second = copy.deepcopy(surface); second["id"] = "ungranted-registered-source"
        self.code("forbidden", lambda: self.j.append_event(envelope(self.j, restricted, second), restricted))
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM publications").fetchone()[0], 1)

    def test_registered_catalogue_cannot_change_its_registration_source(self):
        config = copy.deepcopy(self.j.adaptive_config)
        config["registered_surfaces"][0]["source_id"] = "scripted-daylight"
        config["config"]["registered_catalogue_digest"] = digest(config["registered_surfaces"])
        config["config_digest"] = digest(config["config"])
        with self.assertRaisesRegex(ValueError, "exact configured registration"):
            Journal(Path(self.work.name) / "wrong-source.db", grants=[grant_config(self.fixture)],
                    profile=ADAPTIVE_PROFILE, clock=lambda: self.now, adaptive_config=config)

    def test_registered_qualification_requires_source_even_when_registration_is_open(self):
        surface = self.row("home-registered-surface-1")
        authority = self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])
        self.assertTrue(self.j.dynamic._registered_authority(surface, authority))
        camera = next(row for row in authority["sources"] if row["source_id"] == surface["source_id"])
        camera["revoked"] = True; camera["revocation_generation"] += 1
        self.assertFalse(next(row for row in authority["registrations"] if row["registration_ref"] == surface["registration_ref"])["revoked"])
        self.assertFalse(self.j.dynamic._registered_authority(surface, authority))
        camera["revoked"] = False
        authority["sources"] = [row for row in authority["sources"] if row["source_id"] != surface["source_id"]]
        self.assertFalse(self.j.dynamic._registered_authority(surface, authority))

    def test_full_configuration_rejects_material_catalogue_and_free_digest_changes(self):
        from adaptive_validation import validate_configuration
        config = copy.deepcopy(self.j.adaptive_config)
        config["materials"][0]["uncertainty_correlation_ref"] = "invented-correlation"
        self.code("adaptive_configuration_digest", lambda: validate_configuration(config))
        config = copy.deepcopy(self.j.adaptive_config); config["registered_surfaces"][0]["transform_origin_m"][0] += .1
        self.code("adaptive_configuration_digest", lambda: validate_configuration(config))
        config = copy.deepcopy(self.j.adaptive_config); config["config_digest"] = "f" * 64
        self.code("adaptive_configuration_digest", lambda: validate_configuration(config))
        registered = self.row("home-registered-surface-1"); registered["uncertainty_correlation_ref"] = "invented-correlation"
        self.code("registration_mismatch", lambda: self.append(registered))

    def test_atomic_goal_output_and_budget_rejection_leave_no_partial_outputs(self):
        from dataclasses import replace
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        commit = prepared(self.j, self.grant, kind="semantic"); goal_output(self, commit)
        self.j.limits = replace(self.j.limits, max_publications=1)
        self.code("journal_full", lambda: self.j.worker_commit(commit, self.grant))
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM publications").fetchone()[0], 0)
        self.assertEqual(self.j.checkpoint(self.grant.expected_worker_id, self.grant, self.identity["site_id"], self.identity["room_id"])["commit_seq"], 1)
        self.j.limits = replace(self.j.limits, max_publications=2048)
        receipt = self.j.worker_commit(commit, self.grant)
        original = self.j.dynamic.lookup_commit(commit["id"], self.grant, self.identity["site_id"], self.identity["room_id"])
        self.assertEqual([row["payload_type"] for row in original["publications"]], ["home-engagement-state-3", "home-experience-goal-1", "home-lighting-policy-decision-3"])
        self.assertEqual(self.j.worker_commit(commit, self.grant), receipt)

    def test_snapshot_goal_dependency_and_predecessor_are_originals_not_heads(self):
        preference = self.row("home-preference-snapshot-1"); first = self.append(preference)
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        commit = prepared(self.j, self.grant, kind="semantic"); goal = goal_output(self, commit)
        goal["preference_refs"] = [{"component_id": preference["id"], "component_digest": digest(preference),
                                    "publication_ref": {"journal_epoch": self.j.stream_epoch, "cursor": first["cursor"], "payload_digest": digest(preference)}}]
        self.j.worker_commit(commit, self.grant)
        self.now = .2; newer = copy.deepcopy(preference); newer.update(id="preference:next", revision=1, expected_predecessor={"revision": 0, "digest": digest(preference)}, issued_at=.2, effective_at=.2, available_at=.2)
        second = self.append(newer); snapshot = self.snapshot()
        self.assertEqual({row["publication_ref"]["cursor"] for row in snapshot["revision_heads"] if row["payload_type"] == preference["schema"]}, {second["cursor"]})
        dependency = next(row for row in snapshot["items"] if row["cursor"] == first["cursor"])
        self.assertEqual(dependency["payload"], preference); self.assertEqual(dependency["payload_digest"], digest(preference))
        self.assertNotIn(first["cursor"], {row["publication_ref"]["cursor"] for row in snapshot["revision_heads"]})

    def test_read_namespace_is_configured_worker_not_reader_and_other_denied(self):
        from journal import Grant
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        commit = prepared(self.j, self.grant, kind="semantic"); goal_output(self, commit); self.j.worker_commit(commit, self.grant)
        config = grant_config(self.fixture, read_only=True, principal="notification-reader")
        config["adaptive_subjects"][0]["subject_rights"][0]["derived_goal_namespace"] = "goal:adaptive-worker:"
        reader = Grant.from_dict(config)
        self.assertEqual(len([row for row in self.j.page(reader, self.identity["site_id"], self.identity["room_id"])["items"] if row["payload_type"] == "home-experience-goal-1"]), 1)
        config["adaptive_subjects"][0]["subject_rights"][0]["derived_goal_namespace"] = "goal:other-worker:"
        wrong = Grant.from_dict(config)
        self.code("adaptive_revision_subject", lambda: self.j.page(wrong, self.identity["site_id"], self.identity["room_id"]))

    def test_owner_read_write_namespace_admission_and_original_restore_agree(self):
        config = grant_config(self.fixture, token="owner-test", principal="synthetic-owner", read_only=True)
        config["operations"] = ["read", "event"]
        config["adaptive_subjects"][0]["subject_rights"][0].update(rights=["read", "write"], derived_goal_namespace="goal:adaptive-worker:")
        trusted = {key: self.fixture[key] for key in ("identity", "clock_profile", "materials", "config_digest", "config", "registered_surfaces")}
        journal = Journal(Path(self.work.name) / "namespace.db", grants=[grant_config(self.fixture), config], profile=ADAPTIVE_PROFILE,
                          clock=lambda: self.now, adaptive_config=trusted)
        try:
            owner = journal.authenticate("owner-test"); preference = self.row("home-preference-snapshot-1")
            receipt = journal.dynamic.declaration(preference, owner, family=preference["schema"])
            original = journal.dynamic._revision_store().snapshot()["rows"][0]["publication"]
            self.assertEqual(original["cursor"], receipt["cursor"]); self.assertEqual(original["producer_id"], "synthetic-owner")
            self.assertEqual(journal.snapshot(owner, self.identity["site_id"], self.identity["room_id"], "owner-reader")["revision_heads"][0]["publication_ref"]["cursor"], receipt["cursor"])
        finally:
            journal.close()

    def test_prior_policy_delivery_excluded_from_cut_receipts_and_immutable(self):
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        self.j.worker_commit(prepared(self.j, self.grant, kind="semantic"), self.grant)
        self.now = .2; commit = prepared(self.j, self.grant, t=.2, kind="control"); control_output(self, commit, .2)
        proofs = commit["checkpoint"]["committed_output_inputs"]
        self.assertEqual(len(proofs), 1); self.assertEqual(proofs[0]["publication"]["payload_type"], "home-lighting-policy-decision-3")
        self.assertNotIn(proofs[0]["publication"]["cursor"], {row["ref"]["cursor"] for row in commit["input_cut"]["receipts"]})
        wrong = copy.deepcopy(commit); wrong["checkpoint"]["committed_output_inputs"][0]["received_at"] = 0
        self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
        self.j.worker_commit(commit, self.grant)

    def test_feedback_ack_requires_exact_later_cut_original_and_ten_past_samples(self):
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        self.j.worker_commit(prepared(self.j, self.grant, kind="semantic"), self.grant)
        self.now = .2; first = prepared(self.j, self.grant, t=.2, kind="control"); control = control_output(self, first, .2)
        wrong = copy.deepcopy(first); wrong["outputs"][0]["execution"]["samples"] = []
        feedback = wrong["outputs"][0]["execution"]["feedback"]; feedback["sample_digest"] = digest([])
        feedback["execution_digest"] = digest({key: body for key, body in wrong["outputs"][0]["execution"].items() if key != "feedback"})
        wrong["checkpoint"]["pending_feedback"]["component_digest"] = digest(feedback)
        self.code("invalid_commit", lambda: self.j.worker_commit(wrong, self.grant))
        receipt = self.j.worker_commit(first, self.grant)
        self.now = .4; second = prepared(self.j, self.grant, t=.4, kind="control"); control_output(self, second, .4)
        output_receipt = next(row for row in receipt["receipts"] if row["id"] == control["id"])
        second["checkpoint"]["acknowledged_feedback"] = {"frontier": first["checkpoint"]["pending_feedback"],
            "publication_ref": {"journal_epoch": self.j.stream_epoch, "cursor": output_receipt["cursor"], "payload_digest": output_receipt["payload_digest"]}, "received_at": .4}
        wrong = copy.deepcopy(second); wrong["checkpoint"]["acknowledged_feedback"]["frontier"]["component_digest"] = "f" * 64
        self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
        self.j.worker_commit(second, self.grant)

    def test_past_feedback_keeps_historical_null_goal_after_quality_revision(self):
        geometry = self.row("home-geometry-3"); geometry_receipt = self.append(geometry)
        environment = self.row("home-lighting-environment-1"); environment_receipt = self.append(environment)
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        semantic = prepared(self.j, self.grant, kind="semantic"); old_goal = goal_output(self, semantic)
        request, context = exposure_request(self, old_goal, geometry, geometry_receipt, environment, environment_receipt, semantic["checkpoint"]["authority"])
        semantic["checkpoint"]["pipeline"]["policy"]["requests"] = [request]
        self.j.worker_commit(semantic, self.grant)
        last_receipt = last_control = None
        for t in (.2, .4, .6, .8):
            self.now = t; commit = prepared(self.j, self.grant, t=t, kind="control"); control = control_output(self, commit, t)
            if t > .2:
                past_exposure(self, commit, control, context)
                proof = next(row for row in last_receipt["receipts"] if row["id"] == last_control["id"])
                control["consumed_refs"] = [{"component_id": last_control["execution"]["feedback"]["id"],
                    "component_digest": digest(last_control["execution"]["feedback"]),
                    "publication_ref": {"journal_epoch": self.j.stream_epoch, "cursor": proof["cursor"], "payload_digest": proof["payload_digest"],
                                        "component_id": last_control["execution"]["feedback"]["id"]}}]
            context["installed_at"] = t
            commit["checkpoint"]["pipeline"]["exposure_context"] = copy.deepcopy(context)
            last_receipt = self.j.worker_commit(commit, self.grant); last_control = control
        self.now = 1; revision = prepared(self.j, self.grant, t=1, kind="semantic")
        new_goal = goal_output(self, revision); new_goal.update(id="goal-output:quality1", revision=1,
            expected_predecessor={"revision": 0, "digest": digest(old_goal)})
        new_goal["qualities"]["illuminance_band"]["desired_lux"] = 400
        revision["checkpoint"]["goal_heads"] = [new_goal]
        new_request = copy.deepcopy(request); new_request.update(id="request:quality1",
            goal_ref={"component_id": new_goal["id"], "component_digest": digest(new_goal), "publication_ref": None})
        new_request["objectives"][0]["desired_lux"] = 400
        revision["checkpoint"]["pipeline"]["policy"]["requests"] = [new_request]
        revision["checkpoint"]["pipeline"]["exposure_context"] = copy.deepcopy(context)
        self.j.worker_commit(revision, self.grant)
        commit = prepared(self.j, self.grant, t=1, kind="control"); control = control_output(self, commit, 1)
        feedback = past_exposure(self, commit, control, context)
        self.assertIsNone(feedback["exposures"][0]["goal_ref"]["publication_ref"])
        self.assertNotIn(old_goal, commit["checkpoint"]["goal_heads"])
        wrong = copy.deepcopy(commit); wrong["outputs"][0]["execution"]["feedback"]["exposures"][0]["goal_ref"] = new_request["goal_ref"]
        wrong["checkpoint"]["pending_feedback"]["component_digest"] = digest(wrong["outputs"][0]["execution"]["feedback"])
        self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
        wrong = copy.deepcopy(commit); wrong["outputs"][0]["execution"]["feedback"]["exposures"][0]["environment_refs"] = []
        wrong["checkpoint"]["pending_feedback"]["component_digest"] = digest(wrong["outputs"][0]["execution"]["feedback"])
        self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
        receipt = self.j.worker_commit(commit, self.grant)
        self.assertEqual(self.j.worker_commit(commit, self.grant), receipt)

    def test_feedback_cannot_invent_prior_installed_context(self):
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        self.j.worker_commit(prepared(self.j, self.grant, kind="semantic"), self.grant)
        self.now = .2; commit = prepared(self.j, self.grant, t=.2, kind="control"); control = control_output(self, commit, .2)
        control["execution"]["feedback"]["exposures"] = example("home-lighting-outcome-feedback-1")["exposures"]
        # The example carries no exposures: construct a closed but fabricated one.
        if not control["execution"]["feedback"]["exposures"]:
            reference = {"component_id": "invented", "component_digest": "f" * 64, "publication_ref": None}
            control["execution"]["feedback"]["exposures"] = [{"goal_ref": reference, "request_ref": reference, "preference_refs": [], "environment_refs": [],
                "from_t": 0, "t": .2, "status": "unavailable", "reason": "invented", "weighted_coverage": None, "worst13_coverage": None,
                "protected_max_lux": None, "support_valid_until": 1, "environment_valid_until": None}]
        commit["checkpoint"]["pending_feedback"]["component_digest"] = digest(control["execution"]["feedback"])
        self.code("invalid_reference", lambda: self.j.worker_commit(commit, self.grant))

    def test_target_original_component_material_and_fixture_dictionary_integrity(self):
        geometry = self.row("home-geometry-3"); geometry_receipt = self.append(geometry)
        environment = self.row("home-lighting-environment-1"); environment_receipt = self.append(environment)
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        semantic = prepared(self.j, self.grant, kind="semantic"); goal = goal_output(self, semantic)
        request, context = exposure_request(self, goal, geometry, geometry_receipt, environment, environment_receipt, semantic["checkpoint"]["authority"])
        semantic["checkpoint"]["pipeline"]["policy"]["requests"] = [request]; self.j.worker_commit(semantic, self.grant)
        self.now = .2; commit = prepared(self.j, self.grant, t=.2, kind="control"); control = control_output(self, commit, .2)
        target = context["targets"][0]; snapshot = control["geometry_snapshot"]
        snapshot["object_heads"] = [{"object_ref": geometry["objects"][0]["object_ref"], "status": "fresh", "reason": "original_fresh",
            "estimate_ref": target["geometry_ref"], **{key: geometry[key] for key in ("observed_at", "available_at", "valid_until", "capture")},
            "estimate": geometry["objects"][0]}]
        snapshot["digest"] = content_digest(snapshot); commit["input_cut"]["geometry_snapshot_digest"] = snapshot["digest"]
        commit["input_cut"]["digest"] = content_digest(commit["input_cut"]); control["input_cut_digest"] = commit["input_cut"]["digest"]
        control["allocation"]["targets"] = [target]; commit["checkpoint"]["pipeline"]["exposure_context"] = context
        self.assertTrue(validate_adaptive(commit)["ok"], validate_adaptive(commit))
        for field in ("patches", "transform_origin_m", "uncertainty_correlation_ref", "geometry_ref"):
            wrong = copy.deepcopy(commit); bad = wrong["outputs"][0]["allocation"]["targets"][0]
            if field == "patches": bad[field][0]["centre_m"][0] += .01
            elif field == "transform_origin_m": bad[field][0] += .01
            elif field == "uncertainty_correlation_ref": bad[field] = "forged:correlation"
            else: bad[field]["component_id"] = "book-2"
            with self.subTest(field=field), self.assertRaises(JournalError) as caught:
                self.j.worker_commit(wrong, self.grant)
            self.assertIn(caught.exception.code, {"invalid_reference", "invalid_payload"})
        wrong = copy.deepcopy(commit); execution = wrong["outputs"][0]["execution"]
        execution["fixture_ids"] = list(reversed(execution["fixture_ids"]))
        execution["feedback"]["execution_digest"] = digest({key: body for key, body in execution.items() if key != "feedback"})
        wrong["checkpoint"]["pending_feedback"]["component_digest"] = digest(execution["feedback"])
        self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
        self.j.worker_commit(commit, self.grant)

    def test_applied_follow_stop_does_not_attach_to_new_association(self):
        self.append(self.row("home-geometry-3")); instruction = self.intent(expiry=3); self.append(instruction)
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        self.j.worker_commit(prepared(self.j, self.grant, kind="semantic", outcomes=[(instruction["id"], "applied", "stop")]), self.grant)
        self.now = .2; geometry = copy.deepcopy(self.fixture["later"][0]); geometry["objects"][0]["association_generation"] = 1; self.append(geometry)
        authority = self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])
        self.assertTrue(authority["follows"][0]["follow_allowed"]); self.assertEqual(authority["follows"][0]["association_generation"], 1)
        self.now = 1; self.j.worker_commit(prepared(self.j, self.grant, t=1, kind="semantic"), self.grant)
        self.assertTrue(self.j.dynamic.authority(self.identity["site_id"], self.identity["room_id"])["follows"][0]["follow_allowed"])

    def test_new_keep_here_capture_cannot_inherit_previous_instruction_pose(self):
        geometry = self.row("home-geometry-3"); geometry_receipt = self.append(geometry)
        first_intent = self.intent("keep_here", expiry=3); self.append(first_intent)
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        self.j.worker_commit(prepared(self.j, self.grant, kind="semantic", outcomes=[(first_intent["id"], "applied", "freeze")]), self.grant)
        self.now = .2; commit = prepared(self.j, self.grant, t=.2, kind="control"); control = control_output(self, commit, .2)
        first_head = {**install_geometry_head(self, commit, control, geometry, geometry_receipt), "capture_intent_id": first_intent["id"]}
        commit["checkpoint"]["pipeline"]["frozen_heads"] = [first_head]
        commit["checkpoint"]["pipeline"]["intent_ids"].append(first_intent["id"])
        wrong = copy.deepcopy(commit); wrong["checkpoint"]["pipeline"]["frozen_heads"][0]["capture_intent_id"] = "unapplied:keep_here"
        self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
        self.j.worker_commit(commit, self.grant)
        for t in (.4, .6, .8):
            self.now = t; commit = prepared(self.j, self.grant, t=t, kind="control"); control_output(self, commit, t)
            if t == .4:
                wrong = copy.deepcopy(commit); wrong["checkpoint"]["pipeline"]["frozen_heads"][0]["reason"] = "rewritten_capture"
                self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
            self.j.worker_commit(commit, self.grant)
        self.now = 1; geometry = copy.deepcopy(next(row for row in self.fixture["one"] if row["schema"] == "home-geometry-3")); second_geometry = self.append(geometry)
        second_intent = self.intent("keep_here", expiry=3); second_intent["id"] = "intent:keep_here:second"; self.append(second_intent)
        semantic = prepared(self.j, self.grant, t=1, kind="semantic", outcomes=[(second_intent["id"], "applied", "new_freeze")])
        semantic["checkpoint"]["pipeline"]["frozen_heads"] = [first_head]; self.j.worker_commit(semantic, self.grant)
        commit = prepared(self.j, self.grant, t=1, kind="control"); control = control_output(self, commit, 1)
        second_head = {**install_geometry_head(self, commit, control, geometry, second_geometry), "capture_intent_id": second_intent["id"]}
        commit["checkpoint"]["pipeline"]["frozen_heads"] = [second_head]
        commit["checkpoint"]["pipeline"]["intent_ids"].append(second_intent["id"])
        wrong = copy.deepcopy(commit); wrong["checkpoint"]["pipeline"]["frozen_heads"] = [{**first_head, "capture_intent_id": second_intent["id"]}]
        self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
        receipt = self.j.worker_commit(commit, self.grant)
        self.assertEqual(self.j.worker_commit(commit, self.grant), receipt)
        self.assertNotEqual(first_head["estimate_ref"], second_head["estimate_ref"])

    def test_invalidated_capture_id_cannot_be_erased_or_recapture_a_new_pose(self):
        geometry = self.row("home-geometry-3"); receipt = self.append(geometry)
        intent = self.intent("keep_here", expiry=3); self.append(intent)
        self.j.worker_commit(prepared(self.j, self.grant), self.grant)
        self.j.worker_commit(prepared(self.j, self.grant, kind="semantic", outcomes=[(intent["id"], "applied", "freeze")]), self.grant)
        self.now = .2; commit = prepared(self.j, self.grant, t=.2, kind="control"); control = control_output(self, commit, .2)
        head = {**install_geometry_head(self, commit, control, geometry, receipt), "capture_intent_id": intent["id"]}
        commit["checkpoint"]["pipeline"].update(frozen_heads=[head], intent_ids=[intent["id"]]); self.j.worker_commit(commit, self.grant)
        self.now = .4; commit = prepared(self.j, self.grant, t=.4, kind="control"); control_output(self, commit, .4)
        commit["checkpoint"]["pipeline"]["frozen_heads"] = []; self.j.worker_commit(commit, self.grant)
        self.now = .6; geometry = copy.deepcopy(geometry)
        geometry.update(id="new-pose:geometry", observed_at=.6, available_at=.6, valid_until=1.6)
        geometry["capture"].update(capture_seq=3, observed_at=.6)
        for patch in geometry["objects"][0]["patches"]: patch["centre_m"][0] += .2
        receipt = self.append(geometry)
        commit = prepared(self.j, self.grant, t=.6, kind="control"); control = control_output(self, commit, .6)
        head = {**install_geometry_head(self, commit, control, geometry, receipt), "capture_intent_id": intent["id"]}
        wrong = copy.deepcopy(commit); wrong["checkpoint"]["pipeline"]["frozen_heads"] = [head]
        self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
        wrong = copy.deepcopy(commit); wrong["checkpoint"]["pipeline"]["intent_ids"] = []
        self.code("invalid_reference", lambda: self.j.worker_commit(wrong, self.grant))
        self.j.worker_commit(commit, self.grant)


class AdaptiveHTTPTests(unittest.TestCase):
    setUpClass = AdaptiveServiceTests.__dict__["setUpClass"]
    row = AdaptiveServiceTests.row
    def setUp(self):
        AdaptiveServiceTests.setUp(self)
        self.server = LocalServer(("127.0.0.1", 0), self.j)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown(); self.thread.join(timeout=2); self.server.server_close(); AdaptiveServiceTests.tearDown(self)

    def request(self, path, body=None):
        headers = {"Authorization": "Bearer adaptive-test"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + PREFIX + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with self.opener.open(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_http_adaptive_routes_retry_frontier_and_profile_isolation(self):
        self.assertEqual(self.request("/preference-snapshots", self.row("home-preference-snapshot-1"))[0], 200)
        self.assertEqual(self.request("/events", envelope(self.j, self.grant, self.row("home-geometry-3")))[0], 200)
        scope = "?site_id=" + self.identity["site_id"] + "&room_id=" + self.identity["room_id"]
        status, cut = self.request("/adaptive-input-cut" + scope + "&consumer_id=adaptive-worker&after=0&t=0")
        self.assertEqual(status, 200); self.assertTrue(cut["complete"])
        self.assertEqual(self.request("/dynamic-input-cut" + scope + "&consumer_id=adaptive-worker&after=0&t=0")[0], 404)
        commit = prepared(self.j, self.grant)
        status, receipt = self.request("/worker-commits", commit)
        self.assertEqual(status, 200, receipt); self.assertEqual(self.request("/worker-commits", commit), (200, receipt))
        status, saved = self.request("/adaptive-commits/" + commit["id"] + scope)
        self.assertEqual(status, 200); self.assertEqual(saved["receipt"], receipt)
        status, snapshot = self.request("/snapshot" + scope + "&consumer_id=adaptive-worker")
        self.assertEqual(status, 200, snapshot); self.assertEqual(snapshot["schema"], "home-context-snapshot-3")

    def test_http_pending_snapshot_has_no_delivery_side_effects(self):
        geometry = self.row("home-geometry-3"); geometry.update(available_at=.2, valid_until=1.2)
        self.assertEqual(self.request("/events", envelope(self.j, self.grant, geometry))[0], 200)
        path = "/snapshot?site_id=" + self.identity["site_id"] + "&room_id=" + self.identity["room_id"] + "&consumer_id=adaptive-worker"
        status, rejected = self.request(path)
        self.assertEqual(status, 409); self.assertEqual(rejected["error"]["code"], "snapshot_pending_inputs")
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM dynamic_deliveries").fetchone()[0], 0)
        self.now = .2; self.assertEqual(self.request(path)[0], 200)

    def test_http_registered_source_binding_and_revocation_reject_fresh_support(self):
        surface = self.row("home-registered-surface-1")
        wrong = copy.deepcopy(surface); wrong.update(id="wrong-source-http", source_id="scripted-daylight")
        status, rejected = self.request("/events", envelope(self.j, self.grant, wrong))
        self.assertEqual(status, 403, rejected)
        self.assertEqual(rejected["error"]["code"], "forbidden")
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM publications").fetchone()[0], 0)
        status, receipt = self.request("/events", envelope(self.j, self.grant, surface))
        self.assertEqual(status, 200, receipt)
        self.assertEqual(self.request("/worker-commits", prepared(self.j, self.grant))[0], 200)
        self.assertEqual(self.request("/worker-commits", prepared(self.j, self.grant, kind="semantic"))[0], 200)
        self.now = .2
        commit = prepared(self.j, self.grant, t=.2, kind="control")
        control = control_output(self, commit, .2); install_surface_head(self, commit, control, surface, receipt)
        self.assertEqual(self.request("/worker-commits", commit)[0], 200)
        self.now = .4
        revocation = {"schema": "home-context-revocation-1", "version": 1, "id": "revoke-registered-http", **self.identity,
                      "source_id": surface["source_id"], "revision": "*", "available_at": .4, "reason": "synthetic_source_closed"}
        status, revoked = self.request("/revocations", envelope(self.j, self.grant, revocation))
        self.assertEqual(status, 200, revoked)
        old_commit = self.j.checkpoint(self.grant.expected_worker_id, self.grant, self.identity["site_id"], self.identity["room_id"])["commit_seq"]
        commit = prepared(self.j, self.grant, t=.4, kind="control")
        control = control_output(self, commit, .4); install_surface_head(self, commit, control, surface, receipt)
        status, rejected = self.request("/worker-commits", commit)
        self.assertEqual(status, 409, rejected); self.assertEqual(rejected["error"]["code"], "revoked")
        self.assertEqual(self.j.checkpoint(self.grant.expected_worker_id, self.grant, self.identity["site_id"], self.identity["room_id"])["commit_seq"], old_commit)
        install_surface_head(self, commit, control, surface, receipt, status="rejected")
        status, stopped = self.request("/worker-commits", commit)
        self.assertEqual(status, 200, stopped)
        authority = commit["checkpoint"]["authority"]
        self.assertFalse(next(row for row in authority["sources"] if row["source_id"] == "scripted-daylight")["revoked"])

    def test_http_second_freeze_requires_its_own_fresh_capture(self):
        def boundary(t, kind="control", outcomes=None):
            self.now = t
            old = self.j.checkpoint(self.grant.expected_worker_id, self.grant, self.identity["site_id"], self.identity["room_id"])["checkpoint"]
            status, fence = self.request("/adaptive-input-cut?site_id=" + self.identity["site_id"] + "&room_id=" + self.identity["room_id"]
                + "&consumer_id=adaptive-worker&after=" + str(old["input_cursor"] if old else 0) + "&t=" + str(t))
            self.assertEqual(status, 200, fence)
            return prepared(self.j, self.grant, t=t, kind=kind, outcomes=outcomes, fence=fence)
        geometry = self.row("home-geometry-3")
        status, first_geometry = self.request("/events", envelope(self.j, self.grant, geometry)); self.assertEqual(status, 200)
        intent = AdaptiveServiceTests.intent(self, "keep_here", expiry=3)
        self.assertEqual(self.request("/intent-events", intent)[0], 200)
        self.assertEqual(self.request("/worker-commits", boundary(0, "bootstrap"))[0], 200)
        self.assertEqual(self.request("/worker-commits", boundary(0, "semantic", [(intent["id"], "applied", "freeze")]))[0], 200)
        commit = boundary(.2); control = control_output(self, commit, .2)
        first_head = {**install_geometry_head(self, commit, control, geometry, first_geometry), "capture_intent_id": intent["id"]}
        commit["checkpoint"]["pipeline"]["frozen_heads"] = [first_head]
        commit["checkpoint"]["pipeline"]["intent_ids"].append(intent["id"])
        self.assertEqual(self.request("/worker-commits", commit)[0], 200)
        for t in (.4, .6, .8):
            commit = boundary(t); control_output(self, commit, t)
            self.assertEqual(self.request("/worker-commits", commit)[0], 200)
        self.now = 1; geometry = copy.deepcopy(next(row for row in self.fixture["one"] if row["schema"] == "home-geometry-3"))
        status, second_geometry = self.request("/events", envelope(self.j, self.grant, geometry)); self.assertEqual(status, 200)
        intent = AdaptiveServiceTests.intent(self, "keep_here", expiry=3); intent["id"] = "intent:keep_here:second"
        self.assertEqual(self.request("/intent-events", intent)[0], 200)
        semantic = boundary(1, "semantic", [(intent["id"], "applied", "new_freeze")]); semantic["checkpoint"]["pipeline"]["frozen_heads"] = [first_head]
        self.assertEqual(self.request("/worker-commits", semantic)[0], 200)
        commit = boundary(1); control = control_output(self, commit, 1)
        second_head = {**install_geometry_head(self, commit, control, geometry, second_geometry), "capture_intent_id": intent["id"]}
        commit["checkpoint"]["pipeline"]["frozen_heads"] = [second_head]
        commit["checkpoint"]["pipeline"]["intent_ids"].append(intent["id"])
        wrong = copy.deepcopy(commit); wrong["checkpoint"]["pipeline"]["frozen_heads"] = [{**first_head, "capture_intent_id": intent["id"]}]
        status, rejected = self.request("/worker-commits", wrong)
        self.assertEqual(status, 409, rejected); self.assertEqual(rejected["error"]["code"], "invalid_reference")
        status, receipt = self.request("/worker-commits", commit)
        self.assertEqual(status, 200, receipt); self.assertEqual(self.request("/worker-commits", commit), (200, receipt))


if __name__ == "__main__":
    unittest.main()
