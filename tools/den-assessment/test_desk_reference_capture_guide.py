"""Candidate-free Guide HTTP contracts; authored measurements, no prediction I/O.

Proposal only. The coordinator applies/runs this against the reviewed Guide.
Only POSIX directory fsync is doubled on Windows. Recorder writes/file fsync,
bootstrap core, raw measurement conversion and authenticated loopback HTTP are real.
"""
from contextlib import contextmanager
from copy import deepcopy
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('candidate_free_reference_guide', HERE / 'desk-geometry-guide.py')
guide = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guide)

PARTS = ('work', 'body', 'head', 'hand_left', 'hand_right', 'protection')
AUTHORITY = dict(geometry=False, association=False, engagement=False, beam=False, physical=False)
REQUIREMENTS = ('max_work_center_error_m', 'min_containment_fraction', 'max_undercoverage_fraction',
                'min_complete_protection_fraction', 'min_reference_s', 'max_capture_to_available_s')


def references():
    return dict(frame='z_up_metric_floor0', unit='m',
        equipment_ref=dict(revision=2, digest='a'*64, file_sha256='b'*64),
        binding=dict(content_revision='authored-reference-only', registration_version=1,
            manifest_digest='c'*64, frame_digest='d'*64, canonical_collision_digest='e'*64),
        floor=dict(id='floor', normal=[0, 0, 1], offset_m=0),
        walls=[dict(id='left', label='Left', normal_xy=[1, 0], offset_m=0),
               dict(id='front', label='Front', normal_xy=[0, 1], offset_m=0)],
        reference_uncertainty_m=.001)


def point(x, y, z):
    return dict(wall_distances_m=[x, y], height_above_floor_m=z, uncertainty_m=.005)


def freeze_body():
    return dict(references=references(), protocol=dict(reference_method='tape',
        supported_configuration_ids=['seated-desk'], max_windows=2),
        requirements=dict.fromkeys(REQUIREMENTS), selected_anchor_label='Independently measured nose')


def start_body():
    parts = {name: dict(status='unknown', outer_corners=None, thickness_m=None) for name in PARTS}
    parts['work'] = dict(status='available', outer_corners=[point(1.5, 2, 1.08), point(2.5, 2.6, 1.08)], thickness_m=.04)
    return dict(window_id='held-owner-1', configuration_id='seated-desk', parts=parts,
        selected_anchor=dict(status='available', original_measurement=point(2.7, 2.3, 1.3)),
        reference_uncertainty_m=.01, activity='unknown')


