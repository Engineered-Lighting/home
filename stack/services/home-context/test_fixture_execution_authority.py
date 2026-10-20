"""Offline issuer controls. The tiny codec below is a unit-test double only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from journal import Journal
from fixture_execution_validation import (FixtureAuthorityDenied, FixtureAuthorityPolicy,
    FixtureExecutionAuthority, canonical_bytes, parse_canonical, CAP_FIELDS, GEN_FIELDS, PIN_NAMES)


class FictionalCodec:
    def encode_context(self, context):
        return canonical_bytes(context)

    def encode_plan(self, p):
        content = {k: v for k, v in p.items() if k not in {"grant_id", "grant_body_sha256"}}
        body = hashlib.sha256(canonical_bytes(content)).digest()*12
        return body[:369] + int(p["grant_id"]).to_bytes(8, "little") + bytes.fromhex(p["grant_body_sha256"])


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3]/"artifacts")
        self.addCleanup(self.directory.cleanup)
        self.journal = Journal(Path(self.directory.name)/"journal.sqlite")
        self.addCleanup(self.journal.close)
        self.records = []
        self.journal._recorder = self.records.append
        self.now = 1_100_000
        self.pins = {key: hashlib.sha256(key.encode()).hexdigest() for key in PIN_NAMES}
        self.context = {"pins": {key:self.pins[key] for key in PIN_NAMES[:6]},
                        "ownership_generation":"4", "principal_id":"3", "session_id":"2", "boot_generation":"1"}
        self.codec = FictionalCodec()
        self.context_digest = hashlib.sha256(self.codec.encode_context(self.context)).hexdigest()
        self.state = {**dict(zip(GEN_FIELDS, ("5", "6", "4"))),
            **{key:"4000000" for key in CAP_FIELDS}, "source_cut":"07"*32,
            "protection":"08"*32,"authority":"09"*32,"domain_generations":[0]*8,
            "evidence_observed_home_us":"950000","decision_ref":"0d"*32,"eligible":True}
        self.reservation = {"request_id":"task-1","grant_id":"1","revision_id":"1","mode":"autonomous-task",
                            "intent_sha":"0e"*32,"state":"reserved"}
        self.plan = {"kind":"MOVE", "manual":False,"domain_mask":128,"field_mask":[0]*7+[12],"preset_id":"0",
            "grant_id":"1", "grant_body_sha256":"0"*64,"revision_id":"1","predecessor_id":"0",
            "motion_id":"10","expected_state_generation":"0","context_digest":self.context_digest,
            "issued_home_us":"1000000","support_expiry_home_us":"3900000",
            **{key:deepcopy(self.state[key]) for key in ("source_cut","protection","authority","domain_generations",*CAP_FIELDS)}}
        self.policy = FixtureAuthorityPolicy("home-task", "7", "fixture-A", "00"*16,
            self.context, self.pins, "3", "fictional-lookup-only")
        self.authority = self.construct(create=True)

    def construct(self, **kwargs):
        return FixtureExecutionAuthority(self.journal, policy=self.policy, codec=self.codec,
            state_provider=lambda:deepcopy(self.state), reservation_reader=lambda *_:deepcopy(self.reservation),
            command_validator=lambda p,s:p.get("preset_id")=="0", clock=lambda:self.now, **kwargs)

    def admit(self, plan=None, reservation=None):
        return self.authority.admit("task-1", reservation or self.reservation, plan or self.plan)

    def test_original_grant_is_durable_in_same_journal_before_return(self):
        result = self.admit()
        self.assertEqual(self.journal.db.execute("SELECT COUNT(*) FROM fixture_execution_grants").fetchone()[0],1)
        self.assertEqual([x["kind"] for x in self.records], ["fixture_grant_prepare", "fixture_grant_commit"])
        self.assertEqual(result["grant_body"]["schema"], "home-fixture-execution-grant-1")
        self.assertEqual(result["grant_body_sha256"],hashlib.sha256(canonical_bytes(result["grant_body"])).hexdigest())
        self.assertEqual(result["wire_sha256"],hashlib.sha256(bytes.fromhex(result["wire_hex"])).hexdigest())
        self.assertEqual(result["grant_body"]["caps"], {key:self.state[key] for key in CAP_FIELDS})
        self.assertFalse(result["physical_permission"])

    def test_exact_retry_restart_retains_original_bytes_and_availability_even_after_expiry(self):
        result = self.admit(); self.now = 10_000_000
        self.authority = self.construct()
        self.assertEqual(self.admit(),result)
        self.assertEqual(len(self.records),2)
        self.plan["issued_home_us"] = "1000001"
        with self.assertRaisesRegex(FixtureAuthorityDenied,"changed immutable"):
            self.admit()

    def test_lookup_separate_credential_challenge_and_current_generations(self):
        result = self.admit(); nonce = b"x"*32
        lookup = self.authority.lookup("home-task",7,"fictional-lookup-only",1,nonce)
        self.assertEqual(lookup["request_nonce"],nonce)
        self.assertEqual(lookup["record"]["grant_body"],result["grant_body"])
        self.assertEqual(lookup["record"]["version"],1)
        for issuer,epoch,credential in (("other",7,"fictional-lookup-only"),("home-task",8,"fictional-lookup-only"),("home-task",7,"submit")):
            with self.assertRaises(FixtureAuthorityDenied):
                self.authority.lookup(issuer,epoch,credential,1,nonce)
        self.state["authority_generation"] = "7"
        with self.assertRaisesRegex(FixtureAuthorityDenied,"superseded"):
            self.authority.lookup("home-task",7,"fictional-lookup-only",1,nonce)

    def test_lookup_freshness_budget_accounts_for_independent_home_timestamp_error(self):
        self.authority.policy=FixtureAuthorityPolicy(**{**self.policy.__dict__,"clock_error_us":100_000})
        self.admit()
        row=self.authority.lookup("home-task",7,"fictional-lookup-only",1,b"x"*32)["record"]
        self.assertEqual(row["status_not_after_home_us"],str(self.now+250_000-100_000))

    def test_relevant_change_and_exclusive_expiry_refuse(self):
        result = self.admit()
        for field, value in (("source_generation","9"),("source_cut","ab"*32),("domain_generations",[0]*7+[1])):
            old = self.state[field]; self.state[field] = value
            with self.assertRaises(FixtureAuthorityDenied):
                self.authority.validate_admission(result["grant_body"],self.reservation,result["plan"])
            self.state[field] = old
        self.now = 3_900_000
        with self.assertRaises(FixtureAuthorityDenied):
            self.authority.validate_admission(result["grant_body"],self.reservation,result["plan"])

    def test_all_original_caps_are_exact_and_cannot_be_refreshed(self):
        for key in CAP_FIELDS:
            for value in ("0","4000001","3999999"):
                with self.subTest(key=key,value=value):
                    p=deepcopy(self.plan);p[key]=value
                    with self.assertRaises(FixtureAuthorityDenied):self.admit(p)
        self.assertFalse(self.records)

    def test_tightened_still_current_cap_cannot_authorize_longer_original_wire(self):
        result=self.admit()
        for key in CAP_FIELDS:
            self.state[key]="2000000"
            with self.assertRaisesRegex(FixtureAuthorityDenied,"caps changed"):
                self.authority.validate_admission(result["grant_body"],self.reservation,result["plan"])
            with self.assertRaisesRegex(FixtureAuthorityDenied,"caps changed"):
                self.authority.lookup("home-task",7,"fictional-lookup-only",1,b"x"*32)
            self.state[key]="4000000"

    def test_new_committed_decision_or_failed_target_revalidation_with_same_cut_refuse(self):
        result=self.admit();original=self.state["decision_ref"]
        for changed_ref,validator in (("ee"*32,lambda p,s:True),(original,lambda p,s:False)):
            self.state["decision_ref"]=changed_ref;self.authority.command_validator=validator
            with self.assertRaisesRegex(FixtureAuthorityDenied,"decision/target"):
                self.authority.validate_admission(result["grant_body"],self.reservation,result["plan"])
            with self.assertRaisesRegex(FixtureAuthorityDenied,"decision/target"):
                self.authority.lookup("home-task",7,"fictional-lookup-only",1,b"x"*32)

    def test_unconsumed_reservation_and_task_domain_are_required(self):
        for state in ("superseded","bound","terminal","dispatching"):
            self.reservation["state"]=state
            with self.assertRaises(FixtureAuthorityDenied):self.admit()
        self.reservation["state"]="reserved"
        for changes in ({"manual":True},{"domain_mask":1},{"field_mask":[1]+[0]*6+[12]}, {"preset_id":"1"}):
            p={**self.plan,**changes}
            with self.assertRaises(FixtureAuthorityDenied):self.admit(p)
        self.assertFalse(self.records)

    def test_independent_target_validation_cannot_be_replaced_by_supplied_digest(self):
        self.authority.command_validator=lambda p,s:False
        with self.assertRaisesRegex(FixtureAuthorityDenied,"committed-target"):
            self.admit()
        self.assertFalse(self.records)

    def test_failure_winning_the_journal_lock_cannot_be_cleared_by_inflight_admission(self):
        entered=threading.Event();resume=threading.Event();errors=[]
        original=self.codec.encode_plan
        def paused(plan):
            entered.set()
            self.assertTrue(resume.wait(2))
            return original(plan)
        self.codec.encode_plan=paused
        def attempt():
            try:self.admit()
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=attempt);thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.journal.lock:
                self.authority.failed=True;self.journal._recorder_failed=True
                self.journal.db.execute("UPDATE fixture_execution_meta SET blocked=1 WHERE id=1")
        finally:
            resume.set();thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors),1)
        self.assertIsInstance(errors[0],FixtureAuthorityDenied)
        self.assertEqual(self.journal.db.execute("SELECT COUNT(*) FROM fixture_execution_grants").fetchone()[0],0)
        self.assertEqual(self.journal.db.execute("SELECT blocked FROM fixture_execution_meta").fetchone()[0],1)

    def test_recorder_failure_prevents_dispatch_and_latches_session(self):
        def broken(_):raise OSError("fictional disk failure")
        self.journal._recorder=broken
        with self.assertRaises(OSError):self.admit()
        self.assertTrue(self.journal._recorder_failed)
        self.assertEqual(self.journal.db.execute("SELECT COUNT(*) FROM fixture_execution_grants").fetchone()[0],0)
        with self.assertRaises(FixtureAuthorityDenied):self.admit()

    def test_lost_commit_ack_remains_original_durable_but_unconfirmed(self):
        def broken(record):
            self.records.append(record)
            if record["kind"]=="fixture_grant_commit":raise OSError("fictional lost durable archive ACK")
        self.journal._recorder=broken
        with self.assertRaises(OSError):self.admit()
        self.assertEqual(self.journal.db.execute("SELECT COUNT(*) FROM fixture_execution_grants").fetchone()[0],1)
        with self.assertRaises(FixtureAuthorityDenied):self.admit()
        self.journal._recorder_failed=False;self.journal._recorder=self.records.append
        self.authority=self.construct()
        with self.assertRaisesRegex(FixtureAuthorityDenied,"unconfirmed"):self.admit()

    def test_missing_ready_and_unconfigured_authority_deny(self):
        self.journal._recorder=None
        with self.assertRaisesRegex(FixtureAuthorityDenied,"recorder READY"):self.admit()
        with self.assertRaises(FixtureAuthorityDenied):FixtureExecutionAuthority().admit("x",{}, {})

    def test_bound_capacity_no_pruning_and_changed_epoch_no_migration(self):
        self.admit()
        self.authority.policy=FixtureAuthorityPolicy(**{**self.policy.__dict__,"capacity":1})
        self.reservation.update(request_id="task-2",grant_id="2",revision_id="2")
        self.plan.update(grant_id="2",revision_id="2")
        with self.assertRaisesRegex(FixtureAuthorityDenied,"capacity"):
            self.authority.admit("task-2",self.reservation,self.plan)
        self.policy=FixtureAuthorityPolicy(**{**self.policy.__dict__,"issuer_epoch":"8"})
        with self.assertRaisesRegex(FixtureAuthorityDenied,"epoch mismatch"):self.construct()

    def test_attributed_revocation_refuses_lookup_but_preserves_original(self):
        result=self.admit();self.authority.revoke("1",reason="operator-withdrawal")
        self.assertEqual(self.admit(),result)
        with self.assertRaisesRegex(FixtureAuthorityDenied,"revoked"):
            self.authority.lookup("home-task",7,"fictional-lookup-only",1,b"x"*32)

    def test_clock_rollback_and_revocation_archive_loss_remain_fenced_after_restart(self):
        self.admit();self.now=1_200_000
        self.authority.lookup("home-task",7,"fictional-lookup-only",1,b"x"*32)
        self.now=1_100_000
        with self.assertRaisesRegex(FixtureAuthorityDenied,"rollback"):
            self.authority.lookup("home-task",7,"fictional-lookup-only",1,b"x"*32)
        self.now=1_300_000;self.authority=self.construct()
        with self.assertRaisesRegex(FixtureAuthorityDenied,"unconfirmed"):
            self.authority.lookup("home-task",7,"fictional-lookup-only",1,b"x"*32)

    def test_failed_revocation_archive_cannot_reauthorize_original_on_reconstruction(self):
        self.admit()
        def broken(_):raise OSError("fictional revocation recorder loss")
        self.journal._recorder=broken
        with self.assertRaises(OSError):self.authority.revoke("1",reason="operator-withdrawal")
        self.journal._recorder_failed=False;self.journal._recorder=self.records.append
        self.authority=self.construct()
        with self.assertRaisesRegex(FixtureAuthorityDenied,"unconfirmed"):
            self.authority.lookup("home-task",7,"fictional-lookup-only",1,b"x"*32)

    def test_canonical_duplicate_float_unicode_coercion_and_whitespace_refuse(self):
        for raw in (b'{"a":1,"a":2}',b'{ "a":1}',b'{"a":NaN}',b'{"a":1.0}',b'{"a":-1}'):
            with self.assertRaises(FixtureAuthorityDenied):parse_canonical(raw)
        for value in ({"a":float("nan")},{"a":1.5},{"a":"\u2603"},{"a":2**64}):
            with self.assertRaises(FixtureAuthorityDenied):canonical_bytes(value)


if __name__=="__main__":unittest.main()
