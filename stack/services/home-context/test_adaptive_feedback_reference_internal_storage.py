"""Actual local SQLite and nonnetwork R71 comparisons; no HTTP/devices/models."""
import copy
import hashlib
import json
import random
import sqlite3
import struct
import unittest
import zlib
from unittest.mock import patch

import adaptive_feedback_reference_internal_storage as storage
import test_adaptive_feedback_reference_context_service as reference
import test_adaptive_feedback_context_service as legacy
from adaptive_feedback_validation import PrivateOriginLedger
from adaptive_feedback_reference_phase_views import create_fixture_clock, create_phase_view_profile, selection_configuration as phase_selection
from journal import Journal, JournalError, canonical, digest
import fixture_storage_diagnostics as diagnostic


def frame(raw, declared=None, suffix=b""):
    return (storage.MAGIC + struct.pack(">I", len(raw) if declared is None else declared)
            + hashlib.sha256(raw).digest() + zlib.compress(raw, 9) + suffix)


class CodecTests(unittest.TestCase):
    def test_exact_magic_version_and_strictly_smaller_complete_frame(self):
        text = canonical({"body": "repeated-original-" * 1000})
        encoded = storage._encode_value(text)
        self.assertEqual(encoded, frame(text.encode()))
        self.assertLess(len(encoded), len(text.encode()))
        self.assertEqual(storage._decode_value(encoded, digest(json.loads(text))), text)
        self.assertEqual(storage.MAGIC, b"HOME-R71-ORIGIN-REQ-ZLIB-1\0")

    def test_small_and_incompressible_values_keep_plain_text_exact(self):
        rng = random.Random(29)
        for value in ({"a": 1}, {"noise": "".join(chr(rng.randrange(32, 127)) for _ in range(40))}, None):
            text = canonical(value)
            self.assertIs(storage._encode_value(text), text)
            self.assertEqual(storage._decode_value(text, digest(value)), text)

    def test_utf8_unicode_and_js_compatible_number_text_roundtrip(self):
        value = {"\U0001f642": "café 中文 " * 600, "\ue000": [0.0, -0.0, 1e-7, 1e21, 4.2]}
        text = canonical(value)
        self.assertEqual(storage._decode_value(storage._encode_value(text), digest(value)), text)
        self.assertEqual(storage._decode_value(text, digest(value)), text)

    def test_exact_256k_decoded_bound_and_overflow(self):
        text = canonical("x" * (storage.MAX_BYTES - 2))
        self.assertEqual(len(text.encode()), 262144)
        self.assertEqual(storage._decode_value(storage._encode_value(text), digest(json.loads(text))), text)
        with self.assertRaises(JournalError): storage._encode_value(canonical("x" * (storage.MAX_BYTES - 1)))
        with self.assertRaises(JournalError): storage._decode_value(b"x" * (storage.MAX_BYTES + 1), "0" * 64)

    def test_wrong_magic_version_unframed_bytes_and_wrong_type_refuse(self):
        text = canonical({"x": "repeat" * 1000}); raw = text.encode(); valid = frame(raw)
        for bad in (valid.replace(b"ZLIB-1", b"ZLIB-2", 1), b"unknown" + valid, raw, bytearray(valid), memoryview(valid), None):
            with self.subTest(kind=type(bad).__name__):
                with self.assertRaises(JournalError): storage._decode_value(bad, hashlib.sha256(raw).hexdigest())

    def test_length_digest_row_hash_and_truncation_refuse(self):
        raw = canonical({"x": "repeated" * 1000}).encode(); valid = frame(raw)
        for bad in (valid[:-1], frame(raw, len(raw) - 1), frame(raw, len(raw) + 1),
                    valid[:len(storage.MAGIC) + 4] + b"\0" * 32 + valid[storage._HEADER_BYTES:]):
            with self.assertRaises(JournalError): storage._decode_value(bad, hashlib.sha256(raw).hexdigest())
        with self.assertRaises(JournalError): storage._decode_value(valid, "0" * 64)
        for expected in (None, 1, "A" * 64, "short"):
            with self.assertRaises(JournalError): storage._decode_value(valid, expected)

    def test_trailing_bytes_second_stream_and_bomb_never_expand_beyond_bound(self):
        raw = canonical({"x": "same" * 1000}).encode()
        for bad in (frame(raw, suffix=b"tail"), frame(raw, suffix=zlib.compress(b"second")),
                    frame(b"x" * (storage.MAX_BYTES + 1), storage.MAX_BYTES), frame(raw, storage.MAX_BYTES + 1)):
            with self.assertRaises(JournalError): storage._decode_value(bad, hashlib.sha256(raw).hexdigest())

    def test_noncanonical_and_invalid_utf8_plain_or_encoded_refuse(self):
        for raw in (b" " * 1000 + b'{"a":1}', b'{"a":1.0}' + b" " * 1000, b"\xff" * 2000,
                    b'{"a":1,"a":1}' + b" " * 1000, b'NaN' + b" " * 1000):
            with self.assertRaises(JournalError): storage._decode_value(frame(raw), hashlib.sha256(raw).hexdigest())
        with self.assertRaises(JournalError): storage._decode_value('{ "a":1}', digest({"a": 1}))
        with self.assertRaises(JournalError): storage._encode_value("\ud800")


