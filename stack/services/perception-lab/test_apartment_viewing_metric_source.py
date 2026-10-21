"""Actual single-owner source/Store/paired input, authored camera and models."""
from copy import deepcopy
from dataclasses import replace
import base64,hashlib,json
import threading,time,unittest
from unittest.mock import patch
import test_apartment_viewing_source as viewing
import test_apartment_viewing_metric as metric
import test_apartment_view_alignment as alignment_fixture
from apartment_viewing_metric_source import MetricInputFactory

class MetricSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):metric.ViewingMetricTests.setUpClass.__func__(cls)
    setUp=viewing.ApartmentViewingTests.setUp
    build=viewing.ApartmentViewingTests.build
    def factory(self,*,block=None):
        from chrony_preview_client import _CAPTURE_LOCK
        owner=self
        class Client:
            def _read(_,path):
                owner.reads.append(path)
                if block is not None and path=='/frame.npz':block[0].set();block[1].wait(2)
                if path=='/frame.jpg':return owner.originals['anchor_b'],owner.headers['b']
                return owner.originals['npz' if path=='/frame.npz' else 'calibration'],''
        class Clock:
            def begin(_,receipt):
                owner.assertTrue(_CAPTURE_LOCK.locked(),'same commissioned capture turn must be held')
                return 'authored-qualified-clock-start'
            def finish(_,value):
                owner.assertTrue(_CAPTURE_LOCK.locked(),'clock proof must finish before another commissioned capture publishes a new clock')
                return deepcopy(owner.proof)
        self.reads=[]
        return MetricInputFactory(Client(),clock=Clock())
    def approximate_configuration(self):
        alignment_fixture.ViewAlignmentTests.setUpClass();self.addCleanup(alignment_fixture.ViewAlignmentTests.doClassCleanups)
        fixture=alignment_fixture.ViewAlignmentTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        import apartment_viewing_metric_bootstrap as bootstrap
        originals={'alignment':alignment_fixture.encoded(fixture.doc),'review':alignment_fixture.encoded(fixture.review),
            'source_candidate':alignment_fixture.encoded(fixture.candidate)}
        config={'schema':bootstrap.SCHEMA,'version':1,'enabled':True,'pack':fixture.pack,'mount_revision':fixture.doc['mount']['revision'],
            'files':{role:{'path':bootstrap.PRIVATE_PATH+'/authored-metric/'+role+'.json','sha256':alignment_fixture.sha(raw)} for role,raw in originals.items()}}
        return fixture,bootstrap,originals,config
    def anchors(self,runtime):
        self.now=1800000000.59
        pump=runtime.preview_pumps[self.session['id']]
        first=pump.consume(self.originals['anchor_a'],self.headers['a'])
        second=pump.consume(self.originals['anchor_b'],self.headers['b'])
        return first,second
    def test_upper_anchor_is_fetched_after_npz_within_the_same_clock_turn(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        self.anchors(runtime)
        self.assertEqual(self.reads,['/frame.npz','/frame.npz','/calibration','/frame.jpg'])
        self.assertIsNotNone(source.metric.lease)
        self.assertEqual(dict(source.metric.lease._originals)['anchor_b'],self.originals['anchor_b'])
    def test_fresh_npz_uses_the_closest_actual_lower_anchor_without_extending_it(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        self.now=1800000000.59;pump=runtime.preview_pumps[self.session['id']]
        older=viewing.capture.header(self.originals['anchor_a'],sequence=4,observed_ns=int((viewing.capture.NOW-.5)*1e9))
        pump.consume(self.originals['anchor_a'],older)
        pump.consume(self.originals['anchor_a'],self.headers['a'])
        lease=source.metric.lease;self.assertIsNotNone(lease,source.metric.reason)
        self.assertEqual(dict(lease._originals)['anchor_a'],self.originals['anchor_a'])
        headers=json.loads(lease._headers_json)
        self.assertEqual(headers['a'],self.headers['a'],'a fresh original must not inherit the older pump bracket')
        from assessment_existing_api import _receipt
        from bundle_rgb_input import anchor_metadata
        original=anchor_metadata(_receipt(self.originals['anchor_a'],self.headers['a']),site_id='victoria',camera_id='den',available_at=self.now,started_at=self.session['started_at'])
        self.assertLessEqual(lease.image_metadata['valid_until'],original['valid_until'])
        self.assertTrue(lease.image_metadata['reading_timing_qualified'])
        self.assertEqual(self.reads,['/frame.npz','/calibration','/frame.jpg'])
    def test_single_retry_replaces_a_lagging_bundle_with_the_fresh_original(self):
        import io
        import numpy as np
        configured=self.factory();read=configured.client._read
        with np.load(io.BytesIO(self.originals['npz']),allow_pickle=False) as bundle:
            arrays={name:bundle[name].copy() for name in bundle.files}
        meta=json.loads(arrays['metadata_json'].tobytes())
        meta['frames']['rgb'].update(sequence=7,device_timestamp_seconds=999.9)
        arrays['metadata_json']=np.frombuffer(json.dumps(meta).encode(),dtype=np.uint8)
        buffer=io.BytesIO();np.savez_compressed(buffer,**arrays);lagging=buffer.getvalue()
        reads=0
        def delayed(path):
            nonlocal reads
            data,header=read(path)
            if path=='/frame.npz':
                reads+=1
                if reads==1:return lagging,header
            return data,header
        configured.client._read=delayed
        runtime=self.build(metric_factory=configured);source=runtime.apartment_viewing_sessions[self.session['id']]
        self.now=1800000000.59;pump=runtime.preview_pumps[self.session['id']]
        older=viewing.capture.header(self.originals['anchor_a'],sequence=4,observed_ns=int((viewing.capture.NOW-.5)*1e9))
        pump.consume(self.originals['anchor_a'],older)
        pump.consume(self.originals['anchor_a'],self.headers['a'])
        lease=source.metric.lease;self.assertIsNotNone(lease,source.metric.reason)
        self.assertEqual(reads,2,'one bounded retry, no polling loop')
        self.assertEqual(dict(lease._originals)['npz'],self.originals['npz'])
        self.assertEqual(json.loads(lease._headers_json)['a'],self.headers['a'])
        self.assertTrue(lease.image_metadata['reading_timing_qualified'])
    def test_upper_anchor_from_another_clock_receipt_cannot_be_rebound(self):
        configured=self.factory();read=configured.client._read
        def changed(path):
            data,header=read(path)
            if path=='/frame.jpg':
                receipt=json.loads(base64.urlsafe_b64decode(header));receipt['host_clock_validation_id']='changed-clock-receipt'
                header=base64.urlsafe_b64encode(json.dumps(receipt).encode()).decode()
            return data,header
        configured.client._read=changed
        runtime=self.build(metric_factory=configured);source=runtime.apartment_viewing_sessions[self.session['id']]
        self.anchors(runtime)
        self.assertIsNone(source.metric.lease);self.assertEqual(source.metric.prepared,0)
        self.assertEqual(source.metric.reason,'bundled_receiver_proof_invalid')
    def test_exact_metric_result_is_projected_even_after_a_newer_jpeg_result(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        original=runtime.executor
        def pose(request):
            value=original(request);value['result']=deepcopy(self.event['payload']['result'])
            value['result']['frame_id']=request['frame']['frame_id'];return value
        runtime.executor=runtime.runners[self.session['id']].executor=pose
        self.anchors(runtime);deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            events=[e for e in self.store.list_events(self.session['id'],0,100)
                if e['kind']=='perception.output' and e['payload']['arm']=='b']
            if events:break
            time.sleep(.005)
        self.assertEqual(len(events),1)
        newer=deepcopy(events[0]['payload']);newer.update(media_ids=['newer-jpeg'],frame_id='newer-frame')
        self.store.append_event(self.session['id'],'perception.output',newer)
        source.metric.finalize()
        self.assertEqual(source.metric.projected,1)
        self.assertIsNone(source.metric.lease)
    def test_metric_clock_refusal_keeps_successive_exact_jpeg_outputs_available(self):
        configured=self.factory()
        configured.clock.begin=lambda _:(_ for _ in ()).throw(ValueError('receiver_clock_anchor_changed'))
        runtime=self.build(metric_factory=configured);source=runtime.apartment_viewing_sessions[self.session['id']]
        self.now=1800000000.59;pump=runtime.preview_pumps[self.session['id']]
        first=pump.consume(self.originals['anchor_a'],self.headers['a'])
        self.assertEqual(first['state'],'awaiting_metric_frame')
        outputs=[]
        later_header=viewing.capture.header(self.jpeg,sequence=17,observed_ns=int((viewing.capture.NOW+1.5)*1e9))
        frames=((self.originals['anchor_b'],self.headers['b']),(self.jpeg,later_header))
        for index,(jpeg,header) in enumerate(frames):
            self.now=1800000000.59+index
            reply=pump.consume(jpeg,header)
            self.assertIn('media_ids',reply,'optional metric refusal hid an otherwise valid exact RGB frame')
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                outputs=[e for e in self.store.list_events(self.session['id'],0,100)
                    if e['kind']=='perception.output' and e['payload']['media_ids']==reply['media_ids']]
                if len(outputs)==2:break
                time.sleep(.005)
            self.assertEqual(len(outputs),2,'both actual paired arms must settle this exact JPEG')
            from apartment_viewing_source import registered_media,viewing_bytes
            record=registered_media(self.store,self.session['id'],reply['media_ids'][0])
            self.assertEqual(record['mime_type'],'image/jpeg')
            self.assertEqual(viewing_bytes(self.store,self.session['id'],record['id'],self.session['generation']),jpeg)
            self.assertTrue(all(e['payload']['frame_id']==record['metadata']['frame_id'] for e in outputs))
            self.assertTrue(all(e['payload']['arm_snapshot']['layers']['skeleton']['available'] for e in outputs))
            self.assertNotIn('source_npz_sha256',record['metadata'],'JPEG fallback must not borrow metric provenance')
        live=runtime.live(self.session);status=live['apartment_viewing']['metric']
        self.assertEqual(status['state'],'unavailable');self.assertEqual(status['reason'],'receiver_clock_anchor_changed')
        self.assertTrue(all(arm.get('frame') for arm in live['arms'].values()))
        self.assertTrue(all('approximate_room_pose' not in arm for arm in live['arms'].values()))
        self.assertEqual(self.reads,[]);self.assertIsNone(source.metric.lease)
        self.assertLessEqual(source.status()['resident_bytes'],8388608)
        self.assertEqual(self.store.list_media(self.session['id'])['items'],[])
    def test_metric_registration_refusal_keeps_exact_jpeg_and_no_metric_association(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        with patch.object(source,'register_metric_frame',side_effect=ValueError('metric_registration_retired')):
            first,second=self.anchors(runtime)
        self.assertEqual(source.metric.reason,'metric_registration_retired')
        self.assertIn('media_ids',second,'retired metric registration must not suppress valid exact JPEG analysis')
        from apartment_viewing_source import registered_media
        record=registered_media(self.store,self.session['id'],second['media_ids'][0])
        self.assertEqual(record['mime_type'],'image/jpeg');self.assertNotIn('source_npz_sha256',record['metadata'])
        self.assertIsNone(source.metric.lease);self.assertIsNone(source.metric.image_record)
        self.assertIsNone(source.metric.snapshot());self.assertIsNone(source.metric.packet)
        self.assertLessEqual(source.status()['resident_bytes'],8388608)
    def test_duplicate_jpeg_fallback_preserves_original_availability_and_expiry(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        with patch.object(source,'register_metric_frame',side_effect=ValueError('metric_registration_retired')):
            _,first=self.anchors(runtime)
        from apartment_viewing_source import registered_media
        original=registered_media(self.store,self.session['id'],first['media_ids'][0])
        self.now+=.1
        again=runtime.preview_pumps[self.session['id']].consume(self.originals['anchor_b'],self.headers['b'])
        self.assertEqual(again['media_ids'],first['media_ids']);self.assertEqual(source.accepted,1)
        self.assertEqual(registered_media(self.store,self.session['id'],again['media_ids'][0]),original)
        self.assertIsNone(source.metric.lease);self.assertEqual(source.metric.reason,'metric_registration_retired')
    def test_stop_at_metric_refusal_prevents_late_jpeg_fallback(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        def stopped(_):
            runtime.stop_session(self.session)
            raise ValueError('metric_registration_retired')
        with patch.object(source,'register_metric_frame',side_effect=stopped):
            with self.assertRaisesRegex(ValueError,'viewing source stopped'):self.anchors(runtime)
        self.assertEqual(source.accepted,0);self.assertEqual(source.status()['resident_bytes'],0)
        self.assertEqual(self.seen,[]);self.assertTrue(runtime.runners[self.session['id']].closed)
    def test_same_npz_png_is_registered_before_both_actual_arms_and_union_is_bounded(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        original=runtime.executor
        def pose(request):
            value=original(request);value['result']=deepcopy(self.event['payload']['result'])
            value['result']['frame_id']=request['frame']['frame_id'];return value
        runtime.executor=runtime.runners[self.session['id']].executor=pose
        first,second=self.anchors(runtime)
        self.assertEqual(first['state'],'awaiting_metric_frame')
        self.assertTrue(second['media_ids']);self.assertEqual(self.reads,['/frame.npz','/frame.npz','/calibration','/frame.jpg'])
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            completed=[e for e in self.store.list_events(self.session['id'],0,100) if e['kind']=='perception.output']
            if len(completed)==2:break
            time.sleep(.005)
        self.assertEqual(len(completed),2)
        self.assertEqual({request['arm'] for request,_,_ in self.seen},{'a','b'})
        mids={tuple(request['frame']['media_ids']) for request,_,_ in self.seen};self.assertEqual(mids,{tuple(second['media_ids'])})
        from apartment_viewing_source import registered_media
        record=registered_media(self.store,self.session['id'],second['media_ids'][0]);self.assertEqual(record['mime_type'],'image/png')
        self.assertEqual(record['metadata']['source_npz_sha256'],self.pins['originals']['npz']['sha256'])
        lease=source.metric.lease
        self.assertIs(source.entries[record['id']]['data'],lease.image_bytes,'registered PNG and lease share one allocation')
        expected={id(raw):len(raw) for raw in source.metric.retained_byte_allocations()}
        expected.update({id(entry['data']):len(entry['data']) for entry in source.entries.values()})
        overhead=sum(entry['charge']-len(entry['data']) for entry in source.entries.values())
        self.assertEqual(source.status()['resident_bytes'],sum(expected.values())+overhead)
        self.assertLessEqual(source.status()['resident_bytes'],8388608)
        source.metric.finalize()
        self.assertTrue(source.metric.lease is None,source.metric.reason)
        self.assertEqual(source.metric.reason,'reviewed_view_alignment_unavailable')
        self.assertEqual(self.store.list_media(self.session['id'])['items'],[])
    def test_stop_during_real_capture_does_not_wait_for_runtime_lock_or_adopt_late_bytes(self):
        entered,release=threading.Event(),threading.Event();self.addCleanup(release.set)
        runtime=self.build(metric_factory=self.factory(block=(entered,release)))
        self.now=1800000000.59;pump=runtime.preview_pumps[self.session['id']]
        pump.consume(self.originals['anchor_a'],self.headers['a'])
        failures=[]
        def capture():
            try:pump.consume(self.originals['anchor_b'],self.headers['b'])
            except Exception as e:failures.append(type(e).__name__)
        thread=threading.Thread(target=capture);thread.start();self.assertTrue(entered.wait(1))
        started=time.monotonic();runtime.stop_session(self.session);self.assertLess(time.monotonic()-started,.5)
        source=runtime.apartment_viewing_sessions[self.session['id']]
        self.assertEqual(source.status()['resident_bytes'],0);self.assertIsNone(source.metric.previous)
        release.set();thread.join(2);self.assertFalse(thread.is_alive())
        self.assertEqual(source.status()['resident_bytes'],0);self.assertEqual(self.seen,[])
        self.assertEqual(self.reads,['/frame.npz'],'Stop must refuse the later calibration request')
    def test_budget_refusal_occurs_before_any_additional_npz_read(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        self.now=1800000000.59
        for i in range(8):source.add_media(self.session['id'],b'j'*(256*1024),'image/jpeg',
            {'valid_until':self.now+2,'frame_id':'authored-'+str(i)},'budget-'+str(i))
        with self.assertRaisesRegex(ValueError,'viewing RAM budget exhausted'):self.anchors(runtime)
        self.assertEqual(self.reads,[])
        self.assertEqual(source.metric.reason,'metric_capture_budget_unavailable')
        self.assertEqual(source.status()['resident_frames'],8,'fallback must not evict an unexpired original or exceed the frame cap')
        self.assertLessEqual(source.status()['resident_bytes'],8388608)
    def test_protected_bootstrap_projects_actual_supported_joint_and_stop_retires_every_owned_buffer(self):
        fixture,bootstrap,originals,config=self.approximate_configuration()
        base=self.factory();by_path={config['files'][role]['path']:raw for role,raw in originals.items()}
        with patch.object(bootstrap,'_read_private',side_effect=lambda path:by_path[path]):
            configured=bootstrap.load_factory(config,client=base.client)
        configured.clock=base.clock
        token='authored-metric-source-'+('d'*40)
        self.store.settings=replace(self.store.settings,token_grants=({'subject':'viewer','site_id':'victoria',
            'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'capabilities':['read']},))
        runtime=self.build(metric_factory=configured);source=runtime.apartment_viewing_sessions[self.session['id']]
        original=runtime.executor
        def pose(request):
            value=original(request);value['result']=deepcopy(self.event['payload']['result']);value['result']['frame_id']=request['frame']['frame_id'];return value
        runtime.executor=runtime.runners[self.session['id']].executor=pose
        self.anchors(runtime);deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if len([e for e in self.store.list_events(self.session['id'],0,100) if e['kind']=='perception.output'])==2:break
            time.sleep(.005)
        source.metric.finalize();packet=runtime.live(self.session)['arms']['B']['approximate_room_pose']
        self.assertTrue(packet['renderable'],packet)
        self.assertEqual(packet['label'],'Approximate room placement');self.assertIsNone(packet['room_pose'])
        self.assertFalse(packet['physical_permission']);self.assertEqual([j['id'] for j in packet['approximate_pose']['joints']],[0])
        self.assertEqual(len(source.metric.projection.retained_byte_allocations),3)
        self.assertLessEqual(source.status()['resident_bytes'],8388608)
        from app import create_app,PREFIX
        from fastapi.testclient import TestClient
        with TestClient(create_app(self.store.settings,runtime,self.store)) as client:
            path=PREFIX+'/sessions/'+self.session['id'];headers={'Authorization':'Bearer '+token,'X-Home-Lab-Site':'victoria'}
            live=client.get(path+'/live',headers=headers).json();graph=client.get(path+'/graph',headers=headers).json()
            mid=live['arms']['B']['frame']['media_id'];image_path=path+'/media/'+mid;params={'generation':self.session['generation']}
            descriptor=client.get(image_path+'/descriptor',params=params,headers=headers)
            pixels=client.get(image_path,params=params,headers=headers)
            self.assertEqual(descriptor.status_code,200);self.assertEqual(pixels.status_code,200)
            self.authored_source_fixture={'schema':'home-authored-metric-source-fixture-1','live':live,'graph':graph,
                'descriptor':descriptor.json(),'png_base64':base64.b64encode(pixels.content).decode(),
                'packet':packet,'external_camera_model_and_alignment':'authored','household_pixels':False}
        runtime.stop_session(self.session);self.assertEqual(source.status()['resident_bytes'],0)
        self.assertIsNone(source.metric.snapshot());self.assertEqual(self.store.list_media(self.session['id'])['items'],[])
    def test_private_bootstrap_refuses_wrong_hash_path_pack_and_missing_explicit_opt_in(self):
        fixture,bootstrap,originals,config=self.approximate_configuration()
        by_path={config['files'][role]['path']:raw for role,raw in originals.items()};base=self.factory()
        for edit in ('hash','path','pack','optin'):
            changed=deepcopy(config)
            if edit=='hash':changed['files']['alignment']['sha256']='0'*64
            if edit=='path':changed['files']['alignment']['path']='/tmp/public-view-alignment.json'
            if edit=='pack':changed['pack']['frame_sha256']='0'*64
            if edit=='optin':changed['enabled']=False
            with patch.object(bootstrap,'_read_private',side_effect=lambda path:by_path[path]):
                with self.assertRaises(ValueError,msg=edit):bootstrap.load_factory(changed,client=base.client)

    def test_missing_fixed_policy_keeps_default_and_malformed_policy_blocks_only_viewing(self):
        from managed_detector_app import create_managed_runtime
        from controller import Controller
        from types import SimpleNamespace
        import apartment_viewing_metric_bootstrap as bootstrap
        config=deepcopy(self.config);config.pop('existing_api_assessment');config['schema_version']=1
        profiles=deepcopy(self.profiles)
        for p in profiles.values():p.update(profile_id='yolo26s-rtmpose-cpu-v1',model_version='detector-pose-v1')
        config.update(pose_profiles=profiles,apartment_viewing=deepcopy(viewing.POLICY))
        clients={arm:SimpleNamespace(probe=lambda:[],close=lambda:None) for arm in ('a','b')}
        with patch.object(bootstrap.os,'lstat',side_effect=FileNotFoundError):
            pair=create_managed_runtime(self.store,SimpleNamespace(camera_id='den'),config,clients)
        self.addCleanup(pair.close)
        caps=Controller(self.store,pair).capabilities()
        self.assertEqual(caps['apartment_viewing'],__import__('apartment_viewing_source').capability(viewing.POLICY))
        self.assertEqual(next(r for r in caps['experiments'] if r['id']=='apartment-viewing')['status'],'ready')
        pair.close()
        with patch.object(bootstrap.os,'lstat',return_value=object()),patch.object(bootstrap,'_read_private',return_value=b'{bad json'):
            blocked=create_managed_runtime(self.store,SimpleNamespace(camera_id='den'),config,clients)
        self.addCleanup(blocked.close)
        caps=Controller(self.store,blocked).capabilities()
        self.assertFalse(caps['apartment_viewing']['provisioned'])
        self.assertEqual(caps['apartment_viewing']['reason'],'metric_viewing_policy_invalid')
        self.assertEqual(next(r for r in caps['experiments'] if r['id']=='apartment-viewing')['status'],'blocked')
        self.assertEqual(next(r for r in caps['experiments'] if r['id']=='placement-best')['status'],'ready')
        self.assertNotIn('apartment_viewing_metric',config)

    def test_status_announces_the_registered_png_while_it_is_fetchable(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        self.assertIsNone(source.metric.status()['registered_frame'])
        _,second=self.anchors(runtime)
        record=source.metric.image_record
        announced=runtime.live(self.session)['apartment_viewing']['metric']['registered_frame']
        self.assertEqual(announced,{'media_id':second['media_ids'][0],'frame_id':record['metadata']['frame_id'],
            'media_sha256':record['sha256'],'generation':self.session['generation'],'event_id':None})
        self.assertEqual(record['metadata']['valid_until'],source.metric.lease.image_metadata['valid_until'])
        deadline=time.monotonic()+2
        while time.monotonic()<deadline and len([e for e in self.store.list_events(self.session['id'],0,100) if e['kind']=='perception.output'])<2:
            time.sleep(.005)
        output=[e for e in self.store.list_events(self.session['id'],0,100) if e['kind']=='perception.output' and e['payload']['arm']=='b'][-1]
        source.metric.finalize()
        self.assertEqual(source.metric.status()['registered_frame'],{**announced,'event_id':output['id']})
        self.now=record['metadata']['valid_until'];source.prune()
        self.assertIsNone(source.metric.status()['registered_frame'],'strict: gone with the PNG at its original deadline')
        runtime.stop_session(self.session);self.assertIsNone(source.metric.status()['registered_frame'])

    def test_actual_png_descriptor_range_auth_generation_and_original_expiry(self):
        runtime=self.build(metric_factory=self.factory());self.anchors(runtime)
        source=runtime.apartment_viewing_sessions[self.session['id']]
        from app import create_app,PREFIX
        from fastapi.testclient import TestClient
        token='authored-private-png-'+('b'*40)
        self.store.settings=replace(self.store.settings,token_grants=({'subject':'viewer','site_id':'victoria',
            'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'capabilities':['read']},))
        mid=source.metric.image_record['id'];path=PREFIX+'/sessions/'+self.session['id']+'/media/'+mid
        headers={'Authorization':'Bearer '+token,'X-Home-Lab-Site':'victoria'};params={'generation':self.session['generation']}
        with TestClient(create_app(self.store.settings,runtime,self.store)) as client:
            self.assertEqual(client.get(path+'/descriptor',params=params).status_code,401)
            self.assertEqual(client.get(path,params=params,headers={**headers,'X-Home-Lab-Site':'echo'}).status_code,403)
            self.assertEqual(client.get(path,params={'generation':self.session['generation']+1},headers=headers).status_code,410)
            result=client.get(path,params=params,headers={**headers,'Range':'bytes=0-7'})
            self.assertEqual(result.status_code,206);self.assertEqual(result.content,b'\x89PNG\r\n\x1a\n')
            self.now=source.metric.lease.image_metadata['valid_until']
            self.assertEqual(client.get(path,params=params,headers=headers).status_code,410)
            self.assertIsNone(source.metric.lease);self.assertEqual(source.status()['resident_frames'],0)
        self.assertEqual(self.store.list_media(self.session['id'])['items'],[])

    def test_optional_source_epoch_fault_keeps_owned_lifecycle_cleanup_and_bound(self):
        runtime=self.build(metric_factory=self.factory());self.anchors(runtime)
        source=runtime.apartment_viewing_sessions[self.session['id']]
        changed=viewing.capture.decoded_header(self.headers['b']);changed['session']='different-camera-epoch'
        header=base64.urlsafe_b64encode(json.dumps(changed).encode()).decode()
        with self.assertRaises(ValueError):source.ingest(self.session['id'],self.originals['anchor_b'],header)
        self.assertTrue(source.closed);self.assertTrue(source.expiry_dispatch_started)
        self.assertEqual(source.status()['resident_bytes'],0)
        self.assertTrue(source.expiry_dispatch_done.wait(1))
        self.assertTrue(runtime.runners[self.session['id']].closed)

    def test_private_reader_uses_nofollow_directory_descriptors_and_closes_them(self):
        import apartment_viewing_metric_bootstrap as bootstrap
        from types import SimpleNamespace
        import stat
        current=[10];closed=[];calls=[]
        def opened(name,flags,**kwargs):
            calls.append((name,flags,kwargs));current[0]+=1;return current[0]
        def info(fd):
            regular=fd==17
            return SimpleNamespace(st_mode=(stat.S_IFREG|0o600) if regular else (stat.S_IFDIR|0o700),st_uid=123,
                st_size=2,st_dev=1,st_ino=fd,st_mtime_ns=1)
        with patch.object(bootstrap.os,'name','posix'),patch.object(bootstrap.os,'O_DIRECTORY',0x10000,create=True),\
             patch.object(bootstrap.os,'O_NOFOLLOW',0x20000,create=True),patch.object(bootstrap.os,'open',side_effect=opened),\
             patch.object(bootstrap.os,'close',side_effect=closed.append),patch.object(bootstrap.os,'fstat',side_effect=info),\
             patch.object(bootstrap.os,'geteuid',return_value=123,create=True),patch.object(bootstrap.os,'read',side_effect=[b'{}',b'']):
            self.assertEqual(bootstrap._read_private(bootstrap.PRIVATE_PATH+'/authored/a.json'),b'{}')
        self.assertTrue(all(flags&0x20000 for _,flags,_ in calls))
        self.assertTrue(all('dir_fd' in kwargs for _,_,kwargs in calls[1:]))
        self.assertEqual(set(closed),set(range(11,18)))

    def test_pre_frame_clock_refusal_is_visible_without_claiming_approximate_pixels(self):
        configured=self.factory()
        configured.clock.begin=lambda _:(_ for _ in ()).throw(ValueError('receiver_clock_anchor_changed'))
        runtime=self.build(metric_factory=configured);self.anchors(runtime)
        live=runtime.live(self.session);status=live['apartment_viewing']['metric']
        self.assertEqual(status['state'],'unavailable');self.assertEqual(status['reason'],'receiver_clock_anchor_changed')
        self.assertEqual(status['prepared_frames'],0);self.assertEqual(self.reads,[])
        self.assertFalse(status['physical_permission'])
        self.assertTrue(all('approximate_room_pose' not in arm for arm in live['arms'].values()))

    def test_stop_during_projection_cannot_resurrect_a_late_approximate_packet(self):
        fixture,bootstrap,originals,config=self.approximate_configuration();base=self.factory()
        by_path={config['files'][role]['path']:raw for role,raw in originals.items()}
        with patch.object(bootstrap,'_read_private',side_effect=lambda path:by_path[path]):
            configured=bootstrap.load_factory(config,client=base.client)
        configured.clock=base.clock
        runtime=self.build(metric_factory=configured);source=runtime.apartment_viewing_sessions[self.session['id']]
        original=runtime.executor
        def pose(request):
            value=original(request);value['result']=deepcopy(self.event['payload']['result'])
            value['result']['frame_id']=request['frame']['frame_id'];return value
        runtime.executor=runtime.runners[self.session['id']].executor=pose
        self.anchors(runtime);deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if len([e for e in self.store.list_events(self.session['id'],0,100) if e['kind']=='perception.output'])==2:break
            time.sleep(.005)
        entered,release=threading.Event(),threading.Event();self.addCleanup(release.set)
        original_project=source.metric.projection.ingest
        def delayed(*args,**kwargs):entered.set();release.wait(2);return original_project(*args,**kwargs)
        source.metric.projection.ingest=delayed
        projection=threading.Thread(target=source.metric.finalize);projection.start();self.assertTrue(entered.wait(1))
        started=time.monotonic();runtime.stop_session(self.session);self.assertLess(time.monotonic()-started,.5)
        release.set();projection.join(2);self.assertFalse(projection.is_alive())
        self.assertIsNone(source.metric.snapshot());self.assertIsNone(source.metric.packet)
        self.assertEqual(source.status()['resident_bytes'],0)

    def test_actual_stop_does_not_wait_for_registration_metadata_while_capture_owns_no_source_lock(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        entered,release,stopped=threading.Event(),threading.Event(),threading.Event();self.addCleanup(release.set)
        original=source.register_metric_frame
        def delayed(lease):entered.set();release.wait(2);return original(lease)
        source.register_metric_frame=delayed
        self.now=1800000000.59;pump=runtime.preview_pumps[self.session['id']]
        pump.consume(self.originals['anchor_a'],self.headers['a'])
        errors=[]
        def capture():
            try:pump.consume(self.originals['anchor_b'],self.headers['b'])
            except Exception as error:errors.append(type(error).__name__)
        def stop():
            try:runtime.stop_session(self.session)
            finally:stopped.set()
        capture_thread=threading.Thread(target=capture);capture_thread.start();self.assertTrue(entered.wait(1))
        stop_thread=threading.Thread(target=stop);stop_thread.start()
        progressed=stopped.wait(.3)
        release.set();capture_thread.join(2);stop_thread.join(2)
        self.assertFalse(capture_thread.is_alive());self.assertFalse(stop_thread.is_alive())
        self.assertTrue(progressed,'actual Stop waited on owner.lock held across registration/current metadata checks')
        self.assertEqual(source.status()['resident_bytes'],0);self.assertEqual(self.seen,[])

    def test_slow_preparation_retires_expired_metric_png_but_preserves_current_jpeg(self):
        import apartment_viewing_metric
        original=apartment_viewing_metric.prepare_metric_frame
        def late(**kwargs):
            lease=original(**kwargs);self.now=lease.descriptor['retirement_deadline'];return lease
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        with patch.object(apartment_viewing_metric,'prepare_metric_frame',side_effect=late):self.anchors(runtime)
        self.assertEqual(source.accepted,1,'only the independently current JPEG may be registered')
        self.assertEqual(source.metric.reason,'metric_registration_retired');self.assertIsNone(source.metric.lease)
        self.assertEqual([entry['record']['mime_type'] for entry in source.entries.values()],['image/jpeg'])
        record=next(iter(source.entries.values()))['record']
        self.assertGreater(record['metadata']['valid_until'],self.now)
        self.assertNotIn('source_npz_sha256',record['metadata']);self.assertIsNone(source.metric.packet)
    def test_metric_failure_cannot_fallback_after_original_jpeg_expiry(self):
        import apartment_viewing_metric
        original=apartment_viewing_metric.prepare_metric_frame
        def late(**kwargs):
            lease=original(**kwargs);self.now=viewing.capture.NOW+5;return lease
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        with patch.object(apartment_viewing_metric,'prepare_metric_frame',side_effect=late):
            with self.assertRaisesRegex(ValueError,'preview stale'):self.anchors(runtime)
        self.assertEqual(source.accepted,0);self.assertIsNone(source.metric.lease);self.assertEqual(self.seen,[])
        self.assertEqual(source.status()['resident_bytes'],0)
    def test_generation_change_at_metric_refusal_retires_before_jpeg_fallback(self):
        runtime=self.build(metric_factory=self.factory());source=runtime.apartment_viewing_sessions[self.session['id']]
        def changed(_):
            runtime.runners[self.session['id']].generation+=1
            raise ValueError('metric_registration_retired')
        with patch.object(source,'register_metric_frame',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'scope changed'):self.anchors(runtime)
        self.assertTrue(source.closed);self.assertEqual(source.accepted,0);self.assertEqual(self.seen,[])
        self.assertTrue(source.expiry_dispatch_done.wait(2))
        self.assertEqual(source.status()['resident_bytes'],0)

if __name__=='__main__':unittest.main()
