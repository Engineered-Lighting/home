"""Actual source factory/Store/paired presentation; external camera/model authored."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_existing_api_assessment_factory as fixture
import live_capture_fixtures as capture
from live_detector_app import build_runtime, validate_config
from preview_client import PreviewPump
from registered_image import RegisteredImage

POLICY=dict(schema='home-apartment-viewing-policy-1',version=1,enabled=True,
    session_seconds=300,max_frames=8,max_retained_bytes=8388608)

class ApartmentViewingTests(unittest.TestCase):
    setUp=fixture.ExistingApiAssessmentFactoryTests.setUp
    def build(self,*,metric_factory=None):
        self.profiles=deepcopy(self.profiles)
        for profile in self.profiles.values():profile.update(profile_id='yolo26s-rtmpose-cpu-v1',model_version='detector-pose-v1')
        self.store.settings=replace(self.store.settings,max_session_seconds=300)
        self.store.transition(self.session['id'],'stopped')
        self.session,_=self.store.create_session('den','apartment-viewing','Authored viewing','viewing-start')
        self.store.transition(self.session['id'],'starting');self.store.transition(self.session['id'],'recording')
        self.session=self.store.get_session(self.session['id'])
        config=deepcopy(self.config);config.pop('existing_api_assessment');config['schema_version']=1
        config['apartment_viewing']=deepcopy(POLICY)
        config['pose_profiles']=deepcopy(self.profiles)
        self.assertEqual(validate_config(config),config)
        with patch('recorder.StreamCopySupervisor',side_effect=AssertionError('viewing must not start or construct an encoded recorder')):
            runtime=build_runtime(self.store,SimpleNamespace(camera_id='den'),config,experiment_id='apartment-viewing',
                profiles_override=self.profiles,transport_factory=lambda *_:lambda *_:None,lease=SimpleNamespace(ready=lambda:True),
                apartment_metric_source=metric_factory)
        self.addCleanup(runtime.close);self.seen=[]
        def execute(request):
            pixels,meta=RegisteredImage(self.store)(request['frame']);self.seen.append((request,meta,pixels.shape))
            profile=self.profiles[request['arm']]
            return dict(request_id=request['request_id'],engine_epoch=profile['engine_epoch'],artifact_sha256=profile['artifact_sha256'],
                terminal=True,state='completed',result=dict(schema_version=1,coordinate_space='source_pixels',frame_id=request['frame']['frame_id'],
                    objects=[],boxes=[],keypoints=[{'x':100,'y':100,'confidence':.9}],skeleton_edges=[]))
        runtime.executor=execute
        def dormant(pump):
            pump.thread=threading.Thread(target=lambda:None);pump.thread.start();pump.thread.join()
        with patch.object(PreviewPump,'start',dormant):out=runtime.start_session(self.session)
        self.assertEqual(out['inference'],'ready',out)
        self.assertFalse(out['encoded_recording']);self.assertEqual(out['recording_artifact'],'unavailable')
        self.now=capture.NOW+.2
        return runtime
    def consume(self,runtime,index=1):
        header=capture.header(self.jpeg,sequence=index)
        return runtime.preview_pumps[self.session['id']].consume(self.jpeg,header)
    def test_actual_composition_continues_past_eight_without_archiving_or_starting_another_pump(self):
        runtime=self.build()
        for i in range(1,13):
            self.now=capture.NOW+.2+i*.5
            runtime.preview_pumps[self.session['id']].consume(self.jpeg,
                capture.header(self.jpeg,sequence=i,observed_ns=int((capture.NOW+i*.5)*1e9)))
        self.assertEqual(len(runtime.preview_pumps),1)
        self.assertFalse(runtime.preview_pumps[self.session['id']].stopped.is_set())
        source=runtime.apartment_viewing_sessions[self.session['id']]
        self.assertEqual(source.status()['accepted_frames'],12)
        self.assertLessEqual(source.status()['resident_frames'],8)
        self.assertLessEqual(source.status()['resident_bytes'],8388608)
        self.assertEqual(self.store.list_media(self.session['id'])['items'],[])
        deadline=time.monotonic()+2
        while not self.seen and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(self.seen)
        self.assertTrue(all(meta['viewing_only'] for _,meta,_ in self.seen))
        self.assertFalse(source.status()['physical_permission'])
    def test_stop_clears_owned_media_and_late_results_remain_terminal_without_resurrection(self):
        runtime=self.build();result=self.consume(runtime)
        source=runtime.apartment_viewing_sessions[self.session['id']]
        runtime.stop_session(self.session)
        self.assertEqual(source.status()['resident_bytes'],0)
        with self.assertRaises(Exception):self.consume(runtime,2)
        from apartment_viewing_source import registered_media
        with self.assertRaises(Exception):registered_media(self.store,self.session['id'],result['media_ids'][0],self.session['generation'])
    def test_default_placement_keeps_existing_finite_archive_policy(self):
        from test_bundle_rgb_input import POLICY as archive
        self.assertEqual(archive['max_captures'],8);self.assertEqual(archive['max_retained_bytes'],41943040)
        from managed_detector_app import enabled_recipes
        config=deepcopy(self.config);config['pose_profiles']=deepcopy(self.profiles)
        self.assertNotIn('apartment-viewing',enabled_recipes(config))
        config['apartment_viewing']=deepcopy(POLICY)
        self.assertIn('apartment-viewing',enabled_recipes(config))
    def wait_output(self,runtime):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            rows=self.store.list_events(self.session['id'],0,100)
            found=[e for e in rows if e['kind']=='perception.output']
            if found:return found[0]
            time.sleep(.01)
        self.fail('actual paired output did not settle')
    def test_real_authenticated_descriptor_range_and_bytes_reject_wrong_scope_expiry_and_stop(self):
        from fastapi.testclient import TestClient
        from app import create_app,PREFIX
        token='authored-viewer-'+('a'*40)
        self.store.settings=replace(self.store.settings,token_grants=({'subject':'viewer','site_id':'victoria',
            'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'capabilities':['read']},))
        runtime=self.build();out=self.consume(runtime);mid=out['media_ids'][0];generation=self.session['generation']
        headers={'Authorization':'Bearer '+token,'X-Home-Lab-Site':'victoria'}
        with TestClient(create_app(self.store.settings,runtime,self.store)) as client:
            path=PREFIX+'/sessions/'+self.session['id']+'/media/'+mid
            self.assertEqual(client.get(path+'/descriptor',params={'generation':generation}).status_code,401)
            bad={**headers,'X-Home-Lab-Site':'echo'}
            self.assertEqual(client.get(path+'/descriptor',params={'generation':generation},headers=bad).status_code,403)
            descriptor=client.get(path+'/descriptor',params={'generation':generation},headers=headers)
            self.assertEqual(descriptor.status_code,200,descriptor.text)
            self.assertTrue(descriptor.json()['viewing_only'])
            image=client.get(path,params={'generation':generation},headers=headers)
            self.assertEqual(image.status_code,200,image.text);self.assertEqual(image.content,self.jpeg)
            # The actual public browser media adapter consumes this exact ASGI
            # descriptor and these exact authenticated bytes, not a lookalike.
            node="""const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const x=JSON.parse(fs.readFileSync(0,'utf8')),raw=Uint8Array.from(Buffer.from(x.bytes,'base64'));
