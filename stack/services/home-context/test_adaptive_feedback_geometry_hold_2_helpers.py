"""Bounded real G2 journals and helper regressions; no HTTP/devices/models."""
import copy
import hashlib
import json
import sqlite3
import struct
import subprocess
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from journal import Grant, Journal, JournalError, canonical, digest
from adaptive_feedback_geometry_hold_2_validation import G2_PROFILE, PrivateOriginLedger, _PHASE_VIEWS_FALLBACK
import adaptive_feedback_geometry_hold_2_phase_views as phase
import adaptive_feedback_geometry_hold_2_internal_storage as storage
import geometry_hold_2_fixture_storage_diagnostics as diagnostic

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FIXTURE = json.loads((HERE / "test_geometry_hold_2_helpers_fixture.json").read_text(encoding="utf-8"))


def frame(raw, declared=None, suffix=b""):
    return (storage.MAGIC + struct.pack(">I", len(raw) if declared is None else declared)
            + hashlib.sha256(raw).digest() + zlib.compress(raw, 9) + suffix)


class WorkerProcess:
    def __init__(self, options):
        self.process = subprocess.Popen(
            ["node", "--max-old-space-size=512", str(HERE / "test_geometry_hold_2_helpers_fixture.cjs"), "--rpc"],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.call("create", options=options)

    def call(self, method, *args, **fields):
        self.process.stdin.write(json.dumps({"method": method, "args": args, **fields}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("fixture worker ended: " + self.process.stderr.read())
        result = json.loads(line)
        if not result["ok"]:
            raise RuntimeError(result["error"])
        return result["value"]

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process.stdout.close()
        self.process.stderr.close()


class Host:
    def __init__(self, *, origin=True, views=False, journal_class=Journal, fixture=None):
        self.fixture = copy.deepcopy(FIXTURE if fixture is None else fixture)
        self.clock = phase.create_fixture_clock()
        self.j = journal_class(":memory:", profile=G2_PROFILE,
                               adaptive_feedback_geometry_hold_2_config=self.fixture["configuration"],
                               grants=self.fixture["grants"], clock=self.clock.read)
        self.clock.bind(self.j)
        self.j._set_meta("epoch", "g2-helper-fixture-journal-1")
        self.worker = next(grant for grant in self.j.grants if "worker_commit" in grant.operations)
        self.original_profile, self.original_ledger = self.j.dynamic, self.j.dynamic.origins
        if origin:
            self.j.dynamic.origins = storage.create_origin_storage_ledger(self.j, storage.selection_configuration())
        self.ledger = self.j.dynamic.origins
        if views:
            self.worker_static_bindings = {key: copy.deepcopy(self.fixture["options"][key])
                                          for key in phase._BINDINGS_KEYS}
            self.j.dynamic = phase.create_phase_view_profile(self.j, phase.selection_configuration(), self.clock,
                                                             worker_static_bindings=self.worker_static_bindings)
        self.auth = self.j.dynamic._auth(self.worker)
        self.native = None
        self.last_receipt = None

    def close(self):
        if self.native is not None:
            self.native.close()
        self.j.close()

    def native_worker(self):
        if self.native is None:
            self.native = WorkerProcess(self.fixture["options"])
        return self.native

    def input_cut(self, t):
        self.clock.advance(t)
        return self.j.dynamic.input_cut(self.worker, self.j.dynamic.identity["site_id"],
            self.j.dynamic.identity["room_id"], consumer_id=self.worker.expected_consumer_id,
            after=0, t=t, limit=128)

    def prepare_bootstrap(self):
        return self.native_worker().call("prepareBootstrap", {"cut": self.input_cut(0)})

    def accept(self, commit):
        receipt = self.j.dynamic.worker_commit(commit, self.worker, request_canonical_json=canonical(commit))
        self.native_worker().call("acknowledge", commit, receipt)
        self.last_receipt = receipt
        return receipt

    def prepare_boundary(self, t):
        cut = self.input_cut(t)
        # These are actual current journal publications delivered at the actual
        # worker cut. Worker-processing FIRST is independently recorded at commit.
        for publication in cut["publications"]:
            self.native_worker().call("ingest", publication, t)
        if self.last_receipt:
            committed = []
            for receipt in self.last_receipt["receipts"]:
                lookup = self.j.dynamic.lookup_original(self.worker, self.j.dynamic.identity["site_id"],
                    self.j.dynamic.identity["room_id"], cursor=receipt["cursor"],
                    consumer_id=self.worker.expected_consumer_id)
                if lookup["publication"]["payload_type"] in ("home-lighting-policy-decision-3", "home-lighting-settled-exposure-1"):
                    committed.append(lookup["publication"])
            if committed:
                self.native_worker().call("ingestCommittedOutputs", committed, t)
        return self.native_worker().call("prepareBoundary", {"t": t, "cut": cut})

    def lifecycle(self):
        records = []
        bootstrap = self.prepare_bootstrap(); records.append((bootstrap, self.accept(bootstrap)))
        semantic = self.prepare_boundary(0); records.append((semantic, self.accept(semantic)))
        control = self.prepare_boundary(.2); records.append((control, self.accept(control)))
        return records


class CodecTests(unittest.TestCase):
    def test_exact_G2_frame_and_smaller_complete_representation(self):
        text = canonical({"body": "repeated-original-" * 1000})
        encoded = storage._encode_value(text)
        self.assertEqual(storage.MAGIC, b"HOME-G2-ORIGIN-REQ-ZLIB-1\0")
        self.assertEqual(encoded, frame(text.encode()))
        self.assertLess(len(encoded), len(text.encode()))
        self.assertEqual(storage._decode_value(encoded, digest(json.loads(text))), text)

    def test_plain_fallback_and_unicode_canonical_roundtrip(self):
        for value in ({"a": 1}, None, {"emoji": "🙂 café 中文 " * 500, "numbers": [1e-7, 1e21, 4.2]}):
            text = canonical(value)
            self.assertEqual(storage._decode_value(storage._encode_value(text), digest(value)), text)
        small = canonical({"a": 1})
        self.assertIs(storage._encode_value(small), small)

    def test_exact_decoded_256KiB_limit_and_overflow(self):
        text = canonical("x" * (storage.MAX_BYTES - 2))
        self.assertEqual(len(text.encode()), 262144)
        self.assertEqual(storage._decode_value(storage._encode_value(text), digest(json.loads(text))), text)
        with self.assertRaises(JournalError): storage._encode_value(canonical("x" * (storage.MAX_BYTES - 1)))

    def test_old_R71_and_unknown_frames_are_refused(self):
        raw = canonical({"x": "same" * 1000}).encode(); valid = frame(raw)
        for bad in (valid.replace(b"HOME-G2", b"HOME-R71", 1), valid.replace(b"ZLIB-1", b"ZLIB-2", 1),
                    raw, bytearray(valid), memoryview(valid), None):
            with self.subTest(kind=type(bad).__name__):
                with self.assertRaises(JournalError): storage._decode_value(bad, hashlib.sha256(raw).hexdigest())

    def test_length_digest_truncation_and_expected_hash_are_independent(self):
        raw = canonical({"x": "same" * 1000}).encode(); valid = frame(raw)
        for bad in (valid[:-1], frame(raw, len(raw) - 1), frame(raw, len(raw) + 1),
                    valid[:len(storage.MAGIC) + 4] + b"\0" * 32 + valid[storage._HEADER_BYTES:]):
            with self.assertRaises(JournalError): storage._decode_value(bad, hashlib.sha256(raw).hexdigest())
        for expected in ("0" * 64, "A" * 64, None, "short"):
            with self.assertRaises(JournalError): storage._decode_value(valid, expected)

    def test_trailing_streams_and_bombs_refuse_under_decoded_bound(self):
        raw = canonical({"x": "same" * 1000}).encode()
        for bad in (frame(raw, suffix=b"tail"), frame(raw, suffix=zlib.compress(b"second")),
                    frame(b"x" * (storage.MAX_BYTES + 1), storage.MAX_BYTES), frame(raw, storage.MAX_BYTES + 1)):
            with self.assertRaises(JournalError): storage._decode_value(bad, hashlib.sha256(raw).hexdigest())

    def test_noncanonical_duplicate_keys_nonfinite_and_bad_utf8_refuse(self):
        for raw in (b'{ "a":1}', b'{"a":1,"a":1}', b'NaN', b"\xff" * 2000):
            with self.assertRaises(JournalError): storage._decode_value(frame(raw), hashlib.sha256(raw).hexdigest())
        with self.assertRaises(JournalError): storage._encode_value("\ud800")


class RealG2Tests(unittest.TestCase):
    def host(self, **options):
        host = Host(**options); self.addCleanup(host.close); return host

    def test_real_G2_owner_and_constructor_free_origin_adoption(self):
        h = self.host(origin=False)
        self.assertEqual(type(h.j.dynamic).__name__, "AdaptiveFeedbackGeometryHold2Profile")
        self.assertIs(type(h.j.dynamic.origins), PrivateOriginLedger)
        before = list(h.j.db.iterdump())
        with patch.object(PrivateOriginLedger, "__init__", side_effect=AssertionError("constructor rerun")):
            adopted = storage.create_origin_storage_ledger(h.j, storage.selection_configuration())
        self.assertEqual(list(h.j.db.iterdump()), before)
        self.assertIs(adopted.connection, h.j.db)
        self.assertIsNot(adopted, h.j.dynamic.origins)
        with self.assertRaises(JournalError): adopted.lookup(h.auth, "absent")

    def test_exact_selection_and_old_profile_refuse_without_new_storage(self):
        h = self.host(origin=False)
        for change in ({"profile": "adaptive-book-feedback-reference-synthetic-1"},
                       {"codec_version": True}, {"extra": True}, {"source_digest": "0" * 64}):
            selection = {**storage.selection_configuration(), **change}
            with self.assertRaises(ValueError): storage.create_origin_storage_ledger(h.j, selection)
        self.assertEqual(set(h.j.adaptive_feedback_geometry_hold_2_config), phase._CONFIG_KEYS)
        self.assertEqual(set(h.j.adaptive_feedback_geometry_hold_2_config["config"]), phase._WORKER_KEYS)
        self.assertEqual(h.j.dynamic.config_digest, FIXTURE["configuration"]["config_digest"])

    def test_genuine_journal_selects_operative_worker_with_distinct_read_only_credential(self):
        fixture = copy.deepcopy(FIXTURE)
        reader = copy.deepcopy(fixture["grants"][0]); reader["operations"] = ["read"]
        reader["token_sha256"] = hashlib.sha256(b"g2-focused-worker-read-token").hexdigest()
        fixture["grants"].append(reader)
        h = self.host(fixture=fixture, views=True)
        selected = h.j.dynamic._worker_grant()
        self.assertIs(selected, h.worker)
        same_identity = [g for g in h.j.grants if (g.principal_id, g.producer_id, g.producer_epoch) ==
                         (selected.principal_id, selected.producer_id, selected.producer_epoch)]
        self.assertEqual(len(same_identity), 2)
        self.assertEqual(len({g.token_sha256 for g in same_identity}), 2)
        self.assertTrue(all(g.expected_consumer_id == selected.expected_consumer_id for g in same_identity))
        read_only = next(g for g in same_identity if "worker_commit" not in g.operations)
        self.assertEqual(read_only.operations, frozenset({"read"}))
        self.assertEqual(selected.expected_worker_id, selected.producer_id)
        self.assertEqual(selected.expected_consumer_id, selected.producer_id)

    def test_worker_grant_selection_requires_unique_operative_fixed_tuple(self):
        h = self.host(); p = h.j.dynamic; before = h.j.grants
        for grants in ((h.worker, h.worker), tuple(g for g in before if "worker_commit" not in g.operations)):
            h.j.grants = grants
            try:
                with self.assertRaises(JournalError) as caught: p._worker_grant()
                self.assertEqual(caught.exception.code, "issuer_unavailable")
            finally: h.j.grants = before
        bad = copy.deepcopy(FIXTURE["grants"][0])
        bad["expected_worker_id"] = bad["expected_consumer_id"] = "different-fixed-worker"
        replacement = Grant.from_dict(bad, profile=G2_PROFILE)
        h.j.grants = tuple(replacement if g is h.worker else g for g in before)
        try:
            with self.assertRaises(JournalError) as caught: p._worker_grant()
            self.assertEqual(caught.exception.code, "issuer_unavailable")
        finally: h.j.grants = before

    def test_profile_keeps_the_closed_five_typed_operations_and_existing_writer_event_right(self):
        raw = copy.deepcopy(FIXTURE["grants"][1]); raw["operations"] = ["read", "event"]
        self.assertEqual(Grant.from_dict(raw, profile=G2_PROFILE).operations, frozenset({"read", "event"}))
        raw["operations"] = ["read", "preference_write"]
        with self.assertRaises(ValueError): Grant.from_dict(raw, profile=G2_PROFILE)

    def test_actual_blob_roundtrip_retry_FIRST_and_origin_conflict(self):
        h = self.host(); request = {"id": "helper-origin", "body": "original" * 1800}
        first = {"status": "rejected", "first_received_at": 0}
        saved = h.j._transaction(lambda: h.ledger.remember(h.auth, request["id"], request, first, validate_receipt=lambda _: True))
        row = h.j.db.execute("SELECT typeof(request_json),request_json FROM adaptive_feedback_origins").fetchone()
        self.assertEqual(row[0], "blob")
        self.assertEqual(storage._decode_value(row[1], digest(request)), canonical(request))
        h.clock.advance(1)
        retry = h.ledger.remember(h.auth, request["id"], request, {"status": "applied"},
                                 validate_receipt=lambda _: self.fail("retry revalidated receipt"))
        self.assertEqual(retry, saved); self.assertEqual(retry["first_receipt"], first)
        with self.assertRaises(JournalError): h.ledger.retry(h.auth, request["id"], {**request, "changed": True})

    def test_origin_transaction_and_atomic_rollback_still_apply(self):
        h = self.host(); request = {"id": "rollback-origin", "body": "original" * 1000}
        with self.assertRaises(JournalError):
            h.ledger.remember(h.auth, request["id"], request, {"status": "rejected"}, validate_receipt=lambda _: True)
        def transaction():
            h.ledger.remember(h.auth, request["id"], request, {"status": "rejected"}, validate_receipt=lambda _: True)
            raise JournalError("forced_after_origin", "bounded rollback probe")
        with self.assertRaises(JournalError): h.j._transaction(transaction)
        self.assertEqual(h.j.db.execute("SELECT count(*) FROM adaptive_feedback_origins").fetchone()[0], 0)
        self.assertIsNone(h.ledger.lookup(h.auth, request["id"]))

    def test_live_binding_changes_refuse_before_empty_lookup_SQL(self):
        for field, target, value in (("profile", "journal", "old"), ("config_digest", "profile", "0" * 64),
                                     ("clock", "journal", lambda: 0), ("origins", "profile", object())):
            h = self.host(); owner = h.j if target == "journal" else h.j.dynamic
            original = getattr(owner, field); statements = []
            h.j.db.set_trace_callback(statements.append); setattr(owner, field, value)
            try:
                with self.assertRaises(JournalError): h.ledger.lookup(h.auth, "absent")
                self.assertEqual(statements, [])
            finally: setattr(owner, field, original); h.j.db.set_trace_callback(None)

    def test_captured_platform_and_origin_method_identity_refuse_before_SQL(self):
        h = self.host(); statements = []; h.j.db.set_trace_callback(statements.append)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("replacement must not run")):
            with self.assertRaises(JournalError): h.ledger.lookup(h.auth, "absent")
        self.assertEqual(statements, [])
        with patch.object(PrivateOriginLedger, "_encode_request_storage", lambda self, text: text):
            with self.assertRaises(JournalError): h.ledger.lookup(h.auth, "absent")
        self.assertEqual(statements, [])

    def test_phase_tokens_are_actual_transaction_invocation_and_single_use_bound(self):
        h = self.host(views=True); commit = h.prepare_bootstrap(); p = h.j.dynamic
        def transaction():
            invocation = object(); token = p._prepare_phase_checked_views(commit, None, h.worker, invocation=invocation)
            self.assertIsNotNone(token)
            with self.assertRaises(JournalError): p._consume_phase_checked_views(token, commit, None, h.worker, invocation=object())
            with self.assertRaises(JournalError): p._consume_phase_checked_views(token, copy.deepcopy(commit), None, h.worker, invocation=invocation)
            self.assertIsNot(p._consume_phase_checked_views(token, commit, None, h.worker, invocation=invocation), _PHASE_VIEWS_FALLBACK)
            with self.assertRaises(JournalError): p._consume_phase_checked_views(token, commit, None, h.worker, invocation=invocation)
            return {"done": True}
        h.j._transaction(transaction)
        self.assertEqual(phase.phase_view_metrics(p)["successful_consumptions"], 1)

    def test_phase_exact_recipe_is_detached_and_missing_or_inert_invalid_recipe_refuses_before_SQL(self):
        h = self.host(); statements = []; h.j.db.set_trace_callback(statements.append)
        bindings = {key: copy.deepcopy(h.fixture["options"][key]) for key in phase._BINDINGS_KEYS}
        for bad in (None, {**bindings, "extra": 1}, {**bindings, "duration_s": True},
                    {**bindings, "duration_s": 121}, {**bindings, "subjectBindings": ()},
                    {**bindings, "source_origins": {"cyclic": None}}):
            if bad is not None and "cyclic" in bad.get("source_origins", {}):
                bad["source_origins"]["cyclic"] = bad
            with self.assertRaises(ValueError):
                phase.create_phase_view_profile(h.j, phase.selection_configuration(), h.clock,
                                                 worker_static_bindings=bad)
        self.assertEqual(statements, []); h.j.db.set_trace_callback(None)
        p = phase.create_phase_view_profile(h.j, phase.selection_configuration(), h.clock,
                                             worker_static_bindings=bindings)
        h.j.dynamic = p
        bindings["duration_s"] = 1
        self.assertEqual(p._worker_static_bindings["duration_s"], 3)
        self.assertEqual(p._worker_static_bindings_digest, digest({
            "subjectBindings": [], "revision_authorizations": [], "source_origins": {}, "runtime_duration_s": 3}))
        self.assertTrue(p._commissioned_clock())

    def test_phase_worker_recipe_mutation_replacement_and_digest_change_refuse_before_prepare(self):
        h = self.host(views=True); p = h.j.dynamic
        original = p._worker_static_bindings
        for replacement, changed_digest in (({**original, "duration_s": 1}, None),
                                            (copy.deepcopy(original), None), (original, "0" * 64)):
            p._worker_static_bindings = replacement
            previous_digest = p._worker_static_bindings_digest
            if changed_digest is not None: p._worker_static_bindings_digest = changed_digest
            try:
                with self.assertRaises(JournalError):
                    p._prepare_phase_checked_views({}, None, h.worker, invocation=object())
            finally:
                p._worker_static_bindings = original; p._worker_static_bindings_digest = previous_digest
        original["subjectBindings"].append({"mutated": True})
        with self.assertRaises(JournalError): p._prepare_phase_checked_views({}, None, h.worker, invocation=object())

    def test_phase_worker_recipe_change_during_invocation_refuses_consumption(self):
        h = self.host(views=True); commit = h.prepare_bootstrap(); p = h.j.dynamic
        def transaction():
            invocation = object(); token = p._prepare_phase_checked_views(commit, None, h.worker, invocation=invocation)
            p._worker_static_bindings["duration_s"] = 1
            with self.assertRaises(JournalError):
                p._consume_phase_checked_views(token, commit, None, h.worker, invocation=invocation)
            self.assertTrue(token._used)
            return {"done": True}
        h.j._transaction(transaction)

    def test_phase_worker_transition_class_and_instance_replacement_are_not_commissioned(self):
        h = self.host(views=True); p = h.j.dynamic
        p._worker_transition = lambda *_: self.fail("replacement executed")
        self.assertFalse(p._commissioned_clock()); del p._worker_transition
        with patch.object(type(p), "_worker_transition", lambda *_: self.fail("replacement executed")):
            self.assertFalse(p._commissioned_clock())
        self.assertTrue(p._commissioned_clock())

    def test_phase_original_body_mutation_and_finished_transaction_refuse(self):
        h = self.host(views=True); commit = h.prepare_bootstrap(); p = h.j.dynamic; saved = []; invocation = object()
        def transaction():
            token = p._prepare_phase_checked_views(commit, None, h.worker, invocation=invocation); saved.append(token)
            commit["id"] += "-changed"
            with self.assertRaises(JournalError): p._consume_phase_checked_views(token, commit, None, h.worker, invocation=invocation)
            return {"done": True}
        h.j._transaction(transaction)
        with self.assertRaises(JournalError): p._consume_phase_checked_views(saved[0], commit, None, h.worker, invocation=invocation)

    def test_phase_clock_change_falls_back_once_and_never_reuses_token(self):
        h = self.host(views=True); commit = h.prepare_bootstrap(); p = h.j.dynamic
        def transaction():
            invocation = object(); token = p._prepare_phase_checked_views(commit, None, h.worker, invocation=invocation)
            h.clock.advance(.01)
            self.assertIs(p._consume_phase_checked_views(token, commit, None, h.worker, invocation=invocation), _PHASE_VIEWS_FALLBACK)
            with self.assertRaises(JournalError): p._consume_phase_checked_views(token, commit, None, h.worker, invocation=invocation)
            return {"done": True}
        h.j._transaction(transaction)
        self.assertEqual(phase.phase_view_metrics(p)["consume_fallbacks"], 1)

    def test_real_bootstrap_semantic_control_and_restore_use_genuine_worker_FIRST(self):
        h = self.host(views=True); records = h.lifecycle()
        self.assertEqual([commit["kind"] for commit, _ in records], ["bootstrap", "semantic", "control"])
        self.assertEqual([receipt["commit_seq"] for _, receipt in records], [1, 2, 3])
        checkpoint = h.native_worker().call("snapshot")
        originals = []
        for descriptor in checkpoint["dependency_receipts"]:
            cursor = descriptor["source_ref"]["publication_ref"]["cursor"]
            lookup = h.j.dynamic.lookup_worker_original(h.worker, h.j.dynamic.identity["site_id"],
                h.j.dynamic.identity["room_id"], cursor=cursor, consumer_id=h.worker.expected_consumer_id)
            first = lookup["worker_receipt"]
            self.assertIsNotNone(first)
            self.assertEqual(first["consumer_id"], h.worker.producer_id)
            self.assertEqual(first["received_at"], descriptor["received_at"])
            self.assertEqual(first["eligible_at"], descriptor["eligible_at"])
            originals.append({"publication": lookup["original"]["publication"], "first_received_at": first["received_at"]})
        reader = next(grant for grant in h.j.grants if "worker_commit" not in grant.operations)
        h.clock.advance(1)
        p = h.j.dynamic; site, room = p.identity["site_id"], p.identity["room_id"]
        p.read_cut(reader, site, room, consumer_id=reader.expected_consumer_id)
        policy_id = next(output["id"] for output in records[1][0]["outputs"]
                         if output["schema"] == "home-lighting-policy-decision-3")
        semantic_policy = next(receipt for receipt in records[1][1]["receipts"] if receipt["id"] == policy_id)
        proof = p.lookup_output_proof(reader, site, room, cursor=semantic_policy["cursor"],
                                     consumer_id=reader.expected_consumer_id,
                                     expected_families=["home-lighting-policy-decision-3"])
        self.assertEqual((proof["schema"], proof["version"]), ("home-context-worker-output-proof-6", 6))
        self.assertEqual(proof["consumer_receipt"]["consumer_id"], reader.expected_consumer_id)
        self.assertEqual(proof["consumer_receipt"]["received_at"], 1)
        self.assertEqual(proof["worker_receipt"]["consumer_id"], h.worker.expected_consumer_id)
        self.assertEqual(proof["worker_receipt"]["received_at"], .2)
        self.assertNotEqual(proof["consumer_receipt"], proof["worker_receipt"])
        self.assertEqual(proof["prior_transition"], {"request_canonical_json": canonical(records[0][0]),
                                                     "receipt": records[0][1]})
        self.assertEqual(json.loads(proof["prior_transition"]["request_canonical_json"])["checkpoint"]["schema"],
                         "home-context-worker-checkpoint-9")
        restored = WorkerProcess(h.fixture["options"]); self.addCleanup(restored.close)
        restored.call("restore", checkpoint, {"original_rows": originals})
        self.assertEqual(restored.call("snapshot"), checkpoint)
        if originals:
            bad = copy.deepcopy(originals); bad[0]["first_received_at"] += .01
            with self.assertRaises(RuntimeError): restored.call("restore", checkpoint, {"original_rows": bad})
            self.assertEqual(restored.call("snapshot"), checkpoint)
        metrics = phase.phase_view_metrics(h.j.dynamic)
        self.assertEqual(metrics["successful_consumptions"], 3)

    def test_G2_diagnostic_profile_composes_with_real_storage_and_phase_owners(self):
        h = self.host(views=True, journal_class=diagnostic.StorageDiagnosticJournal)
        h.accept(h.prepare_bootstrap())
        before = h.j.db.total_changes; result = h.j.fixture_storage_diagnostics()
        self.assertEqual(result["profile"], G2_PROFILE)
        self.assertEqual(result["allocation_status"], "available")
        self.assertEqual(h.j.db.total_changes, before)
        self.assertFalse(result["actuated"]); self.assertFalse(result["acceptance_enabled"])
        stored = result["stored_columns"]["tables"]
        self.assertEqual(next(row for row in stored if row["table"] == "feedback_worker_commit_originals")["rows"], 1)

    def test_G2_diagnostic_SQL_failure_preserves_rollback_and_redacts_SQL_text(self):
        h = self.host(journal_class=diagnostic.StorageDiagnosticJournal)
        h.j.db.execute("CREATE TABLE helper_values(value INTEGER CHECK(value>=0))")
        def transaction():
            h.j.db.execute("INSERT INTO helper_values VALUES(1)")
            h.j.db.execute("INSERT INTO helper_values VALUES(-1)")
        with self.assertRaises(JournalError): h.j._transaction(transaction)
        self.assertEqual(h.j.db.execute("SELECT count(*) FROM helper_values").fetchone()[0], 0)
        result = h.j.fixture_storage_diagnostics()
        self.assertEqual(result["sqlite_failure_count"], 1)
        self.assertFalse(result["last_sqlite_failure"]["transaction_active_after_error"])
        self.assertNotIn("helper_values", canonical(result["last_sqlite_failure"]))
        self.assertNotIn("INSERT INTO", canonical(result["last_sqlite_failure"]))
        self.assertNotIn("constraint failed", canonical(result["last_sqlite_failure"]).lower())
        self.assertEqual(set(result["last_sqlite_failure"]), {"operation", "sqlite_errorcode",
                         "sqlite_errorname", "transaction_active_after_error"})
        self.assertEqual(result["last_sqlite_failure"]["sqlite_errorname"], "SQLITE_CONSTRAINT_CHECK")

    def test_foreign_diagnostic_profile_and_journal_subclass_are_not_commissioned(self):
        with self.assertRaises(ValueError): diagnostic.StorageDiagnosticJournal(":memory:", profile="adaptive-book-feedback-reference-synthetic-1")
        class ForeignJournal(Journal):
            pass
        h = self.host(origin=False, journal_class=ForeignJournal)
        with self.assertRaises(ValueError): storage.create_origin_storage_ledger(h.j, storage.selection_configuration())


if __name__ == "__main__":
    unittest.main()
