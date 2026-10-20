"""Manual controls through actual public journal/worker; authored external inputs."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from journal import Journal, Limits, PREFIX, digest
from live_test_helpers import LiveHttp, NOW, PROFILE, SCOPE, barrier, event, fact
from live_scene_test_helpers import HUMAN_ACTOR, HUMAN_TOKEN
from live_scene_test_helpers import intent as scene_intent
from live_worker_test_helpers import WorkerProbe, worker_receipt_resolver, WORKER_TOKEN, WORKER_ID
from manual_test_helpers import manual_fixture, OTHER_ACTOR, OTHER_TOKEN
from room_manual import validate_manual_policy


class RoomManualPublicBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='authored-manual-', dir=Path(__file__).parent)
        self.addCleanup(self.work.cleanup)

    def start(self,*,room_mutator=None,actor_ref=HUMAN_ACTOR,limits=None):
        self.actor_ref=actor_ref
        self.service, self.grants, self.config = manual_fixture(second_actor=True,room_mutator=room_mutator,actor_ref=actor_ref)
        self.now = NOW + .2
        self.j = Journal(Path(self.work.name)/'authored.sqlite', grants=self.grants, profile=PROFILE,limits=limits,
            clock=lambda:self.now,live_config=self.service,live_receipt_resolver=worker_receipt_resolver,
            live_wall_clock=lambda:self.now,live_monotonic=lambda:self.now-NOW)
        self.addCleanup(self.j.close)
        from archive import Recorder
        self.recorder = Recorder(Path(self.work.name)/'originals.jsonl',
            {'schema':'home-live-session-manifest-1','version':1,'source':'authored-manual-public-boundary'},
            max_records=4096,max_bytes=64*1024*1024,min_free_bytes=1024)
        self.addCleanup(lambda:self.recorder.drain({'status':'complete'}))
        self.j.attach_recorder(self.recorder.record)
        self.j.dynamic.worker.control_authority.activate('manual-test-activate')
        self.http = LiveHttp(self.j)
        self.addCleanup(self.http.close)
        self.config['service_url'] = f'http://127.0.0.1:{self.http.server.server_port}'
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.probe.command('start',config=self.config,t=self.now)

    def tick(self,*,prepare=False):
        if prepare: self.probe.command('fault',mode='before_commit')
        result = self.probe.command('tick',t=self.now)
        if prepare:
            import json
            return json.loads(result['commit_posts'][-1])
        self.assertEqual(len(result['decisions']),1,{k:result.get(k) for k in ('statuses','http_errors')})
        return result['decisions'][0]

    def hold(self, control, *, suffix='one', duration=10):
        row = control['domains'][0]
        return dict(schema='home-live-intent-event-4',version=4,id='manual:'+suffix,**deepcopy(SCOPE),
            manifest_ref=deepcopy(self.service['manifest_ref']),actor_ref=self.actor_ref,
            origin='human_preference',truth_role='runtime_intent',kind='manual_ambient_hold',
            target={k:row[k] for k in ('fixture_id','domain')},set_at=self.now,available_at=self.now,
            valid_until=self.now+2,value=dict(policy_ref=deepcopy(control['policy_ref']),
                expected_generation=row['generation'],expected_control_generation=control['control_generation'],
                expected_control_id=None if row['hold'] is None else row['hold']['intent_ref']['id'],
                brightness_fraction=.1,cct_kelvin=3200,duration_s=duration),reply_to=None)

    def apply_one(self,duration=10):
        initial=self.tick(); self.now+=1
        command=self.hold(initial['room_control'],duration=duration)
        status,receipt=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual(status,200,receipt)
        applied=self.tick(); self.now+=1
        return command,receipt,applied,self.tick()

    def release(self,control,*,suffix='return',actor=None):
        command=self.hold(control,suffix=suffix)
        command.update(kind='return_ambient_auto',actor_ref=actor or self.actor_ref)
        for key in ('brightness_fraction','cct_kelvin','duration_s'): del command['value'][key]
        return command

    def test_hold_without_presence_is_applied_and_original_survives_its_application_window(self):
        self.start()
        initial = self.tick()
        self.assertEqual(len(initial['room_control']['domains']),28)
        self.assertNotEqual(initial['background']['status'],'requested')
        self.now += 1
        command = self.hold(initial['room_control'])
        status, accepted = self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        applied = self.tick()
        owner = applied['room_control']['domains'][0]
        self.assertEqual(owner['mode'],'manual')
        self.assertEqual(owner['hold']['actor_ref'],HUMAN_ACTOR)
        self.assertEqual(owner['hold']['accepted_at'],accepted['committed_at'])
        self.assertEqual(owner['hold']['hold_until'],accepted['committed_at']+10)
        self.assertEqual([r['status'] for r in applied['intent_results']],['applied'])
        self.assertNotEqual(applied['background']['status'],'requested')
        self.assertNotEqual(applied['scene']['status'],'selected')
        self.assertIsNone(applied['room_output']['task']['fixture_id'])
        self.now += 2.2
        continued = self.tick()
        self.assertEqual(continued['room_control']['domains'][0]['hold'],owner['hold'])
        self.assertEqual(continued['intent_results'],[])
        row = continued['room_output']['fixtures'][0]['domains'][0]
        self.assertEqual(row['desired'],dict(brightness_fraction=.1,cct_kelvin=3200))
        self.assertGreater(row['sample']['brightness_fraction'],0)
        self.assertEqual(sum(r['mode']=='manual' for r in continued['room_control']['domains']),1)
        self.assertEqual(continued['room_control']['domains'][0]['hold']['intent_ref']['id'],command['id'])

    def test_recorded_stop_fences_prior_manual_capacity_before_source_withdrawal(self):
        self.start()
        command,receipt,applied,current=self.apply_one()
        self.assertGreater(current['room_output']['fixtures'][0]['domains'][0]['sample']['brightness_fraction'],0)
        authority=self.j.dynamic.worker.control_authority.retire('manual-test-stop','operator_stop')
        self.assertFalse(authority['after']['control_active'])
        self.now+=1
        stopped=self.tick()
        self.assertFalse(stopped['room_control']['control_active'])
        self.assertEqual(stopped['room_control']['domains'][0]['mode'],'auto')
        self.assertEqual(stopped['room_control']['domains'][0]['generation'],2)
        self.assertIsNone(stopped['room_output']['fixtures'][0]['domains'][0]['sample'])
        self.assertEqual(stopped['intent_results'],[])

    def test_authorized_other_actor_returns_hold_and_exact_retry_keeps_first_outcome(self):
        self.start()
        command,receipt,applied,current=self.apply_one()
        status,retried=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual((status,retried),(200,receipt))
        changed=deepcopy(command); changed['value']['brightness_fraction']=.2
        status,conflict=self.http.request('POST',PREFIX+'/intent-events',changed,token=HUMAN_TOKEN)
        self.assertEqual(status,409,conflict)
        release=self.release(current['room_control'],actor=OTHER_ACTOR)
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',release,token=OTHER_TOKEN)
        self.assertEqual(status,200,accepted)
        self.now+=1; returned=self.tick()
        self.assertEqual(returned['room_control']['domains'][0]['mode'],'auto')
        self.assertEqual(returned['room_control']['domains'][0]['generation'],2)
        self.assertIsNone(returned['room_output']['fixtures'][0]['domains'][0]['sample'])
        self.assertEqual([r['reason'] for r in returned['intent_results']],['manual_return_applied'])
        self.now+=3
        status,retried=self.http.request('POST',PREFIX+'/intent-events',release,token=OTHER_TOKEN)
        self.assertEqual((status,retried),(200,accepted))
        route=PREFIX+'/intent-events/'+release['id']+'?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']
        self.assertEqual(self.http.request('GET',route,token=HUMAN_TOKEN)[0],404)
        status,lookup=self.http.request('GET',route,token=OTHER_TOKEN)
        self.assertEqual(status,200,lookup)
        self.assertEqual(lookup['event']['actor_ref'],OTHER_ACTOR)
        self.assertEqual(lookup['application_result']['reason'],'manual_return_applied')

    def test_rehashed_manual_sample_forgery_refuses_without_settlement_or_checkpoint_change(self):
        self.start()
        command,receipt,applied,current=self.apply_one()
        route=PREFIX+'/worker-commits/'+WORKER_ID+'?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']
        status,before=self.http.request('GET',route,token=WORKER_TOKEN)
        self.assertEqual(status,200,before)
        self.now+=1; forged=self.tick(prepare=True)
        decision=forged['publications'][-1]['payload']; output=decision['room_output']
        output['fixtures'][0]['domains'][0]['sample']['brightness_fraction']=.2
        revision='room:'+digest([SCOPE,decision['seq'],{k:v for k,v in output.items() if k!='revision_id'}])[:48]
        output['revision_id']=revision
        decision['background_delivery']['revision_id']=revision
        decision['room_control']['room_revision_id']=revision
        status,refused=self.http.request('POST',PREFIX+'/worker-commits',forged,token=WORKER_TOKEN)
        self.assertEqual(status,409,refused)
        self.assertEqual(refused['error']['code'],'invalid_room_output',refused)
        status,after=self.http.request('GET',route,token=WORKER_TOKEN)
        self.assertEqual(status,200,after)
        # Service clock metadata is freshly sampled on every GET. Refusal must
        # preserve the durable worker checkpoint and its committed cursors.
        self.assertEqual({k:v for k,v in after.items() if k!='service_clock'},
                         {k:v for k,v in before.items() if k!='service_clock'})

    def test_owned_zero_releases_original_positive_capacity_for_another_domain(self):
        self.start(room_mutator=lambda room:room['fixtures'][0].update(max_power_w=.1))
        command,receipt,applied,current=self.apply_one(duration=25)
        self.now+=1; bright=self.tick()
        self.assertAlmostEqual(bright['room_output']['fixtures'][0]['domains'][0]['sample']['brightness_fraction'],.1)
        zero=self.hold(bright['room_control'],suffix='zero',duration=20)
        zero['value']['brightness_fraction']=0
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',zero,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        self.now+=1; self.tick()
        self.now+=1; fading=self.tick()
        self.assertGreater(fading['room_output']['fixtures'][0]['domains'][0]['sample']['brightness_fraction'],0)
        self.now+=1; dark=self.tick()
        owner=dark['room_control']['domains'][0]
        self.assertEqual((owner['mode'],owner['hold']['brightness_fraction']),('manual',0))
        self.assertEqual(dark['room_output']['fixtures'][0]['domains'][0]['sample']['brightness_fraction'],0)
        # The old positive down-ramp lease remains reserved, but an owned zero
        # must never renew it beyond the last applicable positive trajectory.
        last_positive_until=self.now-1+3
        later=self.hold(dark['room_control'],suffix='other-domain',duration=15)
        later['target']['domain']='radial2'
        later['value']['expected_generation']=dark['room_control']['domains'][1]['generation']
        later['value']['expected_control_id']=None
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',later,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        self.now+=1; blocked=self.tick()
        self.assertIsNone(blocked['room_output']['fixtures'][0]['domains'][1]['sample'])
        self.assertEqual(blocked['room_control']['domains'][1]['mode'],'manual')
        self.now=last_positive_until; released=self.tick()
        self.assertIsNotNone(released['room_output']['fixtures'][0]['domains'][1]['sample'])
        self.now+=1; sampling=self.tick()
        self.assertGreater(sampling['room_output']['fixtures'][0]['domains'][1]['sample']['brightness_fraction'],0)
        self.assertEqual(sampling['room_control']['domains'][0]['hold'],owner['hold'])

    def test_horizon_and_domain_bounds_refuse_without_original_then_expire_exactly(self):
        self.start()
        current=self.tick(); self.now+=1
        command=self.hold(current['room_control'],duration=120)
        status,refused=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual((status,refused['error']['code']),(409,'manual_horizon_exceeded'))
        route=PREFIX+'/intent-events/'+command['id']+'?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']
        self.assertEqual(self.http.request('GET',route,token=HUMAN_TOKEN)[0],404)
        command['value'].update(duration_s=2,brightness_fraction=.6)
        status,refused=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual((status,refused['error']['code']),(400,'manual_value_out_of_bounds'))
        self.assertEqual(self.http.request('GET',route,token=HUMAN_TOKEN)[0],404)
        command['value']['brightness_fraction']=.1
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        applied=self.tick()
        self.assertEqual(applied['room_control']['domains'][0]['hold']['hold_until'],accepted['committed_at']+2)
        self.now=accepted['committed_at']+2; expired=self.tick()
        self.assertEqual(expired['room_control']['domains'][0]['mode'],'auto')
        self.assertEqual(expired['room_control']['domains'][0]['generation'],2)
        self.assertIsNone(expired['room_output']['fixtures'][0]['domains'][0]['sample'])
        self.assertEqual(expired['intent_results'],[])

    def test_reserved_return_survives_shared_history_saturation_by_legacy_intents(self):
        self.start()
        command,receipt,applied,current=self.apply_one(duration=30)
        for index in range(126):
            legacy=scene_intent('legacy-capacity:'+str(index),at=self.now)
            status,accepted=self.http.request('POST',PREFIX+'/intent-events',legacy,token=HUMAN_TOKEN)
            self.assertEqual(status,200,(index,accepted))
        overflow=scene_intent('legacy-capacity:overflow',at=self.now)
        status,refused=self.http.request('POST',PREFIX+'/intent-events',overflow,token=HUMAN_TOKEN)
        self.assertEqual((status,refused['error']['code']),(507,'journal_full'))
        extra=self.hold(current['room_control'],suffix='extra')
        extra['target']['domain']='radial2'
        extra['value'].update(expected_generation=0,expected_control_id=None)
        status,refused=self.http.request('POST',PREFIX+'/intent-events',extra,token=HUMAN_TOKEN)
        self.assertEqual((status,refused['error']['code']),(507,'journal_full'))
        release=self.release(current['room_control'])
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',release,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        self.assertEqual(self.http.request('POST',PREFIX+'/intent-events',release,token=HUMAN_TOKEN),(200,accepted))
        self.now+=1; returned=self.tick()
        self.assertEqual(returned['room_control']['domains'][0]['mode'],'auto')
        self.assertIn('manual_return_applied',[r['reason'] for r in returned['intent_results']])

    def test_replacement_pending_and_stale_return_cannot_consume_successor_reservation(self):
        self.start()
        command,receipt,applied,current=self.apply_one(duration=20)
        replacement=self.hold(current['room_control'],suffix='replacement',duration=15)
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',replacement,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        fanout=self.hold(current['room_control'],suffix='fanout')
        status,refused=self.http.request('POST',PREFIX+'/intent-events',fanout,token=HUMAN_TOKEN)
        self.assertEqual((status,refused['error']['code']),(409,'manual_pending_conflict'))
        stale=self.release(current['room_control'],suffix='stale-return')
        status,refused=self.http.request('POST',PREFIX+'/intent-events',stale,token=HUMAN_TOKEN)
        self.assertEqual((status,refused['error']['code']),(409,'manual_pending_conflict'))
        self.now+=1; replaced=self.tick()
        self.assertEqual(replaced['room_control']['domains'][0]['hold']['intent_ref']['id'],replacement['id'])
        stale.update(set_at=self.now,available_at=self.now,valid_until=self.now+2)
        status,refused=self.http.request('POST',PREFIX+'/intent-events',stale,token=HUMAN_TOKEN)
        self.assertEqual((status,refused['error']['code']),(409,'manual_generation_conflict'))
        release=self.release(replaced['room_control'],suffix='successor-return')
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',release,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        self.now+=1; returned=self.tick()
        self.assertEqual(returned['room_control']['domains'][0]['mode'],'auto')

    def test_all_28_ambient_domains_own_and_sample_without_presence(self):
        self.start()
        current=self.tick(); self.now+=1
        originals={}
        for index,row in enumerate(current['room_control']['domains']):
            command=self.hold(current['room_control'],suffix='all:'+str(index),duration=20)
            command['target']={k:row[k] for k in ('fixture_id','domain')}
            status,receipt=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
            self.assertEqual(status,200,receipt)
            originals[command['id']]=receipt['committed_at']+20
        applied=self.tick()
        self.assertEqual(len(applied['intent_results']),28)
        self.now+=2; current=self.tick()
        self.assertEqual([d['mode'] for d in current['room_control']['domains']],['manual']*28)
        for owner in current['room_control']['domains']:
            self.assertEqual(owner['hold']['hold_until'],originals[owner['hold']['intent_ref']['id']])
        for fixture in current['room_output']['fixtures']:
            self.assertEqual([r['status'] for r in fixture['domains'][:7]],['modeled']*7)
            self.assertTrue(all(r['sample']['brightness_fraction']>0 for r in fixture['domains'][:7]))
            self.assertIsNone(fixture['domains'][7]['sample'])
        self.assertNotEqual(current['background']['status'],'requested')

    def test_source_only_barrier_restarts_output_without_rebasing_manual_owner(self):
        self.start()
        status,source_original=self.http.request('POST',PREFIX+'/events',event(fact()))
        self.assertEqual(status,200,source_original)
        command,receipt,applied,current=self.apply_one(duration=20)
        original=current['room_control']['domains'][0]['hold']
        self.now=NOW+4
        status,accepted=self.http.request('POST',PREFIX+'/events',event(barrier([source_original],event_id='manual-source-loss',at=self.now,schema='home-live-fact-1')))
        self.assertEqual(status,200,accepted)
        after=self.tick()
        self.assertEqual(after['room_control']['domains'][0]['hold'],original)
        self.assertEqual(after['room_control']['control_generation'],current['room_control']['control_generation'])
        self.assertTrue(after['room_control']['control_active'])
        self.assertGreater(after['room_output']['authority_generation'],current['room_output']['authority_generation'])
        self.assertEqual(after['room_output']['fixtures'][0]['domains'][0]['sample']['brightness_fraction'],0)
        self.assertEqual(after['intent_results'],[])
        self.now+=1; resumed=self.tick()
        self.assertGreater(resumed['room_output']['fixtures'][0]['domains'][0]['sample']['brightness_fraction'],0)

    def test_expired_output_assessment_preserves_owner_and_allows_return(self):
        self.start(room_mutator=lambda room:room['fixtures'][0]['ambient'][0].update(valid_until=NOW+3))
        command,receipt,applied,current=self.apply_one(duration=20)
        original=current['room_control']['domains'][0]['hold']
        self.now+=1; unavailable=self.tick()
        row=unavailable['room_output']['fixtures'][0]['domains'][0]
        self.assertEqual((row['status'],row['reason']),('unavailable','manual_room_assessment_expired'))
        self.assertIsNone(row['sample'])
        self.assertEqual(unavailable['room_control']['domains'][0]['hold'],original)
        release=self.release(unavailable['room_control'])
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',release,token=OTHER_TOKEN)
        self.assertEqual(status,403,accepted)
        release['actor_ref']=OTHER_ACTOR
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',release,token=OTHER_TOKEN)
        self.assertEqual(status,200,accepted)
        self.now+=1; returned=self.tick()
        self.assertEqual(returned['room_control']['domains'][0]['mode'],'auto')
        self.assertEqual(returned['intent_results'][0]['reason'],'manual_return_applied')

    def test_long_ids_all_28_reserved_returns_reach_128_history_and_apply(self):
        actor='manual-actor-'+('a'*147)
        self.assertEqual(len(actor),160)
        self.start(actor_ref=actor)
        current=self.tick(); self.now+=1
        for index,row in enumerate(current['room_control']['domains']):
            command=self.hold(current['room_control'],suffix=str(index).zfill(3)+('h'*150),duration=90)
            self.assertEqual(len(command['id']),160)
            command['target']={k:row[k] for k in ('fixture_id','domain')}
            status,receipt=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
            self.assertEqual(status,200,receipt)
        current=self.tick(); self.now+=1
        for index in range(72):
            legacy=scene_intent('legacy:'+str(index).zfill(3)+('l'*150),at=self.now)
            legacy['actor_ref']=actor
            status,receipt=self.http.request('POST',PREFIX+'/intent-events',legacy,token=HUMAN_TOKEN)
            self.assertEqual(status,200,(index,receipt))
        current=self.tick()
        self.assertTrue(all(d['mode']=='manual' for d in current['room_control']['domains']))
        first=None
        for index,row in enumerate(current['room_control']['domains']):
            release=self.release(current['room_control'],suffix=str(index).zfill(3)+('r'*150))
            release['target']={k:row[k] for k in ('fixture_id','domain')}
            release['value'].update(expected_generation=row['generation'],expected_control_id=row['hold']['intent_ref']['id'])
            status,receipt=self.http.request('POST',PREFIX+'/intent-events',release,token=HUMAN_TOKEN)
            self.assertEqual(status,200,(index,receipt))
            if first is None: first=(release,receipt)
        self.now+=1; returned=self.tick()
        self.assertEqual([d['mode'] for d in returned['room_control']['domains']],['auto']*28)
        self.assertEqual([r['reason'] for r in returned['intent_results']],['manual_return_applied']*28)
        self.assertEqual(self.http.request('POST',PREFIX+'/intent-events',first[0],token=HUMAN_TOKEN),(200,first[1]))
        overflow=scene_intent('history-after-all-returns',at=self.now)
        overflow['actor_ref']=actor
        status,refused=self.http.request('POST',PREFIX+'/intent-events',overflow,token=HUMAN_TOKEN)
        self.assertEqual((status,refused['error']['code']),(507,'journal_full'))

    def test_resource_policy_accepts_only_explicit_successor_literal_caps(self):
        self.start()
        policy=self.config['manual_policy']; room=self.config['room_policy']
        validate_manual_policy(policy,room)
        for limits in ({'derived_event_bytes':65536,'worker_commit_bytes':1048576},
                       {'derived_event_bytes':262144,'worker_commit_bytes':2097152},
                       {'derived_event_bytes':262144,'worker_commit_bytes':True},
                       {'derived_event_bytes':262144,'worker_commit_bytes':1048576,'raw_event_bytes':262144}):
            changed=deepcopy(policy);changed['resource_limits']=limits
            with self.subTest(limits=limits),self.assertRaises(ValueError): validate_manual_policy(changed,room)

    def test_rehashed_owner_deadline_forgery_cannot_extend_original_admission(self):
        self.start()
        command,receipt,applied,current=self.apply_one()
        route=PREFIX+'/worker-commits/'+WORKER_ID+'?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']
        status,before=self.http.request('GET',route,token=WORKER_TOKEN)
        self.assertEqual(status,200,before)
        self.now+=1; forged=self.tick(prepare=True)
        decision=forged['publications'][-1]['payload']; cp=forged['checkpoint']
        cp['state']['manual_controls']['domains'][0]['hold']['hold_until']+=1
        decision['room_control']['domains'][0]['hold']['hold_until']+=1
        cp['state']['room_execution']['ambient'][0]['authority']['hold_until']+=1
        output=decision['room_output'];output['fixtures'][0]['domains'][0]['authority']['hold_until']+=1
        revision='room:'+digest([SCOPE,decision['seq'],{k:v for k,v in output.items() if k!='revision_id'}])[:48]
        output['revision_id']=revision
        decision['background_delivery']['revision_id']=revision
        decision['room_control']['room_revision_id']=revision
        status,refused=self.http.request('POST',PREFIX+'/worker-commits',forged,token=WORKER_TOKEN)
        self.assertEqual((status,refused['error']['code']),(409,'invalid_manual_control'))
        status,after=self.http.request('GET',route,token=WORKER_TOKEN)
        self.assertEqual(status,200,after)
        self.assertEqual({k:v for k,v in after.items() if k!='service_clock'},
                         {k:v for k,v in before.items() if k!='service_clock'})

    def test_successor_keeps_raw_intent_and_producer_body_bound_at_64k(self):
        self.start()
        current=self.tick(); self.now+=1
        command=self.hold(current['room_control'])
        command['padding']='x'*65536
        status,refused=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual(status,413,refused)
        source=event(fact());source['padding']='x'*65536
        status,refused=self.http.request('POST',PREFIX+'/events',source)
        self.assertEqual(status,413,refused)
        route=PREFIX+'/intent-events/'+command['id']+'?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']
        self.assertEqual(self.http.request('GET',route,token=HUMAN_TOKEN)[0],404)

    def lower_ceiling_refuses(self,limits,*,header_only=False):
        self.start(limits=limits)
        current=self.tick(); self.now+=1
        for index,row in enumerate(current['room_control']['domains']):
            command=self.hold(current['room_control'],suffix='lower:'+str(index),duration=20)
            command['target']={k:row[k] for k in ('fixture_id','domain')}
            status,receipt=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
            self.assertEqual(status,200,receipt)
        route=PREFIX+'/worker-commits/'+WORKER_ID+'?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']
        status,before=self.http.request('GET',route,token=WORKER_TOKEN)
        self.assertEqual(status,200,before)
        prepared=self.tick(prepare=True)
        if header_only:
            import http.client
            import json
            # Exercise Content-Length refusal before transmitting a body. An
            # oversized eager send can race the server's deliberate close on
            # Windows and produce a socket reset instead of the 413 readback.
            body=json.dumps(prepared).encode('utf-8')
            self.assertGreater(len(body),limits.max_manual_worker_commit_bytes)
            connection=http.client.HTTPConnection('127.0.0.1',self.http.server.server_port,timeout=3)
            try:
                connection.putrequest('POST',PREFIX+'/worker-commits')
                connection.putheader('Authorization','Bearer '+WORKER_TOKEN)
                connection.putheader('Content-Type','application/json')
                connection.putheader('Content-Length',str(len(body)))
                connection.endheaders()
                response=connection.getresponse()
                status,refused=response.status,json.loads(response.read())
            finally: connection.close()
        else:
            status,refused=self.http.request('POST',PREFIX+'/worker-commits',prepared,token=WORKER_TOKEN)
        self.assertEqual(status,413,refused)
        status,after=self.http.request('GET',route,token=WORKER_TOKEN)
        self.assertEqual(status,200,after)
        self.assertEqual({k:v for k,v in after.items() if k!='service_clock'},
                         {k:v for k,v in before.items() if k!='service_clock'})
        lookup=PREFIX+'/intent-events/'+command['id']+'?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id']
        status,original=self.http.request('GET',lookup,token=HUMAN_TOKEN)
        self.assertEqual(status,200,original)
        self.assertNotIn('application_result',original)

    def test_independent_lower_derived_event_ceiling_refuses_atomically(self):
        self.lower_ceiling_refuses(Limits(max_manual_derived_event_bytes=65536))

    def test_independent_lower_worker_commit_ceiling_refuses_atomically(self):
        self.lower_ceiling_refuses(Limits(max_manual_worker_commit_bytes=65536),header_only=True)

    def test_last_three_seconds_and_exact_operating_horizon_keep_original_bounds(self):
        self.start()
        self.tick()
        horizon=self.config['manual_policy']['operating_not_after']
        self.now=horizon-2
        late=self.tick()
        self.assertEqual(late['room_control']['valid_until'],horizon)
        self.now+=1
        command=self.hold(late['room_control'],suffix='horizon-exact',duration=1)
        status,accepted=self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN)
        self.assertEqual(status,200,accepted)
        applied=self.tick()
        self.assertEqual(applied['room_control']['valid_until'],horizon)
        self.assertEqual(applied['room_control']['domains'][0]['hold']['hold_until'],horizon)
        self.now=horizon
        expired=self.tick()
        self.assertEqual(expired['room_control']['valid_until'],horizon)
        self.assertEqual(expired['room_control']['domains'][0]['mode'],'auto')
        self.assertIsNone(expired['room_output']['fixtures'][0]['domains'][0]['sample'])
        fresh=self.hold(expired['room_control'],suffix='after-horizon',duration=1)
        status,refused=self.http.request('POST',PREFIX+'/intent-events',fresh,token=HUMAN_TOKEN)
        self.assertEqual((status,refused['error']['code']),(409,'manual_horizon_exceeded'))
        self.assertEqual(self.http.request('POST',PREFIX+'/intent-events',command,token=HUMAN_TOKEN),(200,accepted))


if __name__=='__main__':
    unittest.main()
