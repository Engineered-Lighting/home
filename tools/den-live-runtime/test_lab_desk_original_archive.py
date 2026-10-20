"""Actual authored desk-publisher originals through the durable existing sidecar."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for rel in ('stack/services/home-context', 'stack/services/perception-lab'):
    if str(ROOT / rel) not in sys.path:
        sys.path.append(str(ROOT / rel))

from journal import digest
from live_capture import _original_digest
import lab_original_archive as archive
import test_lab_live_desk_admission as publisher_fixture


class DeskOriginalArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Actual publisher/Journal with authored independent assessments only.
        # This proves record compatibility, not an installed anchor capability.
        f = publisher_fixture.LabLiveDeskAdmissionTests()
        f.setUp()
        cls.addClassCleanup(f.doCleanups)
        result = f.consume()
        if result['status'] != 'admitted':
            raise AssertionError(result)
        cls.original = deepcopy(f.retained[0])
        cls.camera = deepcopy(cls.original['inputs']['capture_original'])
        cls.binding = {'session_id': f.producer.scope['session_id'], 'method_ref': f.lab.method,
                       'model_ref': f.lab.model}
        cls.desk = {'scope': deepcopy(f.producer.scope), 'policy_digest': digest(f.policy),
                    **{k: deepcopy(f.policy[k]) for k in
                       ('source_id', 'method_ref', 'receipt_store_id', 'receipt_component_id')}}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='authored-desk-sidecar-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'originals.jsonl'
        if os.name == 'nt':
            sync = patch.object(archive, 'sync_parent')
            sync.start()
            self.addCleanup(sync.stop)

    def writer(self, *, desk=True, **kwargs):
        instance = archive.LabOriginalArchive(str(self.path), binding=deepcopy(self.binding),
            **({'desk_binding': deepcopy(self.desk)} if desk else {}), **kwargs)
        self.addCleanup(instance.finalize)
        return instance

    def read(self, **kwargs):
        return archive.read_lab_originals(self.path, expected_desk_binding=deepcopy(self.desk), **kwargs)

    def test_actual_publisher_original_retains_exact_raw_ack_and_ns_with_real_fsync(self):
        real_fsync = os.fsync
        with patch.object(archive.os, 'fsync', wraps=real_fsync) as calls:
            writer = self.writer()
            camera_ack = writer(deepcopy(self.camera))
            first = writer(deepcopy(self.original))
            self.assertEqual(first, {'status': 'retained', 'digest': _original_digest(self.original, limit=512*1024)})
            before = calls.call_count
            self.assertEqual(writer(deepcopy(self.original)), first)
            self.assertEqual(calls.call_count, before, 'exact retry does not append or fsync another record')
            self.assertEqual(writer.finalize()['status'], 'finalized')
            self.assertGreaterEqual(calls.call_count, 4)
        saved = self.read(expected_digests=[camera_ack['digest'], first['digest']])
        self.assertEqual(saved[first['digest']], self.original)
        self.assertEqual(saved[first['digest']]['inputs']['capture_original'], self.camera)
        ns = saved[first['digest']]['inputs']['capture_original']['frame_metadata']['source_timing_receipt']['observed_at_unix_ns']
        self.assertIs(type(ns), int)
        self.assertGreater(ns, 2**53)

    def test_no_desk_binding_keeps_historical_manifest_bytes_and_refuses_desk(self):
        writer = self.writer(desk=False)
        manifest = json.loads(self.path.read_bytes().splitlines()[0])['body']
        self.assertEqual(set(manifest), {'schema', 'version', 'previous', 'kind', 'binding'})
        expected = {'schema': 'home-lab-original-record-1', 'version': 1, 'previous': None,
                    'kind': 'manifest', 'binding': self.binding}
        self.assertEqual(self.path.read_bytes(), archive.encoded({'body': expected,
            'sha256': hashlib.sha256(archive.encoded(expected)).hexdigest()}) + os.linesep.encode())
        with self.assertRaisesRegex(ValueError, 'binding_refused'):
            writer(deepcopy(self.original))
        ack = writer(deepcopy(self.camera))
        writer.finalize()
        self.assertEqual(archive.read_lab_originals(self.path)[ack['digest']], self.camera)
        with self.assertRaisesRegex(ValueError, 'desk_archive_binding_refused'):
            self.read()

    def test_reader_requires_exact_external_binding_not_self_asserted_manifest(self):
        writer = self.writer()
        writer(deepcopy(self.original))
        writer.finalize()
        with self.assertRaisesRegex(ValueError, 'desk_archive_binding_refused'):
            archive.read_lab_originals(self.path)
        for key in ('source_id', 'policy_digest', 'method_ref', 'receipt_store_id', 'receipt_component_id', 'scope'):
            wrong = deepcopy(self.desk)
            if key == 'scope': wrong[key]['domain_epoch'] = 'foreign'
            elif key == 'method_ref': wrong[key]['digest'] = 'f' * 64
            elif key == 'policy_digest': wrong[key] = 'f' * 64
            else: wrong[key] = 'foreign'
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'desk_archive_binding_refused'):
                archive.read_lab_originals(self.path, expected_desk_binding=wrong)

    def test_changed_identity_input_scope_and_original_references_refuse(self):
        writer = self.writer()
        writer(deepcopy(self.original))
        changed = deepcopy(self.original)
        changed['produced_at'] += .01
        with self.assertRaisesRegex(ValueError, 'identity_conflict'):
            writer(changed)
        changes = [
            lambda r: r['inputs']['binding']['scope'].update(domain_epoch='foreign'),
            lambda r: r['inputs']['capture_original']['event'].update(session_id='foreign'),
            lambda r: r['inputs']['capture_record']['ref'].update(digest='f' * 64),
            lambda r: r['inputs']['binding']['anchor_original_ref'].update(receipt_id='foreign'),
            lambda r: r['inputs']['anchor'].update(point_m=[9, 9, 9]),
            lambda r: r['inputs']['binding']['parent_publication_refs'][0].update(cursor=999),
            lambda r: r['inputs']['capture_original'].pop('frame_metadata'),
            lambda r: r['policy'].update(source_id='foreign'),
        ]
        for index, mutate in enumerate(changes):
            r = deepcopy(self.original)
            mutate(r)
            r['inputs_digest'] = _original_digest(r['inputs'], limit=512*1024)
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'binding_refused'):
                writer(r)

    def test_missing_requested_original_and_unsealed_or_truncated_archive_stay_incomplete(self):
        writer = self.writer()
        ack = writer(deepcopy(self.original))
        with self.assertRaises(ValueError):
            self.read(expected_digests=[ack['digest']])
        writer.finalize()
        with self.assertRaisesRegex(ValueError, 'missing_original'):
            self.read(expected_digests=[ack['digest'], 'f' * 64])
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.read()

    def test_desk_only_digest_bound_does_not_widen_capture_record_bound(self):
        # Authored metadata volume variant, not another qualified publication.
        r = deepcopy(self.original)
        r['inputs']['candidate']['diagnostic_note'] = 'x' * 70000
        r['inputs']['candidate']['digest'] = _original_digest({k:v for k,v in r['inputs']['candidate'].items() if k!='digest'}, limit=512*1024)
        r['inputs']['binding']['candidate_digest'] = r['policy']['candidate_digest'] = r['inputs']['candidate']['digest']
        r['inputs']['assessment']['candidate_digest'] = r['inputs']['candidate']['digest']
        assessment_ref = {'id': r['inputs']['assessment']['id'], 'digest': _original_digest(r['inputs']['assessment'], limit=512*1024)}
        r['inputs']['binding']['assessment_ref'] = r['policy']['assessment_ref'] = assessment_ref
        r['inputs_digest'] = _original_digest(r['inputs'], limit=512*1024)
        desk = deepcopy(self.desk)
        desk['policy_digest'] = digest(r['policy'])
        writer = archive.LabOriginalArchive(str(self.path), binding=self.binding, desk_binding=desk)
        self.addCleanup(writer.finalize)
        ack = writer(r)
        writer.finalize()
        self.assertEqual(archive.read_lab_originals(self.path, expected_desk_binding=desk)[ack['digest']], r)
        too_large = deepcopy(r)
        too_large['inputs']['candidate']['diagnostic_note'] = 'x' * (512*1024)
        with self.assertRaises(ValueError):
            archive.original_digest(too_large)
        camera = deepcopy(self.camera)
        camera['frame_metadata']['diagnostic_note'] = 'x' * 70000
        with self.assertRaises(ValueError):
            archive.original_digest(camera)

    def test_fsync_failure_never_acknowledges_desk_original(self):
        writer = self.writer()
        with patch.object(archive.os, 'fsync', side_effect=OSError('authored storage failure')):
            with self.assertRaises(OSError):
                writer(deepcopy(self.original))
        self.assertFalse(writer.records)
        self.assertEqual(writer.finalize()['status'], 'incomplete')
        with self.assertRaises(ValueError):
            self.read()


if __name__ == '__main__':
    unittest.main()
