"""Actual factory/Store paired originals; camera/model external I/O is authored.

No tests run by the implementing agent. The coordinator runs this finite suite.
The reused fixture's static qualification objects are never passed to candidates.
"""
from copy import deepcopy
import base64
import json
import io
from pathlib import Path
import subprocess
import sys
import unittest
import zipfile
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'home-context'))
from lab_assessment_candidates import LabAssessmentCandidates, AUTHORITY, ROLES, sha, EXISTING_MODE, EXISTING_ROLES
from live_capture import PARTS
from paired_preview_client import encoded
from test_lab_live_desk_anchor import GeneratedStereoFixture


class CandidateFactoryFixture(GeneratedStereoFixture):
    """Actual factory with authored raw EEPROM and lifecycle readiness only.

    No static spatial qualification or commissioned depth-clock substitute is
    consumed. The EEPROM describes an explicit fictional 3.75 cm RGB offset;
    production rgbd_model/DepthLifter perform the actual projection unchanged.
    The managed readiness receipt is an explicitly authored external lifecycle
    double, appended before the first pair/model offer on the real Store.
    """
    def append_pair(self, **kwargs):
        from test_paired_preview_client import authored_pair
        original = authored_pair
        rotation = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
        def calibrated(*args, **options):
            pair = original(*args, **options)
            # Authored raw RGB is an all-zero NV12 plane. Bind that actual
            # content BEFORE the real RGB admission; the old placeholder
            # digest is not an exact existing-API measurement original.
            receipt = json.loads(base64.urlsafe_b64decode(pair['header'] + '=' * ((-len(pair['header'])) % 4)))
            receipt['preview_sha256'] = sha(bytes(540 * 640))
            pair['header'] = base64.urlsafe_b64encode(encoded(receipt)).decode()
            pair['calibration']['eeprom'] = {
                'cameraData': [[0, {'extrinsics': {'toCameraSocket': -1}}],
                    [1, {'extrinsics': {'toCameraSocket': 0, 'rotationMatrix': deepcopy(rotation),
                        'translation': {'x': -3.75, 'y': 0., 'z': 0.}}}]],
                'stereoRectificationData': {'leftCameraSocket': 1, 'rightCameraSocket': 2,
                                          'rectifiedRotationLeft': deepcopy(rotation)}}
            return pair
        if not getattr(self, '_candidate_ready', False):
            self.store.append_event(self.session['id'], 'workers.ready', {
                'receipts': {'b': {'state': 'ready', 'session_id': self.session['id'],
                    'engine_epoch': self.profile['engine_epoch'], 'profile': deepcopy(self.profile),
                    'expires_at': self.now + 30}}, 'fixture_evidence': 'authored_external_lifecycle_double'})
            self._candidate_ready = True
        self.runtime.paired_depth_clocks.pop('den', None)
        kwargs['derive'] = False
        with patch('test_paired_preview_client.authored_pair', side_effect=calibrated):
            return super().append_pair(**kwargs)


def candidate_inputs(f):
    rows = f.store.list_media(f.session['id'])['items']
    pair = next(row for row in rows if row['metadata'].get('stream') == 'paired_preview_original'
                and row['metadata'].get('rgb_media_id') == f.rgb['id'])
    records = {}
    for role, mid in (('rgb', f.rgb['id']), ('bundle', f.paired['bundle_media_id']), ('paired_original', pair['id'])):
        row = f.store.get_media(f.session['id'], mid)
        records[role] = {'record': row, 'data': f.store.media_path(f.session['id'], mid).read_bytes()}
    return deepcopy(f.row), deepcopy(f.rgb), records


