"""Source6 producer tests: real config4 factory, authored external I/O only."""
from copy import deepcopy
from decimal import Decimal
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import test_bundle_rgb_input as factory
import test_lab_live_desk_anchor as legacy
import live_capture_fixtures as capture
from bundle_ingest import calibration_content_id
from depth_clock import DepthClock
from journal import digest
from lab_bundled_rgb_stereo import (BundledRgbStereoSource, BASE_ROLES, ROW_KEYS,
    build_bundled_clock_original, validate_bundled_rgb_originals, validate_bundled_mapped_depth)
from pose_composition import POLICY, MODEL_VERSION
from preview_client import LocalBundleClient
from registered_image import RegisteredImage
from rtmpose_worker import RTMPoseWorker
from spatial_validation import mount_signature
from staged_specialist import composite_manifest
from bundle_rgb_input import CAMERA_SHA, QualifiedReceiverClock, receipt_upper
from home_shared.protocol import camera_frame


class AuthoredBundledClock:
    """One fictional affine clock, checked by the unchanged camera functions."""
    def __init__(self, nv12, *, shift=Decimal(0)):
        self.nv12 = nv12
        self.shift_ns = int(shift * 10**9)
        path = capture.ROOT / 'tools/fixtures/installed-oak-capture-20261016.py'
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != CAMERA_SHA:
            raise ValueError('authored_camera_clock_source_changed')
        name = '_source6_authored_camera_clock'
        spec = importlib.util.spec_from_file_location(name, path)
        self.camera = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.camera
        spec.loader.exec_module(self.camera)
        self.evidence = dict(version=1, semantics='commissioned_camera_host_clock_v1',
            host_boot_id='00000000-0000-0000-0000-000000000001',
            validation_id='synthetic-host-clock-validation', checked_monotonic_ns=10_400_000_000+self.shift_ns,
            checked_unix_ns=self.utc_ns(10_400_000_000+self.shift_ns),
            valid_until_monotonic_ns=11_400_000_000+self.shift_ns, uncertainty_seconds=.000001)
        self.receiver_before = self.point(10_580_000_000+self.shift_ns)
        self.receiver_after = self.point(10_600_000_000+self.shift_ns)
        # Processing availability follows the actual authored full-body point;
        # it is neither an exposure nor an extension of any original lease.
        self.completed_at = float(Decimal(self.utc_ns(self.receiver_after.after_ns)+1_000_000)/10**9)

    @staticmethod
    def utc_ns(monotonic_ns):
        return capture.NOW*10**9 + monotonic_ns - 10_000_000_000

    def point(self, before_ns):
        return self.camera.HostPoint(before_ns, before_ns, self.utc_ns(before_ns), before_ns+100)

    def header(self, jpeg, sequence, observed_at):
        midpoint_ns = int((Decimal(10)+Decimal(str(observed_at))-Decimal(capture.NOW))*10**9)
        sent = self.point(midpoint_ns-10_000_000)
        receive_end = midpoint_ns+10_000_000
        received = self.point(receive_end-100)
        frame_device_ns = int((Decimal(50)+Decimal(str(observed_at))-Decimal(capture.NOW))*10**9)
        preview = self.camera.Preview('synthetic-camera-epoch', sequence, frame_device_ns/1e9,
            received.after_ns/1e9, self.nv12, exposure_duration_seconds=.01)
        frame = self.camera.CausalFrame(preview, format(sequence, '032x'), frame_device_ns-1_000_000,
            frame_device_ns, sent, received, 1000, hashlib.sha256(self.nv12).hexdigest(),
            self.camera.HostClockEvidence(**self.evidence))
        receipt = self.camera.causal_timing(frame, self.point(receive_end+100), self.evidence['host_boot_id'])
        receipt['jpeg_sha256'] = hashlib.sha256(jpeg).hexdigest()
        return base64.urlsafe_b64encode(json.dumps(receipt).encode()).decode()

    def context(self):
        points = iter((self.receiver_before, self.receiver_after))
        self.camera.host_point = lambda: next(points)
        self.camera.read_protected_json = lambda *_: deepcopy(self.evidence)
        pilot = SimpleNamespace(CONFIG=Path('/authored/private/host-clock-config.json'),
            load_config=lambda _: SimpleNamespace(output_path='/authored/private/host-clock.json', uid=1000),
            _boot_id=lambda: self.evidence['host_boot_id'])
        return self.camera, pilot


