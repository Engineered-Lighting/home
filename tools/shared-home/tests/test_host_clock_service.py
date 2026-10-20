"""One-shot publisher tests; Linux cases use protected workstation-local files."""
from dataclasses import asdict, replace
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from home_shared import host_clock_service as service
from home_shared.ntp_clock import CameraHostClockEvidence, NtpClockConfig, NtpKissOfDeath


def config(path='/run/fixture-clock/host-clock.json', uid=123):
    return service.HostClockConfig(socket.gethostname().lower(), uid, path, 64,
        NtpClockConfig('127.0.0.1', 'fixture-reference', 'commissioned_unauthenticated_ntp',
                       .001, 86400, 1000, 500, .5, 2, .5))


class ConfigTests(unittest.TestCase):
    def test_exact_protected_configuration_has_no_accuracy_defaults(self):
        value = dict(version=1, **asdict(config()))
        self.assertEqual(service.parse_config(json.dumps(value).encode()), config())
        for key in value:
            changed = dict(value); del changed[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                service.parse_config(json.dumps(changed).encode())
        changed = json.loads(json.dumps(value)); del changed['ntp']['drift_slew_ppm']
        with self.assertRaises(ValueError): service.parse_config(json.dumps(changed).encode())

    def test_unknown_inline_endpoint_and_writable_path_shapes_fail(self):
        for changes in ({'output_path': '/tmp/../host-clock.json'}, {'output_path': '/run/clock/other.json'},
                        {'expected_hostname': 'home-app'}, {'uid': True},
                        {'minimum_reference_interval_seconds': 0}, {'minimum_reference_interval_seconds': 10**400}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(config(), **changes)

    def test_clockstep_and_suspend_cannot_refresh_receipt(self):
        first = service._Point(1000, 2000, 1000000, 1010)
        service._continuous(first, service._Point(2000, 3000, 1001000, 2010), config().ntp)
        for last in (service._Point(2000, 4000, 1001000, 2010),
                     service._Point(2000, 3000, 2001000, 2010)):
            with self.assertRaises(ValueError): service._continuous(first, last, config().ntp)

    @unittest.skipIf(sys.platform == 'linux', 'non-Linux fail-closed case')
    def test_windows_cannot_publish_or_load_linux_provisioning(self):
        with self.assertRaises(ValueError): service.HostClockPublisher(config())
        with self.assertRaises(ValueError): service.load_config('/run/nonexistent.json')


@unittest.skipUnless(sys.platform == 'linux', 'requires real Linux ownership, dirfd, flock and BOOTTIME')
class PublisherLinuxTests(unittest.TestCase):
    def setUp(self):
        # /tmp has writable ancestors and is deliberately not accepted.
        self.temp = tempfile.TemporaryDirectory(prefix='clock-fixture-', dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / 'host-clock.json'
        self.config = config(str(self.path), os.geteuid())
        self.publisher = service.HostClockPublisher(self.config)
        self.addCleanup(self.publisher.close)

    def sample(self):
        point = service._point()
        evidence = CameraHostClockEvidence(service._boot_id(), 'fixture-only', point.after,
            point.wall + point.after - point.before, point.after + 2_000_000_000, .01)
        return SimpleNamespace(evidence=evidence, server_ip=self.config.ntp.server_ip,
            commissioning_id=self.config.ntp.commissioning_id, server_trust=self.config.ntp.server_trust)

    def publish(self, sample=None):
        with patch.object(service, 'NtpClockSampler') as sampler:
            sampler.return_value.sample.side_effect = self.sample if sample is None else lambda: sample
            result = self.publisher.publish_once()
            self.assertEqual(sampler.return_value.sample.call_count, 1)
            return result

    def test_atomic_protected_receipt_is_accepted_by_actual_camera_reader(self):
        value = self.publish()
        source = Path(__file__).resolve().parents[2] / 'victoria-camera' / 'capture.py'
        spec = importlib.util.spec_from_file_location('clock_fixture_camera', source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        parsed = module.HostClockEvidence(**module.read_protected_json(self.path, os.geteuid()))
        module.check_host_clock(parsed, module.host_point(), service._boot_id())
        self.assertEqual(json.loads(self.path.read_text()), value)
        self.assertEqual(self.path.stat().st_mode & 0o022, 0)
        with self.assertRaises(RuntimeError): self.publisher.publish_once()

    def test_real_localhost_udp_to_protected_camera_receipt(self):
        from test_ntp_clock import reply, udp_fixture
        def respond(request, _peer, _server):
            return reply(request, time.time_ns() - 1_400_000)
        with udp_fixture(respond) as requests:
            value = self.publisher.publish_once()
        self.assertEqual(len(requests), 1)
        self.assertEqual(json.loads(self.path.read_text()), value)
        self.assertEqual(value['host_boot_id'], service._boot_id())
        self.assertLessEqual(value['valid_until_monotonic_ns'] - value['checked_monotonic_ns'], 5_000_000_000)

    def test_process_lifetime_lock_prevents_second_publisher(self):
        self.publish()
        original = self.path.read_bytes()
        with self.assertRaises(BlockingIOError): service.HostClockPublisher(self.config)
        self.assertEqual(self.path.read_bytes(), original)

    def test_second_invocation_cannot_skip_reference_polling_floor(self):
        self.publish()
        self.publisher.close()
        next_publisher = service.HostClockPublisher(self.config)
        self.addCleanup(next_publisher.close)
        with patch.object(service, 'NtpClockSampler') as sampler:
            with self.assertRaises(RuntimeError): next_publisher.publish_once()
            sampler.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_failure_clears_receipt_and_persists_attempt_without_retry(self):
        self.path.write_text('{}')
        with patch.object(service, 'NtpClockSampler') as sampler:
            sampler.return_value.sample.side_effect = OSError('synthetic network failure')
            with self.assertRaises(RuntimeError): self.publisher.publish_once()
            self.assertEqual(sampler.return_value.sample.call_count, 1)
        self.assertFalse(self.path.exists())
        self.assertTrue((self.directory / '.host-clock-reference.json').exists())

    def test_kod_refusal_survives_fresh_process_after_polling_interval(self):
        with patch.object(service, 'NtpClockSampler') as sampler:
            sampler.return_value.sample.side_effect = NtpKissOfDeath('RATE')
            with self.assertRaises(RuntimeError): self.publisher.publish_once()
        state = json.loads((self.directory / '.host-clock-reference.json').read_text())
        self.assertTrue(state['reference_blocked'])
        self.publisher.close()
        following = service.HostClockPublisher(self.config)
        self.addCleanup(following.close)
        point = service._point()
        later = service._Point(point.before + 100_000_000_000, point.boot + 100_000_000_000,
                               point.wall + 100_000_000_000, point.after + 100_000_000_000)
        with patch.object(service, '_point', return_value=later), patch.object(service, 'NtpClockSampler') as sampler:
            with self.assertRaises(RuntimeError): following.publish_once()
            sampler.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_atomic_replace_failure_leaves_no_partial_camera_receipt(self):
        original = os.replace
        def replace_file(source, destination, **kwargs):
            if destination == 'host-clock.json': raise OSError('synthetic rename failure')
            return original(source, destination, **kwargs)
        with patch.object(service.os, 'replace', side_effect=replace_file):
            with self.assertRaises(RuntimeError): self.publish()
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.directory.glob('.clock-*')), [])

    def test_rename_delay_cannot_leave_newly_expired_receipt(self):
        point, rename = service._point, os.replace
        delayed = [False]
        def current():
            value = point()
            if delayed[0]:
                return service._Point(value.before + 3_000_000_000, value.boot + 3_000_000_000,
                                      value.wall + 3_000_000_000, value.after + 3_000_000_000)
            return value
        def replace_file(source, destination, **kwargs):
            rename(source, destination, **kwargs)
            if destination == 'host-clock.json': delayed[0] = True
        with patch.object(service, '_point', side_effect=current), patch.object(service.os, 'replace', side_effect=replace_file):
            with self.assertRaises(RuntimeError): self.publish()
        self.assertFalse(self.path.exists())

    def test_expired_or_wrong_boot_sample_never_published(self):
        sample = self.sample()
        sample.evidence = replace(sample.evidence, host_boot_id='00000000-0000-0000-0000-000000000000')
        with self.assertRaises(RuntimeError): self.publish(sample)
        self.assertFalse(self.path.exists())

    def test_close_and_concurrent_probe_cannot_release_outstanding_owner(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def sample():
            entered.set(); release.wait(2)
            return self.sample()
        def run():
            try: self.publisher.publish_once()
            except Exception as exc: errors.append(exc)
        with patch.object(service, 'NtpClockSampler') as sampler:
            sampler.return_value.sample.side_effect = sample
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                with self.assertRaises(RuntimeError): self.publisher.close()
                with self.assertRaises(RuntimeError): self.publisher.publish_once()
                with self.assertRaises(BlockingIOError): service.HostClockPublisher(self.config)
            finally:
                release.set(); worker.join(2)
        self.assertEqual(errors, [])

    def test_symlink_hardlink_and_writable_artifacts_rejected(self):
        self.publisher.close()
        target = self.directory / 'unrelated'
        target.write_text('unchanged')
        self.path.symlink_to(target)
        with self.assertRaises(ValueError): service.HostClockPublisher(self.config)
        self.assertEqual(target.read_text(), 'unchanged')
        self.path.unlink()
        os.link(target, self.path)
        with self.assertRaises(ValueError): service.HostClockPublisher(self.config)
        self.path.unlink()
        self.path.write_text('{}'); self.path.chmod(0o666)
        with self.assertRaises(ValueError): service.HostClockPublisher(self.config)

    def test_protected_config_exact_uid_and_parent_checks(self):
        path = self.directory / 'service.json'
        path.write_text(json.dumps(dict(version=1, **asdict(self.config))))
        self.assertEqual(service.load_config(path), self.config)
        path.chmod(0o666)
        with self.assertRaises(ValueError): service.load_config(path)
        self.directory.chmod(0o777)
        try:
            with self.assertRaises(ValueError): service.HostClockPublisher(self.config)
        finally:
            self.directory.chmod(0o700)


if __name__ == '__main__':
    unittest.main()
