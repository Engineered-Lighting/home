"""Development preview timing (owner decision, 2026-10-19) through the actual
protected bootstrap, RAM source/Store/PairedRunner, desk preview and ASGI.

Late originals become named warnings only for the arm-B metric preview, only
when the v2 private policy says 'development'. Strict stays the default and
keeps the 1726 packets. External camera, model and alignment are authored.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import threading
import time
import unittest
from unittest.mock import patch

import test_apartment_viewing_metric_source as source_fixture
import test_apartment_viewing_metric_successor as successor
import test_apartment_desk_preview as preview_fixture
import apartment_viewing_metric
import apartment_viewing_metric_bootstrap as bootstrap

def sha(raw):return hashlib.sha256(raw).hexdigest()
LATE_CHECKS={'registration_expired','dispatch_late','lease_retired','finalize_expired','media_expired'}


class DevelopmentTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_fixture.MetricSourceTests.setUpClass()
        cls.addClassCleanup(source_fixture.MetricSourceTests.doClassCleanups)

    def setUp(self):
        self.f=source_fixture.MetricSourceTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def build(self,*,timing='development',people='one',late=None,block=None,settle=True,uncertain_anchor=False,verified_epoch=None,
              arm_b_state=None):
        """late: None, 'capture' (proof expired before prepare) or 'registration'."""
        f=self.f
        fixture,_,originals,config=successor.configuration(f,timing=timing,verified_epoch=verified_epoch)
        base=f.factory();self.candidate=preview_fixture.configured_candidate(fixture.pack)
        raw=preview_fixture.encoded(self.candidate);path=bootstrap.PRIVATE_PATH+'/authored-desk-preview/candidate.json'
        policy=dict(schema='home-apartment-desk-preview-source-1',version=1,enabled=True,candidate=dict(path=path,sha256=sha(raw)))
        by_path={config['files'][role]['path']:value for role,value in originals.items()}
        by_path.update({bootstrap.DESK_PREVIEW_POLICY_PATH:preview_fixture.encoded(policy),path:raw})
        with patch.object(bootstrap.os,'lstat',return_value=object()),\
                patch.object(bootstrap,'_read_private',side_effect=lambda p:by_path[p]):
            configured=bootstrap.load_factory(config,client=base.client)
        configured.clock=base.clock
        self.token='authored-development-'+'d'*40
        f.store.settings=replace(f.store.settings,token_grants=({'subject':'viewer','site_id':'victoria',
            'token_sha256':sha(self.token.encode()),'capabilities':['read']},))
        runtime=f.build(metric_factory=configured);source=runtime.apartment_viewing_sessions[f.session['id']]
        event=preview_fixture.add_shoulders(f.event)
        if people=='none':event['payload']['result'].update(objects=[],pose_instances=[])
        original=runtime.executor
        def execute(request):
            if block is not None and request['arm']=='b':block.wait(5)
            value=original(request);value['result']=deepcopy(event['payload']['result'])
            value['result']['frame_id']=request['frame']['frame_id']
            if arm_b_state is not None and request['arm']=='b':value.update(state=arm_b_state,result={'reason':'authored_'+arm_b_state})
            return value
        runtime.executor=runtime.runners[f.session['id']].executor=execute
        prepare=apartment_viewing_metric.prepare_metric_frame
        def delayed(**kwargs):
            if late=='capture':
                f.now+=3  # original proof expires during preparation, after actual receipt
                kwargs['now_s']=f.now
            lease=prepare(**kwargs)
            if late=='registration':f.now=lease.descriptor['retirement_deadline']+.05
            return lease
        import bundle_rgb_input
        anchored=bundle_rgb_input.anchor_metadata
        def uncertain(receipt,**kwargs):  # only the anchor stage sees a clock bound above 1 s
            return anchored({**receipt,'uncertainty_seconds':1.2},**kwargs)
        with patch.object(apartment_viewing_metric,'prepare_metric_frame',side_effect=delayed),                patch.object(bundle_rgb_input,'anchor_metadata',side_effect=uncertain if uncertain_anchor else anchored):
            f.anchors(runtime)
        if settle:
            deadline=time.monotonic()+3
            while time.monotonic()<deadline and not self.outputs('b'):time.sleep(.005)
            self.assertTrue(self.outputs('b'),'actual arm-B output did not settle')
            source.metric.finalize()
            tracker=source.metric.projection
            if tracker is not None and tracker.received is not None:
                # Pin the 2 s display receipt so a loaded machine cannot expire it mid-test.
                received=tracker.received;tracker.monotonic=lambda:received+.5
        return runtime,source,configured

    def outputs(self,arm):
        return [e for e in self.f.store.list_events(self.f.session['id'],0,100)
                if e['kind']=='perception.output' and e['payload']['arm']==arm]

    def live(self,runtime):return runtime.live(self.f.session)

    def asgi_live(self,runtime):
        # The authenticated route; its app shutdown ends the session, so last.
        from app import create_app,PREFIX
        from fastapi.testclient import TestClient
        headers={'Authorization':'Bearer '+self.token,'X-Home-Lab-Site':'victoria'}
        with TestClient(create_app(self.f.store.settings,runtime,self.f.store)) as client:
            response=client.get(PREFIX+'/sessions/'+self.f.session['id']+'/live',headers=headers)
        self.assertEqual(response.status_code,200);return response.json()

    def test_late_original_is_a_warned_supporting_preview_only_under_development(self):
        runtime,source,configured=self.build(late='registration')
        self.assertEqual(configured.capability['timing_policy'],'development')
        live=self.live(runtime);arm=live['arms']['B'];packet=arm['virtual_target_preview']
        self.assertTrue(packet['available'],packet)
        self.assertEqual((packet['evidence'],packet['evidence_event_id']),('supporting',self.outputs('b')[-1]['id']))
        self.assertEqual(packet['timing']['policy'],'development')
        checks={item['check'] for item in packet['timing']['warnings']}
        self.assertTrue(LATE_CHECKS<=checks,checks)
        self.assertTrue(all(set(item)=={'check','at','deadline'} for item in packet['timing']['warnings']))
        self.assertEqual(len(checks),len(packet['timing']['warnings']),'one entry per check')
        self.assertEqual(packet['timing'],arm['approximate_room_pose']['timing'])
        record=source.entries[packet['frame']['media_id']]['record']
        self.assertEqual(packet['frame']['original_times']['valid_until'],record['metadata']['valid_until'],'original times are never rewritten')
        self.assertLess(record['metadata']['valid_until'],self.f.now)
        self.assertEqual(live['apartment_viewing']['metric']['registered_frame']['event_id'],packet['evidence_event_id'])
        self.assertEqual(live['apartment_viewing']['metric']['timing_policy'],'development')
        requests={arm:json.loads(raw) for arm,raw in self.f.store.db.execute('SELECT arm,payload FROM paired_requests').fetchall()}
        original=record['metadata']['valid_until']
        self.assertEqual(requests['b']['preview_timing'],{'policy':'development','original_valid_until':original,'hold_s':10.})
        self.assertEqual(requests['b']['frame']['valid_until'],max(original,record['metadata']['available_at']+10.))
        self.assertNotIn('preview_timing',requests['a']);self.assertEqual(requests['a']['frame']['valid_until'],original)
        self.assertEqual(self.outputs('b')[-1]['payload']['valid_until'],packet['observation']['valid_until'])
        from apartment_viewing_source import registered_media
        from store import LabError
        with self.assertRaises(LabError):registered_media(self.f.store,self.f.session['id'],record['id'],self.f.session['generation'],fresh=True)
        from app import create_app,PREFIX
        from fastapi.testclient import TestClient
        headers={'Authorization':'Bearer '+self.token,'X-Home-Lab-Site':'victoria'}
        base=PREFIX+'/sessions/'+self.f.session['id']
        with TestClient(create_app(self.f.store.settings,runtime,self.f.store)) as client:
            served=client.get(base+'/live',headers=headers).json()['arms']['B']['virtual_target_preview']
            params={'generation':self.f.session['generation']}
            descriptor=client.get(base+'/media/'+record['id']+'/descriptor',params=params,headers=headers)
            pixels=client.get(base+'/media/'+record['id'],params=params,headers=headers)
        self.assertEqual((served['available'],served['evidence'],served['timing']['policy']),(True,'supporting','development'))
        self.assertEqual((descriptor.status_code,pixels.status_code),(200,200),'Home routes serve the retained PNG')
        retention=descriptor.json()['retention']
        self.assertEqual((retention['policy'],retention['original_valid_until']),('development',original))
        self.assertGreater(retention['retained_until'],self.f.now)
        self.assertEqual(descriptor.json()['metadata']['valid_until'],original)
        self.assertEqual(sha(pixels.content),descriptor.json()['sha256'])
        runtime.stop_session(self.f.session);self.assertEqual(source.status()['resident_bytes'],0)

    def test_the_same_late_original_still_refuses_under_strict_with_the_1726_packet(self):
        runtime,source,configured=self.build(timing='strict',late='registration',settle=False)
        self.assertEqual(configured.capability['timing_policy'],'strict')
        self.assertEqual(source.metric.reason,'metric_registration_retired')
        arm=self.live(runtime)['arms']['B']
        self.assertNotIn('evidence',arm.get('virtual_target_preview',{}))
        self.assertFalse(self.f.store.db.execute("SELECT 1 FROM paired_requests WHERE payload LIKE '%preview_timing%'").fetchone())
        self.assertNotIn('timing_policy',self.live(runtime)['apartment_viewing']['metric'])
        runtime.stop_session(self.f.session);self.assertEqual(source.status()['resident_bytes'],0);runtime.close()
        self.setUp();runtime,source,_=self.build(timing='strict')
        arm=self.live(runtime)['arms']['B'];packet=arm['virtual_target_preview']
        self.assertTrue(packet['available'],packet)
        self.assertNotIn('evidence',packet);self.assertNotIn('policy',packet['timing'])
        self.assertNotIn('policy',arm['approximate_room_pose']['timing'])
        runtime.stop_session(self.f.session)

    def test_capture_time_proof_expiry_is_a_warning_not_a_jpeg_fallback(self):
        runtime,source,_=self.build(late='capture')
        packet=self.live(runtime)['arms']['B']['virtual_target_preview']
        self.assertTrue(packet['available'],packet)
        self.assertIn('proof_expired',{item['check'] for item in packet['timing']['warnings']})
        self.assertEqual(self.f.reads.count('/frame.npz'),2)
        runtime.stop_session(self.f.session)

    def test_anchor_stage_clock_refusal_is_a_warning_only_under_development(self):
        runtime,source,_=self.build(uncertain_anchor=True)
        packet=self.live(runtime)['arms']['B']['virtual_target_preview']
        self.assertTrue(packet['available'],packet)
        self.assertIn('clock_uncertainty',{item['check'] for item in packet['timing']['warnings']})
        record=source.entries[packet['frame']['media_id']]['record']
        self.assertFalse(record['metadata']['reading_timing_qualified'])
        self.assertEqual(self.outputs('b')[-1]['payload']['reading_binding']['status'],'unavailable')
        runtime.stop_session(self.f.session);runtime.close()
        self.setUp()
        with self.assertRaisesRegex(ValueError,'bundled_anchor_clock_refused'):
            self.build(timing='strict',settle=False,uncertain_anchor=True)
        self.assertEqual(self.f.reads.count('/frame.npz'),0,'strict: refused at the anchor, before capture')

    def test_evidence_classifies_contrary_integrity_and_newer_outputs(self):
        runtime,source,_=self.build(people='none',late='registration')
        packet=self.live(runtime)['arms']['B']['virtual_target_preview']
        self.assertFalse(packet['available'])
        self.assertEqual((packet['reason'],packet['evidence']),('approximate_observed_instance_unavailable','contrary'))
        self.assertEqual(packet['evidence_event_id'],self.outputs('b')[-1]['id'])
        self.assertTrue(packet['timing']['warnings'])
        self.assertEqual(packet['timing']['warnings'],source.metric._warnings(source.metric.latest_png['media_id']),
            'a refusal keeps its own frame warnings across polls')
        runtime.stop_session(self.f.session);runtime.close()
        self.setUp()
        runtime,source,_=self.build(verified_epoch='authored-other-camera-session')
        packet=self.live(runtime)['arms']['B']['virtual_target_preview']
        self.assertEqual((packet['reason'],packet['evidence']),('approximate_camera_epoch_unverified','integrity'))
        runtime.stop_session(self.f.session);runtime.close()
        # Owner decision: development does not run the per-frame mount check at all.
        self.setUp()
        import spatial_validation
        with patch.object(spatial_validation,'check_mount',side_effect=AssertionError('development must not run check_mount')):
            runtime,source,_=self.build()
        arm=self.live(runtime)['arms']['B'];packet=arm['virtual_target_preview']
        self.assertTrue(packet['available'],packet);self.assertEqual(packet['evidence'],'supporting')
        detection=arm['approximate_room_pose']['approximate_pose']['registration']['mount_movement_detection']
        self.assertEqual((detection['status'],detection['result']),('mount_check_skipped_development',None))
        self.assertNotIn('mount',' '.join(w['check'] for w in packet['timing']['warnings']),'no per-frame mount warning')
        runtime.stop_session(self.f.session);runtime.close()
        for people,expected in ((1,'neutral'),(0,'contrary')):
            with self.subTest(newer_people=people):
                self.setUp();runtime,source,_=self.build()
                first=self.live(runtime)['arms']['B']['virtual_target_preview'];self.assertEqual(first['evidence'],'supporting')
                # A newer actual arm-B output (JPEG fallback) without a metric packet.
                source.metric.factory.clock.begin=lambda _:(_ for _ in ()).throw(ValueError('receiver_clock_anchor_changed'))
                if people==0:
                    executor=runtime.runners[self.f.session['id']].executor
                    def empty(request):
                        value=executor(request);value['result'].update(objects=[],boxes=[],pose_instances=[]);return value
                    runtime.executor=runtime.runners[self.f.session['id']].executor=empty
                self.f.now+=1;capture=source_fixture.viewing.capture
                header=capture.header(self.f.jpeg,sequence=17,observed_ns=int(self.f.now*1e9))
                runtime.preview_pumps[self.f.session['id']].consume(self.f.jpeg,header)
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and len(self.outputs('b'))<2:time.sleep(.005)
                packet=self.live(runtime)['arms']['B']['virtual_target_preview']
                self.assertEqual(packet['available'],people==1,'only a contrary newer output withdraws')
                self.assertEqual((packet['evidence'],packet['evidence_event_id']),(expected,self.outputs('b')[-1]['id']))
                if people==1:
                    self.assertIn('newer_observation_not_projected',{w['check'] for w in packet['timing']['warnings']})
                    self.assertEqual(packet['observation'],first['observation'],'the held preview keeps its own observation')
                else:self.assertEqual(packet['reason'],'desk_preview_observation_changed')
                runtime.stop_session(self.f.session);runtime.close()

    def test_published_png_stays_fetchable_twelve_seconds_then_retires(self):
        runtime,source,_=self.build();published=self.f.now
        packet=self.live(runtime)['arms']['B']['virtual_target_preview'];mid=packet['frame']['media_id']
        from apartment_viewing_source import viewing_bytes
        from store import LabError
        generation=self.f.session['generation'];deadline=source.entries[mid]['record']['metadata']['valid_until']
        self.f.now=deadline+1
        with self.assertRaises(LabError):viewing_bytes(self.f.store,self.f.session['id'],mid,generation)  # e.g. Qwen: strict
        self.assertTrue(viewing_bytes(self.f.store,self.f.session['id'],mid,generation,retained=True))
        self.assertIn('media_expired',{w['check'] for w in source.metric.frame_warnings[mid]})
        self.assertIsNotNone(source.metric.status()['registered_frame'])
        self.f.now=published+12.1
        with self.assertRaises(LabError):viewing_bytes(self.f.store,self.f.session['id'],mid,generation,retained=True)
        source.prune();self.assertIsNone(source.metric.status()['registered_frame'])
        runtime.stop_session(self.f.session);runtime.close()
        self.setUp();runtime,source,_=self.build(timing='strict')
        mid=source.metric.latest_png['media_id'];self.f.now=source.entries[mid]['record']['metadata']['valid_until']
        with self.assertRaises(LabError):viewing_bytes(self.f.store,self.f.session['id'],mid,self.f.session['generation'],retained=True)
        runtime.stop_session(self.f.session)

    def test_preview_outlives_the_two_second_receipt_inside_its_ten_second_window(self):
        runtime,source,_=self.build();published=self.f.now
        tracker=source.metric.projection;received=tracker.received
        tracker.monotonic=lambda:received+2.5
        arm=self.live(runtime)['arms']['B'];packet=arm['virtual_target_preview']
        self.assertFalse(arm['approximate_room_pose']['renderable'],'approximate_room_pose keeps its strict 2 s display hold')
        self.assertTrue(packet['available'],packet)
        self.assertIn('approximate_receipt_hold_elapsed',{w['check'] for w in packet['timing']['warnings']})
        self.f.now=published+9.9;self.assertTrue(self.live(runtime)['arms']['B']['virtual_target_preview']['available'])
        self.f.now=published+10
        packet=self.live(runtime)['arms']['B']['virtual_target_preview']
        self.assertEqual((packet['available'],packet['reason'],packet['evidence']),(False,'desk_preview_hold_elapsed','supporting'))
        runtime.stop_session(self.f.session)

    def test_eviction_keeps_pinned_pngs_and_drops_other_expired_retained_frames_first(self):
        runtime,source,_=self.build();pinned=set(source.pinned.values());self.assertTrue(pinned)
        now=self.f.now;added=[]
        for index in range(7):
            added.append(source.add_media(self.f.session['id'],b'j'*1024,'image/jpeg',
                {'valid_until':now+30,'frame_id':'authored-'+str(index)},'authored-'+str(index))['id'])
        with source.lock:source.entries[added[0]]['retain_until']=now+30;source.entries[added[0]]['record']['metadata']['valid_until']=now+.5
        self.f.now=now+1
        source.add_media(self.f.session['id'],b'j'*1024,'image/jpeg',{'valid_until':now+30,'frame_id':'authored-x'},'authored-x')
        self.assertNotIn(added[0],source.entries,'expired, retained and unpinned goes first')
        self.assertTrue(pinned<=set(source.entries),'the published and supporting PNGs survive')
        with self.assertRaisesRegex(ValueError,'viewing RAM budget exhausted'):
            source.add_media(self.f.session['id'],b'j'*1024,'image/jpeg',{'valid_until':now+30,'frame_id':'authored-y'},'authored-y')
        self.assertTrue(pinned<=set(source.entries));self.assertEqual(len(source.entries),8)
        runtime.stop_session(self.f.session)

    def test_a_waiting_lease_is_held_ten_seconds_not_retired_at_its_original_deadline(self):
        release=threading.Event();self.addCleanup(release.set)
        runtime,source,_=self.build(block=release,settle=False)
        lease=source.metric.lease;self.assertIsNotNone(lease)
        self.f.now=lease.descriptor['retirement_deadline']+1;source.prune()
        self.assertIs(source.metric.lease,lease,'development: not retired at the original deadline')
        self.f.now=source.metric.lease_until+.01;source.prune()
        self.assertIsNone(source.metric.lease);self.assertEqual(source.metric.reason,'metric_original_expired')
        release.set();runtime.stop_session(self.f.session);self.assertEqual(source.status()['resident_bytes'],0)

    def test_a_terminal_arm_b_outcome_frees_the_single_lease_at_once_under_development(self):
        for timing,state in (('development','cancelled'),('development','rejected'),('strict','cancelled')):
            with self.subTest(timing=timing,state=state):
                self.setUp();runtime,source,_=self.build(timing=timing,arm_b_state=state,settle=False)
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and not self.f.store.db.execute(
                        "SELECT 1 FROM paired_requests WHERE arm='b' AND state=?",(state,)).fetchone():time.sleep(.005)
                lease=source.metric.lease;self.assertIsNotNone(lease);source.metric.finalize()
                if timing=='development':
                    self.assertIsNone(source.metric.lease,'no arm-B output can arrive: capture is free again')
                    self.assertEqual(source.metric.reason,'metric_arm_b_'+state)
                else:
                    self.assertIs(source.metric.lease,lease,'strict keeps the 1726 lease rule')
                runtime.stop_session(self.f.session);self.assertEqual(source.status()['resident_bytes'],0);runtime.close()


if __name__=='__main__':unittest.main()
