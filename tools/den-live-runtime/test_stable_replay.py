"""Config5/policy2 originals -> public journal -> actual Worker4 -> causal replay.

Geometry and optics are authored. No snapshot, stored decision or checkpoint is
substituted for the live input path; only archived outputs serve as replay oracles.
"""
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / 'tools/den-live-runtime', ROOT / 'stack/services/home-context',
                  ROOT / 'stack/services/perception-lab'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import test_runtime as runtime_cases
from journal import PREFIX
from live_delivery_test_helpers import delivery_geometry
from live_test_helpers import NOW, barrier, event, fact, publication_ref
from test_live_stable_delivery import stable_fixture


class StableCausalReplayTests(unittest.TestCase):
    fixture_factory = staticmethod(stable_fixture)
    replay = runtime_cases.ActualOrdinaryReplayTests.replay

    def setUp(self):
        self.allowed_ports = set()
        connect = socket.create_connection
        def local_only(address, *args, **kwargs):
            if address[0] not in {'127.0.0.1', 'localhost', '::1'} or address[1] not in self.allowed_ports:
                raise AssertionError('nonfixture_network_forbidden')
            return connect(address, *args, **kwargs)
        guard = patch('socket.create_connection', side_effect=local_only)
        guard.start()
        self.addCleanup(guard.stop)
        for target in ('ultralytics_worker.UltralyticsWorker.load', 'rtmpose_worker.RTMPoseWorker.load'):
            guard = patch(target, side_effect=AssertionError('native_model_loading_forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        runtime_cases.ActualOrdinaryReplayTests.setUp(self)
        self.allowed_ports.update((self.http.server.server_port, self.rpc.server.server_port))

    def publish(self, seq, y=0):
        self.now = NOW + seq - 1 + .2
        payload = fact('authored-stable-replay-fact-' + str(seq), seq=seq)
        if self.head:
            payload['head']['supersedes'] = publication_ref(self.head)
        status, self.head = self.http.request('POST', PREFIX + '/events', event(payload))
        self.assertEqual(status, 200, self.head)
        geometry = delivery_geometry(seq)
        geometry['parts']['work']['shape']['center_m'][1] = y
        if self.geometry_head:
            geometry['head']['supersedes'] = publication_ref(self.geometry_head)
        status, self.geometry_head = self.http.request('POST', PREFIX + '/events', event(geometry))
        self.assertEqual(status, 200, self.geometry_head)
        self.driver.cycle(self.now)

    def records(self):
        return [json.loads(line)['record'] for line in self.archive.read_text(encoding='utf8').splitlines()]

    def test_stable_positive_service_survives_noise_then_withdraws_and_replays_actual_causal_outputs(self):
        self.assertEqual(self.service['schema'], 'home-live-ingress-config-5')
        self.assertEqual(self.config['live_manifest']['version'], 5)
        self.assertEqual(self.config['delivery_policy']['schema'], 'home-live-task-delivery-policy-2')
        description = self.manifest['worker_description']
        self.assertIn('home-live-shadow-decision-4.schema.json', description['delivery_schema_pins'])
        self.assertIn('home-live-worker-checkpoint-4.schema.json', description['delivery_schema_pins'])
        self.assertEqual(description['config_digest'], self.service['manifest']['worker']['config_digest'])
        self.assertIn('app/src/home-live-task-delivery.js', description['module_hashes'])
        for seq in range(1, 7):
            self.publish(seq)
        first = [r['data']['prepared']['decision'] for r in self.records() if r['kind'] == 'worker_prepared'][-1]
        self.assertEqual(first['execution']['status'], 'virtual', first)
        for seq in range(7, 19):
            self.publish(seq, .005 if seq % 2 else -.005)
        rows = self.records()
        prepared = [r['data']['prepared'] for r in rows if r['kind'] == 'worker_prepared']
        decisions = [p['decision'] for p in prepared]
        for value in prepared:
            self.assertEqual(value['checkpoint']['schema'], 'home-live-worker-checkpoint-4')
            self.assertEqual(value['checkpoint']['state']['engagement']['module_hashes'], description['module_hashes'])
        for decision in decisions[6:]:
            self.assertEqual(decision['schema'], 'home-live-shadow-decision-4')
            self.assertEqual(decision['scene']['scene_id'], first['scene']['scene_id'])
            self.assertEqual(decision['allocation']['assignments'], first['allocation']['assignments'])
            self.assertEqual(decision['display_lease']['geometry_refs'], decision['input_cut']['geometry_refs'])
        self.assertGreater(decisions[-1]['execution']['readback'][0]['brightness_fraction'], 0)
        source_geometries = [p['payload'] for r in rows if r['kind'] == 'journal_prepare'
                             for p in r['data']['publications'] if p['payload_type'] == 'home-live-geometry-1']
        self.assertEqual(len(source_geometries), 18)
        self.assertEqual({p['parts']['work']['shape']['center_m'][1] for p in source_geometries[6:]}, {-.005, .005})
        self.assertTrue(any(r['data']['original_receipts'] for r in rows if r['kind'] == 'journal_prepare'))

        # An actual journal source-generation barrier retires both current heads.
        # The shared authored barrier helper encodes integer Unix nanoseconds.
        self.now = NOW + 19
        withdrawal = barrier([self.head, self.geometry_head], event_id='authored-stable-stop', at=self.now)
        status, withdrawal_receipt = self.http.request('POST', PREFIX + '/events', event(withdrawal))
        self.assertEqual(status, 200, withdrawal_receipt)
        self.driver.cycle(self.now)
        drained = self.driver.drain(deadline_ms=1000, through_cursor=withdrawal_receipt['cursor'])
        self.assertEqual(drained['status'], 'settled', drained)
        self.assertGreaterEqual(drained['input_cursor'], withdrawal_receipt['cursor'])
        self.assertEqual(self.recorder.drain({'status': 'complete', 'reason': 'authored_stable_source_stop',
            'withdrawal_receipt': withdrawal_receipt, 'worker_status': 'settled',
            'terminal_cursor': drained['input_cursor']})['status'], 'drained')
        rows = self.records()
        final = [r['data']['prepared']['decision'] for r in rows if r['kind'] == 'worker_prepared'][-1]
        self.assertEqual(final['execution']['status'], 'unavailable', final)
        final_cut = [r['data']['cut'] for r in rows if r['kind'] == 'worker_cut'][-1]
        self.assertEqual(final_cut['schema'], 'home-live-input-cut-4')
        self.assertEqual(final_cut['source_generations']['synthetic-source'], 2)
        self.assertEqual(final_cut['items'], [])
        self.assertGreaterEqual(final_cut['cursor'], withdrawal_receipt['cursor'])
        status, reproduced = self.replay()
        self.assertEqual(status, 0, reproduced)
        self.assertEqual(reproduced['status'], 'reproduced')
        self.assertEqual(reproduced['ticks'], 19)
        self.assertEqual(reproduced['commits'], 19)
        self.assertFalse(reproduced['acceptance_eligible'])
        self.assertFalse(reproduced['physical_authority'])


if __name__ == '__main__':
    unittest.main()