class ReferenceCaptureHTTPTests(unittest.TestCase):
    @contextmanager
    def running(self, directory, *, record=None):
        base = Path(directory); now = [100.0]; mono = [1_000_000_000]
        # The actual per-file fsync remains; Windows cannot fsync a directory.
        patches = [patch.object(guide, 'sync_reference_directory', return_value=None)]
        if record is not None: patches.append(patch.object(guide.ReferenceActions, 'record', new=record))
        for item in patches: item.start()
        server = thread = None
        try:
            server = guide.Guide(base/'candidate-must-not-exist.json', seconds=20,
                reference_capture_directory=base/'references',
                assessment_clock=lambda: now[0], assessment_monotonic=lambda: mono[0])
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            yield server, now, mono
        finally:
            if server is not None: server.stopped.set()
            if thread is not None: thread.join(2)
            for item in reversed(patches): item.stop()
            if thread is not None: self.assertFalse(thread.is_alive(), 'Guide must finish before private temp directory cleanup')

    def post(self, server, path, body, *, origin=None, nonce=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            headers = {'Origin': server.origin if origin is None else origin,
                'X-Desk-Nonce': server.nonce if nonce is None else nonce, 'Content-Type': 'application/json'}
            connection.request('POST', path, guide.encoded(body), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally: connection.close()

    def actions(self, directory):
        return [json.loads(row) for row in (Path(directory)/'references/actions.jsonl').read_bytes().splitlines()]

    def test_no_candidate_http_freeze_start_end_retains_raw_independent_references(self):
        with tempfile.TemporaryDirectory(prefix='authored-reference-bootstrap-') as directory:
            with self.running(directory) as (server, now, mono):
                self.assertEqual(self.post(server, '/reference/start', start_body())[0], 409)
                status, frozen = self.post(server, '/reference/freeze', freeze_body())
                self.assertEqual(status, 200, frozen); self.assertEqual(frozen['status'], 'frozen')
                status, started = self.post(server, '/reference/start', start_body())
                self.assertEqual(status, 200, started); self.assertEqual(started['window_id'], 'held-owner-1')
                now[0] = 102.; mono[0] += 2_000_000_000
                status, ended = self.post(server, '/reference/end', {'window_id': 'held-owner-1'})
                self.assertEqual(status, 200, ended); self.assertEqual(ended['duration_s'], 2)
                status, result = self.post(server, '/reference/results', {})
                self.assertEqual(status, 200, result); self.assertEqual(result['status'], 'references_recorded')
                self.assertFalse(result['candidate_available']); self.assertFalse(result['predictions_supplied'])
                self.assertFalse(result['qualified']); self.assertIsNone(result['operational_geometry'])
                self.assertEqual(result['authority'], AUTHORITY)
                self.assertEqual(result['operational_qualification']['decision'], 'block')
                actions = self.actions(directory); self.assertEqual([a['kind'] for a in actions], ['freeze','start','end','results'])
                self.assertEqual(actions[0]['body']['references'], references())
                start = actions[1]['body']
                self.assertEqual(start['reference_parts']['work']['outer_corners'], start_body()['parts']['work']['outer_corners'])
                for name in PARTS[1:]:
                    self.assertEqual(start['reference_parts'][name]['status'], 'unknown')
                    self.assertIsNone(start['reference_parts'][name]['shape'])
                    self.assertIsNone(start['reference_parts'][name]['outer_corners'])
                self.assertEqual(start['selected_anchor']['original_measurement'], start_body()['selected_anchor']['original_measurement'])
                document = actions[-1]['body']['reference_document']
                self.assertIsNone(document['candidate_ref']); self.assertIsNone(document['collection_binding'])
                self.assertEqual(document['clock'], dict(role='server_entry_not_sensor_exposure', qualification=None, error_bound_s=None))
                self.assertEqual(self.post(server, '/stop', {})[0], 200)
            self.assertFalse((Path(directory)/'candidate-must-not-exist.json').exists())
            self.assertFalse((Path(directory)/'references/commissioning.json').exists())

    def test_same_origin_nonce_and_reference_only_namespace_reject_before_recording(self):
        with tempfile.TemporaryDirectory(prefix='authored-reference-bootstrap-auth-') as directory:
            with self.running(directory) as (server, _, _):
                for overrides in ({'origin':'http://foreign.invalid'}, {'nonce':'foreign-nonce'}):
                    self.assertEqual(self.post(server, '/reference/freeze', freeze_body(), **overrides)[0], 409)
                for path in ('/prepare','/camera/snapshot','/assessment/freeze','/evaluation/freeze','/evaluation/export'):
                    self.assertNotEqual(self.post(server, path, {})[0], 200)
                forged = freeze_body(); forged['resolver'] = 'browser-cannot-select-authority'
                self.assertEqual(self.post(server, '/reference/freeze', forged)[0], 409)
                self.assertEqual(self.actions(directory), [])
                self.assertEqual(self.post(server, '/reference/freeze', freeze_body())[0], 200)
                for body in (dict(start_body(), observed_at=99), dict(start_body(), selected_anchor=dict(status='unknown', original_measurement=point(1,2,3)))):
                    self.assertEqual(self.post(server, '/reference/start', body)[0], 409)
                malformed = start_body(); malformed['parts']['head']['outer_corners'] = [point(1,2,3),point(2,3,4)]
                self.assertEqual(self.post(server, '/reference/start', malformed)[0], 409)
                self.assertEqual([a['kind'] for a in self.actions(directory)], ['freeze'])

    def test_exact_freeze_window_retries_keep_original_receipts_and_changed_bodies_refuse(self):
        with tempfile.TemporaryDirectory(prefix='authored-reference-bootstrap-retry-') as directory:
            with self.running(directory) as (server, now, mono):
                first = self.post(server, '/reference/freeze', freeze_body())
                self.assertEqual(self.post(server, '/reference/freeze', freeze_body()), first)
                changed = freeze_body(); changed['references']['equipment_ref']['revision'] += 1
                self.assertEqual(self.post(server, '/reference/freeze', changed)[0], 409)
                first_start = self.post(server, '/reference/start', start_body())
                now[0] = 101.; mono[0] += 1_000_000_000
                self.assertEqual(self.post(server, '/reference/start', start_body()), first_start)
                changed = start_body(); changed['activity'] = 'focused'
                self.assertEqual(self.post(server, '/reference/start', changed)[0], 409)
                first_end = self.post(server, '/reference/end', {'window_id':'held-owner-1'})
                self.assertEqual(self.post(server, '/reference/end', {'window_id':'held-owner-1'}), first_end)
                self.assertEqual([a['kind'] for a in self.actions(directory)], ['freeze','start','end'])

    def test_failed_exact_recorder_ack_never_acknowledges_start_or_resumes(self):
        real_record = guide.ReferenceActions.record
        def wrong_ack(recorder, action):
            actual = real_record(recorder, action)
            return dict(status='retained', sha256='f'*64) if action['kind']=='start' else actual
        with tempfile.TemporaryDirectory(prefix='authored-reference-bootstrap-ack-') as directory:
            with self.running(directory, record=wrong_ack) as (server, _, _):
                self.assertEqual(self.post(server, '/reference/freeze', freeze_body())[0], 200)
                self.assertEqual(self.post(server, '/reference/start', start_body())[0], 409)
                self.assertEqual(self.post(server, '/reference/start', start_body())[0], 409)
                self.assertEqual(self.post(server, '/reference/end', {'window_id':'held-owner-1'})[0], 409)
                self.assertEqual(self.post(server, '/reference/results', {})[0], 409)
                self.assertEqual(self.post(server, '/stop', {})[0], 200)
            self.assertEqual([a['kind'] for a in self.actions(directory)], ['freeze','start','guide_closed'])
            self.assertFalse((Path(directory)/'references/commissioning.json').exists())

    def test_stop_with_open_interval_retains_unfinished_state_without_fabricating_end(self):
        with tempfile.TemporaryDirectory(prefix='authored-reference-bootstrap-stop-') as directory:
            with self.running(directory) as (server, _, _):
                self.assertEqual(self.post(server, '/reference/freeze', freeze_body())[0], 200)
                self.assertEqual(self.post(server, '/reference/start', start_body())[0], 200)
                status, result = self.post(server, '/reference/results', {})
                self.assertEqual(status, 200, result); self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(result['open_windows'], 1)
                self.assertEqual(self.post(server, '/stop', {})[0], 200)
            rows = self.actions(directory); self.assertNotIn('end', [a['kind'] for a in rows])
            self.assertEqual(rows[-1]['kind'], 'guide_closed')
            self.assertTrue(rows[-1]['data']['unfinished_interval_is_not_ended'])
            self.assertEqual(server.fileno(), -1)

    def test_recorder_ready_and_constructor_mode_refusal_and_close_failure_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='authored-reference-bootstrap-ready-') as directory:
            base = Path(directory)
            with patch.object(guide.ReferenceActions, '__init__', side_effect=OSError('authored-recorder-unavailable')), \
                 patch.object(guide.HTTPServer, '__init__') as bind:
                with self.assertRaises((OSError, ValueError)):
                    guide.Guide(base/'candidate.json', reference_capture_directory=base/'no-ready')
                bind.assert_not_called()
            with patch.object(guide, 'sync_reference_directory', return_value=None):
                for extra in (dict(assessment_directory=base/'mixed'), dict(evaluate_desk_method=True),
                              dict(camera_targets=type('ForbiddenCamera',(),dict(snapshot=lambda:None,point=lambda *a:None,resolve_point=lambda *a:None))())):
                    with self.assertRaises(ValueError):
                        guide.Guide(base/'candidate.json', reference_capture_directory=base/'references', **extra)
                server = guide.Guide(base/'candidate.json', reference_capture_directory=base/'cleanup')
            server.stopped.set()
            try:
                with patch.object(guide.ReferenceActions, 'close', side_effect=OSError('authored-close-failure')):
                    with self.assertRaises(OSError): server.run()
                self.assertEqual(server.fileno(), -1, 'server_close must still happen when recorder close raises')
            finally:
                # Close the actual file after the explicit failure-port test.
                server.reference_actions.close('authored-test-final-cleanup')
                server.server_close()


if __name__ == '__main__': unittest.main()