class GeneratedBundledFixture:
    """Actual one-pair factory fixture reusable by the runtime/worker lane.

    All measurement/clock/registration/profile/association truth here is authored.
    Only external camera/clock/model ports and Windows directory sync are doubled.
    """
    qualification = legacy.GeneratedStereoFixture.qualification

    def __init__(self, case):
        self.case = case
        self.f = factory.BundledRgbFactoryTests('runTest'); case.addCleanup(self.f.doCleanups); self.f.setUp()
        self.directory, self.store, self.session = self.f.directory, self.f.store, self.f.session
        self.scope = dict(site_id='victoria', room_id='den', session_id=self.session['id'], domain_epoch='authored-domain')
        self.model_manifests = {
            'detector': {'sha256': '1'*64, 'device_id': 'authored-detector', 'engine_epoch': 'authored-model-epoch'},
            'pose': {'sha256': '2'*64, 'device_id': 'authored-pose', 'engine_epoch': 'authored-model-epoch'}}
        self.profile = composite_manifest(self.model_manifests['detector'], self.model_manifests['pose'],
            profile_id='yolo26s-rtmpose-cpu-v1', engine_epoch='authored-model-epoch', policy=POLICY,
            model_version=MODEL_VERSION, pose_preprocessing_revision=RTMPoseWorker.preprocessing_revision, runtime_revision='3'*64)
        self.profile['artifact_sha256'] = self.profile['sha256']; self.profiles = {a:deepcopy(self.profile) for a in ('a','b')}
        self.f.profiles = deepcopy(self.profiles)
        rotation = [[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]]
        self.f.calibration['eeprom'] = {'cameraData': [[0, {'extrinsics': {'toCameraSocket': -1}}],
            [1, {'extrinsics': {'toCameraSocket': 0, 'rotationMatrix': rotation, 'translation': {'x':-3.75,'y':0.,'z':0.}}}]],
            'stereoRectificationData': {'leftCameraSocket':1,'rightCameraSocket':2,'rectifiedRotationLeft':rotation}}
        build = factory.build_runtime
        def configured(*args, **kwargs):
            return build(*args, **{**kwargs,'experiment_id':self.session['experiment_id'],'profiles_override':self.profiles})
        with patch.object(factory,'build_runtime',configured): self.runtime = self.f.build_bundled()
        self.f.release.set()
        self.seen = []
        def execute(request):
            image, metadata = RegisteredImage(self.store)(request['frame'])
            self.seen.append((image[0,0].tolist(), deepcopy(request), metadata))
            return legacy.GeneratedStereoFixture.execute(self,request)
        self.runtime.executor = execute
        # The actual factory has already constructed its PairedRunner. Bind
        # the authored external model port there before the first input too.
        self.runtime.runners[self.session['id']].executor = execute
        _, cp, _, _, _ = capture.setup()
        self.projection = deepcopy(cp['projection']); self.projection.update(site_id='victoria',camera_id='den',
            id=calibration_content_id(self.f.calibration), mount_signature=mount_signature(np.full((400,640),1234,np.uint16)))
        self.registration = deepcopy(cp['registration']); self.registration.update(site_id='victoria',room_id='den',camera_id='den')
        self.depth_clock_policy = deepcopy(cp['depth_clock'])
        self.person_ref = {**self.scope,'pid':1,'track_epoch':'assessed-track'}
        self.subject_ref = {'subject_id':'desk-work','subject_epoch':'assessed-work'}
        self.association_method_ref = capture.pin('authored-visible-nose-association')
        self.lab_binding_digest = '4'*64
        self.clock_mapping = dict(mapping_id='authored-bundled-derived-utc-original',
            mapping_digest=digest({'method':'derived-original-midpoint','family':'existing-api-bundled-rgb-1'}),
            source_epoch='synthetic-camera-epoch',original_unit='unix_s',epoch_origin_s=0,max_uncertainty_s=.1)
        self.store.append_event(self.session['id'],'workers.ready',{'receipts':{'b':{
            'state':'ready','session_id':self.session['id'],'engine_epoch':self.profile['engine_epoch'],
            'profile':deepcopy(self.profile),'expires_at':capture.NOW+30}},'fixture_evidence':'authored_external_lifecycle_double'})
        self.append_pair()

    @property
    def now(self): return self.f.now

    @now.setter
    def now(self, value): self.f.now = value

    def append_pair(self, *, sequence=13, observed_at=capture.NOW+.45, depth_delta_s=-.01,
                    expect_output=True, **_):
        # First pair has independently authored close A/B bounds. A later test
        # must preserve the real source's previous-B continuity; no reset here.
        source = self.runtime.bundled_rgb_sessions[self.session['id']]
        first = source.previous is None
        shift = Decimal(str(observed_at))-Decimal(capture.NOW)-Decimal('.45')
        self.f.time_shift = shift
        a_at = observed_at-.01
        previous_at = (None if first else self.store.get_media(self.session['id'],
            source.previous['media_ref']['id'])['metadata']['observed_at'])
        b_at = observed_at+.01 if first else 2*observed_at-previous_at
        device = float(Decimal(50)+Decimal(str(observed_at))-Decimal(str(capture.NOW)))
        meta = capture.frame_metadata(sequence=sequence,depth_sequence=41+sequence-13,
            rgb_device_s=device,depth_device_s=device+depth_delta_s)
        nv12=np.full((540,640),128,np.uint8);nv12[:360]=235
        authored_clock = AuthoredBundledClock(nv12.tobytes(), shift=shift)
        out=io.BytesIO();np.savez_compressed(out,depth_mm=np.full((400,640),1234,np.uint16),
            left=np.zeros((400,640),np.uint8),right=np.zeros((400,640),np.uint8),rgb_nv12=nv12,
            metadata_json=np.frombuffer(json.dumps(meta).encode(),np.uint8))
        self.f.bundle=out.getvalue();self.f.raw_calibration=json.dumps(self.f.calibration).encode()
        def read(_client,path):
            if path=='/frame.npz':self.now=max(self.now,authored_clock.completed_at);return self.f.bundle,''
            if path=='/calibration':return self.f.raw_calibration,''
            raise AssertionError('unknown authored camera route')
        with patch('bundle_rgb_input.clock_context',side_effect=authored_clock.context),patch('bundle_rgb_input.sync_directory'), \
             patch.object(LocalBundleClient,'_read',autospec=True,side_effect=read):
            if first:
                self.now=max(self.now,a_at+.02);self.f.consume(self.runtime,authored_clock.header(self.f.jpeg,sequence-3,a_at))
            self.now=max(self.now,b_at+.02);self.f.consume(self.runtime,authored_clock.header(self.f.jpeg,sequence+3,b_at))
        if expect_output is False:
            frame_id=f'synthetic-camera-epoch-bundle-rgb-{sequence}'
            with self.store.lock:
                dropped=self.store.db.execute("""SELECT arm,state,result FROM paired_requests
                    WHERE session_id=? AND json_extract(payload,'$.frame.frame_id')=?""",
                    (self.session['id'],frame_id)).fetchall()
            self.case.assertEqual(len(dropped),2)
            self.case.assertEqual({row[0] for row in dropped},{'a','b'})
            for row in dropped:
                self.case.assertEqual(row[1],'dropped')
                self.case.assertEqual(json.loads(row[2]),{'reason':'source_sampling_cadence'})
            self.case.assertFalse([e for e in self.store.list_events(self.session['id'])
                if e['kind']=='perception.output' and e['payload'].get('frame_id')==frame_id])
            return None,None
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            rows=[e for e in self.store.list_events(self.session['id']) if e['kind']=='perception.output'
                and e['payload'].get('arm')=='b' and e['payload'].get('frame_id')==f'synthetic-camera-epoch-bundle-rgb-{sequence}']
            if rows:break
            time.sleep(.005)
        self.case.assertTrue(rows, self.runtime.bundled_rgb_sessions[self.session['id']].status())
        self.case.assertTrue(self.seen, 'actual_registered_image_model_port_not_called')
        self.row=rows[-1]
        mid=self.row['payload']['media_ids'][0];self.rgb=self.store.get_media(self.session['id'],mid)
        self.frame=deepcopy(self.rgb['metadata'])
        return self.row,deepcopy(self.frame)

    def source(self, *, depth=True, current=lambda _:True):
        return BundledRgbStereoSource(self.store, expected_bundle_binding={
            'scope':self.scope,'lab_binding_digest':self.lab_binding_digest,
            'bundle_input_policy_sha256':self.runtime.bundled_rgb_sessions[self.session['id']].policy_sha256},
            incumbent_runtime=self.runtime,capture_clock_policy=self.clock_mapping,
            depth_clock=DepthClock(self.store,site_id='victoria',camera_id='den',clock=self.depth_clock_policy,
                calibration=self.projection) if depth else None,current=current,clock=lambda:self.now)


