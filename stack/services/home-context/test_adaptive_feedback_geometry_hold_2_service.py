"""Fresh G2 registry/grant/loopback admission regressions; fictional local evidence."""
import copy
import hashlib
import http.client
import json
import subprocess
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from journal import Grant, Journal, JournalError, PREFIX, canonical
from schema_registry import SchemaRegistry, G2_PROFILE, G2_INTERFACE_DIGEST, schema_digest
from server import LocalServer
import adaptive_feedback_geometry_hold_2_phase_views as phase
import adaptive_feedback_geometry_hold_2_internal_storage as storage
from adaptive_feedback_geometry_hold_2_phase_views import create_fixture_clock
import adaptive_feedback_geometry_hold_2_validation as validation

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
FIXTURE=json.loads((HERE/"test_geometry_hold_2_helpers_fixture.json").read_text(encoding="utf-8"))
TOKEN="fictional-g2-loopback-unit-token-2026"

class RegistryGrantTests(unittest.TestCase):
    def test_exact_raw_graph_and_retained_pins(self):
        registry=SchemaRegistry((),profile=G2_PROFILE)
        self.assertEqual(len(registry.schemas),75)
        self.assertEqual(len(registry.publication_families),36)
        self.assertEqual(registry.interface_digest,G2_INTERFACE_DIGEST)
        self.assertEqual(registry.raw_schema_pins["home-context-worker-checkpoint-9.schema.json"],schema_digest("home-context-worker-checkpoint-9",profile=G2_PROFILE))
        self.assertEqual(schema_digest("home-preference-snapshot-2",profile=G2_PROFILE),schema_digest("home-preference-snapshot-2",profile="adaptive-book-feedback-reference-synthetic-1"))

    def test_new_operations_are_profile_only_and_require_family_and_read(self):
        roles={"geometry_constraint_source":"home-preference-geometry-constraint-source-1","feedback_assessment":"home-preference-learning-assessment-3",
               "write_authority":"home-preference-write-authority-2","write_frontier":"home-preference-write-frontier-2","support_qualification":"home-preference-support-qualification-2"}
        for operation,family in roles.items():
            raw=copy.deepcopy(FIXTURE["grants"][0]);raw["operations"]=["read",operation]
            raw["payload_types"]=[family];raw["schema_pins"]={family:schema_digest(family,profile=G2_PROFILE)}
            raw["revisions"]=[str(int(family.rsplit("-",1)[1]))+".0"]
            self.assertIn(operation,Grant.from_dict(raw,profile=G2_PROFILE).operations)
            for profile in ("legacy","adaptive-book-feedback-synthetic-1","adaptive-book-feedback-reference-synthetic-1"):
                with self.assertRaises(ValueError):Grant.from_dict(raw,profile=profile)
            bad=copy.deepcopy(raw);bad["operations"]=[operation]
            with self.assertRaises(ValueError):Grant.from_dict(bad,profile=G2_PROFILE)
            bad=copy.deepcopy(raw);bad["payload_types"]=[];bad["schema_pins"]={}
            with self.assertRaises(ValueError):Grant.from_dict(bad,profile=G2_PROFILE)

    def test_old_configuration_and_worker_mode_cannot_enter_G2(self):
        fixture=copy.deepcopy(FIXTURE);fixture["configuration"]["config"]["profile_mode"]="feedback_reference"
        clock=create_fixture_clock()
        with self.assertRaises((ValueError,JournalError)):
            Journal(":memory:",profile=G2_PROFILE,adaptive_feedback_geometry_hold_2_config=fixture["configuration"],grants=fixture["grants"],clock=clock.read)

