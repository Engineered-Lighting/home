"""Actual PC proof and original-shaped JEV sidecar; no models or household data."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for rel in ('stack/services/home-context', 'stack/services/perception-lab'):
    sys.path.insert(0, str(ROOT / rel))
from journal import digest
from live_capture import _original_digest
from test_live_pc_support import authored_pc_fixture
from live_test_helpers import SCOPE, reference
from lab_original_archive import LabOriginalArchive, read_lab_originals, semantic_archive_binding


class SemanticOriginalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='authored-semantic-sidecar-')
        self.addCleanup(self.directory.cleanup)
        self.filename = str(Path(self.directory.name) / 'originals.jsonl')
        self.pc = authored_pc_fixture()
        ref = self.pc['record']['ref']
        self.binding = {'session_id': SCOPE['session_id'], 'method_ref': reference('camera'), 'model_ref': reference('model')}
        self.policy = {'schema': 'home-lab-semantic-admission-policy-2', 'version': 2}
        self.policy['ha'] = {'source_id': self.pc['original']['source_id'], 'method_ref': self.pc['original']['method_ref'],
            'model_ref': None, 'receipt_store_id': ref['store_id'], 'receipt_component_id': ref['component_id']}
        self.policy['jev'] = {'source_id': 'jev-semantic', 'method_ref': reference('jev-method'),
            'model_ref': reference('jev'), 'receipt_store_id': 'jev-originals', 'receipt_component_id': 'jev-original'}
        self.semantic = semantic_archive_binding(SCOPE, self.policy)
        self.parent_sync = patch('lab_original_archive.sync_parent')
        self.parent_sync.start()
        self.addCleanup(self.parent_sync.stop)

    def archive(self):
        result = LabOriginalArchive(self.filename, binding=self.binding, semantic_binding=self.semantic)
        self.addCleanup(result.finalize)
        return result

    def wrapper(self, family, original, record):
        source = self.policy[family]
        return {'schema': 'home-original-lab-admission-1', 'version': 1, 'session_id': SCOPE['session_id'],
            'family': family, **{k: deepcopy(source[k]) for k in ('source_id', 'method_ref', 'model_ref')},
            'original_ref': deepcopy(record['ref']), 'receipt_record': deepcopy(record), 'original': deepcopy(original),
            'first_consumer_received_at': original['available_at'] if family == 'ha' else original['first_consumer_received_at']}

    def test_pc_proof_and_jev_request_binding_round_trip_with_domain_epoch(self):
        archive = self.archive()
        pc = self.wrapper('ha', self.pc['original'], self.pc['record'])
        first = archive(pc)
        self.assertEqual(archive(deepcopy(pc)), first)
        jev = {'schema': 'home-original-lab-semantic-2', 'version': 2,
            'session': {'id': SCOPE['session_id'], 'site_id': SCOPE['site_id']},
            'policy_digest': digest(self.policy), 'first_consumer_received_at': 1800000000.5,
            'request_binding': {'work_parent_ref': deepcopy(self.pc['original']['association_ref'])},
            'events': [{'id': 7, 'session_id': SCOPE['session_id'], 'kind': 'reasoning.output',
                        'payload': {'source_ns_preserved': 1792208115779811123}}]}
        ref = {'store_id': 'jev-originals', 'receipt_id': 'jev-original-7', 'component_id': 'jev-original',
               'digest': _original_digest(jev, limit=2*1024**2)}
        second_body = self.wrapper('jev', jev, {'ref': ref})
        second = archive(second_body)
        self.assertEqual(archive.finalize()['status'], 'finalized')
        rows = read_lab_originals(self.filename, expected_digests=[first['digest'], second['digest']])
        self.assertEqual(rows[first['digest']], pc)
        self.assertEqual(rows[second['digest']], second_body)
        self.assertEqual(rows[second['digest']]['original']['events'][0]['payload']['source_ns_preserved'], 1792208115779811123)

    def test_cross_scope_changed_proof_and_record_copy_refuse_before_retained(self):
        archive = self.archive()
        original = self.wrapper('ha', self.pc['original'], self.pc['record'])
        for attack in ('scope', 'body', 'record'):
            with self.subTest(attack=attack):
                changed = deepcopy(original)
                if attack == 'scope': changed['original']['scope']['domain_epoch'] = 'other'
                elif attack == 'body': changed['original']['input_at'] -= 1
                else: changed['receipt_record']['pc_input_original'] = {}
                with self.assertRaisesRegex(ValueError, 'binding_refused'):
                    archive(changed)
        self.assertEqual(len(archive.records), 0)

    def test_semantic_extension_is_not_implicitly_enabled_for_old_archive(self):
        archive = LabOriginalArchive(self.filename, binding=self.binding)
        self.addCleanup(archive.finalize)
        with self.assertRaisesRegex(ValueError, 'binding_refused'):
            archive(self.wrapper('ha', self.pc['original'], self.pc['record']))


if __name__ == '__main__':
    unittest.main()
