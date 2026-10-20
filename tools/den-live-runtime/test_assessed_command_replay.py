"""Actual config13/worker12 replay of data-only retained task originals.

External scene/model/PC observations are authored by the existing Room fixture.
The original below is deliberately not a qualified compiler input. This checks
durable accepted-commit correlation, not commands or native/physical replay.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from archive import Recorder, RecorderRPC, canonical, digest
from room_committed_source import CommittedRoomSource, RoomCommitBinding
from supervisor import NodeWorkerDriver, describe_worker
import test_room_subset_worker as room_fixture


ROOT = Path(__file__).resolve().parents[2]


class AssessedCommandReplayTests(unittest.TestCase):
    def test_manifest2_replays_actual_worker_and_requires_durable_task_commit_transaction(self):
        case = room_fixture.RoomSubsetWorkerTests()
        self.addCleanup(case.doCleanups)
        observed = []
        actual_create = room_fixture.create_production_acquisition
        holder = {}
        node = shutil.which('node')
        self.assertIsNotNone(node)

        def create_recorded(*args, **kwargs):
            graph = actual_create(*args, **kwargs)
            try:
                config = {k: deepcopy(v) for k, v in case.config.items()
                          if k not in {'token', 'token_file', 'service_url'}}
                manifest = dict(schema='home-live-session-manifest-2', version=2,
                    runtime_profile='home-live-runtime-config-3', session_ref=deepcopy(case.identity),
                    configuration_digest=graph.configuration_digest, source_bundle_digest='b'*64,
                    worker_config=config, worker_description=describe_worker(node, config))
                recorder = Recorder(Path(case.directory.name)/'actual-subset.jsonl', manifest,
                    max_records=4096, max_bytes=16*1024**2, min_free_bytes=1)
                case.addCleanup(lambda: recorder.drain({'status': 'incomplete', 'reason': 'authored_cleanup'}))

                class Port:
                    def record(self, body, *, record_id=None):
                        ack = recorder.record(body, record_id=record_id)
                        observed.append(deepcopy(body))
                        return ack

                port = Port()
                graph.journal.attach_recorder(port.record)
                rpc = RecorderRPC(port)
                case.addCleanup(rpc.close)
                holder.update(graph=graph, recorder=recorder, rpc=rpc, endpoint=rpc.start())
                return graph
            except BaseException:
                graph.close()
                raise

        class RecordedProbe:
            """Adapt fixture commands to the unchanged actual worker entry.

            Decisions come only from durably ACKed worker_prepared originals;
            neither a publication nor an accepted checkpoint is synthesized.
            """
            driver = None

            def command(self, op, **fields):
                if op == 'start':
                    self.driver = NodeWorkerDriver(node_runtime=node, config=fields['config'],
                        recorder=holder['endpoint'], controlled_time=fields['t'])
                    return self.driver.readiness()
                if op == 'tick':
                    start = len(observed)
                    self.driver.cycle(fields['t'])
                    status = self.driver.status()
                    case.assertEqual(status['status'], 'ready', status)
                    fresh = observed[start:]
                    accepted = {row['data']['receipt']['id'] for row in fresh
                                if row['kind'] == 'worker_commit_result'
                                and row['data']['receipt']['status'] == 'accepted'}
                    rows = [row['data']['prepared']['decision'] for row in fresh
                            if row['kind'] == 'worker_prepared'
                            and row['data']['prepared']['commit_id'] in accepted]
                    return dict(decisions=deepcopy(rows), http_errors=[])
                if op == 'stop':
                    return self.driver._command('stop')
                raise AssertionError('unrequested_recorded_fixture_command:'+op)

            def close(self):
                if self.driver is not None:
                    self.driver.close()
                    self.driver = None

        with patch.object(room_fixture, 'create_production_acquisition', side_effect=create_recorded), \
                patch.object(room_fixture, 'WorkerProbe', RecordedProbe):
            case.setUp()
        for _ in range(12):
            decision = case.advance()
            if (decision['execution']['status'] == 'virtual'
                    and decision['execution']['readback'][0]['brightness_fraction'] > 0):
                break
        else:
            self.fail('unchanged PC dwell did not earn an actual sampled task')
        graph, recorder = holder['graph'], holder['recorder']
        worker = graph.journal.dynamic.worker
        self.assertEqual(worker.worker_version, 'home-live-context-worker-12.0.0')
        commit = deepcopy(next(row['data']['commit'] for row in reversed(observed)
                               if row['kind'] == 'worker_prepared'))
        binding = RoomCommitBinding(deepcopy(case.identity), worker.policy['worker_id'],
            worker.policy['producer_id'], worker.policy['config_digest'], case.config['manifest_ref']['digest'],
            worker.worker_version, tuple(f['fixture_id'] for f in worker.policy['room_policy']['fixtures']))
        source = CommittedRoomSource(graph.journal, binding=binding, current=lambda: True)
        snapshot = source.install_commit(commit)
        ref = snapshot['room_commit_ref']
        self.assertEqual(snapshot['decision'], decision)
        request = dict(schema='home-fixture-retained-angle-request-2', version=2,
            assessment={'profile_sha256': '2'*64},
            retained_aim_ref=dict(room_commit_ref=deepcopy(ref), input_cut_digest=decision['input_cut']['cut_digest']))
        data = dict(fixture_id=decision['room_output']['task']['fixture_id'], assessment=request,
            assessment_sha256=digest(request), profile_sha256='2'*64, room_commit_ref=deepcopy(ref))
        ack = recorder.record({'kind': 'room_task_assessment_original', 'data': data},
                              record_id='room-task-assessment:'+digest(data))
        self.assertGreater(ack['seq'], 0)
        # Stop every writer before closing this data-only original stream. This
        # is not the Supervisor's full source-withdrawal/worker/native drain.
        case.probe.command('stop')
        case.probe.close()
        holder['rpc'].close()
        graph.close(reason='authored_replay_original_stream_end')
        self.assertEqual(recorder.drain({'status': 'complete',
            'reason': 'authored_data_only_replay_stream'})['status'], 'drained')

        def replay(path):
            run = subprocess.run([node, str(ROOT/'tools/den-live-runtime/replay.cjs'), str(path)],
                cwd=ROOT, capture_output=True, text=True, timeout=15)
            self.assertLessEqual(len(run.stdout.encode()), 65536)
            self.assertEqual(run.stderr, '')
            return run.returncode, json.loads(run.stdout)

        code, result = replay(recorder.path)
        self.assertEqual(code, 0, result)
        self.assertEqual((result['schema'], result['version']), ('home-live-ordinary-replay-result-2', 2))
        self.assertEqual(result['status'], 'reproduced')
        self.assertEqual(result['task_assessment_originals'], 1)
        self.assertFalse(result['canonical_commands_replayed'])
        self.assertFalse(result['acceptance_eligible'])
        self.assertFalse(result['physical_authority'])
        entries = [json.loads(line) for line in recorder.path.read_bytes().splitlines()]
        settled = [entry['record']['data'] for entry in entries
                   if entry['record']['kind'] == 'journal_commit'
                   and isinstance(entry['record']['data'].get('result'), dict)
                   and entry['record']['data']['result'].get('schema') == 'home-context-worker-commit-receipt-1'
                   and entry['record']['data']['result'].get('id') == commit['id']]
        self.assertEqual(len(settled), 1)
        transaction = settled[0]['tx_id']
        self.assertTrue(any(entry['record']['kind'] == 'journal_prepare'
                            and entry['record']['data']['tx_id'] == transaction for entry in entries))
        for mode in ('remove', 'abort'):
            changed = deepcopy(entries)
            if mode == 'remove':
                changed = [entry for entry in changed if not (
                    entry['record']['kind'] in {'journal_prepare', 'journal_commit'}
                    and entry['record']['data'].get('tx_id') == transaction)]
            else:
                for entry in changed:
                    if entry['record']['kind'] == 'journal_commit' and entry['record']['data']['tx_id'] == transaction:
                        entry['record']['kind'] = 'journal_abort'
            # Leave the real worker-prepared/result and task original present;
            # a valid hash chain alone must not replace durable acceptance.
            self.assertTrue(any(entry['record']['kind'] == 'worker_commit_result'
                                and entry['record']['data']['receipt']['id'] == commit['id'] for entry in changed))
            previous = None
            for index, entry in enumerate(changed):
                entry.update(seq=index, previous_digest=previous)
                entry.pop('digest')
                entry['digest'] = digest(entry)
                previous = entry['digest']
            path = Path(case.directory.name)/(mode+'-worker-acceptance.jsonl')
            path.write_text(''.join(canonical(entry)+'\n' for entry in changed), encoding='utf-8', newline='\n')
            code, refused = replay(path)
            self.assertEqual(code, 2, refused)
            self.assertEqual(refused['status'], 'incomplete')
            self.assertEqual(refused['reason'], 'archive_task_commit_transaction_missing')


if __name__ == '__main__': unittest.main()
