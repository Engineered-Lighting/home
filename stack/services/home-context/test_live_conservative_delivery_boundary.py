"""Config7 conservative output through public authenticated journal seams."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from journal import Journal, PREFIX, digest
from live_conservative_test_helpers import conservative_fixture
from live_lifecycle_test_helpers import clearance
from live_scene_test_helpers import HUMAN_TOKEN
from live_delivery_test_helpers import delivery_geometry
from live_test_helpers import MANIFEST, LiveHttp, NOW, PROFILE, SCOPE, event, fact, publication_ref
from live_worker_test_helpers import WorkerProbe, describe, worker_receipt_resolver, WORKER_TOKEN, WORKER_ID
import test_live_delivery_boundary as legacy


class ConservativeDeliveryBoundaryTests(unittest.TestCase):
    decision = staticmethod(legacy.LiveDeliveryBoundaryTests.decision)
    inject_prior_delivery = legacy.LiveDeliveryBoundaryTests.inject_prior_delivery
    prior_checkpoint = legacy.LiveDeliveryBoundaryTests.prior_checkpoint

    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='authored-conservative-boundary-',dir=Path(__file__).parent)
        self.addCleanup(work.cleanup)
        self.service,self.grants,self.config=conservative_fixture()
        # Independent nonzero speed and acceleration qualification for these
        # authored cases; no zero-motion helper can mask horizon inflation.
        source=self.service['manifest']['sources']['synthetic-source']
        for name in ('head','protection'):
            source['part_policies'][name]['age'].update(max_speed_m_s=.1,max_accel_m_s2=.05)
        if self._testMethodName in {'test_reachable_pan_is_accepted_by_independent_journal','test_branch_cut_is_accepted_by_independent_journal'}:
            fixture=self.config['delivery_policy']['fixture'];fixture['origin_m']=[0,2,3]
            if self._testMethodName=='test_reachable_pan_is_accepted_by_independent_journal':
                fixture['mechanics'].update(pan_min_deg=0,pan_max_deg=540)
            else:fixture['local_to_room']=[0,-1,0,1,0,0,0,0,1]
        self.config['source_pins']={name:digest(value) for name,value in self.service['manifest']['sources'].items()}
        description=describe(self.config)
        self.service['manifest']['worker'].update(config_digest=description['config_digest'],source_pins=self.config['source_pins'])
        self.service['manifest']['worker']['delivery_policy']=copy.deepcopy(self.config['delivery_policy'])
        self.service['manifest_ref']['digest']=digest(self.service['manifest'])
        MANIFEST.update(self.service['manifest_ref'])
        self.config.update(manifest_ref=copy.deepcopy(self.service['manifest_ref']),live_manifest=copy.deepcopy(self.service['manifest']))
        for grant in self.grants:
            for binding in grant['live_bindings']:
                binding['manifest_ref']=copy.deepcopy(self.service['manifest_ref'])
        self.now,self.seq,self.heads=NOW+.2,2,{}
        self.j=Journal(Path(work.name)/'authored.sqlite',grants=self.grants,profile=PROFILE,clock=lambda:self.now,
                       live_config=self.service,live_receipt_resolver=worker_receipt_resolver,
                       live_wall_clock=lambda:self.now,live_monotonic=lambda:self.now-NOW)
        self.addCleanup(self.j.close)
        self.http=LiveHttp(self.j);self.addCleanup(self.http.close)
        self.config['service_url']=f'http://127.0.0.1:{self.http.server.server_port}'
        self.probe=WorkerProbe();self.addCleanup(self.probe.close)
        self.scope='?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']
        self.probe.command('start',config=self.config,t=self.now)
        first=self.tick();self.now=NOW+1.2
        intent=clearance('intent:authored-clear',first['person_hazards']['items'],self.config['lifecycle_policy'],self.now)
        status,result=self.http.request('POST',PREFIX+'/intent-events',intent,token=HUMAN_TOKEN)
        self.assertEqual(status,200,result);self.assertEqual(self.tick()['intent_results'][0]['status'],'applied')

    def tick(self,prepare=False):
        if prepare:self.probe.command('fault',mode='before_commit')
        result=self.probe.command('tick',t=self.now)
        if prepare:return json.loads(result['commit_posts'][-1])
        self.assertEqual(len(result['decisions']),1,result)
        return result['decisions'][0]

    def advance(self,head_x=2.5,prepare=False,missing_protection=False):
        self.seq+=1;self.now=NOW+self.seq-1+.2
        geometry=delivery_geometry(self.seq)
        geometry['parts']['head']['shape']['center_m'][0]=head_x
        geometry['parts']['protection']['shape']['items'][0]['center_m'][0]=head_x
        for name in ('head','protection'):
            geometry['parts'][name]['uncertainty']['age'].update(max_speed_m_s=.1,max_accel_m_s2=.05)
        if missing_protection:geometry['parts']['protection']=copy.deepcopy(geometry['parts']['body_forward'])
        for payload in (fact('conservative-fact-'+str(self.seq),seq=self.seq),geometry):
            key=json.dumps(payload['head']['key'],sort_keys=True)
            if key in self.heads:payload['head']['supersedes']=publication_ref(self.heads[key])
            status,result=self.http.request('POST',PREFIX+'/events',event(payload))
            self.assertEqual(status,200,result);self.heads[key]=result
        return self.tick(prepare)

    def establish(self):
        for _ in range(12):result=self.advance()
        self.assertEqual(result['execution']['status'],'virtual',result)
        self.assertGreater(result['execution']['readback'][0]['brightness_fraction'],0)
        return result

    def test_nonzero_speed_and_acceleration_withdraw_honest_output(self):
        self.establish()
        result=self.advance(.6)
        self.assertEqual(result['execution']['status'],'unavailable')
        self.assertEqual(result['allocation']['reason'],'protected_volume_intersection')

    def test_forged_prior_clearance_rejects_independently_at_lease_horizon(self):
        prior=self.establish();state=self.prior_checkpoint()
        commit=self.advance(.6,prepare=True)
        self.inject_prior_delivery(commit,prior,state)
        status,result=self.http.request('POST',PREFIX+'/worker-commits',commit,token=WORKER_TOKEN)
        self.assertEqual(status,409,result)
        self.assertEqual(result['error']['code'],'invalid_delivery_protection',result)

    def test_reachable_pan_is_accepted_by_independent_journal(self):
        self.assertEqual(self.establish()['allocation']['assignments'][0]['pan_deg'],270)

    def test_branch_cut_is_accepted_by_independent_journal(self):
        self.assertEqual(self.establish()['allocation']['assignments'][0]['pan_deg'],-180)

    def test_forged_unavailable_protection_is_a_typed_refusal(self):
        prior=self.establish();state=self.prior_checkpoint()
        commit=self.advance(prepare=True,missing_protection=True)
        self.inject_prior_delivery(commit,prior,state)
        status,result=self.http.request('POST',PREFIX+'/worker-commits',commit,token=WORKER_TOKEN)
        self.assertEqual(status,409,result)
        self.assertEqual(result['error']['code'],'invalid_delivery_geometry',result)


if __name__=='__main__':unittest.main()
