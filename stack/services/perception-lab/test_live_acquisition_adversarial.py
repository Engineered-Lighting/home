"""Adversarial production-graph tests; only external camera/SDK I/O is fake."""
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import threading
import time
import unittest

import test_live_acquisition as factory_fixture


class ProductionAcquisitionAdversarialTests(unittest.TestCase):
    setUp = factory_fixture.ProductionAcquisitionTests.setUp
    construct = factory_fixture.ProductionAcquisitionTests.construct
    camera_pair = factory_fixture.ProductionAcquisitionTests.camera_pair
    control_clock = factory_fixture.ProductionAcquisitionTests.control_clock
    snapshot = factory_fixture.ProductionAcquisitionTests.snapshot

    def test_duplicate_capture_and_expired_retry_do_not_rerun_models_or_refresh_heads(self):
        graph = self.construct()
        self.assertEqual(graph.step()['status'], 'observed')
        original = deepcopy(self.snapshot()['items'][0])
        self.now += .5
        self.mono += .5
        duplicate = graph.step()
        self.assertEqual(duplicate['reason'], 'duplicate_capture')
        self.assertEqual(self.snapshot()['items'][0], original)
        self.assertEqual(graph.status()['counters']['captures'], 1)
        self.assertEqual(self.trace.count('detector'), 1)
        self.now += 4
        self.mono += .5
        expired = graph.step()
        self.assertEqual(expired['reason'], 'capture_expired')
        self.assertEqual(self.snapshot()['items'], [])
        self.assertEqual(self.trace.count('detector'), 1)

    def test_revocation_after_detector_sdk_prevents_pose_and_closes_transient_source(self):
        predict = self.detector.model.predict
        def revoke_during_sdk(**kwargs):
            value = predict(**kwargs)
            self.permitted = False
            return value
        self.detector.model.predict = revoke_during_sdk
        graph = self.construct()
        cycle = graph.step()
        self.assertEqual(cycle['status'], 'failed')
        self.assertEqual(cycle['reason'], 'local_acquisition_revoked')
        self.assertEqual(self.trace, ['capture', 'detector'])
        self.assertTrue(graph.status()['closed'])
        self.assertFalse(graph.status()['inflight'])
        cleanup = graph.close(reason='adversarial_verify')
        self.assertEqual(cleanup['status'], 'stopped')
        self.assertTrue(cleanup['buffers_cleared'])
        self.assertEqual(cleanup['withdrawal'], 'recorded')

    def test_native_timeout_keeps_slot_busy_and_cleanup_unconfirmed_until_sdk_returns(self):
        self.config['limits']['stage_timeout_s'] = .05
        # A changed frozen config needs its matching local workload grant.
        from journal import digest
        self.local_grant['configuration_digest'] = digest(self.config)
        entered, release = threading.Event(), threading.Event()
        predict = self.detector.model.predict
        def stalled(**kwargs):
            entered.set()
            release.wait(2)
            return predict(**kwargs)
        self.detector.model.predict = stalled
        graph = self.construct()
        started = time.monotonic()
        try:
            cycle = graph.step()
            self.assertEqual(cycle['status'], 'failed')
            self.assertTrue(entered.is_set())
            self.assertLess(time.monotonic() - started, .5)
            self.assertTrue(graph.status()['inflight'])
            self.assertEqual(self.snapshot()['items'], [])
            self.mono += .5
            blocked = graph.step()
            self.assertEqual(blocked['reason'], 'local_model_busy')
            self.assertEqual(self.trace.count('capture'), 1)
            cleanup = graph.close(reason='native_timeout')
            self.assertEqual(cleanup['status'], 'unconfirmed')
            self.assertFalse(cleanup['buffers_cleared'])
        finally:
            release.set()
        until = time.monotonic() + 1
        while graph.status()['inflight'] and time.monotonic() < until:
            time.sleep(.01)
        self.assertFalse(graph.status()['inflight'])
        self.assertNotIn('pose', self.trace)
        cleanup = graph.close(reason='native_quiescent')
        self.assertEqual(cleanup['status'], 'stopped')
        self.assertTrue(cleanup['buffers_cleared'])

    def test_publication_grant_does_not_authorize_capture_or_model_work(self):
        self.permitted = False
        graph = self.construct()
        cycle = graph.step()
        self.assertEqual(cycle['reason'], 'local_acquisition_revoked')
        self.assertEqual(self.trace, [])
        self.assertTrue(graph.status()['closed'])

    def test_actual_production_factory_runs_through_development_launcher_and_real_kit(self):
        from producer_development import run_development_session
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required by the actual commissioning kit')
        record = Path(self.work.name) / 'synthetic-owner-record.json'
        kit = Path(__file__).resolve().parents[3] / 'tools' / 'den-commissioning' / 'commission.mjs'
        subprocess.run([node, str(kit), 'template', '--output', str(record)],
                       capture_output=True, timeout=10, check=True)
        graph = self.construct()
        state = graph.status()
        config = {'schema': 'home-producer-development-1', 'version': 1,
                  'session_ref': state['session_ref'], 'source_id': state['source_id'],
                  'configuration_digest': state['configuration_digest'],
                  'max_runtime_s': 5, 'max_cycles': 1, 'interval_s': .5,
                  'commissioning_record': str(record), 'commissioning_freeze': None,
                  'measurement_summary': None}
        report = run_development_session(graph, config, clock=time.monotonic,
                  sleep=time.sleep, stop_requested=lambda: False, node_runtime=node)
        self.assertEqual(report['status'], 'development_complete', report)
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'])
        self.assertEqual(report['graph_counters']['captures'], 1)
        self.assertEqual(report['counts']['observed'], 1)
        self.assertEqual(report['commissioning']['status'], 'incomplete')
        self.assertEqual(report['missing_stages'], ['spatial', 'interaction'])
        self.assertEqual(report['cleanup']['status'], 'stopped')
        self.assertEqual(report['cleanup']['withdrawal'], 'recorded')
        self.assertTrue(report['cleanup']['buffers_cleared'])
        self.assertEqual(report['runtime_qualification'], 'not_assessed')
        self.assertFalse(report['physical_authority'])

    def test_failed_withdrawal_stays_unconfirmed_after_owned_journal_is_closed(self):
        self.journal_grant['operations'] = ['read']
        self.journal_grant['live_bindings'][0]['capabilities'] = ['read']
        graph = self.construct()
        self.assertEqual(graph.step()['status'], 'failed')
        first = graph.close(reason='withdrawal_denied')
        second = graph.close(reason='withdrawal_denied')
        self.assertEqual(first, second)
        self.assertEqual(first['status'], 'unconfirmed')
        self.assertEqual(first['withdrawal'], 'not_recorded')
        self.assertTrue(first['buffers_cleared'])
        self.assertFalse(graph.status()['inflight'])
        # This is a test-owned synthetic ledger, never a household database.
        # Windows refuses this move if SQLite retains its journal handle.
        source = Path(self.work.name) / 'synthetic.sqlite'
        retained = source.with_name('retained-synthetic.sqlite')
        source.rename(retained)
        self.assertTrue(retained.is_file())


if __name__ == '__main__':
    unittest.main()
