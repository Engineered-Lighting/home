"""Private E origin helper checks; no public profile or synthetic scenario."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_feedback_validation import (PrivateOriginLedger, authenticated_origin,
                                          complete_request_digest, snapshot_json,
                                          validate_producer_epochs, validate_checkpoint_frontier)
from journal import JournalError


class AdaptiveFeedbackOriginPreparationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.ledger = PrivateOriginLedger(self.connection)
        self.auth = {"principal_id": "fictional-owner", "producer_id": "feedback-owner",
                     "producer_epoch": "owner-epoch-1"}
        self.request = {"id": "report-1", "value": None, "body": {"basis": "original", "at": 4.2}}
        self.receipt = {"cursor": 1, "admitted_at": 4.2, "result": {"status": "first"}}

    def error(self, code, function):
        with self.assertRaises(JournalError) as error:
            function()
        self.assertEqual(error.exception.code, code)

    @staticmethod
    def internal_receipt(value):
        return type(value) is dict and set(value) == {"cursor", "admitted_at", "result"}

    def remember(self, **overrides):
        args = {"authentication": self.auth, "event_id": "report-1", "request": self.request,
                "first_receipt": self.receipt, "validate_receipt": self.internal_receipt, **overrides}
        return self.ledger.remember(**args)

    def test_origin_is_authenticated_epoch_not_journal_or_payload_epoch(self):
        origin = authenticated_origin(self.auth, "report-1")
        self.assertEqual(origin, {"producer_id": "feedback-owner", "producer_epoch": "owner-epoch-1", "event_id": "report-1"})
        self.error("feedback_authenticated_origin", lambda: authenticated_origin({**self.auth, "journal_epoch": "journal-1"}, "report-1"))
        self.error("feedback_authenticated_origin", lambda: authenticated_origin({"principal_id": "owner", "producer_id": "producer"}, "report-1"))

    def test_exact_retry_returns_first_result_before_mutable_validator(self):
        self.connection.execute("BEGIN IMMEDIATE")
        first = self.remember(); self.connection.commit()
        def mutable(_):
            raise AssertionError("retry must not invoke new receipt/CAS/revocation logic")
        retry = self.remember(first_receipt={"new_clock": 100}, validate_receipt=mutable)
        self.assertEqual(first, retry)

    def test_complete_changed_request_conflicts_under_same_origin(self):
        self.connection.execute("BEGIN IMMEDIATE"); self.remember(); self.connection.commit()
        changed = copy.deepcopy(self.request); changed["body"]["basis"] = "different-original"
        self.error("feedback_origin_conflict", lambda: self.ledger.retry(self.auth, "report-1", changed))
        self.assertEqual(self.ledger.lookup(self.auth, "report-1")["request"], self.request)

    def test_authorized_new_epoch_is_distinct_but_foreign_principal_is_denied(self):
        self.connection.execute("BEGIN IMMEDIATE"); first = self.remember(); self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        new_auth = {**self.auth, "producer_epoch": "owner-epoch-2"}
        second = self.remember(authentication=new_auth); self.connection.commit()
        self.assertNotEqual(first["origin"], second["origin"])
        self.error("feedback_origin_owner", lambda: self.ledger.lookup({**self.auth, "principal_id": "foreign"}, "report-1"))

    def test_absent_or_failed_lookup_is_not_a_result(self):
        self.assertIsNone(self.ledger.lookup(self.auth, "missing"))
        self.assertIsNone(self.ledger.retry(self.auth, "missing", self.request))
        self.error("feedback_authenticated_origin", lambda: self.ledger.lookup({**self.auth, "verified": True}, "missing"))

    def test_sqlite_first_result_survives_close_reopen(self):
        with tempfile.TemporaryDirectory(prefix="home-feedback-origin-", dir=Path(__file__).resolve().parent) as tmp:
            path = Path(tmp) / "fresh.sqlite"
            connection = sqlite3.connect(path)
            ledger = PrivateOriginLedger(connection)
            connection.execute("BEGIN IMMEDIATE")
            first = ledger.remember(self.auth, "report-1", self.request, self.receipt, validate_receipt=self.internal_receipt)
            connection.commit(); connection.close()
            connection = sqlite3.connect(path)
            try:
                restored = PrivateOriginLedger(connection)
                self.assertEqual(restored.retry(self.auth, "report-1", self.request), first)
            finally:
                connection.close()

    def test_caller_transaction_rolls_back_origin_and_first_receipt(self):
        self.connection.execute("BEGIN IMMEDIATE"); self.remember(); self.connection.rollback()
        self.assertIsNone(self.ledger.lookup(self.auth, "report-1"))
        self.error("feedback_origin_transaction_required", self.remember)

    def test_origin_budget_refuses_without_partial_insert(self):
        self.ledger = PrivateOriginLedger(self.connection, maximum_origins=1)
        self.connection.execute("BEGIN IMMEDIATE"); self.remember(); self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        self.error("feedback_origin_budget", lambda: self.remember(event_id="report-2"))
        self.connection.rollback()
        self.assertIsNone(self.ledger.lookup(self.auth, "report-2"))
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM adaptive_feedback_origins").fetchone()[0], 1)

    def test_receipt_validator_is_required_and_cannot_rewrite_original(self):
        self.connection.execute("BEGIN IMMEDIATE")
        self.error("feedback_receipt_validator_required", lambda: self.remember(validate_receipt=None))
        def mutate(value):
            value["admitted_at"] = 99
            return True
        self.error("feedback_receipt_validation", lambda: self.remember(validate_receipt=mutate))
        self.assertEqual(self.receipt["admitted_at"], 4.2)
        self.assertIsNone(self.ledger.lookup(self.auth, "report-1"))

    def test_inputs_and_returned_lookup_cannot_mutate_stored_original(self):
        self.connection.execute("BEGIN IMMEDIATE"); first = self.remember(); self.connection.commit()
        self.request["body"]["at"] = 99; self.receipt["result"]["status"] = "changed"
        first["first_receipt"]["result"]["status"] = "changed-return"
        actual = self.ledger.lookup(self.auth, "report-1")
        self.assertEqual(actual["request"]["body"]["at"], 4.2)
        self.assertEqual(actual["first_receipt"]["result"]["status"], "first")

    def test_inert_json_rejects_cycles_callbacks_invalid_numbers_and_keys(self):
        cycle = []; cycle.append(cycle)
        for value, code in ((cycle, "feedback_json_type"), ({"x": lambda: 1}, "feedback_json_type"),
                            ({1: "x"}, "feedback_json_key"), ({"x": float("nan")}, "feedback_json_number"),
                            ({"x": 9007199254740992}, "feedback_json_number"), ({"x": "\ud800"}, "feedback_json_unicode")):
            self.error(code, lambda v=value: snapshot_json(v))
        class Hostile(dict):
            def items(self):
                raise AssertionError("must not execute inherited callback")
        self.error("feedback_json_type", lambda: snapshot_json(Hostile()))

    def test_full_request_digest_is_canonical_not_normalized_or_literal_spacing(self):
        first = {"a": 1, "b": {"raw": "kept"}}
        self.assertEqual(complete_request_digest(first), complete_request_digest({"b": {"raw": "kept"}, "a": 1.0}))
        self.assertNotEqual(complete_request_digest(first), complete_request_digest({"a": 1}))

    def test_corrupt_stored_request_or_first_receipt_fails_closed(self):
        self.connection.execute("BEGIN IMMEDIATE"); self.remember(); self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute("UPDATE adaptive_feedback_origins SET request_json='{}'")
        self.error("feedback_origin_corrupt", lambda: self.ledger.lookup(self.auth, "report-1"))
        self.connection.rollback()
        self.connection.execute("BEGIN IMMEDIATE")
        self.connection.execute("UPDATE adaptive_feedback_origins SET first_receipt_json='{}'")
        self.error("feedback_origin_corrupt", lambda: self.ledger.lookup(self.auth, "report-1"))
        self.connection.rollback()
        self.assertEqual(self.ledger.lookup(self.auth, "report-1")["first_receipt"], self.receipt)

    def test_configured_epoch_pairs_are_unique_historical_and_isolated(self):
        old = copy.deepcopy(self.auth); new = {**self.auth, "producer_epoch": "owner-epoch-2"}
        rows = validate_producer_epochs([new, old]); new["producer_epoch"] = "mutated"
        self.assertEqual(rows, [old, {**old, "producer_epoch": "owner-epoch-2"}])
        self.error("feedback_producer_configuration", lambda: validate_producer_epochs([old, {**old, "principal_id": "foreign"}]))
        self.error("feedback_producer_configuration", lambda: validate_producer_epochs([]))

    def test_json_depth_nodes_and_bytes_are_finite(self):
        deep = None
        for _ in range(18): deep = [deep]
        for value in (deep, [None] * 20001):
            self.error("feedback_json_bound", lambda v=value: snapshot_json(v))
        self.error("feedback_json_bound", lambda: snapshot_json({"x": "12345"}, maximum_bytes=5))
        self.error("feedback_json_configuration", lambda: snapshot_json(None, maximum_bytes=True))


class FeedbackPhaseFrontierTests(unittest.TestCase):
    """Schedule-only fixtures; no fictional originals or journal admission."""

    def checkpoint(self, *, phase='ready', t=0, semantic=0, control=0, duration=120):
        installed=round(control*.2,9)
        return {'boundary_phase':phase,'runtime_duration_s':duration,'last_t':t,'last_installed_t':installed,
                'semantic_seq':semantic,'control_seq':control,'commit_seq':control+semantic+2,
                'reducer':{'last_t':semantic},'pipeline':{'t':installed,'policy':{'semantic_seq':semantic}},
                'pending_installed':{'control_seq':control},'next_semantic_t':min(duration,semantic+1),
                'next_control_t':min(duration,round((control+1)*.2,9))}

    def test_phase_frontiers_cover_each_integer_and_final_without_combined_counts(self):
        for duration in (1,2,120):
            validate_checkpoint_frontier(self.checkpoint(duration=duration))
            for n in range(1,duration):
                pending=self.checkpoint(phase='control_pending',t=n,semantic=n,control=5*n-1,duration=duration)
                validate_checkpoint_frontier(pending)
                self.assertEqual(pending['commit_seq'],6*n+1)
                ready=self.checkpoint(t=n,semantic=n,control=5*n,duration=duration)
                validate_checkpoint_frontier(ready)
                self.assertEqual(ready['commit_seq'],6*n+2)
            final=self.checkpoint(t=duration,semantic=duration-1,control=5*duration,duration=duration)
            validate_checkpoint_frontier(final)
            self.assertEqual(final['commit_seq'],6*duration+1)

    def test_phase_frontier_rejects_partial_mechanics_skipped_phase_and_duration(self):
        pending=self.checkpoint(phase='control_pending',t=1,semantic=1,control=4)
        for change in ({'last_installed_t':1},{'control_seq':5},{'next_control_t':1.2},
                       {'semantic_seq':0},{'commit_seq':6},{'last_t':1.2},
                       {'boundary_phase':'ready'},{'runtime_duration_s':1},
                       {'runtime_duration_s':True},{'runtime_duration_s':121}):
            with self.subTest(change=change),self.assertRaises(JournalError):
                validate_checkpoint_frontier({**copy.deepcopy(pending),**change})
        final=self.checkpoint(t=120,semantic=119,control=600)
        bad=copy.deepcopy(final);bad['semantic_seq']=120;bad['pipeline']['policy']['semantic_seq']=120;bad['reducer']['last_t']=120;bad['commit_seq']=722
        with self.assertRaises(JournalError):validate_checkpoint_frontier(bad)


class FeedbackPinnedRegistryTests(unittest.TestCase):
    def test_selected_request_lease_descriptors_are_metadata_not_full_payloads(self):
        from adaptive_feedback_validation import validate_feedback
        from schema_registry import SchemaRegistry,FEEDBACK_PROFILE,FEEDBACK_SCHEMAS
        from journal import digest
        registry=SchemaRegistry((),profile=FEEDBACK_PROFILE)
        base=FEEDBACK_SCHEMAS.parent.parent/'home-adaptive-experience'/'examples'
        request=json.loads((base/'home-lighting-request-4.json').read_text())
        staged=json.loads((FEEDBACK_SCHEMAS.parent/'examples/home-lighting-exposure-staged-installation-2.json').read_text())
        for field,selector,body in (('request_ref','policy_request',request),('lease_ref','policy_request_lease',request['lease'])):
            staged[field]['component']={'selector':selector,'id':body['id'],'schema':body['schema'],'version':body['version'],'digest':digest(body)}
        self.assertTrue(validate_feedback(staged,registry)['ok'])
        self.assertTrue(validate_feedback(request,registry)['ok'])
        # Descriptors are closed reference metadata, not standalone payloads.
        # A full malformed request or lease still enters its own validation.
        for body in (request,request['lease']):
            broken=copy.deepcopy(body);broken.pop('valid_until')
            self.assertFalse(validate_feedback(broken,registry)['ok'])
        mismatched=copy.deepcopy(request);mismatched['lease']['person_ref']['pid']+=1
        self.assertIn('lease_subject',validate_feedback(mismatched,registry)['errors'])
        for field in ('request_ref','lease_ref'):
            for key,value in (('selector','unsupported_selector'),('version','2'),('digest','not-a-digest')):
                changed=copy.deepcopy(staged);changed[field]['component'][key]=value
                self.assertFalse(validate_feedback(changed,registry)['ok'],(field,key))
            for key in staged[field]['component']:
                changed=copy.deepcopy(staged);changed[field]['component'].pop(key)
                self.assertFalse(validate_feedback(changed,registry)['ok'],(field,key))
            changed=copy.deepcopy(staged);changed[field]['component']['lease']=None
            self.assertFalse(validate_feedback(changed,registry)['ok'])

    def test_one_pinned_shared_registry_per_e_registry_no_payload_result_cache(self):
        from adaptive_feedback_validation import validate_feedback
        from schema_registry import SchemaRegistry,FEEDBACK_PROFILE,FEEDBACK_SCHEMAS
        original=SchemaRegistry.__init__;profiles=[]
        def counted(registry,grants,**kwargs):
            profiles.append(kwargs.get('profile','legacy'));return original(registry,grants,**kwargs)
        value=json.loads((FEEDBACK_SCHEMAS.parent/'examples/home-geometry-4.json').read_text())
        with patch.object(SchemaRegistry,'__init__',counted):
            registry=SchemaRegistry((),profile=FEEDBACK_PROFILE)
            self.assertEqual(profiles,[FEEDBACK_PROFILE,'adaptive-book-synthetic-1'])
            self.assertTrue(validate_feedback(value,registry)['ok'])
            changed=copy.deepcopy(value);changed['unexpected']=True
            self.assertFalse(validate_feedback(changed,registry)['ok'])
            self.assertTrue(validate_feedback(value,registry)['ok'])
            self.assertEqual(profiles,[FEEDBACK_PROFILE,'adaptive-book-synthetic-1'])
            cold=SchemaRegistry((),profile=FEEDBACK_PROFILE)
            self.assertEqual(profiles,[FEEDBACK_PROFILE,'adaptive-book-synthetic-1']*2)
            self.assertIsNot(cold._feedback_shared_adaptive_registry,registry._feedback_shared_adaptive_registry)
            self.assertEqual(validate_feedback(value,cold),validate_feedback(value,registry))

    def test_shared_schema_registry_and_nested_nodes_are_immutable_and_isolated(self):
        from adaptive_feedback_validation import validate_feedback
        from schema_registry import SchemaRegistry,FEEDBACK_PROFILE,FEEDBACK_SCHEMAS
        registry=SchemaRegistry((),profile=FEEDBACK_PROFILE);shared=registry._feedback_shared_adaptive_registry
        schema,path=shared.schemas['home-fact-2'];required=schema['required']
        for mutation in (lambda:shared.schemas.__setitem__('home-fact-2',None),
                         lambda:shared.documents.__setitem__(path,{}),
                         lambda:schema.update({'additionalProperties':True}),
                         lambda:schema['properties'].clear(),lambda:required.append('injected'),
                         lambda:setattr(shared,'interface_digest','0'*64),
                         lambda:setattr(shared,'schemas',{}),
                         lambda:delattr(shared,'_sealed'),lambda:delattr(shared,'schemas')):
            with self.assertRaises((TypeError,AttributeError)):mutation()
        value=json.loads((FEEDBACK_SCHEMAS.parent/'examples/home-fact-batch-3.json').read_text())
        self.assertTrue(validate_feedback(value,registry)['ok'])
        # The parent E graph remains a separate load; changing a caller's
        # exposed E node cannot change the private immutable D graph.
        parent,_=registry.schemas['home-fact-2'];old=parent['properties']['kind']['enum'][:]
        parent['properties']['kind']['enum'].append('not-a-fact')
        try:self.assertEqual(schema['properties']['kind']['enum'],old)
        finally:parent['properties']['kind']['enum'][:]=old
        self.assertTrue(validate_feedback(value,registry)['ok'])

    def test_base_pin_is_rechecked_for_new_registry_and_all_examples_cold_warm_match(self):
        from adaptive_feedback_validation import validate_feedback
        from schema_registry import SchemaRegistry,FEEDBACK_PROFILE,FEEDBACK_SCHEMAS,_PinnedAdaptiveRegistry
        with self.assertRaises(ValueError):_PinnedAdaptiveRegistry('0'*64)
        warm=SchemaRegistry((),profile=FEEDBACK_PROFILE);cold=SchemaRegistry((),profile=FEEDBACK_PROFILE)
        paths=[p for p in (FEEDBACK_SCHEMAS.parent/'examples').glob('*.json') if p.stem in warm.schemas]
        for path in paths:
            value=json.loads(path.read_text());first=validate_feedback(value,warm)
            self.assertTrue(first['ok'],path.name);self.assertEqual(first,validate_feedback(value,cold));self.assertEqual(first,validate_feedback(value,warm))


if __name__ == "__main__":
    unittest.main()