class BundledRgbStereoTests(unittest.TestCase):
    def fixture(self): return GeneratedBundledFixture(self)

    def test_authored_header_and_receiver_share_one_affine_clock(self):
        # This probe does not build a Store, controller, model or worker. Both
        # camera receipts and the whole-NPZ receiver proof use incumbent checks.
        jpeg = capture.jpeg()
        nv12 = np.full((540,640),128,np.uint8); nv12[:360] = 235
        clock = AuthoredBundledClock(nv12.tobytes())
        receipts = []
        for sequence, offset in ((10, Decimal('.44')), (16, Decimal('.46'))):
            header = clock.header(jpeg, sequence, Decimal(capture.NOW)+offset)
            decoded = capture.decoded_header(header)
            parsed = camera_frame(jpeg, header)
            self.assertEqual(decoded['observed_at_unix_ns'], capture.NOW*10**9+int(offset*10**9))
            self.assertEqual(decoded['host_clock_uncertainty_seconds'], .000001)
            self.assertEqual(parsed.uncertainty_seconds, .01+.01+.000001+.000001)
            self.assertEqual(decoded['preview_sha256'], hashlib.sha256(nv12.tobytes()).hexdigest())
            receipts.append(decoded)
        receiver = QualifiedReceiverClock()
        with patch('bundle_rgb_input.clock_context', side_effect=clock.context):
            proof = receiver.finish(receiver.begin(receipts[-1]))
        self.assertEqual(proof['evidence'], clock.evidence)
        self.assertEqual(proof['before']['unix_ns']-proof['before']['before_ns'], capture.NOW*10**9-10_000_000_000)
        self.assertEqual(proof['after']['unix_ns']-proof['after']['before_ns'], capture.NOW*10**9-10_000_000_000)
        self.assertEqual(receipt_upper(proof, receipts[-1]), 1800000000600001100)
        self.assertGreater(Decimal.from_float(clock.completed_at), Decimal(receipt_upper(proof, receipts[-1]))/10**9)

    def test_actual_factory_C_clock_original_and_pure_replay_preserve_raw_device_scalar(self):
        f=self.fixture();source=f.source();selected=source.capture(f.row,f.frame,f.now)
        self.assertEqual(selected['status'],'selected',selected)
        self.assertEqual(set(selected['rows']),BASE_ROLES)
        c=selected['clock_original'];self.assertEqual(c['clock']['original_unit'],'unix_s')
        self.assertNotIn('source_timing_receipt',f.frame)
        self.assertEqual(c['component']['device_timestamp_seconds'],50.45)
        self.assertEqual(c['component']['device_timestamp_unit'],'seconds')
        self.assertTrue(all(pixel==[254,254,254] for pixel,_,_ in f.seen))
        resolver=lambda ref:next({'record':r,'data':raw} for r,raw in selected['rows'].values() if r['id']==ref['id'])
        got=validate_bundled_rgb_originals(selected['bundled_originals'],media_refs=c['media_refs'],
            expected_bundle_binding=source.binding(),capture_clock_policy=f.clock_mapping,resolve_media=resolver,
            event=f.row,frame=f.frame)
        self.assertEqual(got['clock_original'],c)
        rounded=Decimal.from_float(f.frame['observed_at']);u=Decimal.from_float(c['clock']['uncertainty_s'])
        self.assertLessEqual(rounded-u,Decimal(c['derived_interval']['lower_unix_ns'])/10**9)
        self.assertGreaterEqual(rounded+u,Decimal(c['derived_interval']['upper_unix_ns'])/10**9)

    def test_missing_depth_qualification_keeps_capture_but_never_selects_geometry(self):
        f=self.fixture();source=f.source(depth=False);selected=source.capture(f.row,f.frame,f.now)
        self.assertEqual(selected['status'],'selected',selected)
        original=dict(schema='home-original-lab-observation-2',event=f.row,frame_metadata=f.frame,
            bundled_originals=selected['bundled_originals'],clock_original=selected['clock_original'])
        result=source.select(dict(original=original,frame_metadata=f.frame,media_generation=f.session['generation']),f.now)
        self.assertEqual(result,dict(status='unavailable',reason='qualified_bundled_depth_clock_unavailable'))
        source.quiesce()
        self.assertEqual(source.finalize(),dict(status='finalized',buffers_cleared=True))

    def test_missing_required_C_originals_remain_incomplete_after_unavailable_selection(self):
        for role in ('support','rgb','corrupt_npz'):
            with self.subTest(role=role):
                f=self.fixture();source=f.source()
                if role=='rgb':item=f.rgb
                elif role=='corrupt_npz':
                    item=next(r for r in f.store.list_media(f.session['id'])['items']
                        if r['metadata'].get('stream')=='bundled_rgb_raw_npz')
                else:
                    item=next(r for r in f.store.list_media(f.session['id'])['items']
                        if r['metadata'].get('stream')=='bundled_rgb_input_original' and r['metadata'].get('status')=='retained')
                path=f.store.media_path(f.session['id'],item['id'])
                if role=='corrupt_npz':
                    raw=path.read_bytes();path.write_bytes(bytes([raw[0]^1])+raw[1:])
                else:path.unlink()
                self.assertEqual(source.capture(f.row,f.frame,f.now)['status'],'unavailable')
                source.quiesce()
                self.assertEqual(source.finalize(),dict(status='incomplete',buffers_cleared=False))

    def test_additive_B_depth_mapping_exact_retry_never_refreshes_first_availability(self):
        f=self.fixture();source=f.source();selected=source.capture(f.row,f.frame,f.now)
        self.assertEqual(selected['status'],'selected',selected)
        records={k:dict(record=r,data=raw) for k,(r,raw) in selected['rows'].items()}
        opts=dict(records=records,binding=selected['bundled_originals']['start']['payload']['binding'],
            started=selected['bundled_originals']['start'],terminal=selected['bundled_originals']['terminal'])
        first=source.depth_clock.derive_bundled(f.session['id'],**opts);f.now+=.01
        again=source.depth_clock.derive_bundled(f.session['id'],**opts)
        self.assertEqual(first,again)
        # Store's public result also contains URL/display aliases. Retained
        # originals use the exact persisted row, as the real source reader does.
        rows={**selected['rows'],'mapped_depth':({k:first[k] for k in ROW_KEYS},
            f.store.media_path(f.session['id'],first['id']).read_bytes())}
        validate_bundled_mapped_depth(rows,support={},binding=opts['binding'],depth_clock_policy=f.depth_clock_policy,projection=f.projection)

    def test_narrowed_clock_and_changed_C_metadata_refuse_without_retiming(self):
        f=self.fixture();source=f.source();source.capture_clock_policy['max_uncertainty_s']=.00001
        self.assertEqual(source.capture(f.row,f.frame,f.now)['status'],'unavailable')
        source=f.source();foreign=deepcopy(f.frame);foreign['frame_id']='B-is-not-C'
        self.assertEqual(source.capture(f.row,foreign,f.now)['status'],'unavailable')


if __name__=='__main__':unittest.main()
