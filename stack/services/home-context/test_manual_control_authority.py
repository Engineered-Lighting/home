"""Trusted control lifecycle over the actual journal and original recorder port."""
from pathlib import Path
import tempfile
import unittest

from journal import Journal, JournalError
from live_conservative_test_helpers import conservative_fixture
from live_test_helpers import NOW, PROFILE, reference
from live_worker_test_helpers import worker_receipt_resolver
from manual_control_authority import ManualControlAuthority


class ManualControlAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='manual-authority-', dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        configuration, grants, _ = conservative_fixture()
        self.now = NOW + .2
        self.journal = Journal(Path(self.directory.name) / 'journal.sqlite', grants=grants,
            profile=PROFILE, clock=lambda: self.now, live_config=configuration,
            live_receipt_resolver=worker_receipt_resolver,
            live_wall_clock=lambda: self.now, live_monotonic=lambda: self.now-NOW)
        self.addCleanup(self.journal.close)
        self.records, self.retired, self.retirement_fault = [], [], False
        self.policy = {'policy_ref': reference('authored-manual-policy'), 'operating_not_after': NOW+120}
        def retire(**values):
            if self.retirement_fault:
                raise RuntimeError('authored retirement refusal')
            self.retired.append(values)
        self.authority = ManualControlAuthority(self.journal,
            identity=configuration['manifest']['identity'], manifest_ref=configuration['manifest_ref'],
            policy=self.policy, retire=retire)

    def test_original_activation_is_recorded_before_commit_and_cannot_be_reactivated_after_stop(self):
        self.assertEqual(self.authority.state(), {'control_generation': 0, 'control_active': False})
        with self.assertRaises(JournalError):
            self.authority.activate('start-1')
        self.journal.attach_recorder(self.records.append)
        accepted = self.authority.activate('start-1')
        self.assertEqual(accepted['before'], {'control_generation': 0, 'control_active': False})
        self.assertEqual(accepted['after'], {'control_generation': 1, 'control_active': True})
        self.assertEqual(accepted['applied_at'], NOW+.2)
        self.assertEqual([record['kind'] for record in self.records], ['journal_prepare', 'journal_commit'])
        self.assertEqual(self.records[0]['data']['control_operations'][0]['receipt'], accepted)
        self.now += 1
        self.assertEqual(self.authority.activate('start-1'), accepted)
        self.assertEqual(len(self.records), 2)
        stopped = self.authority.retire('stop-1', 'operator_stop')
        self.assertEqual(stopped['after'], {'control_generation': 2, 'control_active': False})
        self.assertEqual(self.retired, [{'control_generation': 2, 'retired_at': NOW+1.2}])
        self.assertEqual(self.authority.retire('stop-1', 'operator_stop'), stopped)
        with self.assertRaises(JournalError):
            self.authority.activate('start-2')
        with self.assertRaises(JournalError):
            self.authority.retire('stop-1', 'different_reason')

    def test_recorder_prepare_failure_rolls_back_authority(self):
        def unavailable(record):
            raise OSError('authored storage fault')
        self.journal.attach_recorder(unavailable)
        with self.assertRaises(JournalError):
            self.authority.activate('start-1')
        self.assertEqual(self.authority.state(), {'control_generation':0,'control_active':False})

    def test_lost_recorder_commit_ack_is_durable_but_cannot_authorize_new_effects(self):
        def record(value):
            self.records.append(value)
            if value['kind'] == 'journal_commit':
                raise OSError('authored lost final archive acknowledgement')
        self.journal.attach_recorder(record)
        with self.assertRaises(JournalError):
            self.authority.activate('start-1')
        self.assertEqual(self.authority.state(), {'control_generation':1,'control_active':True})
        original = self.records[0]['data']['control_operations'][0]['receipt']
        self.assertEqual(self.authority.activate('start-1'), original)
        with self.assertRaises(JournalError):
            self.authority.retire('stop-1', 'operator_stop')
        self.assertEqual(len(self.records), 2)

    def test_retirement_callback_failure_keeps_the_old_committed_authority(self):
        self.journal.attach_recorder(self.records.append)
        self.authority.activate('start-1')
        self.retirement_fault = True
        with self.assertRaises(RuntimeError):
            self.authority.retire('stop-1', 'operator_stop')
        self.assertEqual(self.authority.state(), {'control_generation':1,'control_active':True})
        self.assertEqual(len(self.records), 2)


if __name__ == '__main__':
    unittest.main()
