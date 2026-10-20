"""Authored existing-endpoint originals; no camera/model or commissioned bounds."""
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT / 'tools/shared-home'))
from assessment_existing_api import validate_match
import live_capture_fixtures as F


def original_pair(*, sequence=7, bundle_sequence=None, **changes):
    jpeg, header, _, calibration = F.inputs(sequence=sequence, **changes)
    metadata = F.frame_metadata(sequence=sequence if bundle_sequence is None else bundle_sequence,
        rgb_device_s=(50_200_000_000 + (sequence - 7) * 1_000_000_000) / 1e9)
    receipt = F.decoded_header(header)
    receipt['preview_sha256'] = hashlib.sha256(bytes(540 * 640)).hexdigest()
    header = base64.urlsafe_b64encode(json.dumps(receipt).encode()).decode()
    return jpeg, header, F.bundle(metadata), json.dumps(calibration, indent=2).encode()


class MatchTests(unittest.TestCase):
    def test_original_existing_endpoint_bytes_match_without_clock_or_person_qualification(self):
        jpeg, header, bundle, calibration = original_pair()
        checked = validate_match(jpeg, header, bundle, calibration)
        self.assertEqual(checked['source_metadata']['frames']['rgb']['sequence'], 7)
        self.assertEqual(checked['receipt']['frame_device_ns'], 50200000000)
        self.assertEqual(checked['calibration'], json.loads(calibration))
        self.assertEqual(checked['observed_at'], 1800000000.)

    def test_exact_match_rejects_nearby_sequence_device_epoch_and_wrong_nv12(self):
        jpeg, header, bundle, raw_cal = original_pair()
        for sequence, reason in ((6, 'behind'), (8, 'ahead')):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                validate_match(jpeg, header, original_pair(bundle_sequence=sequence)[2], raw_cal)
        changed = F.decoded_header(header)
        changed['preview_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'nv12_mismatch'):
            validate_match(jpeg, base64.urlsafe_b64encode(json.dumps(changed).encode()).decode(), bundle, raw_cal)
        meta = F.frame_metadata(rgb_device_s=50.3)
        with self.assertRaisesRegex(ValueError, 'device_mismatch'):
            validate_match(jpeg, header, F.bundle(meta), raw_cal)
        with self.assertRaisesRegex(ValueError, 'epoch_changed'):
            validate_match(jpeg, header, bundle, json.dumps({**json.loads(raw_cal), 'session':'other'}).encode())

    def test_oversized_npy_header_is_refused_before_numpy_load(self):
        import zipfile
        import numpy as np
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            arr = io.BytesIO()
            np.lib.format.write_array_header_1_0(arr, {'descr':'<u2', 'fortran_order':False, 'shape':(4000000, 640)})
            archive.writestr('depth_mm.npy', arr.getvalue())
        jpeg, header, _, raw_cal = original_pair()
        with patch('numpy.load', side_effect=AssertionError('decoder invoked before bounds')) as load:
            with self.assertRaises(Exception):
                validate_match(jpeg, header, stream.getvalue(), raw_cal)
        load.assert_not_called()

    def test_policy_is_closed_and_smaller_explicit_bounds_are_accepted(self):
        from assessment_existing_api import policy
        value = dict(schema='home-lab-existing-api-assessment-policy-1', version=1,
            max_captures=2, max_npz_reads_per_capture=1, max_retained_bytes=1000000)
        self.assertEqual(policy(value), value)
        for changed in ({'max_captures':25}, {'max_captures':True}, {'max_npz_reads_per_capture':3},
                        {'max_retained_bytes':41943041}, {'upstream':'http://elsewhere'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                policy({**value, **changed})


class ExistingApiTapTests(unittest.TestCase):
    def setUp(self):
        from config import Settings
        from store import Store
        from preview_ingest import PreviewIngestor
        from preview_client import LocalBundleClient
        self.now = F.NOW + .2
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Settings(storage_root=Path(self.directory.name), min_free_bytes=0), clock=lambda:self.now)
        self.addCleanup(self.store.close)
        self.now = F.NOW - .5
        self.session, _ = self.store.create_session('den', 'detection', 'Fictional assessment', 'tap-fixture')
        self.store.transition(self.session['id'], 'starting')
        self.store.transition(self.session['id'], 'recording')
        self.now = F.NOW + .2
        self.jpeg, self.header, self.bundle, self.calibration = original_pair()
        self.source = PreviewIngestor(self.store, site_id='victoria', camera_id='den',
            clock_validation_id='fictional-receiver', clock_uncertainty_s=.01)
        self.frame = self.source.ingest(self.session['id'], self.jpeg, self.header)
        self.runtime = SimpleNamespace(store=self.store, closed=False,
            runners={self.session['id']:SimpleNamespace(generation=1, closed=False)},
            preview_pumps={self.session['id']:SimpleNamespace(stopped=threading.Event())})
        self.store.append_event(self.session['id'], 'experiment.activated', {'generation':1})
        self.calls = []
        owner = self
        class Client(LocalBundleClient):
            def _read(self, path):
                owner.calls.append(path)
                return (owner.bundle if path == '/frame.npz' else owner.calibration), ''
        self.client = Client(lambda:'fictional-camera-token')
        self.policy = dict(schema='home-lab-existing-api-assessment-policy-1', version=1,
            max_captures=24, max_npz_reads_per_capture=2, max_retained_bytes=41943040)
        # Windows cannot fsync an opened directory via the Python POSIX API.
        # Only namespace durability is stubbed; actual Store data fsync remains.
        import os
        if os.name == 'nt':
            stub = patch('assessment_existing_api.sync_directory', lambda _:None)
            stub.start(); self.addCleanup(stub.stop)

    def tap(self, **overrides):
        from assessment_existing_api import AssessmentExistingApiTap
        tap = AssessmentExistingApiTap(self.store, site_id='victoria', camera_id='den',
            client=self.client, policy={**self.policy, **overrides})
        tap.bind_runtime(self.runtime)
        return tap

    def test_retains_originals_after_admitted_rgb_and_exact_retry_does_not_read(self):
        tap = self.tap()
        terminal = tap(self.jpeg, self.header)
        self.assertEqual(terminal['status'], 'retained')
        self.assertEqual(self.calls, ['/frame.npz', '/calibration'])
        row = self.store.get_media(self.session['id'], terminal['association_media_ref']['id'])
        association = json.loads(self.store.media_path(self.session['id'], row['id']).read_bytes())
        self.assertEqual(association['capture']['rgb_media_ref']['id'], self.frame['media_ids'][0])
        self.assertEqual(association['depth_clock']['mapping'], None)
        self.assertFalse(association['beam_authority'])
        self.now += .1
        self.assertEqual(tap(self.jpeg, self.header), terminal)
        self.assertEqual(len(self.calls), 2)

    def test_lost_terminal_ack_is_sticky_and_does_not_write_changed_second_terminal(self):
        from assessment_existing_api import TERMINAL_KIND
        tap = self.tap()
        append = self.store.append_event
        def lose_ack(sid, kind, body, **kwargs):
            result = append(sid, kind, body, **kwargs)
            if kind == TERMINAL_KIND:
                raise OSError('fictional lost ACK after durable write')
            return result
        with patch.object(self.store, 'append_event', side_effect=lose_ack):
            tap(self.jpeg, self.header)
        rows = [e for e in self.store.list_events(self.session['id']) if e['kind'] == TERMINAL_KIND]
        self.assertEqual(len(rows), 1)
        self.assertEqual(tap.status()['state'], 'incomplete')
        calls = len(self.calls)
        tap(self.jpeg, self.header)
        self.assertEqual(len(self.calls), calls)

    def test_behind_attempt_is_preserved_then_only_one_bounded_second_read(self):
        tap = self.tap()
        self.bundle = original_pair(bundle_sequence=6)[2]
        terminal = tap(self.jpeg, self.header)
        self.assertEqual(terminal['status'], 'miss')
        self.assertEqual([a['result'] for a in terminal['attempts']], ['behind', 'behind'])
        self.assertEqual(self.calls, ['/frame.npz', '/calibration'] * 2)
        self.assertTrue(all(a['npz_media_ref'] is not None and a['calibration_media_ref'] is not None
                            for a in terminal['attempts']))
        self.assertEqual(tap(self.jpeg, self.header), terminal)
        self.assertEqual(len(self.calls), 4)

    def test_ahead_is_terminal_and_does_not_retry_until_success(self):
        self.bundle = original_pair(bundle_sequence=8)[2]
        terminal = self.tap()(self.jpeg, self.header)
        self.assertEqual((terminal['status'], terminal['reason']), ('miss', 'ahead'))
        self.assertEqual(len(self.calls), 2)

    def test_pre_activation_capture_waits_without_binding_or_fetching(self):
        tap = self.tap()
        runner = self.runtime.runners.pop(self.session['id'])
        self.assertIsNone(tap(self.jpeg, self.header))
        self.assertEqual(tap.status()['state'], 'unbound')
        self.assertFalse(self.calls)
        self.runtime.runners[self.session['id']] = runner
        self.assertEqual(tap(self.jpeg, self.header)['status'], 'retained')

    def test_stop_during_first_read_prevents_calibration_and_latches_incomplete(self):
        tap = self.tap()
        read = self.client._read
        def stop(path):
            raw = read(path)
            self.runtime.preview_pumps[self.session['id']].stopped.set()
            return raw
        with patch.object(self.client, '_read', side_effect=stop):
            terminal = tap(self.jpeg, self.header)
        self.assertEqual(terminal['status'], 'incomplete')
        self.assertEqual(self.calls, ['/frame.npz'])
        self.assertEqual(tap.status()['state'], 'incomplete')
        tap(self.jpeg, self.header)
        self.assertEqual(len(self.calls), 1)

    def test_model_generation_change_during_read_cannot_select_originals(self):
        tap = self.tap()
        read = self.client._read
        def changed(path):
            raw = read(path)
            self.runtime.runners[self.session['id']].generation = 2
            return raw
        with patch.object(self.client, '_read', side_effect=changed):
            terminal = tap(self.jpeg, self.header)
        self.assertEqual((terminal['status'], terminal['reason']), ('incomplete', 'generation_changed'))
        self.assertIsNone(terminal['association_media_ref'])
        self.assertEqual(self.calls, ['/frame.npz'])

    def test_runner_closed_during_read_stops_before_calibration_even_with_same_generation(self):
        tap = self.tap()
        read = self.client._read
        def closed(path):
            raw = read(path)
            self.runtime.runners[self.session['id']].closed = True
            return raw
        with patch.object(self.client, '_read', side_effect=closed):
            terminal = tap(self.jpeg, self.header)
        self.assertEqual((terminal['status'], terminal['reason']), ('incomplete', 'stop'))
        self.assertEqual(self.calls, ['/frame.npz'])
        self.assertIsNone(terminal['association_media_ref'])

    def test_expiry_during_read_retains_raw_without_another_get_or_retiming(self):
        tap = self.tap()
        read = self.client._read
        def expired(path):
            raw = read(path)
            self.now = self.frame['valid_until']
            return raw
        with patch.object(self.client, '_read', side_effect=expired):
            terminal = tap(self.jpeg, self.header)
        self.assertEqual((terminal['status'], terminal['reason']), ('miss', 'expired'))
        self.assertIsNotNone(terminal['attempts'][0]['npz_media_ref'])
        self.assertEqual(self.calls, ['/frame.npz'])
        self.assertEqual(self.store.get_media(self.session['id'], self.frame['media_ids'][0])['metadata']['valid_until'],
                         self.frame['valid_until'])

    def test_exhaustion_records_once_and_never_reads_another_capture(self):
        from assessment_existing_api import WINDOW_KIND
        tap = self.tap(max_captures=1)
        tap(self.jpeg, self.header)
        self.assertEqual(tap.status()['state'], 'exhausted')
        jpeg, header, _, _ = original_pair(sequence=8)
        self.source.ingest(self.session['id'], jpeg, header)
        self.assertIsNone(tap(jpeg, header))
        self.assertEqual(len(self.calls), 2)
        closed = [e for e in self.store.list_events(self.session['id']) if e['kind'] == WINDOW_KIND]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]['payload']['last_original']['sequence'], 7)

    def test_full_derivative_write_budget_refuses_before_false_success(self):
        tap = self.tap(max_retained_bytes=200000)
        terminal = tap(self.jpeg, self.header)
        self.assertEqual(tap.status()['state'], 'incomplete')
        self.assertIsNone(terminal['association_media_ref'])
        self.assertEqual(terminal['reason'], 'capacity_exhausted')
        count = len(self.calls)
        tap(self.jpeg, self.header)
        self.assertEqual(len(self.calls), count)

    def test_same_capture_changed_header_refuses_without_new_source_identity(self):
        tap = self.tap()
        first = tap(self.jpeg, self.header)
        changed = F.decoded_header(self.header)
        changed['preview_sha256'] = '0' * 64
        tap(self.jpeg, base64.urlsafe_b64encode(json.dumps(changed).encode()).decode())
        self.assertEqual(tap.status()['state'], 'incomplete')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(tap(self.jpeg, self.header), first)

    def test_read_receipts_are_frozen_before_durable_store_writes(self):
        tap = self.tap()
        add = self.store.add_media
        def slow(*args, **kwargs):
            self.now += .01
            return add(*args, **kwargs)
        with patch.object(self.store, 'add_media', side_effect=slow):
            terminal = tap(self.jpeg, self.header)
        attempt = terminal['attempts'][0]
        self.assertEqual(attempt['npz_read_completed_at'], F.NOW + .2)
        self.assertLess(attempt['npz_read_completed_at'], attempt['calibration_read_completed_at'])
        self.assertLess(attempt['calibration_read_completed_at'], terminal['processing_completed_at'])


if __name__ == '__main__':
    unittest.main()
