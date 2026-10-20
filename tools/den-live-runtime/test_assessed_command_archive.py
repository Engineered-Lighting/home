"""Data-only task-original recording, not path qualification or native acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from archive import ArchiveRefusal, Recorder, digest


class AssessedCommandArchiveTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='den-task-original-authored-')
        self.addCleanup(self.work.cleanup)
        self.manifest = dict(schema='home-live-session-manifest-2', version=2,
            runtime_profile='home-live-runtime-config-3', session_ref={'site_id': 'authored'},
            configuration_digest='a'*64, source_bundle_digest='b'*64,
            worker_config=None, worker_description=None)
        self.ref = dict(configuration_digest='c'*64, manifest_digest='d'*64,
            worker_version='home-live-context-worker-12.0.0', room_output_schema='home-live-room-output-1',
            commit_id='authored-commit', commit_body_sha256='e'*64, decision_sha256='f'*64,
            room_output_revision_id='authored-output', room_output_sha256='1'*64)

    def recorder(self, manifest=None):
        result = Recorder(Path(self.work.name)/'session.jsonl', manifest or self.manifest,
                          max_records=8, max_bytes=65536, min_free_bytes=1)
        self.addCleanup(lambda: result.drain({'status': 'incomplete'}))
        return result

    def original(self):
        # Intentionally only a data envelope: no compiler can qualify this body.
        request = dict(schema='home-fixture-retained-angle-request-2', version=2,
            assessment={'profile_sha256': '2'*64}, retained_aim_ref={'room_commit_ref': deepcopy(self.ref)})
        return dict(fixture_id='authored-fixture', assessment=request,
            assessment_sha256=digest(request), profile_sha256='2'*64, room_commit_ref=deepcopy(self.ref))

    def test_exact_original_retry_retains_one_unchanged_body(self):
        recorder = self.recorder()
        data = self.original()
        body = {'kind': 'room_task_assessment_original', 'data': data}
        identity = 'room-task-assessment:' + digest(data)
        ack = recorder.record(body, record_id=identity)
        self.assertEqual(recorder.record(deepcopy(body), record_id=identity), ack)
        rows = [json.loads(line) for line in recorder.path.read_bytes().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['record'], body)
        self.assertEqual(ack['seq'], 1)
        self.assertNotIn('authority_created', ack)

    def test_changed_body_or_wrong_profile_refuses_before_append(self):
        recorder = self.recorder()
        data = self.original()
        identity = 'room-task-assessment:' + digest(data)
        recorder.record({'kind': 'room_task_assessment_original', 'data': data}, record_id=identity)
        before = recorder.path.read_bytes()
        changed = deepcopy(data)
        changed['assessment']['issued_home_us'] = 1
        changed['assessment_sha256'] = digest(changed['assessment'])
        with self.assertRaisesRegex(ArchiveRefusal, 'exact_identity'):
            recorder.record({'kind': 'room_task_assessment_original', 'data': changed}, record_id=identity)
        wrong = deepcopy(data)
        wrong['profile_sha256'] = '3'*64
        with self.assertRaisesRegex(ArchiveRefusal, 'binding_refused'):
            recorder.record({'kind': 'room_task_assessment_original', 'data': wrong},
                            record_id='room-task-assessment:' + digest(wrong))
        self.assertEqual(recorder.path.read_bytes(), before)

    def test_historical_manifest_cannot_record_new_original_kind(self):
        recorder = self.recorder({'schema': 'home-live-session-manifest-1', 'version': 1})
        data = self.original()
        before = recorder.path.read_bytes()
        with self.assertRaisesRegex(ArchiveRefusal, 'kind_refused'):
            recorder.record({'kind': 'room_task_assessment_original', 'data': data},
                            record_id='room-task-assessment:' + digest(data))
        self.assertEqual(recorder.path.read_bytes(), before)

    def test_crossed_manifest_profile_refuses_before_file_creation(self):
        path = Path(self.work.name)/'session.jsonl'
        for edit in (lambda m: m.update(runtime_profile='home-live-runtime-config-2'),
                     lambda m: m.update(version=1),
                     lambda m: m.update(allowed_kinds=['anything'])):
            value = deepcopy(self.manifest); edit(value)
            with self.assertRaises(ArchiveRefusal):
                self.recorder(value)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
