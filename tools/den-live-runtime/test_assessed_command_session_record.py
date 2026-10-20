"""Actual session record method with real bounded causal Recorder only.

Constructor metadata is bypassed explicitly: no Journal, canonical authority,
plant, command thread or native fixture is constructed or qualified here.
"""
from copy import deepcopy
import json
from pathlib import Path
import threading
import unittest

from archive import Recorder, digest
from room_command_projection import RETAINED_TASK_PROFILE1
from room_command_session import CanonicalCommandSession
import test_assessed_command_archive as fixture


class AssessedCommandSessionRecordTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AssessedCommandArchiveTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def session(self, *, profile, legacy=False, name='session.jsonl'):
        manifest = ({'schema': 'home-live-session-manifest-1', 'version': 1}
                    if legacy else deepcopy(self.f.manifest))
        recorder = Recorder(Path(self.f.work.name)/name, manifest,
            max_records=8, max_bytes=65536, min_free_bytes=1)
        self.addCleanup(lambda: recorder.drain({'status': 'incomplete', 'reason': 'test_cleanup'}))
        owner = object.__new__(CanonicalCommandSession)
        owner.task_profile = deepcopy(profile)
        owner.worker_recorder = recorder
        # Neither canonical router nor sidecar may consume this data-only kind.
        # The real constructor/binding is deliberately outside this pure test.
        owner.canonical_recorder = object()
        owner.lock, owner.cancel = threading.RLock(), threading.Event()
        owner.error, owner.writers = None, 0
        return owner, recorder

    def body(self):
        data = self.f.original()
        return {'kind': 'room_task_assessment_original', 'data': data}, 'room-task-assessment:'+digest(data)

    def test_actual_record_retains_once_and_returns_real_durable_ack_on_exact_retry(self):
        owner, recorder = self.session(profile=RETAINED_TASK_PROFILE1)
        body, identity = self.body()
        ack = owner.record(body, record_id=identity)
        first_bytes = recorder.path.read_bytes()
        self.assertEqual(owner.record(deepcopy(body), record_id=identity), ack)
        self.assertEqual(recorder.path.read_bytes(), first_bytes)
        lines = [json.loads(line) for line in first_bytes.splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1]['record'], body)
        self.assertEqual(ack, dict(schema='home-live-recorded-1', version=1, seq=1,
                                   digest=lines[1]['digest']))
        entry = deepcopy(lines[1]); retained_digest = entry.pop('digest')
        self.assertEqual(digest(entry), retained_digest)
        self.assertEqual(recorder.status()['records'], 2)
        self.assertIsNone(owner.error)
        self.assertFalse(owner.cancel.is_set())
        self.assertEqual(owner.writers, 0)
        self.assertNotIn('authority_created', ack)

    def test_legacy_or_absent_task_profile_refuses_without_append_and_poisons_session(self):
        for name, profile, legacy in (('legacy', RETAINED_TASK_PROFILE1, True),
                                      ('absent_profile', None, False)):
            with self.subTest(case=name):
                owner, recorder = self.session(profile=profile, legacy=legacy, name=name+'.jsonl')
                before = recorder.path.read_bytes()
                body, identity = self.body()
                with self.assertRaisesRegex(ValueError, 'assessed_task_original_session_profile_required'):
                    owner.record(body, record_id=identity)
                self.assertEqual(recorder.path.read_bytes(), before)
                self.assertEqual(recorder.status()['records'], 1)
                self.assertEqual(recorder.status()['status'], 'ready')
                self.assertEqual(owner.error, 'command_recording_or_pairing_incomplete')
                self.assertTrue(owner.cancel.is_set())
                self.assertEqual(owner.writers, 0)


if __name__ == '__main__': unittest.main()
