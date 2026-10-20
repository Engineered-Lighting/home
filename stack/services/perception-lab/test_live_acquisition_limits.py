"""Public bounded acquisition behavior using synthetic external I/O only."""
from pathlib import Path
import threading
import time
import unittest

import test_live_acquisition as fixture
from journal import digest


class AcquisitionLimitsTests(unittest.TestCase):
    setUp = fixture.ProductionAcquisitionTests.setUp
    construct = fixture.ProductionAcquisitionTests.construct
    camera_pair = fixture.ProductionAcquisitionTests.camera_pair
    control_clock = fixture.ProductionAcquisitionTests.control_clock
    snapshot = fixture.ProductionAcquisitionTests.snapshot

    def test_missing_or_over_budget_memory_monitor_cannot_dispatch_camera(self):
        graph = self.construct()
        for reading in (None, 0, 513 * 1024**2):
            with self.subTest(memory=reading):
                graph.rss = lambda: reading
                result = graph.step()
                self.assertEqual(result['reason'], 'memory_limit')
                self.assertEqual(self.trace, [])
                self.assertEqual(self.snapshot()['items'], [])

    def test_source_gap_recovers_only_with_new_original_capture(self):
        graph = self.construct()
        self.assertEqual(graph.step()['status'], 'observed')
        first = self.snapshot()['items'][0]
        graph.rss = lambda: None
        self.assertEqual(graph.step()['reason'], 'memory_limit')
        self.assertEqual(self.snapshot()['items'], [])
        graph.rss = lambda: 64 * 1024**2
        self.now += 1
        self.mono += 1
        self.sequence = 8
        self.assertEqual(graph.step()['status'], 'observed')
        current = self.snapshot()['items'][0]
        self.assertGreater(current['payload']['source_generation'], first['payload']['source_generation'])
        self.assertEqual(current['payload']['observed_at'], first['payload']['observed_at'] + 1)
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'] * 2)

    def test_runtime_equality_blocks_without_camera_or_new_observation(self):
        graph = self.construct()
        self.mono += self.config['limits']['max_runtime_s']
        result = graph.step()
        self.assertEqual(result['reason'], 'runtime_limit')
        self.assertEqual(self.trace, [])
        self.assertEqual(self.snapshot()['items'], [])

    def test_cycle_limit_stops_owned_graph_and_preserves_shared_reader(self):
        self.config['limits']['max_cycles'] = 1
        self.local_grant['configuration_digest'] = digest(self.config)
        graph = self.construct()
        self.assertEqual(graph.step()['status'], 'observed')
        self.now += 1
        self.mono += 1
        self.sequence = 8
        result = graph.step()
        self.assertEqual(result['reason'], 'cycle_limit')
        self.assertTrue(graph.status()['closed'])
        self.assertEqual(self.trace, ['capture', 'detector', 'pose'])
        self.assertEqual(self.occupancy.reading()['state'], 'occupied')
        self.assertEqual(graph.close()['status'], 'stopped')

    def test_existing_journal_is_never_reopened_or_implicitly_resumed(self):
        (Path(self.work.name) / 'synthetic.sqlite').touch()
        with self.assertRaises(FileExistsError):
            self.construct()
        self.assertEqual(self.trace, [])

    def test_stalled_camera_owner_keeps_slot_busy_and_discards_late_capture(self):
        self.config['limits']['stage_timeout_s'] = .05
        self.local_grant['configuration_digest'] = digest(self.config)
        entered, release = threading.Event(), threading.Event()
        transport = self.camera['pair_transport']
        def stalled(*args, **options):
            entered.set()
            release.wait(2)
            return transport(*args, **options)
        self.camera['pair_transport'] = stalled
        graph = self.construct()
        try:
            started = time.monotonic()
            result = graph.step()
            self.assertTrue(entered.is_set())
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['reason'], 'adapter_deadline')
            self.assertEqual(self.snapshot()['items'], [])
            self.mono += .5
            self.assertEqual(graph.step()['reason'], 'local_model_busy')
            self.assertEqual(graph.status()['counters']['cycles'], 1)
            self.assertEqual(graph.close()['status'], 'unconfirmed')
        finally:
            release.set()
        until = time.monotonic() + 1
        while graph.status()['inflight'] and time.monotonic() < until:
            time.sleep(.01)
        self.assertFalse(graph.status()['inflight'])
        self.assertNotIn('detector', self.trace)
        self.assertNotIn('pose', self.trace)
        self.assertEqual(graph.close()['status'], 'stopped')


if __name__ == '__main__': unittest.main()