def existing_candidate_inputs(f, *, max_captures=1, result='exact'):
    """Actual existing-API tap over the same real factory/Store originals.

    Only the two fixed camera-owner reads are external I/O doubles. The modern
    fixture's earlier originals are not consumed by this successor.
    """
    from assessment_existing_api import AssessmentExistingApiTap, START_KIND, TERMINAL_KIND
    from preview_client import LocalBundleClient
    class Client(LocalBundleClient):
        def _read(self, path):
            if result == 'http_unavailable': raise OSError('authored missing endpoint')
            if path == '/frame.npz': return f.pair['bundle'], ''
            if path == '/calibration': return encoded(f.pair['calibration']), ''
            raise AssertionError('unexpected camera path')
    tap = AssessmentExistingApiTap(f.store, site_id='victoria', camera_id='den',
        client=Client(lambda: 'authored-token'), policy=dict(
            schema='home-lab-existing-api-assessment-policy-1', version=1, max_captures=max_captures,
            max_npz_reads_per_capture=2, max_retained_bytes=41943040))
    tap.bind_runtime(f.runtime)
    f.now += .1  # Genuine separate retention, after original model availability.
    with patch('assessment_existing_api.sync_directory') if sys.platform == 'win32' else _no_patch():
        terminal = tap(f.pair['jpeg'], f.pair['header'])
    events = f.store.list_events(f.session['id'])
    started = next(e for e in events if e['kind'] == START_KIND)
    terminal = next(e for e in events if e['kind'] == TERMINAL_KIND)
    media = None
    if terminal['payload']['status'] == 'retained':
        association = f.store.get_media(f.session['id'], terminal['payload']['association_media_ref']['id'])
        a = json.loads(f.store.media_path(f.session['id'], association['id']).read_bytes())
        refs = dict(rgb=started['payload']['capture']['rgb_media_ref'], bundle=a['bundle_media_ref'],
                    calibration_original=a['calibration_original_media_ref'], association=terminal['payload']['association_media_ref'])
        media = {role: {'record': f.store.get_media(f.session['id'], ref['id']),
                       'data': f.store.media_path(f.session['id'], ref['id']).read_bytes()} for role, ref in refs.items()}
    return tap, started, terminal, media


def _no_patch():
    from contextlib import nullcontext
    return nullcontext()


