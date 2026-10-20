"""Owner reference actions through the actual opt-in bounded guide HTTP server.

Camera/model/collection I/O is absent from the tracer. Independent generated
wall measurements remain distinct from the candidate's original survey.
"""
from copy import deepcopy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_desk_distinct_parts import guide, survey, measured_box

REQUIREMENTS = ('max_work_center_error_m', 'min_containment_fraction', 'max_undercoverage_fraction',
                'min_complete_protection_fraction', 'min_reference_s', 'max_capture_to_available_s')


class HeldReferenceGuideTests(unittest.TestCase):
    def test_http_freeze_actual_start_end_retains_independent_raw_measurements(self):
        with tempfile.TemporaryDirectory(prefix='authored-held-guide-') as directory:
            base = Path(directory)
            candidate = guide.prepare_candidate(survey(), now=lambda: 90)
            now = [100.0]; mono = [1_000_000_000]
            def no_collection(): raise ValueError('collection_not_yet_available')
            # Windows lacks the POSIX directory-fsync operation. Only that
            # external namespace call is doubled; files/exclusive creation,
            # file fsync, actual core and loopback HTTP remain real.
            with patch.object(guide, 'sync_reference_directory', return_value=None):
                server = guide.Guide(base / 'unused-survey.json', seconds=20,
                    assessment_candidate=candidate, assessment_collection_loader=no_collection,
                    assessment_directory=base / 'independent-references',
                    assessment_clock=lambda: now[0], assessment_monotonic=lambda: mono[0])
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            self.addCleanup(lambda: (server.stopped.set(), thread.join(2)))
            def post(path, value):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=4)
                try:
                    connection.request('POST', path, guide.encoded(value), headers={
                        'Origin': server.origin, 'X-Desk-Nonce': server.nonce, 'Content-Type': 'application/json'})
                    reply = connection.getresponse()
                    return reply.status, json.loads(reply.read())
                finally: connection.close()
            status, frozen = post('/assessment/freeze', dict(protocol={
                'reference_method': 'tape', 'supported_configuration_ids': ['seated-desk'], 'max_windows': 2},
                requirements=dict.fromkeys(REQUIREMENTS)))
            self.assertEqual(status, 200, frozen)
            self.assertEqual(frozen['status'], 'frozen')
            refs = deepcopy(candidate['original_survey']['references'])
            parts = {name: {'status': 'unknown'} for name in ('work','body','head','hand_left','hand_right','protection')}
            parts['work'] = dict(status='available', outer_corners=measured_box([2,2,1.08],[.3,.2,.04]), thickness_m=.1)
            forged = deepcopy(parts); forged['head'] = dict(status='available', shape={'kind':'aabb'})
            self.assertEqual(post('/assessment/start', dict(configuration_id='seated-desk', references=refs,
                parts=forged, reference_uncertainty_m=.01, activity='focused'))[0], 409)
            foreign = deepcopy(refs); foreign['equipment_ref']['revision'] += 1
            self.assertEqual(post('/assessment/start', dict(configuration_id='seated-desk', references=foreign,
                parts=parts, reference_uncertainty_m=.01, activity='focused'))[0], 409)
            status, started = post('/assessment/start', dict(configuration_id='seated-desk', references=refs,
                parts=parts, reference_uncertainty_m=.01, activity='focused'))
            self.assertEqual(status, 200, started)
            self.assertEqual(started['status'], 'started')
            now[0] = 102.; mono[0] += 2_000_000_000
            status, ended = post('/assessment/end', {'window_id': started['window_id']})
            self.assertEqual(status, 200, ended)
            self.assertEqual(ended['status'], 'ended')
            actions = [json.loads(raw) for raw in (base / 'independent-references/actions.jsonl').read_bytes().splitlines()]
            self.assertEqual(len(actions), 3)
            self.assertIn('outer_corners', json.dumps(actions))
            self.assertNotIn('camera_xyz_m', json.dumps(actions))
            self.assertEqual(candidate['original_survey'], survey())
            self.assertEqual(post('/assessment/report', {'filename':'untrusted.jsonl'})[0],409)
            status, stopped = post('/stop', {})
            self.assertEqual(status, 200, stopped)
            thread.join(2); self.assertFalse(thread.is_alive())


if __name__ == '__main__': unittest.main()
