"""Actual source4/Journal/Node-worker acceptance, with fictional observations.

The new command input reader may use only the independently accepted current
publication. No canonical/native fixture process or physical I/O is selected.
"""
from copy import deepcopy
import unittest

import test_lab_stereo_worker_runtime as fixture
from room_committed_source import CommittedRoomSource, RoomCommitBinding, RoomCommitUnavailable


class CommittedRoomSourceTests(unittest.TestCase):
    def test_actual_worker_commit_current_samples_and_withdrawal(self):
        owner = fixture.LabStereoWorkerRuntimeTests()
        owner.setUp()
        self.addCleanup(owner.doCleanups)
        graph = owner.create()
        owner.clear_authored_bootstrap()
        journal = graph.journal
        worker = journal.dynamic.worker
        active = [True]
        binding = RoomCommitBinding(deepcopy(owner.source.scope),owner.worker['worker_id'],owner.worker['producer_id'],
            worker.policy['config_digest'],journal.dynamic.config['manifest_ref']['digest'],worker.worker_version,
            tuple(f['fixture_id'] for f in worker.policy['room_policy']['fixtures']))
        source = CommittedRoomSource(journal,binding=binding,current=lambda:active[0],clock=lambda:owner.f.now)
        with self.assertRaisesRegex(RoomCommitUnavailable,'no_accepted'):
            source.current()

        def latest_commit():
            return next(row['data']['commit'] for row in reversed(owner.records()) if row['kind']=='worker_prepared')

        first = latest_commit()
        installed = source.install_commit(first)
        self.assertEqual(installed['receipt']['status'],'accepted')
        self.assertFalse(installed['native_achieved_truth'])
        self.assertEqual(len(source.sample_components(binding.fixture_ids[0])['domains']),8)
        with self.assertRaisesRegex(RoomCommitUnavailable,'foreign_room_fixture'):
            source.sample_components('foreign-fixture')

        positive = None
        for offset in range(2,9):
            camera,ha,_ = owner.append_pair_and_input(offset)
            # Fresh source heads must be committed before the old cut can
            # supply commands. Readiness is not a prepared-row assertion.
            with self.assertRaisesRegex(RoomCommitUnavailable,'new_room_input'):
                source.current()
            if offset == 2:
                owner.append_current_jev(camera,ha)
            owner.f.now = fixture.live.NOW+offset+.55
            decision = owner.tick()
            commit = latest_commit()
            with self.assertRaisesRegex(RoomCommitUnavailable,'checkpoint_superseded'):
                source.current()
            result = source.install_commit(commit)
            self.assertEqual(result['decision'],decision)
            for member in decision['room_output']['fixtures']:
                components = source.sample_components(member['fixture_id'])
                self.assertEqual(len(components['domains']),8)
                for original,component in zip(member['domains'],components['domains']):
                    self.assertNotIn('desired',component)
                    expected = original['sample'] if owner.f.now < original['valid_until'] else None
                    self.assertEqual(component['sample'],expected)
                    self.assertEqual(component['valid_until'],original['valid_until'])
            selected = decision['room_output']['task']['fixture_id']
            if selected and any(row['sample'] and row['sample']['brightness_fraction'] > 0
                for row in source.sample_components(selected)['domains']):
                positive = deepcopy(result)
        self.assertIsNotNone(positive)

        exact = latest_commit()
        changed = deepcopy(exact)
        changed['publications'][0]['payload']['id'] += '-changed'
        with self.assertRaisesRegex(RoomCommitUnavailable,'not_durably_accepted'):
            source.install_commit(changed)
        self.assertEqual(source.current()['commit'],exact)  # Bad adoption preserves the old exact selection.
        removed = journal.db.execute('SELECT receipt FROM receipts WHERE event_id=?',('worker-commit/'+exact['id'],)).fetchone()[0]
        journal.db.execute('DELETE FROM receipts WHERE event_id=?',('worker-commit/'+exact['id'],))
        with self.assertRaisesRegex(RoomCommitUnavailable,'not_durably_accepted'):
            source.current()
        # Restore the original bytes only; no new receipt or new commit is invented.
        from journal import canonical,digest
        journal.db.execute('INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)',
            (exact['producer_id'],exact['stream_epoch'],'worker-commit/'+exact['id'],digest(exact),removed,
             canonical({'schema':'home-context-worker-commit-tombstone-1','site_id':exact['site_id'],'room_id':exact['room_id']})))

        member = next(f for f in source.current()['decision']['room_output']['fixtures']
                      if any(row['sample'] is not None for row in f['domains']))
        until = max(row['valid_until'] for row in member['domains'])
        readings = iter((owner.f.now,owner.f.now-1))
        source.clock = lambda:next(readings)
        with self.assertRaisesRegex(RoomCommitUnavailable,'clock_rollback'):
            source.current()
        source.clock = lambda:owner.f.now
        # A slow installed currentness check must use its completion time,
        # never the earlier check's still-live lease.
        owner.f.now = until-.1
        checks = [0]
        def delayed_current():
            checks[0] += 1
            if checks[0] == 2:
                owner.f.now = until
            return active[0]
        source.current_scope = delayed_current
        self.assertTrue(all(row['sample'] is None for row in source.sample_components(member['fixture_id'])['domains']))
        source.current_scope = lambda:active[0]
        owner.f.now = until  # Exclusive expiry does not become an invented zero-duty command.
        self.assertTrue(all(row['sample'] is None for row in source.sample_components(member['fixture_id'])['domains']))
        active[0] = False
        with self.assertRaisesRegex(RoomCommitUnavailable,'session_not_current'):
            source.current()
        active[0] = True
        graph.withdraw()
        with self.assertRaisesRegex(RoomCommitUnavailable,'authority_changed|source_generation_changed'):
            source.current()


if __name__ == '__main__':
    unittest.main()