class HTTPTests(unittest.TestCase):
    def setUp(self):
        fixture=copy.deepcopy(FIXTURE)
        fixture["grants"][0]["token_sha256"]=hashlib.sha256(TOKEN.encode()).hexdigest()
        self.fixture=fixture;self.clock=create_fixture_clock()
        self.j=Journal(":memory:",profile=G2_PROFILE,adaptive_feedback_geometry_hold_2_config=fixture["configuration"],grants=fixture["grants"],clock=self.clock.read)
        self.clock.bind(self.j)
        self.j.dynamic.origins=storage.create_origin_storage_ledger(self.j,storage.selection_configuration())
        self.j.dynamic=phase.create_phase_view_profile(self.j,phase.selection_configuration(),self.clock,
            worker_static_bindings={k:copy.deepcopy(fixture["options"][k]) for k in ("subjectBindings","revision_authorizations","source_origins","duration_s")})
        self.server=LocalServer(("127.0.0.1",0),self.j)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={"poll_interval":.01},daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.j.close()
    def request(self,path,method="GET",body=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        try:
            wire=None if body is None else canonical(body)
            conn.request(method,PREFIX+path,body=wire,headers={"Authorization":"Bearer "+TOKEN,"Content-Type":"application/json"})
            response=conn.getresponse();return response.status,json.loads(response.read())
        finally:conn.close()
    def scope(self):
        i=self.fixture["configuration"]["identity"]
        return "site_id="+i["site_id"]+"&room_id="+i["room_id"]+"&consumer_id="+self.fixture["grants"][0]["expected_consumer_id"]
    def prepare(self,cut):
        result=subprocess.run(["node","--max-old-space-size=512",str(HERE/"test_geometry_hold_2_helpers_fixture.cjs"),"--bootstrap-from-live-cut"],
                              input=canonical({"cut":cut,"options":self.fixture["options"]}),text=True,encoding="utf-8",capture_output=True,timeout=30,
                              creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
    def test_authenticated_live_bootstrap_retry_checkpoint_and_origin(self):
        status,cut=self.request("/feedback-input-cut?"+self.scope()+"&after=0&t=0&limit=128")
        self.assertEqual(status,200);commit=self.prepare(cut)
        status,receipt=self.request("/worker-commits","POST",commit);self.assertEqual(status,200,receipt)
        self.assertEqual(receipt["schema"],"home-context-worker-commit-receipt-5");self.assertEqual(receipt["commit_seq"],1)
        status,retry=self.request("/worker-commits","POST",commit);self.assertEqual((status,retry),(200,receipt))
        worker=self.fixture["grants"][0]["expected_worker_id"]
        status,checkpoint=self.request("/worker-commits/"+worker+"?"+self.scope())
        self.assertEqual(status,200,checkpoint);self.assertEqual(checkpoint["checkpoint"],commit["checkpoint"])
        self.assertEqual(checkpoint["schema"],"home-context-worker-checkpoint-response-7")
        status,found=self.request("/feedback-commits/"+commit["id"]+"?"+self.scope())
        self.assertEqual(status,200,found);self.assertEqual(found["schema"],"home-context-feedback-commit-response-3")
        self.assertEqual(found["receipt"],receipt)
    def test_exact_family_routes_and_historical_route_refusal(self):
        for route,family in (("worker-control-output-proofs","home-lighting-control-carrier-1"),("worker-application-output-proofs","home-intent-application-result-3"),
                             ("worker-policy-output-proofs","home-lighting-policy-decision-3"),("worker-exposure-output-proofs","home-lighting-settled-exposure-1"),
                             ("worker-feedback-application-output-proofs","home-experience-feedback-application-1")):
            status,body=self.request("/"+route+"?"+self.scope()+"&cursor=1&expected_family="+family)
            self.assertEqual(status,404,body)
        status,_=self.request("/worker-policy-output-proofs?"+self.scope()+"&cursor=1&expected_family=home-lighting-control-carrier-1")
        self.assertEqual(status,400)
        status,_=self.request("/feedback-worker-output-proofs?"+self.scope()+"&cursor=1");self.assertEqual(status,404)
    def test_generic_new_issuer_and_carrier_routes_refuse(self):
        for family in ("home-lighting-control-carrier-1","home-preference-geometry-constraint-source-1","home-preference-learning-assessment-3",
                       "home-preference-write-authority-2","home-preference-write-frontier-2","home-preference-support-qualification-2"):
            status,body=self.request("/events","POST",{"payload_type":family})
            self.assertEqual(status,403,body)

class ResolverTests(unittest.TestCase):
    def fixture(self,*,extra="operative",reverse=False):
        f=copy.deepcopy(FIXTURE);reader=next(g for g in f["grants"] if g["operations"]==["read"])
        extra_grant=copy.deepcopy(reader);extra_grant["token_sha256"]=hashlib.sha256(("resolver-extra-"+extra).encode()).hexdigest()
        if extra=="operative":extra_grant["operations"]=["event","read"]
        if extra!="none":f["grants"].append(extra_grant)
        if reverse:f["grants"].reverse()
        clock=create_fixture_clock();journal=Journal(":memory:",profile=G2_PROFILE,adaptive_feedback_geometry_hold_2_config=f["configuration"],grants=f["grants"],clock=clock.read)
        clock.bind(journal)
        authority={"writer_principal_id":reader["principal_id"],"writer_origin":{"producer_id":reader["producer_id"],"producer_epoch":reader["producer_epoch"]}}
        issuer=next(g for g in journal.grants if g.principal_id==reader["principal_id"] and g.operations==frozenset({"read"}))
        return journal,authority,issuer
    def test_pure_read_resolver_is_unique_independent_of_order_and_operative_credential(self):
        for reverse in (False,True):
            journal,authority,issuer=self.fixture(reverse=reverse)
            try:
                selected=journal.dynamic._configured_read_resolver(issuer,authority)
                self.assertEqual(selected.operations,frozenset({"read"}));self.assertIs(selected,issuer)
            finally:journal.close()
    def test_missing_duplicate_consumer_scope_and_current_epoch_refuse(self):
        journal,authority,issuer=self.fixture(extra="duplicate")
        try:
            with self.assertRaises(JournalError):journal.dynamic._configured_read_resolver(issuer,authority)
        finally:journal.close()
        journal,authority,issuer=self.fixture()
        try:
            journal.grants=tuple(g for g in journal.grants if g.operations!=frozenset({"read"}) or g.principal_id!=issuer.principal_id)
            with self.assertRaises(JournalError):journal.dynamic._configured_read_resolver(issuer,authority)
        finally:journal.close()
        for change in ("consumer","scope","epoch"):
            journal,authority,issuer=self.fixture(extra="none")
            try:
                if change=="consumer":
                    from dataclasses import replace
                    issuer=replace(issuer,expected_consumer_id="wrong-consumer")
                elif change=="scope":authority["writer_principal_id"]="different-principal"
                else:
                    body=journal.dynamic.authority(journal.dynamic.identity["site_id"],journal.dynamic.identity["room_id"])
                    row=next(p for p in body["producers"] if p["producer_id"]==issuer.producer_id);row["revoked"]=True;row["revocation_generation"]+=1
                    journal._set_meta("adaptive-authority",canonical(body))
                with self.assertRaises(JournalError):journal.dynamic._configured_read_resolver(issuer,authority)
            finally:journal.close()
    def test_bridge_loaded_callable_and_platform_changes_refuse_before_cached_validity(self):
        self.assertTrue(validation._bridge_current())
        with patch.object(validation,"_bridge",lambda *a,**k:{"ok":True}):
            with self.assertRaises(JournalError):validation._bridge_current()
        with patch.object(validation.subprocess,"run",lambda *a,**k:None):
            with self.assertRaises(JournalError):validation._bridge_current()
        with patch.object(validation.Path,"read_bytes",lambda *a:b""):
            with self.assertRaises(JournalError):validation._bridge_current()
        method=phase._PROFILE_CLASS._worker_transition_values;prior=method.__code__
        try:
            method.__code__=(lambda self,commit,old,grant:None).__code__
            self.assertFalse(phase._source_current())
        finally:method.__code__=prior
        self.assertTrue(phase._source_current())

class TimingTests(unittest.TestCase):
    def test_real_commit_bridge_profile_preserves_kernel_deadline(self):
        from test_adaptive_feedback_geometry_hold_2_helpers import Host
        host=Host(views=True);profile=[]
        try:
            for kind,t in (("bootstrap",0),("semantic",0),("control",.2)):
                commit=host.prepare_bootstrap() if kind=="bootstrap" else host.prepare_boundary(t)
                host.accept(commit)
                metrics=copy.deepcopy(host.j.dynamic._g2_last_worker_commit_metrics);metrics["kind"]=kind;profile.append(metrics)
                self.assertLess(metrics["elapsed_ms"],5000,metrics)
            print(json.dumps({"scope":"actual empty-scene G2 service bridge timing, no complete session or Native admission","commits":profile},sort_keys=True))
        finally:host.close()

if __name__=="__main__":unittest.main()
