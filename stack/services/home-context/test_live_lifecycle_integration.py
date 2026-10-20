"""Actual local HTTP/journal/worker joins with fictional observations only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from journal import Journal, PREFIX, digest
from live_delivery_test_helpers import delivery_geometry
from live_lifecycle_test_helpers import lifecycle_fixture, clearance, brightness, lifecycle
from live_scene_test_helpers import HUMAN_TOKEN
from live_test_helpers import LiveHttp, NOW, PROFILE, event, fact, publication_ref
from live_worker_test_helpers import WorkerProbe, worker_receipt_resolver, WORKER_TOKEN


class LifecycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='authored-211ab-', dir=Path(__file__).parent)
        self.addCleanup(work.cleanup)
        self.now, self.seq, self.heads, self.originals = NOW-2.2, 0, {}, {}
        self.service, self.grants, self.config = lifecycle_fixture()
        self.journal = Journal(Path(work.name)/'authored.sqlite', grants=self.grants, profile=PROFILE,
            clock=lambda:self.now, live_config=self.service, live_receipt_resolver=worker_receipt_resolver)
        self.addCleanup(self.journal.close)
        self.http=LiveHttp(self.journal);self.addCleanup(self.http.close)
        self.config['service_url']=f'http://127.0.0.1:{self.http.server.server_port}'
        self.probe=WorkerProbe();self.addCleanup(self.probe.close)
        self.probe.command('start',config=self.config,t=self.now)
        bootstrap=self.tick()
        self.assertEqual(bootstrap['person_hazards']['items'][0]['kind'],'unreconciled_session_history')
        self.now=NOW-1.8
        command=clearance('bootstrap-clearance',bootstrap['person_hazards']['items'],self.config['lifecycle_policy'],self.now)
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        self.now=NOW-1.2
        cleared=self.tick();self.assertEqual(cleared['person_hazards']['items'],[],cleared)

    def tick(self):
        result=self.probe.command('tick',t=self.now)
        self.assertEqual(len(result['decisions']),1,result)
        return result['decisions'][0]

    def publish(self,payload):
        key=json.dumps(payload['head']['key'],sort_keys=True)
        if key in self.heads:payload['head']['supersedes']=publication_ref(self.heads[key])
        status,receipt=self.http.request('POST',PREFIX+'/events',event(payload))
        self.assertEqual(status,200,receipt)
        self.heads[key]=receipt
        self.originals[payload['id']]=copy.deepcopy(payload)
        return publication_ref(receipt)

    def advance(self,*,positive=True,geometry=True,lifecycle_kind=None,gap_start=None):
        self.seq+=1;self.now=NOW+self.seq-1+.2
        if positive:self.last_fact=self.publish(fact('fictional-use-'+str(self.seq),seq=self.seq))
        if geometry:self.last_geometry=self.publish(delivery_geometry(self.seq))
        if lifecycle_kind:
            proof=lifecycle(self.seq,[self.last_geometry],lifecycle_kind,gap_start)
            proof['available_at']=self.now
            # The historical basis is ancestry only, never supplied old geometry.
            prior=self.originals[self.last_geometry['id']]
            for key in ['observation_root_times','sensor_root_bindings']:
                proof[key]={**prior[key],**proof[key]}
            proof['observation_roots']=sorted(proof['observation_root_times'])
            proof['sensor_roots']=sorted(set(prior['sensor_roots']+proof['sensor_roots']))
            self.publish(proof)
        return self.tick()

    def establish(self):
        for _ in range(8):row=self.advance()
        self.assertEqual(row['execution']['status'],'virtual',row)
        self.assertGreater(row['execution']['readback'][0]['brightness_fraction'],0)
        return row

    def submit(self,command):
        status,receipt=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual(status,200,receipt)
        return receipt

    def lookup(self,command):
        scope=self.service['manifest']['identity']
        return self.http.request('GET',PREFIX+'/intent-events/'+command['id']+f'?site_id={scope["site_id"]}&room_id={scope["room_id"]}',token=HUMAN_TOKEN)

    def test_durable_acceptance_causal_application_lost_ack_restart_and_motion_continuity(self):
        before=self.establish()
        command=brightness('owner-brightness',before['scene'],self.now+.05,.2)
        self.now+=.05
        accepted=self.submit(command)
        self.assertEqual(accepted['status'],'accepted')
        self.assertIsNone(self.lookup(command)[1].get('application_result'))
        self.assertEqual(self.submit(command),accepted)
        self.probe.command('fault',mode='lost_ack')
        after=self.advance()
        self.assertEqual(after['scene']['scene_id'],before['scene']['scene_id'])
        self.assertEqual(after['scene']['example_ref'],before['scene']['example_ref'])
        self.assertEqual(after['goal']['nominal']['cct_kelvin'],before['goal']['nominal']['cct_kelvin'])
        self.assertEqual(after['goal']['nominal']['brightness_fraction'],.2)
        self.assertEqual(after['execution']['continuity'],'continuous')
        result=self.lookup(command)[1]['application_result'];self.assertEqual(result['status'],'applied',result)
        self.probe.command('stop');self.probe.command('start',config=self.config,t=self.now+.1)
        resumed=self.advance()
        self.assertEqual(resumed['goal']['nominal']['brightness_fraction'],.2)
        self.assertEqual(self.lookup(command)[1]['application_result'],result)
        self.assertEqual(self.submit(command),accepted)

    def test_latest_delivered_brightness_wins_and_invalid_scope_settles_rejected(self):
        before=self.establish();self.now+=.05
        first=brightness('earlier',before['scene'],self.now,.3);second=brightness('later',before['scene'],self.now,.6)
        self.submit(first);self.submit(second)
        row=self.advance();self.assertEqual(row['goal']['nominal']['brightness_fraction'],.6)
        self.assertEqual(self.lookup(first)[1]['application_result']['status'],'rejected')
        self.assertEqual(self.lookup(second)[1]['application_result']['status'],'applied')
        self.now+=.05;wrong=brightness('wrong-scene',before['scene'],self.now,.8);wrong['value']['scene_id']='obsolete-scene'
        self.submit(wrong);self.advance();self.assertEqual(self.lookup(wrong)[1]['application_result']['status'],'rejected')

    def test_missing_protection_withdraws_while_correction_metadata_is_retained(self):
        before=self.establish();self.now+=.05
        command=brightness('saved-brightness',before['scene'],self.now,.25);self.submit(command);self.advance()
        self.seq+=1;self.now=NOW+self.seq-1+.2
        self.publish(fact('current-use-no-protection',seq=self.seq))
        geometry=delivery_geometry(self.seq,'head_in_beam');self.publish(geometry)
        row=self.tick();self.assertEqual(row['execution']['status'],'unavailable')
        self.assertEqual(row['goal']['nominal']['brightness_fraction'],.25)

    def test_journal_independently_rejects_forged_correction_or_clearance_settlement(self):
        self.establish();self.probe.command('fault',mode='before_commit')
        self.seq+=1;self.now=NOW+self.seq-1+.2
        self.publish(fact('forgery-use',seq=self.seq));self.publish(delivery_geometry(self.seq))
        prepared=self.probe.command('tick',t=self.now)
        commit=json.loads(prepared['commit_posts'][-1])
        commit['publications'][-1]['payload']['goal']['nominal']['brightness_fraction']=.99
        status,result=self.http.request('POST',PREFIX+'/worker-commits',commit,token=WORKER_TOKEN)
        self.assertEqual(status,409,result)

    def test_qualified_semantic_gap_keeps_corrected_scene_but_source_barrier_does_not(self):
        before=self.establish();self.now+=.05
        command=brightness('gap-correction',before['scene'],self.now,.25);self.submit(command);self.advance()
        self.seq+=1;self.now=NOW+self.seq-1+.2;gap_start=self.now-.2
        missing=fact('semantic-gap',seq=self.seq);missing.update(status='unobservable',value=None,reason='material_occluded')
        self.publish(missing);self.last_geometry=self.publish(delivery_geometry(self.seq))
        proof=lifecycle(self.seq,[self.last_geometry],'continuity_qualified',gap_start);proof['available_at']=self.now;self.publish(proof)
        held=self.tick();self.assertEqual(held['scene_continuity']['status'],'held',held)
        self.assertEqual(held['execution']['status'],'virtual',held)
        self.assertEqual(held['goal']['nominal']['brightness_fraction'],.25)
        resumed=self.advance(lifecycle_kind='continuity_qualified',gap_start=gap_start)
        self.assertEqual(resumed['scene']['scene_id'],before['scene']['scene_id'])
        self.assertEqual(resumed['goal']['nominal']['brightness_fraction'],.25)
        from live_test_helpers import barrier
        self.now=NOW+self.seq
        stop=barrier(list(self.heads.values()),event_id='qualified-gap-source-stop',at=self.now)
        status,receipt=self.http.request('POST',PREFIX+'/events',event(stop));self.assertEqual(status,200,receipt)
        self.now+=1;lost=self.tick();self.assertEqual(lost['execution']['status'],'unavailable')
        self.assertNotEqual(lost['scene_continuity']['status'],'held')
        self.assertTrue(lost['person_hazards']['items'])


if __name__=='__main__':unittest.main()
