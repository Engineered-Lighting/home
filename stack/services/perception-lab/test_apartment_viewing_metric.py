"""Actual original-byte/projection chain; external camera/model/room are authored.

The incumbent Store fixture supplies original bytes only. The new adapter is
given no retained collection receipt and must use its distinct RAM provenance.
"""
from copy import deepcopy
import hashlib
import importlib
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
for folder in ('tools/den-live-runtime', 'tools/shared-home', 'stack/services/home-context'):
    if str(ROOT / folder) not in sys.path:
        sys.path.append(str(ROOT / folder))

def sha(raw): return hashlib.sha256(raw).hexdigest()
def encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class ViewingMetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_bundle_rgb_collect import BundledRgbCollectionTests
        from lab_assessment_collect import read_verified_attempts
        cls.fixture = f = BundledRgbCollectionTests('runTest')
        cls.addClassCleanup(f.doCleanups)
        f.make(pose=True)
        assert f.collect()['complete']
        old = read_verified_attempts(f.output)
        c = old['attempts'][0]
        records = c['media_records']
        cls.originals = {role: f.f.store.media_path(f.f.session['id'], records[old_role]['id']).read_bytes()
                         for role, old_role in (('npz', 'npz'), ('calibration', 'calibration_original'),
                                                ('anchor_a', 'anchor_a'), ('anchor_b', 'anchor_b'))}
        support = json.loads(f.f.store.media_path(f.f.session['id'], records['support']['id']).read_bytes())
        cls.headers = {key: support['anchors'][key]['header'] for key in ('a', 'b')}
        cls.proof = support['receiver_clock']
        cls.binding = {key: deepcopy(old['binding']['binding'][key]) for key in
                       ('session', 'arm', 'model_generation', 'media_generation', 'lab_profile')}
        cls.binding['sampling_hz'] = 2
        cls.event = c['original_event']
        cls.frame = c['original_frame_record']
        cls.pins = dict(originals={key: dict(sha256=sha(raw), size=len(raw)) for key, raw in cls.originals.items()},
            anchor_headers={key: sha(raw.encode()) for key, raw in cls.headers.items()}, receiver_proof=sha(encoded(cls.proof)),
            capture_epoch=cls.event['payload']['capture_epoch'], timing_generation=0,
            calibration_content_id=records['bundle']['metadata']['calibration_content_id'])

    def setUp(self):
        self.module = importlib.import_module('apartment_viewing_metric')
        self.args = dict(originals=deepcopy(self.originals), anchor_headers=deepcopy(self.headers),
            receiver_proof=deepcopy(self.proof), binding=deepcopy(self.binding), pins=deepcopy(self.pins),
            available_at=1800000000.59, started_at=1799999999.5, now_s=1800000000.59)

    def prepare(self, **updates):
        args = {**self.args, **updates}
        return self.module.prepare_metric_frame(**args)

    def model_inputs(self, lease):
        frame = deepcopy(self.frame)
        frame.update(sha256=sha(lease.image_bytes), size=len(lease.image_bytes), metadata=lease.image_metadata)
        event = deepcopy(self.event)
        p = event['payload']; m = frame['metadata']
        p.update(frame_id=m['frame_id'], capture_epoch=m['capture_epoch'],
                 observed_at=m['observed_at'])
        from runtime import arm_valid_until
        p['valid_until'] = arm_valid_until(m, lease.binding['sampling_hz'])
        p['result']['frame_id'] = m['frame_id']
        p['arm_snapshot']['frame'].update(id=m['frame_id'], captured_at=m['observed_at'])
        return event, frame

    def lift(self, lease, **updates):
        event, frame = self.model_inputs(lease)
        return self.module.lift_supported_joints(lease, **{'event': event, 'image_record': frame,
            'now_s': 1800000000.65, 'projection': 'diagnostic_nominal_eeprom', **updates})

    def test_png_is_decoded_from_same_npz_and_only_supplied_joint_gets_metric_support(self):
        from PIL import Image
        lease = self.prepare()
        self.assertEqual(Image.open(io.BytesIO(lease.image_bytes)).getpixel((0, 0)), (254, 254, 254))
        self.assertEqual(lease.descriptor['schema'], 'home-apartment-viewing-metric-ram-1')
        out = self.lift(lease)
        self.assertEqual(out['schema'], 'home-apartment-viewing-metric-attempt-1')
        self.assertNotIn('collection_mode', out)
        self.assertEqual(out['projection']['status'], 'unqualified_nominal_eeprom')
        self.assertIsNone(out['depth_clock']['exposure_error_bound_s'])
        joints = out['instances'][0]['joints']
        self.assertEqual(len(joints), 17)
        self.assertEqual([j['joint_id'] for j in joints if j['status'] == 'candidate'], [0])
        self.assertAlmostEqual(joints[0]['camera_xyz_m'][2], 1.234)
        self.assertGreaterEqual(joints[0]['depth_support'], 3)
        self.assertIsNone(joints[0]['error_bound_m'])
        self.assertTrue(all(j['camera_xyz_m'] is None for j in joints[1:]))
        self.assertLessEqual(lease.retained_bytes, 8 * 1024**2)
        allocations=lease.retained_byte_allocations
        self.assertTrue(all(isinstance(raw,bytes) for raw in allocations))
        self.assertTrue(any(raw is lease.image_bytes for raw in allocations))
        self.assertEqual(sum({id(raw):len(raw) for raw in allocations}.values()),lease.retained_bytes)
        self.assertFalse(out['physical_permission']); self.assertFalse(out['follow_allowed'])

    def test_original_bytes_headers_proof_and_trusted_pins_are_checked_before_lift(self):
        for role in self.originals:
            args = deepcopy(self.args); args['originals'][role] += b'changed'
            with self.subTest(role=role), self.assertRaises(ValueError): self.prepare(**args)
        for change in (
            lambda a: a['pins'].update(capture_epoch='different-epoch'),
            lambda a: a['pins'].update(timing_generation=1),
            lambda a: a['pins'].update(calibration_content_id='0' * 64),
            lambda a: a['pins'].update(receiver_proof='0' * 64),
            lambda a: a['anchor_headers'].update(a=a['anchor_headers']['b']),
            lambda a: a['binding']['session'].update(site_id='echo'),
        ):
            args = deepcopy(self.args); change(args)
            with self.subTest(change=change), self.assertRaises(ValueError): self.prepare(**args)

    def test_clock_proof_expiry_and_ordinal_mismatch_refuse_even_with_updated_pins(self):
        for change in (
            lambda a: a.update(now_s=1800000003.),
            lambda a: a['receiver_proof']['evidence'].update(valid_until_monotonic_ns=10_599_000_000),
            lambda a: a['receiver_proof']['after'].update(boot_ns=99_000_000_000),
        ):
            args = deepcopy(self.args); change(args)
            args['pins']['receiver_proof'] = sha(encoded(args['receiver_proof']))
            with self.subTest(change=change), self.assertRaises(ValueError): self.prepare(**args)
        # A valid original header for sequence 10 cannot stand in for B=16.
        args = deepcopy(self.args); args['anchor_headers']['b'] = args['anchor_headers']['a']
        args['pins']['anchor_headers']['b'] = sha(args['anchor_headers']['b'].encode())
        with self.assertRaises(ValueError): self.prepare(**args)

    def test_malformed_npz_and_oversized_original_are_refused_before_array_allocation(self):
        for raw in (b'not an NPZ', b'x' * (4 * 1024**2 + 1)):
            args = deepcopy(self.args); args['originals']['npz'] = raw
            args['pins']['originals']['npz'] = dict(sha256=sha(raw), size=len(raw))
            with self.assertRaises(ValueError): self.prepare(**args)

    def test_model_must_analyze_registered_same_npz_png_and_matching_profile(self):
        lease = self.prepare()
        for change in (
            lambda e, f: f.update(sha256='0' * 64),
            lambda e, f: f.update(generation=f['generation'] + 1),
            lambda e, f: f['metadata'].update(capture_epoch='other'),
            lambda e, f: e['payload'].update(media_ids=['unrelated-image']),
            lambda e, f: e['payload'].update(engine_epoch='changed-engine'),
            lambda e, f: e['payload']['result'].update(frame_id='latest-other-frame'),
            lambda e, f: e['payload'].update(observed_at=0),
        ):
            event, frame = self.model_inputs(lease); change(event, frame)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.module.lift_supported_joints(lease, event=event, image_record=frame,
                    now_s=1800000000.65, projection='diagnostic_nominal_eeprom')
        with self.assertRaises(ValueError): self.lift(lease, projection='claimed_checked_reprojection')

    def test_png_metadata_is_defensive_and_originals_retire_after_deadline(self):
        lease = self.prepare(); m = lease.image_metadata; m['frame_id'] = 'changed'
        self.assertNotEqual(lease.image_metadata['frame_id'], 'changed')
        d = lease.descriptor; d['originals']['npz']['sha256'] = 'changed'
        self.assertNotEqual(lease.descriptor['originals']['npz']['sha256'], 'changed')
        event, frame = self.model_inputs(lease)
        with self.assertRaises(ValueError):
            self.module.lift_supported_joints(lease, event=event, image_record=frame,
                now_s=1800000003., projection='diagnostic_nominal_eeprom')

    def projection_inputs(self, lease):
        from test_apartment_room_pose_diagnostic import inputs, digest
        v = inputs(); v.pop('collection'); v.pop('attempt_id')
        v.update(lease=lease, now_s=1800000000.65)
        v['event'], v['image_record'] = self.model_inputs(lease)
        v['scope'].update(sessionId=self.binding['session']['id'], generation=self.binding['media_generation'])
        v['alignment']['camera'].update(capture_epoch=self.pins['capture_epoch'],
            calibration_content_id=self.pins['calibration_content_id'])
        v['alignment_original_bytes'] = (json.dumps(v['alignment'], sort_keys=True, indent=2) + '\n').encode()
        v['review'].update(alignment_sha256=sha(v['alignment_original_bytes']),
            matrix_sha256=digest(v['alignment']['matrix']))
        return v

    def test_ram_projection_has_distinct_provenance_and_still_requires_reviewed_transform(self):
        import apartment_room_pose_diagnostic as diagnostic
        lease = self.prepare(); v = self.projection_inputs(lease)
        out = diagnostic.project_ram_diagnostic_room_pose(**v)
        self.assertTrue(out['renderable'], out['reason'])
        self.assertEqual(out['status'], 'timing_unverified')
        p = out['diagnostic_pose']['provenance']
        self.assertEqual(p['input_family'], 'home-apartment-viewing-metric-ram-1')
        self.assertEqual(p['ram_originals']['npz']['sha256'], self.pins['originals']['npz']['sha256'])
        self.assertEqual(p['collection_sha256_semantics'], 'ram_original_binding_not_retained_collection')
        self.assertEqual(out['timing']['projection_qualification'], 'unqualified_nominal_eeprom')
        self.assertFalse(out['follow_allowed']); self.assertIsNone(out['room_pose'])
        for change in (lambda x: x.update(review=None), lambda x: x['scope'].update(frameDigest='0' * 64),
                       lambda x: x['alignment']['matrix'][0].__setitem__(0, 2.),
                       lambda x: x.update(now_monotonic_s=22.)):
            values = self.projection_inputs(lease); change(values)
            self.assertFalse(diagnostic.project_ram_diagnostic_room_pose(**values)['renderable'])

    def test_zero_depth_and_missing_pose_never_gain_invented_coordinates(self):
        import numpy as np
        args = deepcopy(self.args)
        with np.load(io.BytesIO(args['originals']['npz']), allow_pickle=False) as arrays:
            values = {key: arrays[key].copy() for key in arrays.files}
        values['depth_mm'].fill(0)
        raw = io.BytesIO(); np.savez_compressed(raw, **values)
        args['originals']['npz'] = raw.getvalue()
        args['pins']['originals']['npz'] = dict(sha256=sha(raw.getvalue()), size=len(raw.getvalue()))
        out = self.lift(self.prepare(**args))
        self.assertEqual(out['status'], 'unavailable')
        self.assertTrue(all(j['camera_xyz_m'] is None for j in out['instances'][0]['joints']))
        lease = self.prepare(); event, frame = self.model_inputs(lease)
        event['payload']['result'].pop('pose_instances')
        out = self.module.lift_supported_joints(lease, event=event, image_record=frame, now_s=1800000000.65)
        self.assertEqual(out['status'], 'unavailable'); self.assertEqual(out['instances'], [])

    def test_ram_tracker_retains_one_small_packet_and_never_renews_same_event(self):
        import apartment_room_pose_diagnostic as diagnostic
        lease = self.prepare(); v = self.projection_inputs(lease)
        now = [20.]
        tracker = diagnostic.RamDiagnosticPoseReceiptTracker(scope=v['scope'], alignment=v['alignment'],
            review=v['review'], active_collision_sha256=v['active_collision_sha256'],
            alignment_original_bytes=v['alignment_original_bytes'], monotonic=lambda: now[0])
        event, frame = self.model_inputs(lease)
        ingest = dict(lease=lease, event=event, image_record=frame, now_s=1800000000.65, session_state='recording')
        self.assertTrue(tracker.ingest(**ingest)['renderable'])
        now[0] = 21.; self.assertEqual(tracker.ingest(**ingest)['timing']['receipt_age_s'], 1.)
        now[0] = 22.; self.assertEqual(tracker.ingest(**ingest)['reason'], 'diagnostic_receipt_expired')
        self.assertIsNone(tracker.latest)
        now[0] = 22.1; self.assertFalse(tracker.ingest(**ingest)['renderable'])
        tracker.stop(); self.assertFalse(tracker.ingest(**ingest)['renderable'])
        self.assertIsNone(tracker.latest)

    def test_ram_packet_is_renderable_in_existing_public_consumer_without_admission(self):
        import apartment_room_pose_diagnostic as diagnostic
        import subprocess
        lease = self.prepare(); v = self.projection_inputs(lease)
        packet = diagnostic.project_ram_diagnostic_room_pose(**v)
        envelope = dict(schema='home-apartment-perception-1', scope=v['scope'], session_state='recording',
            source_epoch=packet['frame']['source_epoch'], frame=packet['frame'],
            pose2d=None, models=[], room_pose=None, diagnostic_room_pose=packet)
        script = """const fs=require('node:fs'),assert=require('node:assert/strict');
const View=require(process.argv[1]),input=JSON.parse(fs.readFileSync(0,'utf8'));
const packet=input.diagnostic_room_pose;
const out=View.project({envelope:input,scope:input.scope,clock:null,
 diagnosticReceipt:{eventKey:View.diagnosticEventKey(packet),remainingHoldS:1},collisionDigest:'e'.repeat(64)});
assert.equal(out.diagnosticRoomPose.renderable,true, out.diagnosticRoomPose.reason);
assert.equal(out.diagnosticRoomPose.joints.length,1);
assert.equal(out.roomPose.admitted,false);assert.equal(out.follow.allowed,false);
assert.equal(out.diagnosticRoomPose.provenance.input_family,'home-apartment-viewing-metric-ram-1');
process.stdout.write('same_npz_diagnostic_without_admission');"""
        result = subprocess.run(['node', '-e', script, str(ROOT / 'app/src/home-apartment-perception-view.js')],
            input=json.dumps(envelope), capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'same_npz_diagnostic_without_admission')

    def test_actual_registered_png_paired_runner_preserves_distinct_one_hz_result_validity(self):
        import tempfile
        import time
        from config import Settings
        from store import Store
        from runtime import PairedRunner, arm_valid_until
        from registered_image import RegisteredImage
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        now = [1799999999.5]
        store = Store(Settings(storage_root=Path(temp.name), min_free_bytes=0), clock=lambda: now[0])
        self.addCleanup(store.close)
        session, _ = store.create_session('den', 'detection', 'Authored metric PNG', 'metric-png-1hz')
        store.transition(session['id'], 'starting'); store.transition(session['id'], 'recording')
        session=store.get_session(session['id'])
        args = deepcopy(self.args); args['binding']['session']['id'] = session['id']; args['binding']['sampling_hz'] = 1
        lease = self.prepare(**args); now[0] = 1800000000.65
        image = store.add_media(session['id'], lease.image_bytes, 'image/png', lease.image_metadata, 'same-npz-metric')
        profiles = {arm: {**deepcopy(args['binding']['lab_profile']), 'profile_id': arm} for arm in ('a', 'b')}
        args['binding']['lab_profile'] = profiles['b']; lease = self.prepare(**args)
        calls = []
        def execute(request):
            rgb, metadata = RegisteredImage(store)(request['frame'])
            self.assertEqual(rgb[0, 0].tolist(), [254, 254, 254])
            self.assertEqual(metadata['source_npz_sha256'], self.pins['originals']['npz']['sha256'])
            calls.append(request)
            return dict(request_id=request['request_id'], engine_epoch=profiles[request['arm']]['engine_epoch'],
                artifact_sha256=profiles[request['arm']]['artifact_sha256'], terminal=True, state='completed',
                result={**deepcopy(self.event['payload']['result']), 'frame_id': request['frame']['frame_id']})
        runner = PairedRunner(store, session['id'], profiles, execute, clock=lambda: now[0],
            experiment_generation=args['binding']['model_generation'], sampling_hz={'a': 1, 'b': 1})
        self.addCleanup(runner.pool.shutdown, wait=True); self.addCleanup(runner.close)
        m = lease.image_metadata
        runner.offer({**m, 'session_id': session['id'], 'media_ids': [image['id']]})
        deadline = time.monotonic() + 2
        while True:
            found = [e for e in store.list_events(session['id']) if e['kind'] == 'perception.output' and e['payload']['arm'] == 'b']
            if found: break
            self.assertLess(time.monotonic(), deadline); time.sleep(.005)
        event = found[0]
        self.assertEqual(event['payload']['arm_snapshot']['frame']['captured_at'],m['observed_at'])
        self.assertEqual(event['payload']['arm_snapshot']['frame']['session_time_s'],m['observed_at']-session['started_at'])
        self.assertTrue(m['clock_mapping_id'].startswith('apartment-bundle-clock-1:'))
        self.assertEqual(event['payload']['valid_until'], arm_valid_until(m, 1))
        self.assertGreater(event['payload']['valid_until'], m['valid_until'])
        out = self.module.lift_supported_joints(lease, event=event, image_record=image, now_s=now[0])
        self.assertEqual(out['instances'][0]['joints'][0]['status'], 'candidate')
        self.assertEqual(out['source_times']['valid_until'], event['payload']['valid_until'])
        self.assertEqual(out['original_frame_record']['metadata']['valid_until'], m['valid_until'])


if __name__ == '__main__': unittest.main()
