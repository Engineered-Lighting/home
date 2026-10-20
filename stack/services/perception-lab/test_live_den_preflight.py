"""Authored independent receipts and injected local probes; no host commands."""
import copy
import unittest

from live_den_preflight import LocalDenPreflight
from live_ingress_validation import validate_clock


class LocalDenPreflightTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.policy = {'mapping_id': 'test-independent-control', 'mapping_digest': 'a' * 64,
                       'source_epoch': 'test-boot', 'original_unit': 'unix_ns', 'epoch_origin_s': 0, 'max_uncertainty_s': .01}
        self.receipt = {'schema': 'home-den-local-preflight-1', 'version': 1, 'configuration_digest': 'b' * 64,
            'host': 'authored-host', 'boot_id': 'authored-boot', 'inspected_at': self.now - 5, 'valid_until': self.now + 120,
            'allowed_failed_units': ['documented-observer.service'], 'qualification_current': False,
            'control_clock': {'policy': self.policy, 'uncertainty_s': .001},
            'limits': {'min_available_mb': 1024, 'min_free_bytes': 128 * 1024**2, 'max_temperature_c': 80, 'max_load_per_cpu': .8}}
        self.receipt['execution_boundary'] = {'cgroup': '/system.slice/authored.service', 'service_unit': 'authored.service',
            'memory_max_bytes': 1024**3, 'pids_max': 64, 'cpu_quota_us': 100000, 'cpu_period_us': 100000,
            'service_manager': 'system', 'required_units': ['authored.service'], 'required_containers': []}
        self.probe = {'host': 'authored-host', 'boot_id': 'authored-boot', 'failed_units': ['documented-observer.service'],
                      'new_fault': False, 'available_mb': 2048, 'free_bytes': 256 * 1024**2, 'temperature_c': 50, 'load_per_cpu': .1}
        self.probe.update(boundary={k: v for k, v in self.receipt['execution_boundary'].items() if k not in {'required_units', 'required_containers'}},
                          services_healthy=True, containers_healthy=True)
        self.probe['boundary']['unit_group'] = self.probe['boundary']['cgroup']
        self.calls = []
        self.preflight = LocalDenPreflight('fictional-private-receipt', configuration_digest='b' * 64,
            control_policy=self.policy, archive_directory='fictional-private-directory', clock=lambda: self.now,
            monotonic=lambda: self.now, reader=lambda _: copy.deepcopy(self.receipt),
            probe=lambda directory, inspected, boundary: self.calls.append((directory, inspected)) or copy.deepcopy(self.probe))

    def test_documented_failed_observer_is_preserved_and_current_resources_checked(self):
        self.assertTrue(self.preflight.check())
        self.assertEqual(self.preflight.supervisor({}), {'status': 'ready', 'qualification_current': False})
        self.assertEqual(len(self.calls), 1)

    def test_new_fault_unit_or_resource_failure_refuses(self):
        for key, value in [('new_fault', True), ('failed_units', ['unknown.service']), ('available_mb', 500),
                           ('free_bytes', 0), ('temperature_c', 90), ('load_per_cpu', 1.2), ('boot_id', 'replacement')]:
            with self.subTest(key=key):
                original = self.probe[key]
                self.probe[key] = value
                self.preflight.cached_at = float('-inf')
                self.assertFalse(self.preflight.check())
                self.probe[key] = original

    def test_expiry_pin_and_unqualified_clock_fail_closed(self):
        for mutate in [lambda r: r.update(valid_until=self.now), lambda r: r.update(configuration_digest='c' * 64),
                       lambda r: r['control_clock'].update(uncertainty_s=.5), lambda r: r['limits'].update(max_temperature_c=99)]:
            original = copy.deepcopy(self.receipt)
            mutate(self.receipt)
            self.assertEqual(self.preflight.supervisor({})['status'], 'refused')
            self.receipt = original

    def test_wrong_cgroup_limit_or_unhealthy_required_service_refuses(self):
        self.probe['boundary']['memory_max_bytes'] *= 2
        self.assertFalse(self.preflight.check())
        self.probe['boundary']['memory_max_bytes'] //= 2
        self.probe['containers_healthy'] = False
        self.preflight.cached_at = float('-inf')
        self.assertFalse(self.preflight.check())

    def test_backward_or_nonfinite_monotonic_cannot_reuse_ready(self):
        self.assertTrue(self.preflight.check())
        for now in (self.now - 1, float('nan')):
            self.preflight.monotonic = lambda: now
            self.assertFalse(self.preflight.check())

    def test_clock_mapping_generation_cannot_change_after_admission(self):
        self.assertTrue(self.preflight.check())
        self.receipt['control_clock']['uncertainty_s'] = .002
        with self.assertRaisesRegex(ValueError, 'generation_changed'):
            self.preflight.control_clock()

    def test_control_sample_preserves_exact_original_unix_nanoseconds(self):
        from unittest.mock import patch
        ns = 1800000000123456789
        with patch('live_den_preflight.time.time_ns', return_value=ns):
            value = self.preflight.control_clock()
        self.assertEqual(value['clock']['original_value'], str(ns))
        validate_clock(value['clock'], self.policy, value['observed_at'])

    def test_control_clock_rounds_once_at_real_resolution(self):
        from decimal import Decimal
        from unittest.mock import patch
        ns = 1800000000000000120
        with patch('live_den_preflight.time.time_ns', return_value=ns):
            value = self.preflight.control_clock()
        self.assertEqual(value['observed_at'], float(Decimal(ns)/Decimal(1000000000)))
        self.assertEqual(value['clock']['original_value'], str(ns))
        validate_clock(value['clock'], self.policy, value['observed_at'])
