"""Ordinary config12 replay of actual authored factory/HTTP/worker PC support.

HA, camera/model observations and qualification inputs are authored. The causal
recorder, acquisition factory, public journal, Node worker and Python validator
are ordinary implementations. Archived outputs are replay oracles only.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / 'stack/services/home-context', ROOT / 'stack/services/perception-lab',
                  ROOT / 'tools/den-live-runtime'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from archive import Recorder, RecorderRPC
from supervisor import NodeWorkerDriver
from live_worker_test_helpers import describe
import test_pc_support_worker as joined
import test_runtime as ordinary


class _RecordedProbe:
    """Adapt the existing test port to the real recorded worker child.

    Decisions returned to the joined harness are read only after their actual
    worker_commit_result was durably recorded. No decision is generated here.
    """
    def __init__(self, owner):
        self.owner = owner
        self.rpc = RecorderRPC(owner.recorder)
        self.port = self.rpc.start()
        self.driver = None

    def command(self, operation, **fields):
        if operation == 'start':
            self.driver = NodeWorkerDriver(node_runtime=shutil.which('node'), config=fields['config'],
                recorder=self.port, controlled_time=fields['t'])
            self.owner.driver = self.driver
            return {'decisions': [], 'statuses': [], 'http_errors': []}
        if operation != 'tick' or self.driver is None:
            raise AssertionError('ordinary_replay_fixture_operation_refused')
        before = len(self.owner.records())
        self.driver.cycle(fields['t'])
        fresh = self.owner.records()[before:]
        accepted = {row['data']['receipt']['id'] for row in fresh if row['kind'] == 'worker_commit_result'
                    and row['data']['receipt']['status'] == 'accepted'}
        decisions = [deepcopy(row['data']['prepared']['decision']) for row in fresh
                     if row['kind'] == 'worker_prepared' and row['data']['prepared']['commit_id'] in accepted]
        return {'decisions': decisions, 'statuses': [],
                'http_errors': [] if decisions else [self.driver.status()]}

    def close(self):
        if self.driver is not None:
            self.driver.close()
        self.rpc.close()


class PCSupportOrdinaryReplayTests(unittest.TestCase):
    wait_occupancy = joined.PCSupportWorkerTests.wait_occupancy
    request = joined.PCSupportWorkerTests.request
    checkpoint = joined.PCSupportWorkerTests.checkpoint
    relation = joined.PCSupportWorkerTests.relation
    tick = joined.PCSupportWorkerTests.tick
    advance = joined.PCSupportWorkerTests.advance
    replay = ordinary.ActualOrdinaryReplayTests.replay
    rewrite = ordinary.ActualOrdinaryReplayTests.rewrite

    def records(self):
        return [json.loads(line)['record'] for line in self.archive.read_text(encoding='utf8').splitlines()]

    def setUp(self):
        actual_factory = joined.create_production_acquisition

        def recorded_factory(*args, **kwargs):
            graph = actual_factory(*args, **kwargs)
            try:
                self.work = self.directory
                sanitized = {key: deepcopy(value) for key, value in self.config.items()
                             if key not in {'token', 'token_file', 'service_url'}}
                description = describe(sanitized)
                self.manifest = {'schema': 'home-live-session-manifest-1', 'version': 1,
                    'session_ref': deepcopy(self.identity), 'configuration_digest': description['config_digest'],
                    'source_bundle_digest': 'b' * 64, 'worker_config': sanitized, 'worker_description': description}
                self.archive = Path(self.directory.name) / 'authored-pc-session.jsonl'
                self.recorder = Recorder(self.archive, self.manifest, max_records=1024,
                    max_bytes=16 * 1024 * 1024, min_free_bytes=1)
                self.addCleanup(lambda: self.recorder.drain({'status': 'incomplete', 'reason': 'test_cleanup'}))
                graph.journal.attach_recorder(self.recorder.record)
            except BaseException:
                graph.close()
                raise
            return graph

        # Attach readiness before the existing factory warm-up; the real driver
        # receives the same sole journal/configuration as the joined test.
        with patch.object(joined, 'create_production_acquisition', side_effect=recorded_factory), \
                patch.object(joined, 'WorkerProbe', side_effect=lambda: _RecordedProbe(self)):
            joined.PCSupportWorkerTests.setUp(self)

    def test_actual_pc_progress_replays_and_private_original_losses_are_incomplete(self):
        for _ in range(7):
            self.advance()
        supported = self.relation()
        self.assertEqual(supported['state'], 'supported')
        self.assertGreaterEqual(supported['enter_dwell'], 5)
        self.assertEqual(supported['chosen_clause']['qualification'], 'qualified_pc_input')
        terminal = self.driver.drain(deadline_ms=1000, through_cursor=self.prior_fact_ref['cursor'])
        self.assertEqual(terminal['status'], 'settled', terminal)
        self.assertGreaterEqual(terminal['input_cursor'], self.prior_fact_ref['cursor'])
        self.assertEqual(self.recorder.drain({'status': 'complete', 'reason': 'authored_pc_support_replay',
            'terminal_cursor': terminal['input_cursor']})['status'], 'drained')

        records = self.records()
        source_originals = [original for row in records if row['kind'] == 'journal_prepare'
                            for original in row['data']['original_receipts'] if 'pc_input_original' in original]
        private_cuts = [row['data']['cut']['pc_input_originals'] for row in records if row['kind'] == 'worker_cut']
        self.assertEqual(len(source_originals), 7)
        self.assertTrue(any(private_cuts))
        self.assertEqual(records[0]['kind'], 'session_manifest')
        self.assertEqual(records[1]['kind'], 'journal_prepare')
        self.assertEqual(records[-1]['kind'], 'session_end')
        code, replayed = self.replay()
        self.assertEqual(code, 0, replayed)
        self.assertEqual(replayed['status'], 'reproduced')
        self.assertGreaterEqual(replayed['ticks'], 8)
        self.assertEqual(replayed['ticks'], replayed['commits'])
        self.assertFalse(replayed['physical_authority'])
        self.assertFalse(replayed['acceptance_eligible'])

        # Rehash the complete outer archive after each alteration. These cases
        # must fail because evidence is missing/changed, not because its outer
        # hash chain became invalid. Worker cuts still retain the original body.
        for attack, reason in (
                ('missing_record', 'archive_missing_original_receipt'),
                ('missing_body', 'archive_missing_pc_input_original'),
                ('changed_body', 'archive_pc_input_original_mismatch')):
            with self.subTest(attack=attack):
                def mutate(entries):
                    transaction = next(entry['record']['data'] for entry in entries
                        if entry['record']['kind'] == 'journal_prepare'
                        and any('pc_input_original' in original for original in entry['record']['data']['original_receipts']))
                    original = next(original for original in transaction['original_receipts'] if 'pc_input_original' in original)
                    if attack == 'missing_record':
                        transaction['original_receipts'].remove(original)
                    elif attack == 'missing_body':
                        del original['pc_input_original']
                    else:
                        original['pc_input_original']['input_at'] -= 1
                code, refused = self.replay(self.rewrite(mutate))
                self.assertEqual(code, 2, refused)
                self.assertEqual(refused['status'], 'incomplete')
                self.assertEqual(refused['reason'], reason, refused)
                self.assertFalse(refused['physical_authority'])
                self.assertFalse(refused['acceptance_eligible'])


if __name__ == '__main__':
    unittest.main()
