"""Native reference-profile journals and literal-loopback service regressions."""
import copy
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from adaptive_feedback_reference_validation import (
    REFERENCE_MODE, REFERENCE_PROFILE, PROJECTION_FIELDS, validate_reference,
)
from journal import Journal, JournalError, PREFIX, canonical, digest
from schema_registry import SchemaRegistry, REFERENCE_SCHEMAS, FEEDBACK_SCHEMAS
from server import LocalServer
import test_adaptive_feedback_context_service as legacy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def compact_fixture_checkpoint(cp):
    """Independent test authoring from full synthetic rows, not service encoding."""
    value = copy.deepcopy(cp)
    value.update(schema="home-context-worker-checkpoint-8", version=8)
    by_cursor = {d["source_ref"]["publication_ref"]["cursor"]: d for d in value["dependency_receipts"]}
    for role in ("pending_inputs", "geometry_inputs", "retained_inputs", "committed_output_inputs"):
        value[role] = [{"dependency_digest": by_cursor[row["publication"]["cursor"]]["digest"],
                        **({"component_ids": copy.deepcopy(row["component_ids"])} if "component_ids" in row else {})} for row in cp[role]]
    value["revisions"]["rows"] = [{"dependency_digest": by_cursor[row["publication"]["cursor"]]["digest"]} for row in cp["revisions"]["rows"]]
    pipeline = value["pipeline"]
    if pipeline and pipeline["feedback_exposure"]["held_context"] is not None:
        projection = {k: pipeline["feedback_exposure"]["held_context"][k] for k in PROJECTION_FIELDS}
        if projection == pipeline["exposure_context"]:
            pipeline["exposure_context"] = {"representation": "held_context_projection_1", "digest": digest(projection)}
    return value


class ReferenceFeedbackServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = legacy.fixture()
        cls.f["config"].update(profile_mode=REFERENCE_MODE, interface_digest=SchemaRegistry((), profile=REFERENCE_PROFILE).interface_digest)
        cls.f["config_digest"] = digest(cls.f["config"])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HERE); self.now = 0
        self.identity = self.f["identity"]
        config = {k: self.f[k] for k in ("identity", "clock_profile", "materials", "registered_surfaces", "config", "config_digest", "producer_epochs", "actor_bindings", "classifier_policy", "write_authorities")}
        self.j = Journal(Path(self.tmp.name) / "fresh.sqlite", profile=REFERENCE_PROFILE,
                         adaptive_feedback_reference_config=config, grants=legacy.grants(self.f), clock=lambda: self.now)
        self.owner, self.worker, self.issuer = [next(g for g in self.j.grants if g.principal_id == p) for p in ("owner", "worker", "issuer")]
        self.owner_read = next(g for g in self.j.grants if g.principal_id == "owner" and g.operations == frozenset({"read"}))

    def tearDown(self): self.j.close(); self.tmp.cleanup()
    def scoped(self, name): return legacy.FeedbackServiceTests.scoped(self, name)
    def preference(self): return legacy.FeedbackServiceTests.preference(self)
    def geometry(self, t=0, state='open', available=None): return legacy.FeedbackServiceTests.geometry(self, t, state, available)
    def evidence(self, g, event_id='evidence:1'): return legacy.FeedbackServiceTests.evidence(self, g, event_id)

    def bootstrap(self):
        commit = legacy.FeedbackServiceTests.bootstrap(self)
        commit.update(schema="home-context-worker-commit-8", version=8)
        commit["checkpoint"] = compact_fixture_checkpoint(commit["checkpoint"])
        return commit

    def semantic_genesis(self): return legacy.FeedbackServiceTests.semantic_genesis(self)

    def refusal(self, function, code=None):
        with self.assertRaises(JournalError) as caught: function()
        if code is not None: self.assertEqual(caught.exception.code, code)

    def test_complete_graph_examples_pins_and_old_dialect_rejection(self):
        registry = self.j.schemas
        self.assertEqual(len(registry.schemas), 71)
        for name in registry.schemas:
            example = json.loads((ROOT / "contracts/home-adaptive-feedback-reference/examples" / (name + ".json")).read_text())
            self.assertTrue(validate_reference(example, registry)["ok"], name)
        for name in ("home-context-worker-checkpoint-7", "home-context-worker-commit-7", "home-context-worker-output-proof-3", "home-context-replay-8"):
            old = json.loads((ROOT / "contracts/home-adaptive-feedback/examples" / (name + ".json")).read_text())
            self.assertFalse(validate_reference(old, registry)["ok"], name)
        for name in registry.schemas.keys() & SchemaRegistry((), profile="adaptive-book-feedback-synthetic-1").schemas.keys():
            self.assertEqual((REFERENCE_SCHEMAS / (name + ".schema.json")).read_bytes(), (FEEDBACK_SCHEMAS / (name + ".schema.json")).read_bytes())

    def test_exact_raw_wire_retry_conflict_and_compact_durable_checkpoint(self):
        commit = self.bootstrap(); wire = canonical(commit)
        receipt = self.j.dynamic.worker_commit(commit, self.worker, request_canonical_json=wire)
        self.now = 1
        self.assertEqual(self.j.dynamic.worker_commit(commit, self.worker, request_canonical_json=wire), receipt)
        self.assertEqual(receipt["raw_request_digest"], hashlib.sha256(wire.encode()).hexdigest())
        stored = json.loads(self.j.db.execute("SELECT checkpoint FROM workers").fetchone()[0])
        self.assertEqual(stored, commit["checkpoint"])
        candidate = copy.deepcopy(commit); candidate["id"] = "other"
        self.refusal(lambda: self.j.dynamic.worker_commit(commit, self.worker, request_canonical_json=canonical(candidate)), "noncanonical_commit")
        candidate = copy.deepcopy(commit); candidate["checkpoint"]["static_bindings_digest"] = "1" * 64
        self.refusal(lambda: self.j.dynamic.worker_commit(candidate, self.worker), "identity_conflict")

    def test_typed_checkpoint_response_and_actual_original_proof(self):
        empty = self.j.checkpoint("worker", self.worker, self.identity["site_id"], self.identity["room_id"])
        self.assertEqual(empty["schema"], "home-context-worker-checkpoint-response-6")
        self.assertIsNone(empty["checkpoint"])
        commit = self.semantic_genesis(); receipt = self.j.dynamic.worker_commit(commit, self.worker)
        state = self.j.checkpoint("worker", self.worker, self.identity["site_id"], self.identity["room_id"])
        self.assertEqual(state["checkpoint"], commit["checkpoint"])
        cursor = next(x["cursor"] for x in receipt["receipts"] if x["id"] == "installed-zero")
        proof = self.j.dynamic.lookup_output_proof(self.owner, self.identity["site_id"], self.identity["room_id"], cursor=cursor, consumer_id="owner")
        self.assertEqual(proof["schema"], "home-context-worker-output-proof-4")
        self.assertEqual(proof["commit"], commit)
        self.assertEqual(proof["request_canonical_json"], canonical(commit))
        self.assertTrue(validate_reference(proof, self.j.schemas)["ok"])
        for path, value in ((('receipt', 'control_seq'), 1), (('publication', 'context_seq'), 99),
                            (('receipt', 'first_received_at'), 1), (('commit', 'expected_input_cursor'), 1)):
            mutant = copy.deepcopy(proof); location = mutant
            for key in path[:-1]: location = location[key]
            location[path[-1]] = value
            if path[0] == 'commit':
                mutant['request_canonical_json'] = canonical(mutant['commit']); mutant['receipt']['raw_request_digest'] = digest(mutant['commit'])
            self.assertFalse(validate_reference(mutant, self.j.schemas)["ok"], path)

    def test_prior_compact_roles_are_hydrated_on_durable_cut_reread(self):
        commit = self.semantic_genesis()
        preference = self.preference(); receipt = self.j.dynamic.declaration(preference, self.owner, family=preference["schema"])
        fence = self.j.dynamic.input_cut(self.worker, self.identity["site_id"], self.identity["room_id"], consumer_id="worker", after=0, t=0)
        publication = next(p for p in fence["publications"] if p["cursor"] == receipt["cursor"])
        descriptor = {"source_ref": self.j.dynamic._source_ref(publication), "received_at": 0, "eligible_at": 0}
        descriptor["digest"] = digest(descriptor)
        checkpoint = commit["checkpoint"]
        checkpoint["input_cursor"] = fence["cut_cursor"]
        checkpoint["dependency_receipts"] = [descriptor]
        checkpoint["retained_inputs"] = [{"dependency_digest": descriptor["digest"]}]
        checkpoint["revisions"]["rows"] = [{"dependency_digest": descriptor["digest"]}]
        checkpoint["receipts"] = [{"ref": descriptor["source_ref"]["publication_ref"], "received_at": 0, "eligible_at": 0}]
        cut = commit["input_cut"]
        cut.update(cut_id=fence["cut_id"], input_cursor=fence["cut_cursor"], receipts=copy.deepcopy(checkpoint["receipts"]),
                   eligible_refs=[copy.deepcopy(descriptor["source_ref"]["publication_ref"])], buffered_refs=[])
        cut["digest"] = digest({k: v for k, v in cut.items() if k != "digest"})
        installed = next(output for output in commit["outputs"] if output["schema"] == "home-lighting-installed-boundary-1")
        installed["input_cut_digest"] = cut["digest"]
        checkpoint["pending_installed"]["payload_digest"] = digest(installed)
        self.j.dynamic.worker_commit(commit, self.worker)
        expanded = self.j.dynamic._stored_checkpoint_view(checkpoint, self.worker)
        self.assertEqual(expanded["retained_inputs"][0]["publication"], publication)
        self.assertEqual(expanded["revisions"]["rows"][0]["publication"], publication)
        altered = copy.deepcopy(checkpoint); altered["dependency_receipts"][0]["source_ref"]["producer_epoch"] = "foreign"
        self.refusal(lambda: self.j.dynamic._stored_checkpoint_view(altered, self.worker))
        self.j.db.execute("DELETE FROM feedback_worker_first_receipts WHERE cursor=?", (publication["cursor"],))
        self.refusal(lambda: self.j.dynamic._stored_checkpoint_view(checkpoint, self.worker), "invalid_input_receipt")

    def test_public_lookup_respects_read_only_grants_and_corrupt_raw_original(self):
        commit = self.semantic_genesis(); receipt = self.j.dynamic.worker_commit(commit, self.worker)
        cursor = next(x["cursor"] for x in receipt["receipts"] if x["id"] == "installed-zero")
        self.refusal(lambda: self.j.dynamic.worker_commit(commit, self.owner_read), "forbidden")
        proof = self.j.dynamic.lookup_output_proof(self.owner_read, self.identity["site_id"], self.identity["room_id"], cursor=cursor, consumer_id="owner")
        self.assertEqual(proof["receipt"], receipt)
        self.j.db.execute("UPDATE feedback_worker_commit_originals SET request_digest=? WHERE id=?", ("0" * 64, commit["id"]))
        self.refusal(lambda: self.j.dynamic.lookup_output_proof(self.owner, self.identity["site_id"], self.identity["room_id"], cursor=cursor, consumer_id="owner"), "corrupt_original")

    def test_reference_public_evidence_current_head_and_exact_retry(self):
        legacy.FeedbackServiceTests.test_native_evidence_current_head_and_exact_retry(self)

    def test_reference_evidence_pending_deadline_is_terminal(self):
        legacy.FeedbackServiceTests.test_stub_pending_deadline_terminal(self)

    def test_reference_future_geometry_preserves_snapshot_and_cache_fences(self):
        legacy.FeedbackServiceTests.test_future_geometry_snapshot_and_cache_do_not_advance_early(self)

    def test_reference_producer_revocation_preserves_retry_and_fences_new_work(self):
        legacy.FeedbackServiceTests.test_producer_revoke_preserves_historical_retry_but_fences_new_commands(self)

    def test_reference_source_revocation_prevents_old_cached_support(self):
        legacy.FeedbackServiceTests.test_independent_source_revocation_fences_cached_geometry(self)

    def test_reference_storage_refusal_is_atomic_under_unchanged_limits(self):
        legacy.FeedbackServiceTests.test_atomic_capacity_refusal_does_not_leave_origin_cursor_or_receipt(self)

    def test_worker_original_first_is_separate_durable_future_and_fixed_principal(self):
        from dataclasses import replace
        preference = self.preference(); preference.update(available_at=40, valid_until=120)
        receipt = self.j.dynamic.declaration(preference, self.owner, family=preference["schema"])
        commit = self.bootstrap()
        publication = json.loads(self.j.db.execute("SELECT body FROM publications WHERE cursor=?", (receipt["cursor"],)).fetchone()[0])
        descriptor = {"source_ref": self.j.dynamic._source_ref(publication), "received_at": 0, "eligible_at": 40}
        descriptor["digest"] = digest(descriptor)
        cp, cut = commit["checkpoint"], commit["input_cut"]
        cp.update(input_cursor=receipt["cursor"], dependency_receipts=[descriptor], pending_inputs=[{"dependency_digest": descriptor["digest"]}],
                  receipts=[{"ref": descriptor["source_ref"]["publication_ref"], "received_at": 0, "eligible_at": 40}])
        cp["revisions"]["rows"] = [{"dependency_digest": descriptor["digest"]}]
        cut.update(receipts=copy.deepcopy(cp["receipts"]), eligible_refs=[], buffered_refs=[descriptor["source_ref"]["publication_ref"]])
        cut["digest"] = digest({k: v for k, v in cut.items() if k != "digest"})
        site, room = self.identity["site_id"], self.identity["room_id"]
        readonly = replace(self.worker, operations=frozenset({"read"}), token_sha256=hashlib.sha256(b"reference-worker-read").hexdigest())
        self.j.grants = (*self.j.grants, readonly)
        before = self.j.dynamic.lookup_worker_original(readonly, site, room, cursor=receipt["cursor"], consumer_id="worker")
        self.assertIsNone(before["worker_receipt"])
        self.assertEqual(before["original"]["receipt"]["received_at"], 0)
        self.assertEqual(before["original"]["receipt"]["eligible_at"], 40)
        self.j.dynamic.worker_commit(commit, self.worker)
        after = self.j.dynamic.lookup_worker_original(readonly, site, room, cursor=receipt["cursor"], consumer_id="worker")
        self.assertEqual(after["worker_receipt"], {"consumer_id": "worker", "received_at": 0, "eligible_at": 40,
                                                 "digest": digest({"consumer_id": "worker", "received_at": 0, "eligible_at": 40})})
        self.assertTrue(validate_reference(after, self.j.schemas)["ok"])
        self.now = 1
        self.assertEqual(after["worker_receipt"], self.j.dynamic.lookup_worker_original(readonly, site, room, cursor=receipt["cursor"], consumer_id="worker")["worker_receipt"])
        self.refusal(lambda: self.j.dynamic.lookup_worker_original(self.owner_read, site, room, cursor=receipt["cursor"], consumer_id="owner"), "forbidden")
        foreign = replace(readonly, principal_id="foreign-reader")
        self.refusal(lambda: self.j.dynamic.lookup_worker_original(foreign, site, room, cursor=receipt["cursor"], consumer_id="worker"), "forbidden")
        self.refusal(lambda: self.j.dynamic.lookup_worker_original(readonly, site, room, cursor=receipt["cursor"], consumer_id="owner"), "forbidden")

    def test_http_canonical_compact_admission_retry_and_checkpoint(self):
        server = LocalServer(("127.0.0.1", 0), self.j)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            base = "http://127.0.0.1:" + str(server.server_address[1]) + PREFIX
            opener = build_opener(ProxyHandler({}))
            def request(route, body=None, token="test-worker", extra=""):
                data = canonical(body).encode() if body is not None else None
                query = "?site_id=" + self.identity["site_id"] + "&room_id=" + self.identity["room_id"] + extra
                req = Request(base + route + (query if body is None else ""), data=data,
                              headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST" if body is not None else "GET")
                with opener.open(req, timeout=5) as response: return json.load(response)
            commit = self.bootstrap(); first = request("/worker-commits", commit)
            self.assertEqual(first, request("/worker-commits", commit))
            state = request("/worker-commits/worker")
            self.assertEqual(state["schema"], "home-context-worker-checkpoint-response-6")
            self.assertEqual(state["checkpoint"], commit["checkpoint"])
            preference = self.preference()
            pref_receipt = self.j.dynamic.declaration(preference, self.owner, family=preference["schema"])
            extra = "&cursor=" + str(pref_receipt["cursor"]) + "&consumer_id=worker"
            original = request("/feedback-worker-originals", extra=extra)
            self.assertEqual(original["schema"], "home-context-worker-original-lookup-1")
            self.assertIsNone(original["worker_receipt"])
            with self.assertRaises(HTTPError) as caught:
                request("/feedback-worker-originals", token="test-owner-read", extra=extra)
            self.assertEqual(caught.exception.code, 403)
            with self.assertRaises(HTTPError) as caught:
                request("/feedback-worker-originals", extra=extra + "&worker_id=foreign")
            self.assertEqual(caught.exception.code, 400)
            legacy = copy.deepcopy(commit); legacy.update(schema="home-context-worker-commit-7", version=7)
            with self.assertRaises(HTTPError) as caught: request("/worker-commits", legacy)
            self.assertEqual(caught.exception.code, 400)
        finally:
            server.shutdown(); thread.join(timeout=5); server.server_close()


if __name__ == "__main__": unittest.main()
