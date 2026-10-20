"""Integrated asynchronous timing cache + health continuity contracts."""
import threading
import unittest
from dataclasses import asdict

from home_shared.health import HostClockEvidence
from home_shared.timing_client import TimingClient, TimingClientConfig
from test_health import Clocks, ClockProvider, FakeProbe, SyntheticTimingAuthority, config


class HealthTimingTests(unittest.TestCase):
    def setUp(self):
        self.clocks = Clocks()
        self.clock_authority = ClockProvider(lambda: self.clocks.mono, lambda: self.clocks.wall)
        self.stats_authority = SyntheticTimingAuthority(lambda: self.clocks.mono)
        self.timing = TimingClient(TimingClientConfig("synthetic-authority", 123, 456),
                                   monotonic=lambda: self.clocks.mono)
        self.delay = None
        self.requests = []
        def request(kind, digest, deadline):
            self.requests.append((kind, digest))
            if kind == "clock":
                return self.clock_authority.read()
            if self.delay:
                self.delay[0].set()
                self.delay[1].wait(2)
            return self.stats_authority.read_for(digest)
        # The authenticated IPC serialization/socket boundary is tested in
        # test_timing_client. Here an explicit synthetic authority controls lag.
        self.timing._request = request
        self.health = FakeProbe(config(), clock_provider=self.timing, stats_timing_provider=self.timing,
                                 monotonic=lambda: self.clocks.mono, clock=lambda: self.clocks.wall)
        self.addCleanup(self.cleanup)
        self.collect_timing()
        self.assertIsNone(self.collect_health())  # First snapshot lookup is queued.
        self.clocks.advance(1)
        self.collect_timing()
        self.original = self.collect_health()
        self.assertIsNotNone(self.original)
        self.original_expiry = self.health._cache.valid_until_mono

    def cleanup(self):
        if self.delay:
            self.delay[1].set()
        self.health.stop()
        self.timing.stop()
        for thread in (self.health._worker, self.timing._thread):
            if thread:
                thread.join(1)

    def collect_health(self):
        self.assertTrue(self.health.poll())
        self.health._worker.join(1)
        self.assertFalse(self.health._worker.is_alive())
        return self.health.read()

    def collect_timing(self):
        self.assertTrue(self.timing.poll())
        self.timing._thread.join(1)
        self.assertFalse(self.timing._thread.is_alive())

    def new_snapshot(self):
        self.clocks.advance(1)
        self.health.responses[False]["service"].update(last_updated=int(self.clocks.wall), uptime=201)

    def test_new_snapshot_delayed_receipt_preserves_original_valid_health(self):
        self.new_snapshot()
        bridge = self.collect_health()
        self.assertIs(bridge, self.original)
        self.assertEqual(self.health._cache.valid_until_mono, self.original_expiry)
        self.assertEqual(self.health.last_error, "stats_timing_pending")
        self.delay = threading.Event(), threading.Event()
        self.timing.poll()
        self.assertTrue(self.delay[0].wait(1))
        self.assertIs(self.health.read(), self.original)
        self.delay[1].set()
        self.timing._thread.join(1)
        self.clocks.advance(1)
        current = self.collect_health()
        self.assertIsNotNone(current)
        self.assertEqual(current.frigate_instance, self.original.frigate_instance)
        self.assertGreater(current.checked_mono, self.original.checked_mono)

    def test_fast_valid_clock_schema_roundtrip_cannot_resurrect_cached_health(self):
        legacy_reader = self.clock_authority.read
        def host_reader():
            value = asdict(legacy_reader())
            value["camera_host_uncertainty_seconds"] = value.pop("camera_source_uncertainty_seconds")
            value["version"] = 2
            return HostClockEvidence(**value)
        # Both directions are successful authority responses. No intervening
        # health read is allowed to observe the temporary schema transition.
        for original, alternate in ((legacy_reader, host_reader), (host_reader, legacy_reader)):
            self.clock_authority.read = original
            if original is host_reader:
                self.clocks.advance(1)
                self.collect_timing()
                self.assertIsNotNone(self.collect_health())
            expiry = self.health._cache.valid_until_mono
            generation = self.timing.invalidation_generation()
            self.clocks.advance(1)
            self.clock_authority.read = alternate
            self.collect_timing()
            self.clocks.advance(1)
            self.clock_authority.read = original
            self.collect_timing()
            self.assertLess(self.clocks.mono, expiry)
            self.assertGreaterEqual(self.timing.invalidation_generation(), generation + 2)
            self.assertIsNone(self.health.read())

    def test_pending_evidence_cannot_extend_original_cache_expiry(self):
        self.new_snapshot()
        self.assertIs(self.collect_health(), self.original)
        self.clocks.advance(2)
        self.assertIsNone(self.health.read())
        self.assertIsNone(self.collect_health())

    def test_negative_camera_or_processing_evidence_invalidates_while_pending(self):
        self.new_snapshot()
        self.health.responses[True]["healthy"] = False
        self.assertIsNone(self.collect_health())
        self.assertIsNone(self.health._cache)

    def test_camera_timing_generation_invalidates_even_while_receipt_pending(self):
        self.new_snapshot()
        self.health.responses[True]['timing_generation'] = 1
        self.assertIsNone(self.collect_health())
        self.assertIsNone(self.health._cache)
        self.collect_timing()
        self.clocks.advance(1)
        current = self.collect_health()
        self.assertEqual(current.timing_generation, 1)
        self.assertNotEqual(current.frigate_instance, self.original.frigate_instance)

    def test_processing_failure_invalidates_instead_of_bridging(self):
        self.new_snapshot()
        self.health.responses[False]["cameras"]["den_oak"]["process_fps"] = 0
        self.assertIsNone(self.collect_health())

    def test_session_change_invalidates_instead_of_bridging(self):
        self.new_snapshot()
        self.health.responses[True]["session"] = "replacement-capture"
        self.assertIsNone(self.collect_health())

    def test_pid_change_invalidates_instead_of_bridging(self):
        self.new_snapshot()
        self.health.responses[False]["cameras"]["den_oak"]["pid"] = 999
        self.assertIsNone(self.collect_health())

    def test_authority_explicit_no_evidence_is_not_pending(self):
        self.new_snapshot()
        self.assertIs(self.collect_health(), self.original)
        self.stats_authority.enabled = False
        self.collect_timing()
        self.clocks.advance(1)
        self.assertIsNone(self.collect_health())
        self.assertEqual(self.health.last_error, "health_probe_failed")

    def test_invalid_receipt_does_not_bridge_valid_old_cache(self):
        self.new_snapshot()
        self.assertIs(self.collect_health(), self.original)
        self.stats_authority.changes = {"pi_boot_id": "wrong-boot"}
        self.collect_timing()
        self.clocks.advance(1)
        self.assertIsNone(self.collect_health())

    def test_unqualified_none_provider_is_not_assumed_pending(self):
        self.new_snapshot()
        provider = SyntheticTimingAuthority(lambda: self.clocks.mono)
        provider.enabled = False
        self.health._stats_provider = provider
        self.assertIsNone(self.collect_health())

    def test_exact_snapshot_denial_revokes_cache_before_next_health_probe(self):
        self.clocks.advance(1)
        self.stats_authority.enabled = False
        self.collect_timing()
        self.assertIsNone(self.health.read())
        self.assertEqual(self.health.last_error, "health_evidence_expired_or_clock_changed")

    def test_clock_denial_revokes_cache_before_next_health_probe(self):
        self.clocks.advance(1)
        previous_request = self.timing._request
        self.timing._request = lambda kind, digest, deadline: (
            None if kind == "clock" else previous_request(kind, digest, deadline))
        self.collect_timing()
        self.assertIsNone(self.health.read())

    def test_failed_ipc_revokes_cache_before_next_health_probe(self):
        self.clocks.advance(1)
        def failed_request(*_):
            raise OSError("synthetic IPC failure")
        self.timing._request = failed_request
        self.collect_timing()
        self.assertIsNone(self.health.read())

    def test_newer_digest_denial_revokes_bridged_old_cache(self):
        self.new_snapshot()
        self.assertIs(self.collect_health(), self.original)
        self.stats_authority.enabled = False
        self.collect_timing()
        self.assertIsNone(self.health.read())

    def test_pending_newer_digest_is_not_replaced_by_health_reads(self):
        self.new_snapshot()
        self.assertIs(self.collect_health(), self.original)
        pending = self.timing._pending
        self.assertNotEqual(pending, self.health._cache.timing.snapshot_digest)
        generation = self.timing.invalidation_generation()
        self.assertIs(self.health.read(), self.original)
        self.assertEqual(self.timing._pending, pending)
        self.assertEqual(self.timing.invalidation_generation(), generation)
        self.assertEqual(self.health._cache.valid_until_mono, self.original_expiry)

    def test_failure_with_newer_digest_pending_revokes_old_cache(self):
        self.new_snapshot()
        self.assertIs(self.collect_health(), self.original)
        pending = self.timing._pending
        def failed_request(*_):
            raise OSError("synthetic IPC failure")
        self.timing._request = failed_request
        self.collect_timing()
        self.assertIsNone(self.health.read())
        self.assertEqual(self.timing._resolved_digest, pending)

    def test_success_after_denial_does_not_resurrect_prior_cache(self):
        self.clocks.advance(1)
        self.stats_authority.enabled = False
        self.collect_timing()
        self.stats_authority.enabled = True
        self.clocks.advance(.1)
        self.timing.read_for(self.health._cache.timing.snapshot_digest)
        # Complete a second authority cycle without an intervening health read.
        self.timing._next_poll = self.clocks.mono
        self.collect_timing()
        self.assertIsNone(self.health.read())

    def test_semantically_invalid_clock_revokes_before_next_health_probe(self):
        from dataclasses import replace
        self.clocks.advance(1)
        previous_request = self.timing._request
        def request(kind, digest, deadline):
            receipt = previous_request(kind, digest, deadline)
            return replace(receipt, camera_boot_id="wrong-boot") if kind == "clock" else receipt
        self.timing._request = request
        self.collect_timing()
        self.assertIsNone(self.health.read())

    def test_semantically_invalid_exact_stats_revokes_before_next_probe(self):
        self.clocks.advance(1)
        self.stats_authority.changes = {"policy_revision": "wrong-policy"}
        self.collect_timing()
        self.assertIsNone(self.health.read())

    def test_semantically_invalid_newer_stats_revokes_bridged_cache(self):
        self.new_snapshot()
        self.assertIs(self.collect_health(), self.original)
        self.stats_authority.changes = {"pi_boot_id": "wrong-boot"}
        self.collect_timing()
        self.assertIsNone(self.health.read())

    def test_invalid_then_valid_stats_before_read_cannot_resurrect_cache(self):
        digest = self.health._cache.timing.snapshot_digest
        generation = self.timing.invalidation_generation()
        self.clocks.advance(1)
        self.stats_authority.changes = {"policy_revision": "wrong-policy"}
        self.delay = threading.Event(), threading.Event()
        self.assertTrue(self.timing.poll())
        self.assertTrue(self.delay[0].wait(1))
        # A concurrent health probe can queue the next refresh while the first
        # response is inflight and still sees the preceding valid receipt.
        self.assertIsNotNone(self.timing.read_for(digest))
        self.delay[1].set()
        self.timing._thread.join(1)
        self.assertGreater(self.timing.invalidation_generation(), generation)
        self.assertEqual(self.timing.current_evidence(), (None, None))
        self.stats_authority.changes = {}
        self.clocks.advance(1)
        self.collect_timing()
        self.assertIsNotNone(self.timing.current_evidence()[1])
        self.assertGreater(self.original_expiry, self.clocks.mono)
        self.assertIsNone(self.health.read())

    def test_invalid_then_valid_clock_before_read_cannot_resurrect_cache(self):
        from dataclasses import replace
        self.clocks.advance(1)
        previous_request = self.timing._request
        def invalid_request(kind, digest, deadline):
            receipt = previous_request(kind, digest, deadline)
            return replace(receipt, camera_boot_id="wrong-boot") if kind == "clock" else receipt
        self.timing._request = invalid_request
        generation = self.timing.invalidation_generation()
        self.collect_timing()
        self.assertGreater(self.timing.invalidation_generation(), generation)
        self.timing._request = previous_request
        self.clocks.advance(1)
        self.collect_timing()
        self.assertIsNotNone(self.timing.read())
        self.assertGreater(self.original_expiry, self.clocks.mono)
        self.assertIsNone(self.health.read())

    def test_unreviewed_source_version_revokes_before_publication(self):
        self.clocks.advance(1)
        self.stats_authority.changes = {"source_version": "0.19.0"}
        generation = self.timing.invalidation_generation()
        self.collect_timing()
        self.assertGreater(self.timing.invalidation_generation(), generation)
        self.assertEqual(self.timing.current_evidence(), (None, None))
        self.assertIsNone(self.health.read())
