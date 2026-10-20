"""Held Guide actions -> actual collector originals -> computed review report.

Only external camera/model I/O, independent reference measurements and clocks
are generated. No host, model, private household asset or qualification runs.
"""
from copy import deepcopy
import http.client
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/den-live-runtime'))
import test_lab_assessment_collect as collections
from lab_assessment_collect import read_verified_attempts
from test_desk_distinct_parts import guide, survey, measured_box


class DeskReferenceCollectionJoinTests(unittest.TestCase):
    def test_held_independent_references_score_the_actual_retained_lab_collection(self):
        f = collections.ExistingApiCollectionTests('test_actual_factory_asgi_late_association_collects_private_successor')
        self.addCleanup(f.doCleanups); f.setUp()
        base = Path(f.f.directory.name)
        candidate = guide.prepare_candidate(survey(), now=lambda: f.f.now - 10)
        now, mono = [f.f.now], [1_000_000_000]
        if sys.platform == 'win32':
            sync = patch.object(guide, 'sync_reference_directory')
            sync.start(); self.addCleanup(sync.stop)
        server = guide.Guide(base / 'unused-survey.json', seconds=30,
            assessment_candidate=candidate,
            assessment_collection_loader=lambda: read_verified_attempts(f.output),
            assessment_directory=base / 'held-independent-references',
            assessment_clock=lambda: now[0], assessment_monotonic=lambda: mono[0])
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        self.addCleanup(lambda: (server.stopped.set(), thread.join(2)))
        def post(path, value):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                connection.request('POST', path, guide.encoded(value), headers={
                    'Origin': server.origin, 'X-Desk-Nonce': server.nonce, 'Content-Type': 'application/json'})
                reply = connection.getresponse()
                return reply.status, json.loads(reply.read())
            finally: connection.close()
        status, frozen = post('/assessment/freeze', dict(protocol=dict(reference_method='tape',
            supported_configuration_ids=['seated-neutral'], max_windows=2), requirements=dict(
                max_work_center_error_m=.05, min_containment_fraction=1,
                max_undercoverage_fraction=0, min_complete_protection_fraction=1,
                min_reference_s=1, max_capture_to_available_s=1)))
        self.assertEqual(status, 200, frozen)
        self.assertFalse(f.output.exists(), 'no predictions are collected to choose reference requirements')
        # Independent authored held boxes are literal and narrower than the
        # separately authored proposal. No model output or returned XYZ is used.
        positions = (
            ('work', [2, 2, 1.04], [.1, .1, .02]),
            ('body', [3.5, 2, 1.1], [.08, .08, .2]),
            ('head', [3.5, 2, 1.8], [.05, .05, .05]),
            ('hand_left', [1.55, 2, 1], [.005, .005, .005]),
            ('hand_right', [2.45, 2, 1], [.005, .005, .005]),
            ('protection', [3.5, 2, 1.8], [.05, .05, .05]))
        parts = {name: dict(status='available', outer_corners=measured_box(center, half), thickness_m=.001 if name == 'work' else 0)
                 for name, center, half in positions}
        status, started = post('/assessment/start', dict(configuration_id='seated-neutral',
            references=deepcopy(candidate['original_survey']['references']), parts=parts,
            reference_uncertainty_m=0, activity='focused'))
        self.assertEqual(status, 200, started)
        collected = f.run_collection()
        self.assertTrue(collected['complete'], collected)
        original = f.output.read_bytes()
        now[0] += 2; mono[0] += 2_000_000_000
        status, ended = post('/assessment/end', {'window_id': started['window_id']})
        self.assertEqual(status, 200, ended)
        status, report = post('/assessment/report', {})
        self.assertEqual(status, 200, report)
        self.assertEqual(report['candidate_observations']['attempts'], 1)
        self.assertEqual(report['candidate_observations']['refused_attempts'], 0)
        self.assertTrue(all(row['contained_reference_windows'] == 1 for row in report['proposed_bounds'].values()))
        self.assertEqual(report['operational_qualification']['decision'], 'block')
        self.assertFalse(report['qualified'])
        self.assertIsNone(report['operational_geometry'])
        self.assertNotIn('instances', json.dumps(report))
        self.assertEqual(f.output.read_bytes(), original)
        self.assertEqual(f.f.store.get_session(f.f.session['id'])['state'], 'recording')
        self.assertTrue(all(method == 'GET' for method, _ in f.api.calls))
        status, _ = post('/stop', {})
        self.assertEqual(status, 200)
        thread.join(2); self.assertFalse(thread.is_alive())


if __name__ == '__main__': unittest.main()
