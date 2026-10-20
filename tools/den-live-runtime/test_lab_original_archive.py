"""Original clock fidelity, retry, missing-data and durable-write failures."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab_original_archive import LabOriginalArchive, read_lab_originals, original_digest


class OriginalArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Directory fsync is a required Linux runtime capability. The Windows
        # workstation tests substitute only that external filesystem operation.
        self.parent_sync = patch('lab_original_archive.sync_parent').start()
        self.addCleanup(patch.stopall)
        self.filename = str(Path(self.temp.name) / 'originals.jsonl')
        self.binding = {'session_id': 'lab-test', 'method_ref': {'id': 'method', 'digest': 'a' * 64},
                        'model_ref': {'id': 'model', 'digest': 'b' * 64}}
        self.record = {'schema': 'home-original-lab-observation-1', 'version': 1,
            'event': {'id': 7, 'session_id': 'lab-test', 'kind': 'perception.output',
                      'payload': {'frame_id': 'frame-7'}},
            'frame_metadata': {'source_timing_receipt': {'observed_at_unix_ns': 1792208115779811123}},
            'first_consumer_received_at': 1792208116.5,
            'method_ref': self.binding['method_ref'], 'model_ref': self.binding['model_ref']}

    def archive(self, **kwargs):
        archive = LabOriginalArchive(self.filename, binding=self.binding, **kwargs)
        self.addCleanup(archive.finalize)
        return archive

    def test_original_ns_exact_and_retry_does_not_duplicate(self):
        archive = self.archive()
        first = archive(self.record)
        self.assertEqual(archive(deepcopy(self.record)), first)
        sealed = archive.finalize()
        self.assertEqual((sealed['status'], sealed['records']), ('finalized', 1))
        rows = read_lab_originals(self.filename, expected_digests=[first['digest']])
        self.assertEqual(rows[first['digest']], self.record)
        self.assertEqual(rows[first['digest']]['frame_metadata']['source_timing_receipt']['observed_at_unix_ns'],
                         1792208115779811123)

    def test_changed_retry_and_foreign_session_refuse(self):
        archive = self.archive()
        archive(self.record)
        changed = deepcopy(self.record)
        changed['first_consumer_received_at'] += 1
        with self.assertRaisesRegex(ValueError, 'identity_conflict'):
            archive(changed)
        changed['event']['session_id'] = 'other'
        with self.assertRaisesRegex(ValueError, 'binding_refused'):
            archive(changed)

    def test_missing_original_or_unsealed_sidecar_is_incomplete(self):
        archive = self.archive()
        archive(self.record)
        with self.assertRaises(ValueError):
            read_lab_originals(self.filename)
        archive.finalize()
        with self.assertRaisesRegex(ValueError, 'missing_original'):
            read_lab_originals(self.filename, expected_digests=['c' * 64])

    def test_fsync_failure_never_returns_retained(self):
        archive = self.archive()
        with patch('lab_original_archive.os.fsync', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                archive(self.record)
        self.assertEqual(archive.finalize()['status'], 'incomplete')
        with self.assertRaises(ValueError):
            read_lab_originals(self.filename)

    def test_secret_metadata_and_record_exhaustion_refuse(self):
        archive = self.archive(max_records=1)
        bad = deepcopy(self.record)
        bad['frame_metadata']['authorization'] = 'never archive this'
        with self.assertRaisesRegex(ValueError, 'secret_or_media'):
            archive(bad)
        archive(self.record)
        second = deepcopy(self.record)
        second['event']['id'] = 8
        with self.assertRaisesRegex(ValueError, 'record_limit'):
            archive(second)

    def test_altered_original_body_fails_integrity(self):
        archive = self.archive()
        archive(self.record)
        archive.finalize()
        path = Path(self.filename)
        path.write_bytes(path.read_bytes().replace(b'1792208115779811123', b'1792208115779811124'))
        with self.assertRaisesRegex(ValueError, 'digest_refused'):
            read_lab_originals(self.filename, expected_digests=[original_digest(self.record)])

    def test_parent_sync_failure_refuses_construction(self):
        self.parent_sync.side_effect = OSError('directory sync unavailable')
        with self.assertRaises(OSError):
            LabOriginalArchive(self.filename, binding=self.binding)

    def test_oversized_input_has_bounded_read(self):
        path = Path(self.filename)
        with path.open('wb') as stream:
            stream.truncate(9 * 1024**2)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            read_lab_originals(path)


if __name__ == '__main__':
    unittest.main()