class LabAssessmentCandidateTests(unittest.TestCase):
    def setUp(self):
        self.f = CandidateFactoryFixture(self)
        self.event, self.frame, self.media = candidate_inputs(self.f)
        self.collector = self.make()

    def make(self):
        f = self.f
        return LabAssessmentCandidates(f.session, arm='b', generation=f.row['payload']['generation'],
            media_generation=f.session['generation'], lab_profile=f.profile)

    def collect(self, **kwargs):
        return self.collector.collect(self.event, received_at=kwargs.pop('received_at', self.f.now),
            frame_record=self.frame, paired_records=self.media, **kwargs)

    def test_clean_nominal_core_import_needs_no_home_authority_graph(self):
        # A fresh child cannot inherit the actual-factory fixture's Home
        # imports. Block those modules while retaining genuine Lab deps.
        script = r'''
import builtins, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
for path in (root / 'stack/services/perception-lab', root / 'tools/shared-home'):
    sys.path.append(str(path))
blocked = {'live_capture', 'lab_live_acquisition', 'journal', 'lab_live_runtime', 'paired_preview_client',
           'lab_live_desk_anchor', 'lab_live_desk_admission', 'lab_stereo_desk_capability',
           'fixture_canonical_authority'}
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in blocked:
        raise AssertionError('nominal core imported Home authority: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
from lab_assessment_candidates import LabAssessmentCandidates, AUTHORITY
profile = dict(profile_id='authored-profile', engine_epoch='authored-engine',
               model_version='authored-model', artifact_sha256='a' * 64)
source = LabAssessmentCandidates(dict(id='authored-session', site_id='victoria', camera_id='den'),
    arm='b', generation=1, media_generation=0, lab_profile=profile)
row = source.collect(dict(id=1, session_id='authored-session', kind='perception.output',
    payload=dict(arm='b', generation=1, site_id='victoria', camera_id='den', status='failed',
                 observed_at=1., available_at=2., valid_until=3.)), received_at=2.)
assert row['status'] == 'refused' and row['error_bound_m'] is None
assert tuple(row['parts']) == ('work', 'body', 'head', 'hand_left', 'hand_right',
                              'protection', 'body_forward', 'head_forward')
assert all(part['status'] == 'unavailable' for part in row['parts'].values())
assert all(row[key] == value for key, value in AUTHORITY.items())
assert not blocked.intersection(sys.modules)
print(json.dumps({'status': 'private_candidate_only', 'operational_geometry': row['operational_geometry']}))
'''
        root = HERE.parents[2]
        result = subprocess.run([sys.executable, '-E', '-B', '-c', script, str(root)], cwd=root,
            capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'status': 'private_candidate_only', 'operational_geometry': None})

    def test_actual_factory_outputs_numeric_candidate_without_estimator_or_person_qualification(self):
        with patch('journal.Journal.append_event', side_effect=AssertionError('private candidate attempted admission')):
            row = self.collect()
        self.assertEqual(row['status'], 'candidate', row['reason'])
        nose = row['instances'][0]['joints'][0]
        self.assertEqual(nose['status'], 'candidate')
        self.assertAlmostEqual(nose['camera_xyz_m'][2], 1.234)
        self.assertIsNone(nose['error_bound_m'])
        self.assertEqual(row['coordinate_frame'], 'rectified_left')
        self.assertEqual(set(row['parts']), set(PARTS))
        self.assertTrue(all(v['status'] == 'unavailable' for v in row['parts'].values()))
        self.assertIsNone(row['person_ref']); self.assertIsNone(row['subject_ref'])
        for k, v in AUTHORITY.items(): self.assertEqual(row[k], v, k)

    def test_all_17_joint_slots_preserve_unseen_hands_and_no_protection_enclosure(self):
        row = self.collect()
        joints = row['instances'][0]['joints']
        self.assertEqual([j['joint_id'] for j in joints], list(range(17)))
        for index in (9, 10, 11, 12, 16):
            self.assertEqual(joints[index]['status'], 'unavailable')
            self.assertIsNone(joints[index]['camera_xyz_m'])
        self.assertNotIn('shape', row['parts']['head'])
        self.assertNotIn('shape', row['parts']['protection'])

    def test_immutable_retry_does_not_refresh_any_original_or_first_receipt(self):
        first = self.collect()
        second = self.collect(received_at=self.f.now + 100)
        self.assertEqual(first, second)
        self.assertEqual(first['source_times']['observed_at'], self.event['payload']['observed_at'])
        self.event['payload']['valid_until'] += 1
        with self.assertRaisesRegex(ValueError, 'identity_changed'): self.collect()

    def test_expired_original_is_measured_as_historical_never_made_current(self):
        row = self.collect(received_at=self.event['payload']['valid_until'] + 100)
        self.assertEqual(row['status'], 'candidate')
        self.assertFalse(row['source_fresh_at_collection'])
        self.assertEqual(row['source_times']['valid_until'], self.event['payload']['valid_until'])
        self.assertIsNone(row['operational_geometry'])

    def test_no_person_and_failed_model_attempts_are_retained_not_vacancy(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                row = deepcopy(self.event)
                row['payload']['result'].update(boxes=[], objects=[], pose_instances=[])
                if failed: row['payload']['status'] = 'failed'
                collector = self.make()
                result = collector.collect(row, received_at=self.f.now)
                self.assertEqual(result['original_event'], row)
                self.assertEqual(len(collector.records), 1)
                self.assertEqual(result['status'], 'refused')
                self.assertIsNone(result['person_ref']); self.assertFalse(result['absence_authority'])

    def test_missing_pair_still_retains_pixels_failure_and_original_bytes_digest(self):
        row = self.collector.collect(self.event, received_at=self.f.now)
        self.assertEqual(row['reason'], 'original_pair_unavailable')
        self.assertEqual(row['original_event'], self.event)
        self.assertEqual(row['original_event_sha256'], sha(encoded(self.event)))
        self.assertEqual(row['instances'][0]['joints'][0]['pixel'], [320., 150.])

    def test_unknown_depth_clock_never_becomes_exposure_time_or_zero_error(self):
        pair = self.media['paired_original']
        body = json.loads(pair['data'])
        body.update(depth_clock_status='unavailable', depth_clock_policy=None, depth_clock_mapping=None)
        pair['data'] = encoded(body)
        pair['record'].update(size=len(pair['data']), sha256=sha(pair['data']))
        row = self.collect()
        self.assertEqual(row['status'], 'candidate', row['reason'])
        self.assertEqual(row['depth_clock']['status'], 'unavailable')
        self.assertIsNone(row['depth_clock']['mapping'])
        self.assertIsNone(row['depth_clock']['exposure_error_bound_s'])
        self.assertEqual(row['depth_clock']['first_pair_received_at'], body['first_pair_received_at'])
        self.assertIsNone(row['error_bound_m'])

    def test_bytes_generation_frame_and_calibration_mutations_refuse_before_projection(self):
        originals = deepcopy((self.frame, self.media))
        for attack in ('bytes', 'generation', 'frame', 'calibration'):
            with self.subTest(attack=attack):
                self.frame, self.media = deepcopy(originals)
                if attack == 'bytes': self.media['bundle']['data'] += b'changed'
                if attack == 'generation': self.media['bundle']['record']['generation'] += 1
                if attack == 'frame':
                    self.frame['metadata']['frame_id'] = 'foreign-frame'
                    self.media['rgb']['record'] = deepcopy(self.frame)
                if attack == 'calibration':
                    self.media['bundle']['record']['metadata']['calibration']['depth_units'] = 'foreign'
                with patch('room_mapper.rgbd_model', side_effect=AssertionError('invalid media reached model')):
                    row = self.make().collect(self.event, received_at=self.f.now,
                        frame_record=self.frame, paired_records=self.media)
                self.assertEqual(row['status'], 'refused')
                self.assertTrue(all(j['camera_xyz_m'] is None for i in row['instances'] for j in i['joints']))

    def test_malformed_npz_is_refused_by_existing_header_validator(self):
        bundle = self.media['bundle']; bundle['data'] = b'not-a-numeric-npz'
        bundle['record'].update(size=len(bundle['data']), sha256=sha(bundle['data']))
        pair = self.media['paired_original']; body = json.loads(pair['data'])
        body['bundle_sha256'] = bundle['record']['sha256']; pair['data'] = encoded(body)
        pair['record'].update(size=len(pair['data']), sha256=sha(pair['data']))
        with patch('room_mapper.rgbd_model', side_effect=AssertionError('bad NPZ reached projection')):
            row = self.collect()
        self.assertEqual(row['status'], 'refused')

    def test_more_instances_than_existing_projection_capacity_are_explicit_refusal(self):
        self.event['payload']['result']['pose_instances'] *= 3
        row = self.collect()
        self.assertEqual(row['reason'], 'existing_pose_projection_capacity_exceeded')
        self.assertEqual(row['original_pose_instance_count'], 3)
        self.assertEqual(row['original_event'], self.event)

    def test_oversized_declared_numpy_shape_refuses_before_any_pair_or_projection_decode(self):
        import numpy as np
        header = io.BytesIO()
        np.lib.format.write_array_header_1_0(header, {'descr': '<u2', 'fortran_order': False,
                                                     'shape': (2**30, 2**30)})
        raw = io.BytesIO()
        bundle = self.media['bundle']
        with zipfile.ZipFile(io.BytesIO(bundle['data'])) as before, zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as out:
            for name in before.namelist():
                out.writestr(name, header.getvalue() if name == 'depth_mm.npy' else before.read(name))
        bundle['data'] = raw.getvalue()
        bundle['record'].update(size=len(bundle['data']), sha256=sha(bundle['data']))
        pair = self.media['paired_original']; body = json.loads(pair['data'])
        body['bundle_sha256'] = bundle['record']['sha256']; pair['data'] = encoded(body)
        pair['record'].update(size=len(pair['data']), sha256=sha(pair['data']))
        from recorder import validate_npz
        with patch('paired_preview_client.validate_npz', wraps=validate_npz) as guard, \
             patch('numpy.load', side_effect=AssertionError('NumPy allocation was invoked')) as decoder, \
             patch('room_mapper.rgbd_model', side_effect=AssertionError('nominal projection was invoked')) as model:
            row = self.collect()
        guard.assert_called_once()
        decoder.assert_not_called(); model.assert_not_called()
        self.assertEqual(row['status'], 'refused')

    def test_duplicate_joint_ids_cannot_bind_first_pixel_to_last_sample(self):
        joints = self.event['payload']['result']['pose_instances'][0]['joints']
        duplicate = deepcopy(joints[0]); duplicate['pixel'] = [500., 250.]
        joints.append(duplicate)
        row = self.collect()
        self.assertEqual(row['status'], 'refused')
        self.assertEqual(row['reason'], 'duplicate_original_joint_id')
        self.assertEqual(row['original_event'], self.event)

    def test_negative_or_reversed_pair_receipts_are_not_mapped_as_valid_time(self):
        original = deepcopy(self.media['paired_original'])
        for first, retained in ((-1, self.f.now), (self.f.now, self.f.now - .1)):
            with self.subTest(first=first, retained=retained):
                pair = deepcopy(original); body = json.loads(pair['data'])
                body.update(first_pair_received_at=first, retained_at=retained)
                pair['data'] = encoded(body); pair['record'].update(size=len(pair['data']), sha256=sha(pair['data']))
                self.media['paired_original'] = pair
                row = self.make().collect(self.event, received_at=self.f.now,
                    frame_record=self.frame, paired_records=self.media)
                self.assertEqual(row['status'], 'refused')
                self.assertEqual(row['reason'], 'original_pair_receipt_clock_refused')

    def test_unassessed_collector_clock_skew_does_not_discard_nominal_measurement(self):
        row = self.collect(received_at=self.f.now - 10)
        self.assertEqual(row['status'], 'candidate', row['reason'])
        self.assertEqual(row['collector_clock_qualification'], 'unassessed')
        self.assertIsNone(row['collector_clock_error_bound_s'])
        self.assertFalse(row['source_fresh_at_collection'])
        self.assertLess(row['diagnostic_collector_minus_model_available_s'], 0)


class ExistingApiCandidateTests(unittest.TestCase):
    def setUp(self):
        self.f = CandidateFactoryFixture(self)
        self.tap, self.start, self.terminal, self.media = existing_candidate_inputs(self.f)

    def make(self):
        f = self.f
        return LabAssessmentCandidates(f.session, arm='b', generation=f.row['payload']['generation'],
            media_generation=f.session['generation'], lab_profile=f.profile,
            collection_mode=EXISTING_MODE, existing_api_binding=self.start['payload']['binding'])

    def collect(self, **kwargs):
        return self.make().collect(self.f.row, received_at=self.f.row['payload']['available_at'],
            frame_record=self.f.rgb, paired_records=self.media,
            capture_started=kwargs.pop('capture_started', self.start),
            capture_terminal=kwargs.pop('capture_terminal', self.terminal), **kwargs)

    def test_actual_tap_nominal_sample_preserves_late_retention_and_unknown_exposure(self):
        with patch('journal.Journal.append_event', side_effect=AssertionError('candidate attempted journal')):
            row = self.collect()
        self.assertEqual(row['status'], 'candidate', row['reason'])
        self.assertEqual(row['schema'], 'home-lab-assessment-attempt-2')
        self.assertGreater(row['depth_clock']['association_created_at'], row['source_times']['available_at'])
        self.assertNotIn('first_pair_received_at', row['depth_clock'])
        self.assertEqual(row['depth_clock']['status'], 'unassessed')
        self.assertIsNone(row['depth_clock']['exposure_error_bound_s'])
        self.assertEqual(set(row['media_records']), set(EXISTING_ROLES))
        self.assertEqual(row['first_consumer_received_at'], self.f.row['payload']['available_at'])
        self.assertAlmostEqual(row['instances'][0]['joints'][0]['camera_xyz_m'][2], 1.234)
        self.assertTrue(all(row[k] == value for k, value in AUTHORITY.items()))
        self.assertTrue(all(p['status'] == 'unavailable' for p in row['parts'].values()))

    def test_missing_terminal_retains_original_pixels_without_becoming_a_pair_miss_retry(self):
        result = self.collect(capture_terminal=None)
        self.assertEqual(result['reason'], 'assessment_capture_terminal_missing')
        self.assertEqual(result['instances'][0]['joints'][0]['pixel'], [320., 150.])
        self.assertEqual(result['original_event'], self.f.row)
        self.assertIsNone(result['operational_geometry'])

    def test_start_terminal_frame_and_generation_mutations_refuse(self):
        for key in ('start_id', 'generation', 'frame', 'clock'):
            with self.subTest(key=key):
                start, terminal = deepcopy(self.start), deepcopy(self.terminal)
                if key == 'start_id': terminal['payload']['started_event_id'] += 1
                if key == 'generation': start['payload']['binding']['experiment_generation'] += 1
                if key == 'frame': start['payload']['capture']['rgb_frame_id'] = 'foreign'
                if key == 'clock': terminal['payload']['processing_completed_at'] = start['payload']['tap_started_at'] - 1
                row = self.collect(capture_started=start, capture_terminal=terminal)
                self.assertEqual(row['status'], 'refused', row)

    def test_changed_raw_calibration_and_association_hash_refuse_before_projection(self):
        saved = deepcopy(self.media)
        for role in ('calibration_original', 'association', 'bundle'):
            with self.subTest(role=role):
                self.media = deepcopy(saved)
                self.media[role]['data'] += b'changed'
                with patch('room_mapper.rgbd_model', side_effect=AssertionError('invalid originals projected')):
                    row = self.collect()
                self.assertEqual(row['status'], 'refused')
                self.assertEqual(row['reason'], 'original_media_bytes_or_generation_changed')

    def test_exact_retry_cannot_replace_missing_terminal_or_refresh_first_receipt(self):
        collector = self.make()
        first = collector.collect(self.f.row, received_at=self.f.now, capture_started=self.start)
        retry = collector.collect(self.f.row, received_at=self.f.now + 10, capture_started=self.start)
        self.assertEqual(first, retry)
        with self.assertRaisesRegex(ValueError, 'identity_changed'):
            collector.collect(self.f.row, received_at=self.f.now + 10, capture_started=self.start,
                capture_terminal=self.terminal, frame_record=self.f.rgb, paired_records=self.media)


if __name__ == '__main__': unittest.main()
