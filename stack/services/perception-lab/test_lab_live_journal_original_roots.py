"""Actual Journal root-lineage successor; generated external observations only."""
from copy import deepcopy
import json
import unittest

import test_lab_live_journal_join as legacy
from lab_live_journal_join import LabLiveJournalJoin, ORIGINAL_ROOT_LINEAGE


class LabOriginalRootTests(unittest.TestCase):
    def setUp(self):
        self.f = legacy.LabLiveJournalJoinTests()
        self.f.make_join = lambda: self.make_join(ORIGINAL_ROOT_LINEAGE)
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def make_join(self, profile):
        f = self.f
        return LabLiveJournalJoin(
            {'id': legacy.SCOPE['session_id'], 'site_id': legacy.SCOPE['site_id'],
             'camera_id': 'den', 'state': 'recording'},
            arm='a', generation=1, live_config=f.config, source_id='synthetic-source',
            revision='synthetic-r1', method_ref=legacy.METHOD, model_ref=f.model,
            lab_profile=f.lab_profile, model_manifests=f.model_manifests,
            receipt_store_id='synthetic-receipts', receipt_component_id='pose',
            root_lineage=profile)

    def payloads(self):
        return [json.loads(row[0])['payload'] for row in
                self.f.journal.db.execute('SELECT body FROM publications ORDER BY cursor')]

    def test_actual_shaped_absent_roots_match_incumbent_event_identity_without_mutating_original(self):
        row, frame = self.f.inputs()
        row['payload'].pop('evidence_roots', None)
        original = deepcopy(row)
        result = self.f.publish(row, frame)
        self.assertEqual(result['status'], 'admitted_unqualified')
        self.assertEqual(row, original)
        for payload in self.payloads():
            self.assertEqual(payload['observation_roots'], [str(row['id'])])
            self.assertEqual(payload['observation_root_times'], {str(row['id']): row['payload']['observed_at']})
            self.assertEqual(payload['sensor_root_bindings'], {str(row['id']): self.f.source['sensor_roots']})
            self.assertEqual(payload['available_at'], row['payload']['available_at'])
            ref = payload['original_receipt_refs'][0]
            self.assertEqual(self.f.join.original_receipt(ref['store_id'], ref['receipt_id'])['event'], original)
            self.assertEqual(payload['status'], 'unobservable')

    def test_explicit_roots_keep_original_retry_and_cannot_refresh_another_frame(self):
        row, frame = self.f.inputs()
        row['payload']['evidence_roots'] = ['authored-original-rgb-root']
        original = deepcopy(row)
        accepted = self.f.publish(row, frame)
        self.assertEqual(self.payloads()[0]['observation_roots'], ['authored-original-rgb-root'])
        self.f.now += 100
        self.assertEqual(self.f.publish(row, frame), accepted)
        self.assertEqual(row, original)
        self.f.now = legacy.NOW + 1.2
        later, later_frame = self.f.inputs(13, observed=legacy.NOW + 1, seq=8)
        later['payload']['evidence_roots'] = ['authored-original-rgb-root']
        with self.assertRaisesRegex(ValueError, 'original_lab_root_frame_changed'):
            self.f.publish(later, later_frame)
        self.assertEqual(len(self.payloads()), 2)

    def test_invalid_roots_and_changed_profile_never_admit(self):
        row, frame = self.f.inputs()
        row['payload']['evidence_roots'] = []
        with self.assertRaisesRegex(ValueError, 'lab_event_binding_refused'):
            self.f.publish(row, frame)
        self.assertEqual(self.payloads(), [])
        row, frame = self.f.inputs()
        self.f.join.root_lineage = None
        with self.assertRaisesRegex(ValueError, 'lab_join_binding_changed'):
            self.f.publish(row, frame)
        with self.assertRaisesRegex(ValueError, 'exact_lab_original_root_lineage_required'):
            self.make_join('arbitrary-root-profile')

    def test_default_profile_preserves_historical_binding_digest(self):
        old = self.make_join(None)
        expected = legacy.digest([old.config, old.method, old.model, old.lab_profile,
                                  old.model_manifests, old.adapter.session,
                                  old.adapter.arm, old.adapter.generation])
        self.assertEqual(old.binding_digest, expected)
        self.assertNotEqual(old.binding_digest, self.f.join.binding_digest)


if __name__ == '__main__':
    unittest.main()