class Host:
    scoped = reference.ReferenceFeedbackServiceTests.scoped
    preference = reference.ReferenceFeedbackServiceTests.preference
    bootstrap = reference.ReferenceFeedbackServiceTests.bootstrap
    semantic_genesis = reference.ReferenceFeedbackServiceTests.semantic_genesis

    def __init__(self, fixture, optin=True, journal_class=Journal):
        self.f = copy.deepcopy(fixture); self.identity = self.f["identity"]
        self.clock = create_fixture_clock()
        config = {k: self.f[k] for k in ("identity", "clock_profile", "materials", "registered_surfaces", "config",
                   "config_digest", "producer_epochs", "actor_bindings", "classifier_policy", "write_authorities")}
        self.j = journal_class(":memory:", profile=storage.REFERENCE_PROFILE, adaptive_feedback_reference_config=config,
                         grants=legacy.grants(self.f), clock=self.clock.read)
        self.clock.bind(self.j); self.j._set_meta("epoch", "synthetic-origin-codec-differential-epoch")
        self.original = self.j.dynamic.origins
        if optin:
            try:
                self.j.dynamic.origins = storage.create_origin_storage_ledger(self.j, storage.selection_configuration())
            except BaseException:
                self.j.close()
                raise
        self.ledger = self.j.dynamic.origins
        self.owner, self.worker, self.issuer = [next(g for g in self.j.grants if g.principal_id == p) for p in ("owner", "worker", "issuer")]
        self.auth = self.j.dynamic._auth(self.owner)

    @property
    def now(self): return self.clock.read()
    @now.setter
    def now(self, t): self.clock.advance(t)
    def close(self): self.j.close()


class OriginStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reference.ReferenceFeedbackServiceTests.setUpClass()
        cls.fixture = reference.ReferenceFeedbackServiceTests.f

    def setUp(self):
        self.h = Host(self.fixture); self.addCleanup(self.h.close)
        self.request = {"id": "toy-1", "basis": "original" * 1800, "numbers": [1e-7, 4.2], "unicode": "中文 café"}
        self.receipt = {"cursor": 1, "first_received_at": 4.2, "status": "rejected"}

    def remember(self, host=None, request=None, event_id="toy-1", receipt=None):
        h = host or self.h
        return h.j._transaction(lambda: h.ledger.remember(h.auth, event_id, request or self.request,
                                receipt or self.receipt, validate_receipt=lambda r: type(r) is dict))

    def assert_code(self, code, action):
        with self.assertRaises(JournalError) as error: action()
        self.assertEqual(error.exception.code, code)

    def test_factory_adopts_without_constructor_ddl_connection_or_old_class_mutation(self):
        plain = Host(self.fixture, False); self.addCleanup(plain.close)
        before = list(plain.j.db.iterdump()); connection = plain.j.db
        with patch.object(PrivateOriginLedger, "__init__", side_effect=AssertionError("constructor rerun")):
            adopted = storage.create_origin_storage_ledger(plain.j, storage.selection_configuration())
        self.assertEqual(list(plain.j.db.iterdump()), before)
        self.assertIs(adopted.connection, connection)
        self.assertIs(type(plain.j.dynamic.origins), PrivateOriginLedger)
        self.assertIsNot(adopted, plain.j.dynamic.origins)
        with self.assertRaises(JournalError): adopted._encode_request_storage(canonical(self.request))

    def test_exact_closed_selection_and_double_or_late_commission_refuse(self):
        self.assertEqual(set(storage.selection_configuration()), {"interface_version", "mode", "profile", "source_digest", "codec_version"})
        plain = Host(self.fixture, False); self.addCleanup(plain.close)
        for update in ({"codec_version": 2}, {"profile": "old"}, {"extra": True}, {"source_digest": "0" * 64}):
            with self.assertRaises(ValueError): storage.create_origin_storage_ledger(plain.j, {**storage.selection_configuration(), **update})
        with self.assertRaises(ValueError): storage.create_origin_storage_ledger(self.h.j, storage.selection_configuration())
        self.remember(host=plain)
        with self.assertRaises(ValueError): storage.create_origin_storage_ledger(plain.j, storage.selection_configuration())

    def test_selector_requires_exact_inert_scalar_types_without_equality_callbacks(self):
        plain = Host(self.fixture, False); self.addCleanup(plain.close)
        with self.assertRaises(ValueError):
            storage.create_origin_storage_ledger(plain.j, {**storage.selection_configuration(), "codec_version": True})
        class EqualityTrap:
            calls = 0
            def __eq__(self, other): self.calls += 1; raise AssertionError("unexpected selector callback")
        value = EqualityTrap()
        with self.assertRaises(ValueError):
            storage.create_origin_storage_ledger(plain.j, {**storage.selection_configuration(), "mode": value})
        self.assertEqual(value.calls, 0)

    def test_old_origin_instance_method_overrides_refuse_before_any_sql_or_callback(self):
        for name in ("lookup", "retry", "remember", "_encode_request_storage", "_decode_request_storage"):
            h = Host(self.fixture, False); self.addCleanup(h.close); calls = []; sql = []
            h.original.__dict__[name] = lambda *args, **kwargs: calls.append("override")
            h.j.db.set_trace_callback(sql.append)
            with self.assertRaises(ValueError): storage.create_origin_storage_ledger(h.j, storage.selection_configuration())
            self.assertEqual(calls, []); self.assertEqual(sql, [])

    def test_adopted_instance_and_class_shadow_methods_refuse_before_callback_or_sql(self):
        self.remember(); names = ("lookup", "retry", "remember", "_encode_request_storage", "_decode_request_storage")
        for kind in ("instance", "class"):
            for name in names:
                calls = []; sql = []; target = self.h.ledger if kind == "instance" else type(self.h.ledger)
                def callback(*args, **kwargs): calls.append("override"); return None
                self.h.j.db.set_trace_callback(sql.append)
                with patch.object(target, name, callback):
                    with self.assertRaises(JournalError): self.h.ledger.lookup(self.h.auth, "toy-1")
                self.assertEqual(calls, []); self.assertEqual(sql, [])
        self.h.j.db.set_trace_callback(None)

    def test_substituted_descriptors_never_run_before_old_or_adopted_shape_refusal(self):
        calls = []; sql = []
        class DescriptorTrap:
            def __get__(self, instance, owner): calls.append("descriptor"); raise AssertionError("descriptor ran")
        plain = Host(self.fixture, False); self.addCleanup(plain.close)
        plain.j.db.set_trace_callback(sql.append)
        with patch.object(PrivateOriginLedger, "lookup", DescriptorTrap()):
            self.assert_code("origin_storage_source_changed", lambda: storage.create_origin_storage_ledger(plain.j, storage.selection_configuration()))
        self.h.j.db.set_trace_callback(sql.append)
        for name in ("lookup", "retry", "remember", "_encode_request_storage", "_decode_request_storage", "connection", "maximum_origins"):
            with patch.object(type(self.h.ledger), name, DescriptorTrap(), create=True):
                self.assert_code("origin_storage_binding_changed", lambda: self.h.ledger.lookup(self.h.auth, "toy-1"))
        self.assertEqual(calls, []); self.assertEqual(sql, [])
        self.h.j.db.set_trace_callback(None); plain.j.db.set_trace_callback(None)

    def test_previously_bound_entries_refuse_later_shadow_before_callback_or_sql(self):
        self.remember(); sql = []; calls = []
        entries = (lambda f=self.h.ledger.lookup: f(self.h.auth, "toy-1"),
                   lambda f=self.h.ledger.retry: f(self.h.auth, "toy-1", self.request),
                   lambda f=self.h.ledger.remember: f(self.h.auth, "toy-1", self.request, self.receipt, validate_receipt=lambda _: True),
                   lambda f=self.h.ledger._encode_request_storage: f(canonical(self.request)),
                   lambda f=self.h.ledger._decode_request_storage: f(canonical(self.request), digest(self.request)))
        self.h.j.db.set_trace_callback(sql.append)
        for kind in ("instance", "class"):
            for name in ("lookup", "retry", "remember", "_encode_request_storage", "_decode_request_storage"):
                target = self.h.ledger if kind == "instance" else type(self.h.ledger)
                with patch.object(target, name, lambda *args, **kwargs: calls.append("override")):
                    for entry in entries:
                        self.assert_code("origin_storage_binding_changed", entry)
        self.assertEqual(calls, []); self.assertEqual(sql, [])
        self.h.j.db.set_trace_callback(None)

    def test_exact_initialized_state_and_unbranded_copies_refuse(self):
        plain = Host(self.fixture, False); self.addCleanup(plain.close); sql = []
        plain.original.extra_state = {"approved": True}; plain.j.db.set_trace_callback(sql.append)
        with self.assertRaises(ValueError): storage.create_origin_storage_ledger(plain.j, storage.selection_configuration())
        self.assertEqual(sql, []); plain.j.db.set_trace_callback(None)
        for ledger in (copy.copy(self.h.ledger), object.__new__(type(self.h.ledger))):
            self.assert_code("origin_storage_uncommissioned", lambda: ledger.lookup(self.h.auth, "toy-1"))
        for key, value in (("extra_state", True), ("maximum_origins", False), ("connection", None)):
            with patch.object(self.h.ledger, key, value, create=True):
                self.assert_code("origin_storage_binding_changed", lambda: self.h.ledger.lookup(self.h.auth, "toy-1"))

    def test_fixed_structural_entry_guard_cannot_be_replaced_by_normal_assignment(self):
        for target in (self.h.ledger, type(self.h.ledger)):
            for name in ("__getattribute__", "__setattr__", "__delattr__", "__hash__", "__eq__", "__class__", "__dict__"):
                with self.assertRaises(TypeError): setattr(target, name, lambda *args: None)
        self.assertIsNone(self.h.ledger.lookup(self.h.auth, "not-present"))

    def test_default_plain_hooks_and_selected_actual_blob_storage(self):
        plain = Host(self.fixture, False); self.addCleanup(plain.close)
        expected = self.remember(host=plain); actual = self.remember()
        self.assertEqual(actual, expected)
        old = plain.j.db.execute("SELECT typeof(request_json),request_json FROM adaptive_feedback_origins").fetchone()
        new = self.h.j.db.execute("SELECT typeof(request_json),request_json FROM adaptive_feedback_origins").fetchone()
        self.assertEqual(old[0], "text"); self.assertEqual(new[0], "blob")
        self.assertEqual(old[1], canonical(self.request)); self.assertEqual(storage._decode_value(new[1], digest(self.request)), old[1])
        self.assertEqual(self.h.ledger.lookup(self.h.auth, "toy-1"), plain.ledger.lookup(plain.auth, "toy-1"))
        self.assertEqual(self.h.j.db.execute("SELECT request_digest,first_receipt_json,first_receipt_digest FROM adaptive_feedback_origins").fetchone(),
                         plain.j.db.execute("SELECT request_digest,first_receipt_json,first_receipt_digest FROM adaptive_feedback_origins").fetchone())

    def test_plain_fallback_and_legacy_plain_rows_recover(self):
        self.remember(request={"a": 1})
        row = self.h.j.db.execute("SELECT typeof(request_json),request_json FROM adaptive_feedback_origins").fetchone()
        self.assertEqual(row[0], "text")
        self.h.j.db.execute("UPDATE adaptive_feedback_origins SET request_json=?", (canonical({"a": 1}),))
        self.assertEqual(self.h.ledger.lookup(self.h.auth, "toy-1")["request"], {"a": 1})

    def test_exact_retry_terminal_first_receipt_and_conflict_match_default(self):
        first = self.remember(); before = list(self.h.j.db.iterdump())
        self.h.now = 20
        retry = self.h.ledger.remember(self.h.auth, "toy-1", copy.deepcopy(self.request), {"status": "applied", "new_clock": 20},
                                       validate_receipt=lambda _: (_ for _ in ()).throw(AssertionError("renewed validator")))
        self.assertEqual(retry, first); self.assertEqual(retry["first_receipt"], self.receipt)
        self.assertEqual(list(self.h.j.db.iterdump()), before)
        self.assert_code("feedback_origin_conflict", lambda: self.h.ledger.retry(self.h.auth, "toy-1", {**self.request, "new": True}))

    def test_wrong_principal_rejected_before_any_decode(self):
        self.remember()
        self.h.j.db.execute("UPDATE adaptive_feedback_origins SET request_json=?", (frame(b"x" * (storage.MAX_BYTES + 1), storage.MAX_BYTES),))
        with patch.object(storage, "_decode_value", side_effect=AssertionError("decoded for wrong principal")):
            self.assert_code("feedback_origin_owner", lambda: self.h.ledger.lookup({**self.h.auth, "principal_id": "foreign"}, "toy-1"))

    def test_unknown_or_corrupt_actual_blob_row_never_falls_back_to_json(self):
        self.remember()
        for value in (b'{"id":"toy-1"}', b"unknown-version", frame(canonical(self.request).encode())[:-1]):
            self.h.j.db.execute("UPDATE adaptive_feedback_origins SET request_json=?", (value,))
            self.assert_code("feedback_origin_corrupt", lambda: self.h.ledger.lookup(self.h.auth, "toy-1"))

    def test_plain_and_blob_expected_immutable_row_digest_mismatch_refuse(self):
        self.remember(); self.h.j.db.execute("UPDATE adaptive_feedback_origins SET request_digest=?", ("0" * 64,))
        self.assert_code("feedback_origin_corrupt", lambda: self.h.ledger.lookup(self.h.auth, "toy-1"))

    def test_actual_transaction_rollback_preserves_no_partial_origin(self):
        def attempted():
            self.h.ledger.remember(self.h.auth, "toy-1", self.request, self.receipt, validate_receipt=lambda _: True)
            raise JournalError("forced_after_origin", "synthetic injected rollback")
        self.assert_code("forced_after_origin", lambda: self.h.j._transaction(attempted))
        self.assertEqual(self.h.j.db.execute("SELECT count(*) FROM adaptive_feedback_origins").fetchone()[0], 0)
        self.assertIsNone(self.h.ledger.lookup(self.h.auth, "toy-1"))
        self.assertEqual(self.remember()["first_receipt"], self.receipt)

    def test_public_declaration_lookup_wire_and_retry_equal_default(self):
        plain = Host(self.fixture, False); self.addCleanup(plain.close)
        payload = self.h.preference()
        left = plain.j.dynamic.declaration(copy.deepcopy(payload), plain.owner, family=payload["schema"])
        right = self.h.j.dynamic.declaration(copy.deepcopy(payload), self.h.owner, family=payload["schema"])
        self.assertEqual(left, right)
        left_lookup = plain.j.dynamic.lookup_original(plain.owner, self.h.identity["site_id"], self.h.identity["room_id"], cursor=left["cursor"], consumer_id="owner")
        right_lookup = self.h.j.dynamic.lookup_original(self.h.owner, self.h.identity["site_id"], self.h.identity["room_id"], cursor=right["cursor"], consumer_id="owner")
        self.assertEqual(left_lookup, right_lookup)
        self.assertEqual(plain.ledger.lookup(plain.auth, payload["id"]), self.h.ledger.lookup(self.h.auth, payload["id"]))
        self.h.now = 1; plain.now = 1
        self.assertEqual(self.h.j.dynamic.declaration(copy.deepcopy(payload), self.h.owner, family=payload["schema"]), right)
        self.assertEqual(plain.j.dynamic.declaration(copy.deepcopy(payload), plain.owner, family=payload["schema"]), left)

    def test_public_partial_append_sql_failure_rolls_back_publication_receipt_state_and_origin(self):
        self.h.j.db.execute("CREATE TRIGGER synthetic_origin_failure BEFORE INSERT ON adaptive_feedback_origins "
                            "BEGIN SELECT RAISE(ABORT,'synthetic test failure'); END")
        payload = self.h.preference(); before = list(self.h.j.db.iterdump())
        self.assert_code("journal_unavailable", lambda: self.h.j.dynamic.declaration(payload, self.h.owner, family=payload["schema"]))
        self.assertEqual(list(self.h.j.db.iterdump()), before)

    def test_real_phase_composition_bootstrap_proof_checkpoint_and_origin_decoder(self):
        self.h.j.dynamic = create_phase_view_profile(self.h.j, phase_selection(), self.h.clock)
        commit = self.h.semantic_genesis()
        receipt = self.h.j.dynamic.worker_commit(commit, self.h.worker, request_canonical_json=canonical(commit))
        checkpoint = self.h.j.checkpoint("worker", self.h.worker, self.h.identity["site_id"], self.h.identity["room_id"])
        self.assertEqual(checkpoint["checkpoint"], commit["checkpoint"])
        cursor = next(r["cursor"] for r in receipt["receipts"] if r["id"] == "installed-zero")
        proof = self.h.j.dynamic.lookup_output_proof(self.h.owner, self.h.identity["site_id"], self.h.identity["room_id"], cursor=cursor, consumer_id="owner")
        self.assertEqual(proof["schema"], "home-context-worker-output-proof-4")
        self.assertEqual(proof["request_canonical_json"], canonical(commit)); self.assertEqual(proof["receipt"], receipt)
        self.assertEqual(self.h.j.dynamic.worker_commit(commit, self.h.worker), receipt)

    def test_fake_profile_config_grants_schema_limits_clock_or_origin_composition_refuse(self):
        mutations = [lambda j: setattr(j, "profile", "old"), lambda j: setattr(j, "grants", tuple(list(j.grants))),
                     lambda j: setattr(j, "limits", copy.deepcopy(j.limits)), lambda j: setattr(j, "clock", lambda: 0),
                     lambda j: setattr(j.dynamic, "origins", self.h.original), lambda j: j.adaptive_feedback_reference_config["identity"].update(room_id="other"),
                     lambda j: setattr(j.schemas, "interface_digest", "0" * 64)]
        for mutate in mutations:
            h = Host(self.fixture); self.addCleanup(h.close); mutate(h.j)
            with self.assertRaises(JournalError): h.ledger._encode_request_storage(canonical(self.request))

    def test_changed_expected_source_and_replaced_primitive_method_refuse_without_clock_sampling(self):
        self.remember()
        with patch.dict(storage._SOURCES, {"adaptive_feedback_reference_internal_storage.py": "0" * 64}):
            self.assert_code("origin_storage_source_changed", lambda: self.h.ledger._encode_request_storage(canonical(self.request)))
            self.assert_code("feedback_origin_corrupt", lambda: self.h.ledger.lookup(self.h.auth, "toy-1"))
        with patch.object(PrivateOriginLedger, "_encode_request_storage", lambda self, text: text):
            self.assert_code("origin_storage_source_changed", lambda: self.h.ledger._decode_request_storage(canonical(self.request), digest(self.request)))

    def test_transaction_and_origin_count_limit_still_apply(self):
        self.assert_code("feedback_origin_transaction_required", lambda: self.h.ledger.remember(self.h.auth, "toy-1", self.request, self.receipt, validate_receipt=lambda _: True))
        bounded = Host(self.fixture, False); self.addCleanup(bounded.close)
        bounded.original.maximum_origins = 1
        bounded.j.dynamic.origins = storage.create_origin_storage_ledger(bounded.j, storage.selection_configuration())
        bounded.ledger = bounded.j.dynamic.origins
        self.remember(host=bounded)
        self.assert_code("feedback_origin_budget", lambda: self.remember(host=bounded, event_id="toy-2"))

    def test_actual_sqlite_type_page_counts_and_toy_savings_are_measured_not_capacity(self):
        self.remember()
        row = self.h.j.db.execute("SELECT typeof(request_json),length(CAST(request_json AS BLOB)) FROM adaptive_feedback_origins").fetchone()
        decoded = len(canonical(self.request).encode()); pages = self.h.j.db.execute("PRAGMA page_count").fetchone()[0]; size = self.h.j.db.execute("PRAGMA page_size").fetchone()[0]
        self.assertEqual(row[0], "blob"); self.assertLess(row[1], decoded); self.assertGreater(pages * size, 0)
        print("TOY_ORIGIN_STORAGE " + json.dumps({"scope": "one fictional SQLite request only, not capacity closure", "decoded_bytes": decoded,
              "stored_bytes": row[1], "sqlite_type": row[0], "allocated_pages": pages, "page_size": size}, sort_keys=True))

    def test_actual_current_producer_revocation_fences_new_decl_but_keeps_exact_retry(self):
        payload = self.h.preference(); receipt = self.h.j.dynamic.declaration(payload, self.h.owner, family=payload["schema"])
        revoke = self.h.scoped("home-experience-producer-revocation-1")
        revoke.update(id="revoke-owner", target_producer_id="owner", target_producer_epoch="owner-1", revoked=True, actor_ref="synthetic-owner")
        rr = self.h.j.dynamic.producer_revoke(revoke, self.h.issuer)
        self.assertEqual(self.h.j.dynamic.declaration(payload, self.h.owner, family=payload["schema"]), receipt)
        candidate = copy.deepcopy(payload); candidate.update(id="new-pref", revision=1, expected_predecessor={"revision": 0, "digest": digest(payload)})
        self.assert_code("producer_revoked", lambda: self.h.j.dynamic.declaration(candidate, self.h.owner, family=candidate["schema"]))
        self.assertEqual(int(self.h.j._meta("cursor")), rr["cursor"])

    def test_fixed_diagnostic_class_composes_and_source_or_transaction_swap_refuses(self):
        h = Host(self.fixture, journal_class=diagnostic.StorageDiagnosticJournal); self.addCleanup(h.close)
        h.j.dynamic = create_phase_view_profile(h.j, phase_selection(), h.clock)
        first = self.remember(host=h)
        self.assertEqual(h.ledger.lookup(h.auth, "toy-1"), first)
        self.assertEqual(h.j.fixture_storage_diagnostics()["allocation_status"], "available")
        with patch.object(diagnostic, "SOURCE_DIGEST", "0" * 64):
            self.assert_code("origin_storage_source_changed", lambda: h.ledger._encode_request_storage(canonical(self.request)))
        h.j._transaction = lambda action: action()
        self.assert_code("origin_storage_binding_changed", lambda: h.ledger._encode_request_storage(canonical(self.request)))

    def test_arbitrary_journal_subclass_is_not_a_commissioned_diagnostic(self):
        class ForeignJournal(Journal): pass
        with self.assertRaises(ValueError):
            h = Host(self.fixture, journal_class=ForeignJournal)
            self.addCleanup(h.close)


if __name__ == "__main__": unittest.main()