const scope={siteId:'victoria',sessionId:x.item.session_id,generation:x.item.generation};
const window={HomePerceptionLabTransport:{request:async()=>x.item,requestMedia:async q=>({data:raw.slice(q.offset,q.offset+q.length)})}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{window,Uint8Array,AbortController,DOMException,setTimeout,clearTimeout});
window.HomePerceptionLabMedia.readFrame(x.item,scope).then(v=>{assert.equal(v.bytes.length,raw.length);assert.equal(v.type,'image/jpeg');process.stdout.write('actual_media_adapter_ok')}).catch(e=>{process.stderr.write(String(e));process.exitCode=1});"""
            import base64
            root=Path(__file__).resolve().parents[3]
            browser=subprocess.run(['node','-e',node,str(root/'app/src/home-perception-lab-media.js')],
                input=json.dumps({'item':descriptor.json(),'bytes':base64.b64encode(image.content).decode()}),
                capture_output=True,text=True,timeout=5)
            self.assertEqual(browser.returncode,0,browser.stderr)
            part=client.get(path,params={'generation':generation},headers={**headers,'Range':'bytes=0-31'})
            self.assertEqual(part.status_code,206);self.assertEqual(part.content,self.jpeg[:32])
            self.assertEqual(client.get(path,params={'generation':generation+1},headers=headers).status_code,410)
            self.now+=4
            self.assertEqual(client.get(path,params={'generation':generation},headers=headers).status_code,410)
            self.assertEqual(runtime.apartment_viewing_sessions[self.session['id']].status()['resident_bytes'],0)
    def test_actual_current_pixel_output_cannot_enter_operational_spatial_consumer(self):
        from spatial_consumer import SpatialConsumer
        runtime=self.build();self.consume(runtime);event=self.wait_output(runtime)
        self.assertTrue(event['payload']['current']);self.assertTrue(event['payload']['viewing_only'])
        self.assertTrue(event['payload']['arm_snapshot']['layers']['skeleton']['available'])
        consumer=object.__new__(SpatialConsumer);consumer.store=self.store;consumer.association_revision='authored-association'
        with self.assertRaisesRegex(ValueError,'viewing-only specialist evidence'):
            consumer.ingest(self.session['id'],[dict(source_event_id=event['id'],association_revision='authored-association',
                track_id='frame-local-object',localization='floor_projection')],generation=runtime.runners[self.session['id']].generation)
        self.assertFalse(any(e['kind']=='room.observation' for e in self.store.list_events(self.session['id'],0,100)))
    def test_descriptor_then_new_completed_output_keeps_original_unexpired_range_readable(self):
        from fastapi.testclient import TestClient
        from app import create_app,PREFIX
        token='authored-viewer-'+('b'*40)
        self.store.settings=replace(self.store.settings,token_grants=({'subject':'viewer','site_id':'victoria',
            'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'capabilities':['read']},))
        runtime=self.build();first=self.consume(runtime);self.wait_output(runtime)
        headers={'Authorization':'Bearer '+token,'X-Home-Lab-Site':'victoria'}
        with TestClient(create_app(self.store.settings,runtime,self.store)) as client:
            path=PREFIX+'/sessions/'+self.session['id']+'/media/'+first['media_ids'][0]
            params={'generation':self.session['generation']}
            before=client.get(path+'/descriptor',params=params,headers=headers)
            self.assertEqual(before.status_code,200,before.text)
            self.now+=1.1
            second=runtime.preview_pumps[self.session['id']].consume(self.jpeg,
                capture.header(self.jpeg,sequence=2,observed_ns=int((capture.NOW+1.1)*1e9)))
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                rows=self.store.list_events(self.session['id'],0,100)
                outputs=[e for e in rows if e['kind']=='perception.output' and e['payload']['media_ids']==second['media_ids']]
                if len(outputs)==2:break
                time.sleep(.005)
            self.assertEqual(len(outputs),2,'both actual worker completions replace the original output')
            reply=client.get(path,params=params,headers={**headers,'Range':'bytes=0-31'})
            self.assertEqual(reply.status_code,206,reply.text)
            self.assertEqual(reply.content,self.jpeg[:32])
            after=client.get(path+'/descriptor',params=params,headers=headers)
            self.assertEqual(after.json()['metadata'],before.json()['metadata'],'original freshness must not renew')
            self.now+=4
            self.assertEqual(client.get(path,params=params,headers=headers).status_code,410)
    def test_generation_epoch_and_300_second_bound_close_and_withdraw_without_renewal(self):
        runtime=self.build();self.consume(runtime);source=runtime.apartment_viewing_sessions[self.session['id']]
        with patch('apartment_viewing_source.time.monotonic',return_value=source.created_mono+301):
            with self.assertRaisesRegex(ValueError,'duration exhausted'):source.current()
        self.assertTrue(source.expiry_dispatch_done.wait(2))
        self.assertEqual(source.status()['resident_bytes'],0);self.assertIsNone(source.timer)
        self.assertTrue(runtime.preview_pumps[self.session['id']].stopped.is_set())
        self.assertEqual(self.store.get_session(self.session['id'])['state'],'stopping')
    def test_zero_capture_failures_still_have_a_fixed_five_minute_timer(self):
        runtime=self.build();source=runtime.apartment_viewing_sessions[self.session['id']]
        timer=source.timer;self.assertIsNotNone(timer);self.assertLessEqual(timer.interval,300)
        with self.assertRaises(ValueError):source.ingest(self.session['id'],b'invalid','invalid')
        self.assertIs(source.timer,timer);self.assertEqual(source.status()['accepted_frames'],0)
        # Fire the actual scheduled expiry callback without a five-minute sleep.
        timer.function(*timer.args,**timer.kwargs)
        self.assertTrue(source.expiry_dispatch_done.wait(2))
        self.assertEqual(self.store.get_session(self.session['id'])['state'],'stopping')
        self.assertTrue(runtime.preview_pumps[self.session['id']].stopped.is_set())
        self.assertIsNone(source.timer)
    def test_generation_change_and_capture_epoch_change_cannot_reuse_prior_pixels(self):
        runtime=self.build();self.consume(runtime);source=runtime.apartment_viewing_sessions[self.session['id']]
        runtime.runners[self.session['id']].generation+=1
        with self.assertRaisesRegex(ValueError,'scope changed'):self.consume(runtime,2)
        self.assertEqual(source.status()['resident_frames'],0)
        self.assertNotIn(self.session['id'],getattr(self.store,'_apartment_viewing_sources',{}))
        self.assertTrue(source.expiry_dispatch_done.wait(2),'scope retirement must stop the owned analysis')
        self.assertTrue(runtime.runners[self.session['id']].closed)
    def test_capture_epoch_change_with_same_camera_closes_instead_of_reinterpreting_pixels(self):
        runtime=self.build();self.consume(runtime);source=runtime.apartment_viewing_sessions[self.session['id']]
        with self.assertRaisesRegex(ValueError,'capture epoch changed'):
            runtime.preview_pumps[self.session['id']].consume(self.jpeg,capture.header(self.jpeg,sequence=2,epoch='different-camera-epoch'))
        self.assertEqual(source.status()['resident_bytes'],0)
        self.assertTrue(runtime.preview_pumps[self.session['id']].stopped.is_set())
        self.assertTrue(source.expiry_dispatch_done.wait(2),'epoch retirement must stop the owned analysis')
        self.assertTrue(runtime.runners[self.session['id']].closed)
    def test_duplicate_source_identity_preserves_original_availability_and_has_no_new_lease(self):
        from apartment_viewing_source import registered_media
        runtime=self.build();first=self.consume(runtime);source=runtime.apartment_viewing_sessions[self.session['id']]
        item=registered_media(self.store,self.session['id'],first['media_ids'][0])
        self.now+=.2;again=self.consume(runtime)
        self.assertEqual(first['media_ids'],again['media_ids']);self.assertEqual(source.status()['accepted_frames'],1)
        saved=registered_media(self.store,self.session['id'],first['media_ids'][0])
        self.assertEqual(saved['metadata'],item['metadata'])
    def test_delayed_actual_worker_completion_after_stop_settles_and_retires_every_ram_reference(self):
        runtime=self.build();entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        original=runtime.executor
        def delayed(request):
            value=original(request);entered.set();release.wait(2);return value
        runtime.executor=delayed
        runtime.runners[self.session['id']].executor=delayed
        self.consume(runtime);self.assertTrue(entered.wait(2))
        source=runtime.apartment_viewing_sessions[self.session['id']]
        runtime.stop_session(self.session);release.set()
        deadline=time.monotonic()+2
        while runtime.runners[self.session['id']].futures and time.monotonic()<deadline:time.sleep(.01)
        with self.store.lock:
            states=[row[0] for row in self.store.db.execute('SELECT state FROM paired_requests WHERE session_id=?',(self.session['id'],)).fetchall()]
        self.assertTrue(states);self.assertTrue(all(s=='completed' for s in states),states)
        self.assertEqual(source.status()['resident_frames'],0);self.assertIsNone(source.timer)
        self.assertNotIn(self.session['id'],getattr(self.store,'_apartment_viewing_sources',{}))
    def test_only_original_causal_schema_is_accepted_and_viewing_cannot_switch_into_geometry_recipe(self):
        runtime=self.build();source=runtime.apartment_viewing_sessions[self.session['id']]
        with self.assertRaises(ValueError):
            runtime.preview_pumps[self.session['id']].consume(self.jpeg,capture.header(self.jpeg,schema=2))
        self.assertEqual(source.status()['accepted_frames'],0)
        out=runtime.switch(self.session,{'experiment_id':'placement-best','request_id':'authored-switch'})
        self.assertEqual(out['state'],'blocked');self.assertEqual(out['reason'],'viewing_recipe_requires_new_session')
        ordinary={**self.session,'experiment_id':'placement-best'}
        out=runtime.switch(ordinary,{'experiment_id':'apartment-viewing','request_id':'authored-switch-in'})
        self.assertEqual(out['state'],'blocked');self.assertEqual(out['reason'],'viewing_recipe_requires_new_session')
    def test_actual_managed_capabilities_advertise_ready_viewing_only_when_trusted_policy_enabled(self):
        from managed_detector_app import create_managed_runtime
        from controller import Controller
        config=deepcopy(self.config);config.pop('existing_api_assessment');config['schema_version']=1
        profiles=deepcopy(self.profiles)
        for p in profiles.values():p.update(profile_id='yolo26s-rtmpose-cpu-v1',model_version='detector-pose-v1')
        config.update(pose_profiles=profiles,apartment_viewing=deepcopy(POLICY))
        clients={arm:SimpleNamespace(probe=lambda:[],close=lambda:None) for arm in ('a','b')}
        pair=create_managed_runtime(self.store,SimpleNamespace(camera_id='den'),config,clients)
        self.addCleanup(pair.close)
        caps=Controller(self.store,pair).capabilities()
        recipe=next(r for r in caps['experiments'] if r['id']=='apartment-viewing')
        self.assertEqual(recipe['status'],'ready',recipe)
        self.assertEqual(recipe['readiness']['state'],'ready')
        self.assertEqual(caps['apartment_viewing']['session_seconds'],300)
        self.assertFalse(caps['apartment_viewing']['automatic_renewal'])
    def test_actual_delayed_worker_expiry_never_inverts_runtime_then_runner_lock_order(self):
        runtime=self.build();runner=runtime.runners[self.session['id']]
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        original=runner.executor
        def delayed(request):
            result=original(request);entered.set();release.wait(2);return result
        runner.executor=delayed;self.consume(runtime);self.assertTrue(entered.wait(2))
        source=runtime.apartment_viewing_sessions[self.session['id']]
        source.created_mono-=301
        with runtime.lock:
            release.set()
            # Worker must retire synchronously and finish reconcile without
            # waiting on the runtime lock this real caller currently owns.
            deadline=time.monotonic()+1
            while not source.closed and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(source.closed)
            got=runner.lock.acquire(timeout=.5)
            self.assertTrue(got,'worker held runner.lock while waiting for runtime.lock')
            if got:runner.lock.release()
            self.assertEqual(source.status()['resident_bytes'],0)
            self.assertTrue(source.expiry_dispatch_started)
        self.assertTrue(source.expiry_dispatch_done.wait(2))
        deadline=time.monotonic()+2
        while runner.futures and time.monotonic()<deadline:time.sleep(.01)
        with self.store.lock:
            states=[row[0] for row in self.store.db.execute('SELECT state FROM paired_requests WHERE session_id=?',(self.session['id'],)).fetchall()]
        self.assertTrue(states);self.assertTrue(all(state=='completed' for state in states),states)
        self.assertFalse(source.status()['expiry_stop_pending'])


if __name__=='__main__':unittest.main()
