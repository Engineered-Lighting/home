"""Actual same-journal report commits; all channel and clock I/O is fictional."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"tools/den-live-runtime"))
from fixture_reports import retain_report, validate_native_event, canonical_bytes as report_bytes
from fixture_execution_report_journal import FixtureReportJournal, BASE_FIELDS
from fixture_execution_validation import FixtureAuthorityDenied, record_bytes
from fixture_authority_archive import FixtureAuthorityRecorder, replay_fixture_authority
import test_fixture_execution_authority as authority_cases


class ReportJournalTests(unittest.TestCase):
    def setUp(self):
        self.base=authority_cases.AuthorityTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.base.plan["clock_mapping_revision"]=1
        self.admitted=self.base.admit();p=self.admitted["plan"];g=self.admitted["grant_body"]
        self.scope={"fixture_id":g["fixture_id"],"device_id":g["device_id"],"boot_generation":"1","session_id":"2",
                    "context_digest":g["context_digest"],"pins":g["pins"],"domain_generations":p["domain_generations"],
                    **{k:g[k] for k in ("source_generation","authority_generation","ownership_generation")}}
        self.native={"schema":"home-fixture-execution-report-1","version":1,"event":"phase_changed","result":"ok","phase":"lit",
            "reported_device_us":"1110000","sampled_device_us":None,"received_device_us":None,
            "revision_id":p["revision_id"],"revision_sha256":self.admitted["wire_sha256"],"session_context_sha256":p["context_digest"],
            "motion_id":p["motion_id"],"state_generation":"1","effect_generation":"0","axis":255,"value":0,
            "frame":[0]*21+[4096,0,0],"evidence_source":"simulated","physical_pass":False}
        self.report=retain_report("native-1",report_bytes(self.native),scope=self.scope,first_received_home_us="1120000",admitted=self.admitted)
        self.base.now=1_130_000
        self.current={"eligible":True,"not_after_home_us":"3900000","clock_qualification_not_after_home_us":"4000000"}
        self.mapping={"revision_id":1,"device_anchor_us":"1000000","home_anchor_us":"1000000","uncertainty_us":"0",
                      "drift_ppm":0,"valid_until_home_us":"4000000"}
        self.allowed=True
        self.store=self.construct(create=True)

    def construct(self,**kwargs):
        return FixtureReportJournal(self.base.journal,{k:deepcopy(self.scope[k]) for k in BASE_FIELDS},
            native_validator=validate_native_event,channel_verifier=lambda r,a:self.allowed,
            current_authority=lambda *_:deepcopy(self.current),clock_mapping=lambda:deepcopy(self.mapping),
            clock_home_us=lambda:self.base.now,credential_sha256=hashlib.sha256(b"ingress-only").hexdigest(),**kwargs)

    def commit(self,report=None,credential="ingress-only"):
        return self.store.commit_report(report or self.report,self.admitted,credential)

    def test_same_database_commits_original_and_mapped_lease_before_return(self):
        oldcursor=self.base.journal._meta("cursor")
        result=self.commit()
        self.assertEqual(result["commit"]["cursor"],1)
        self.assertEqual(result["commit"]["stream_epoch"],"fixture-report:"+self.base.journal._meta("epoch"))
        self.assertEqual(self.base.journal._meta("cursor"),oldcursor,"observer stream never corrupts worker cursors")
        self.assertEqual(result["lease"]["sampled_home_us"],"1110000")
        self.assertEqual(result["lease"]["valid_until_home_us"],"3900000")
        self.assertEqual(self.base.records[-1]["kind"],"fixture_report_commit")
        self.assertEqual(self.base.journal.db.execute("SELECT COUNT(*) FROM fixture_observer_reports").fetchone()[0],1)

    def test_exact_retry_and_reopen_do_not_refresh_observation_or_commit(self):
        result=self.commit();self.base.now=1_200_000;self.store=self.construct()
        self.assertEqual(self.commit(),result)
        view=self.store.inspection();self.assertEqual(view["commit"],result["commit"])
        self.assertEqual(view["report"],result["report"])
        self.assertEqual(view["lease"],result["lease"])
        bad=deepcopy(self.report);bad["first_received_home_us"]="1130000"
        with self.assertRaisesRegex(FixtureAuthorityDenied,"immutable"):self.commit(bad)

    def test_scope_wrong_original_category_sample_and_duplicate_keys_refuse(self):
        for change in (lambda r:r["scope"].update(session_id="3"),lambda r:r.update(category="axis_state"),
                       lambda r:r.update(sampled_device_us="999"),lambda r:r.update(original_sha256="0"*64)):
            bad=deepcopy(self.report);change(bad)
            with self.assertRaises(ValueError):self.commit(bad)
        bad=deepcopy(self.report)
        raw=bytes.fromhex(bad["original_hex"]).replace(b'{',b'{"version":1,',1)
        bad.update(original_hex=raw.hex(),original_sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(FixtureAuthorityDenied,"duplicate"):self.commit(bad)

    def test_fixed_ingress_and_original_channel_are_separate_from_browser_read(self):
        with self.assertRaisesRegex(FixtureAuthorityDenied,"credential"):self.commit(credential="browser")
        self.allowed=False
        with self.assertRaisesRegex(FixtureAuthorityDenied,"channel"):self.commit()
        self.assertEqual(self.base.journal.db.execute("SELECT COUNT(*) FROM fixture_observer_reports").fetchone()[0],0)

    def test_authority_withdrawal_expiry_and_mapping_loss_strip_currentness(self):
        result=self.commit()
        for field,value in (("eligible",False),("not_after_home_us","1130000")):
            before=deepcopy(self.current);self.current[field]=value
            view=self.store.inspection();self.assertIsNone(view["lease"])
            self.assertEqual(view["commit"],result["commit"])
            self.current=before
        self.base.now=3_900_000
        self.assertIsNone(self.store.inspection()["lease"])

    def test_clock_mapping_never_replaces_sample_by_fresh_receipt(self):
        self.mapping["home_anchor_us"]="2000000"
        result=self.commit();self.assertIsNone(result["lease"])
        self.mapping["home_anchor_us"]="1000000"
        self.assertIsNone(self.store.inspection()["lease"],"historical receipt cannot become current after remapping")

    def test_revocation_and_clock_rollback_remain_fenced(self):
        self.commit();self.base.authority.revoke("1",reason="operator-withdrawal")
        self.assertIsNone(self.store.inspection()["lease"])
        self.base.now=1_120_000
        with self.assertRaisesRegex(FixtureAuthorityDenied,"rollback"):self.store.inspection()
        self.base.now=1_200_000;self.store=self.construct()
        with self.assertRaisesRegex(FixtureAuthorityDenied,"unconfirmed"):self.store.inspection()

    def test_report_archive_ack_loss_cannot_become_confirmed_after_reconstruction(self):
        def broken(record):
            if record["kind"]=="fixture_report_commit":raise OSError("fictional lost report archive ACK")
            self.base.records.append(record)
        self.base.journal._recorder=broken
        with self.assertRaises(OSError):self.commit()
        self.assertEqual(self.base.journal.db.execute("SELECT COUNT(*) FROM fixture_observer_reports").fetchone()[0],1)
        self.base.journal._recorder_failed=False;self.base.journal._recorder=self.base.records.append
        self.store=self.construct()
        with self.assertRaisesRegex(FixtureAuthorityDenied,"unconfirmed"):self.store.inspection()

    def test_authority_interruption_is_durable_across_reopen_and_restoration(self):
        result=self.commit();self.current["eligible"]=False
        self.assertIsNone(self.store.inspection()["lease"])
        self.current["eligible"]=True;self.store=self.construct()
        view=self.store.inspection()
        self.assertIsNone(view["lease"],"restored authority requires a new original report")
        self.assertEqual(view["report"],result["report"])
        self.assertEqual(view["commit"],result["commit"])

    def test_shortened_lease_cannot_extend_when_caps_restore_after_reopen(self):
        self.commit();self.current["not_after_home_us"]="2000000"
        self.assertEqual(self.store.inspection()["lease"]["valid_until_home_us"],"2000000")
        self.current["not_after_home_us"]="3900000";self.store=self.construct()
        self.assertEqual(self.store.inspection()["lease"]["valid_until_home_us"],"2000000")

    def authority_replay(self,records=None,name="observer-authority.jsonl"):
        path=Path(self.base.directory.name)/name
        pins={"source":"e"*64};parent="f"*64
        recorder=FixtureAuthorityRecorder(path,parent_manifest_sha256=parent,source_pins=pins,
            max_records=128,max_bytes=1048576,min_free_bytes=1)
        for row in (self.base.records if records is None else records):recorder.record(row)
        recorder.drain({"status":"complete"})
        return replay_fixture_authority(path,parent_manifest_sha256=parent,source_pins=pins,
            encode_plan=self.base.codec.encode_plan,encode_context=self.base.codec.encode_context,context=self.base.context)

    def test_recorded_lease_shortening_and_tombstone_replay_exactly(self):
        self.commit();self.current["not_after_home_us"]="2000000";self.store.inspection()
        before=len(self.base.records);self.store.inspection()
        self.assertEqual(len(self.base.records),before,"unchanged polls do not consume archive capacity")
        self.current["eligible"]=False;self.store.inspection()
        replay=self.authority_replay()
        self.assertEqual(replay["status"],"causal_archive_verified",replay)
        self.assertEqual(replay["lease_transitions"],2)
        self.assertIsNone(replay["final_report_leases"][1]["lease"])
        records=deepcopy(self.base.records)
        records=[x for x in records if x["kind"]!="fixture_report_lease_commit"]
        self.assertEqual(self.authority_replay(records,"missing-lease-commit.jsonl")["status"],"incomplete")

    def test_lease_archive_ack_loss_remains_unconfirmed_after_reconstruction(self):
        self.commit()
        def broken(record):
            if record["kind"]=="fixture_report_lease_commit":raise OSError("fictional lost lease archive ACK")
            self.base.records.append(record)
        self.base.journal._recorder=broken;self.current["eligible"]=False
        with self.assertRaises(OSError):self.store.inspection()
        self.base.journal._recorder_failed=False;self.base.journal._recorder=self.base.records.append
        self.store=self.construct()
        with self.assertRaisesRegex(FixtureAuthorityDenied,"unconfirmed"):self.store.inspection()

    def test_authority_replay_refuses_missing_original_or_extended_transition(self):
        self.commit();self.current["not_after_home_us"]="2000000";self.store.inspection()
        no_original=[r for r in self.base.records if not r["kind"].startswith("fixture_grant_")]
        self.assertEqual(self.authority_replay(no_original,"missing-original.jsonl")["status"],"incomplete")
        records=deepcopy(self.base.records)
        change=next(r for r in records if r["kind"]=="fixture_report_lease_prepare")
        change["data"]["next_lease"]["valid_until_home_us"]="4000000"
        result=self.authority_replay(records,"extended-lease.jsonl")
        self.assertEqual(result["status"],"incomplete");self.assertIn("extended",result["reason"])

    def test_authority_replay_refuses_forged_initial_lease_plan_or_clock_mapping(self):
        self.commit()
        for index,mutation in enumerate((
            lambda rows:next(r for r in rows if r["kind"]=="fixture_report_prepare")["data"]["inspection"]["lease"].update(valid_until_home_us=str(2**64-1)),
            lambda rows:next(r for r in rows if r["kind"]=="fixture_grant_prepare")["data"]["receipt"]["plan"].update(source_cut="ff"*32),
            lambda rows:next(r for r in rows if r["kind"]=="fixture_report_prepare")["data"]["clock_mapping"].update(home_anchor_us="1100000"))):
            records=deepcopy(self.base.records);mutation(records)
            self.assertEqual(self.authority_replay(records,"forged-"+str(index)+".jsonl")["status"],"incomplete")

    def test_authority_replay_never_exposes_recorded_lease_as_current_permission(self):
        self.commit();self.base.authority.revoke("1",reason="fictional-withdrawal")
        replay=self.authority_replay()
        self.assertEqual(replay["status"],"causal_archive_verified",replay)
        self.assertFalse(replay["current"])
        self.assertTrue(replay["final_report_leases"][1]["grant_revoked"])
        self.assertFalse(replay["final_report_leases"][1]["current"])
        self.assertEqual(replay["lease_authority"],"historical_recorded_ceilings_only")

    def test_authority_replay_refuses_self_consistent_grants_for_another_context(self):
        # These archives have correct recomputed G/W hashes and transaction
        # bodies. Refusal must come from the separately pinned original C.
        for index,mutate in enumerate((
            lambda r:r["grant_body"].update(session_principal_id="99"),
            lambda r:r["grant_body"].update(ownership_generation="99"),
            lambda r:r["grant_body"]["pins"].update(fixture="ff"*32),
            lambda r:r["plan"].update(context_digest="ff"*32))):
            with self.subTest(index=index):
                records=deepcopy(self.base.records)
                prepare=next(x for x in records if x["kind"]=="fixture_grant_prepare")["data"]
                receipt=prepare["receipt"];mutate(receipt);grant=receipt["grant_body"]
                prefix=self.base.codec.encode_plan(receipt["plan"])[:-40]
                content=b"C12-CONTENT-V1"+prefix
                grant.update(command_content_hex=content.hex(),command_content_sha256=hashlib.sha256(content).hexdigest())
                receipt["grant_body_sha256"]=hashlib.sha256(report_bytes(grant)).hexdigest()
                receipt["plan"]["grant_body_sha256"]=receipt["grant_body_sha256"]
                wire=self.base.codec.encode_plan(receipt["plan"])
                receipt.update(wire_hex=wire.hex(),wire_sha256=hashlib.sha256(wire).hexdigest())
                unsigned=deepcopy(receipt["plan"]);unsigned["grant_body_sha256"]="0"*64
                prepare["input_sha256"]=hashlib.sha256(record_bytes({"plan":unsigned,"reservation":receipt["reservation"]})).hexdigest()
                next(x for x in records if x["kind"]=="fixture_grant_commit")["data"]["grant_body_sha256"]=receipt["grant_body_sha256"]
                result=self.authority_replay(records,"wrong-context-"+str(index)+".jsonl")
                self.assertEqual(result["status"],"incomplete",result)
                self.assertIn("canonical plan projection",result["reason"])

    def test_authority_replay_refuses_report_commit_before_original_receipt(self):
        self.commit();records=deepcopy(self.base.records)
        next(x for x in records if x["kind"]=="fixture_report_prepare")["data"]["inspection"]["commit"]["committed_home_us"]="1"
        next(x for x in records if x["kind"]=="fixture_report_commit")["data"]["commit"]["committed_home_us"]="1"
        result=self.authority_replay(records,"commit-before-receipt.jsonl")
        self.assertEqual(result["status"],"incomplete",result)
        self.assertIn("clock/scope",result["reason"])

    def test_authority_replay_refuses_report_receipt_before_original_admission(self):
        self.commit();records=deepcopy(self.base.records)
        inspection=next(x for x in records if x["kind"]=="fixture_report_prepare")["data"]["inspection"]
        inspection["report"]["first_received_home_us"]="1"
        inspection["commit"]["report_sha256"]=hashlib.sha256(report_bytes(inspection["report"])).hexdigest()
        next(x for x in records if x["kind"]=="fixture_report_commit")["data"]["commit"]=deepcopy(inspection["commit"])
        result=self.authority_replay(records,"receipt-before-admission.jsonl")
        self.assertEqual(result["status"],"incomplete",result)
        self.assertIn("clock/scope",result["reason"])

    def test_authority_replay_refuses_lease_transition_before_commit_or_prior_change(self):
        self.commit();self.base.now=1_200_000;self.current["not_after_home_us"]="3000000";self.store.inspection()
        self.base.now=1_300_000;self.current["not_after_home_us"]="2000000";self.store.inspection()
        for index in (0,1):
            with self.subTest(index=index):
                records=deepcopy(self.base.records)
                changes=[r for r in records if r["kind"]=="fixture_report_lease_prepare"]
                changes[index]["data"]["available_home_us"]=str((1_130_000,1_200_000)[index]-1)
                result=self.authority_replay(records,"transition-clock-"+str(index)+".jsonl")
                self.assertEqual(result["status"],"incomplete",result)
                self.assertIn("clock order",result["reason"])


if __name__=="__main__":unittest.main()
