"""Offline provisioning and lifecycle contracts; no production service starts."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from home_shared.service import VictoriaService, main, validate_host
from home_shared.service_config import load_config


FIXTURES = Path(__file__).parent / "fixtures" / "gateway_client"


def provision(root):
    (root / "credentials").mkdir()
    (root / "trust").mkdir()
    for name in ("camera", "frigate", "gateway", "mqtt-username", "mqtt-password"):
        (root / "credentials" / (name + ".txt")).write_text("fixture-secret\n")
    shutil.copyfile(FIXTURES / "ca.pem", root / "trust" / "ca.pem")
    value = {"version": 1, "expected_hostname": "victoria-den-jetson", "state_directory": "/var/lib/victoria-shared-home",
             "owner_id": "fixture-owner", "observation_source_instance": "victoria-observations",
             "device_id": "victoria-adapter", "configuration_revision": "fixture-config", "max_output_tokens": 128,
             "gateway": {"hostname": "localhost", "port": 8443, "ca_file": "ca.pem", "client_certificate": None, "client_key": None},
             "mqtt": {"hostname": "localhost", "ca_file": "ca.pem", "client_id": "victoria-adapter", "event_capacity": 128},
             "health": {"frigate_hostname": "localhost", "frigate_port": 8971, "ca_file": "ca.pem", "source_instance": "frigate-pi",
                        "detector_id": "hailo", "max_stats_age_seconds": 20, "max_stats_collection_lag_seconds": 2,
                        "policy_revision": "fixture-policy", "camera_host_identity": "jetson", "camera_boot_id": "camera-boot",
                        "pi_host_identity": "pi", "pi_boot_id": "pi-boot", "la_host_identity": "echo", "la_boot_id": "echo-boot"},
             "timing": {"authority_id": "timing-service", "uid": 123, "gid": 456},
             "journal": {"max_records": 100, "free_space_floor_bytes": 1}}
    path = root / "service.json"
    path.write_text(json.dumps(value))
    return path, value


class ServiceConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path, self.value = provision(self.root)

    def test_static_configuration_binds_fixed_registry_and_hides_secrets(self):
        result = load_config(self.path)
        self.assertEqual(dict(result.gateway.cameras), {"den_oak": "den"})
        self.assertEqual(result.binding.site_id, "victoria")
        self.assertEqual(result.runtime.device_id, result.gateway.device_id)
        self.assertEqual(result.health.camera_bearer, "fixture-secret")
        self.assertNotIn("fixture-secret", repr(result))
        self.assertFalse((self.root / "submissions.sqlite").exists())

    def test_unknown_fields_inline_credentials_and_arbitrary_paths_rejected(self):
        for key, value in (("force", True), ("provider_module", "evil"), ("bearer_token", "secret")):
            self.path.write_text(json.dumps(dict(self.value, **{key: value})))
            with self.assertRaises(ValueError): load_config(self.path)
        self.value["gateway"]["ca_file"] = "../external.pem"
        self.path.write_text(json.dumps(self.value))
        with self.assertRaises(ValueError): load_config(self.path)

    def test_config_check_opens_no_network_or_stores_and_claims_no_admission(self):
        output = io.StringIO()
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")), \
                patch("home_shared.service.SubmissionJournal", side_effect=AssertionError("store forbidden")), \
                patch("home_shared.service.VictoriaObservationStore", side_effect=AssertionError("store forbidden")), \
                redirect_stdout(output):
            self.assertEqual(main(["--config", str(self.path), "--check-config"]), 0)
        receipt = json.loads(output.getvalue())
        self.assertTrue(receipt["configuration_valid"])
        self.assertFalse(receipt["deployment_ready"])
        self.assertIn("timing_authority", receipt["requires"])

    def test_missing_or_corrupt_trust_and_credentials_fail_sanitized(self):
        (self.root / "credentials" / "gateway.txt").write_text("secret\r\nInjected: unsafe")
        errors = io.StringIO()
        with redirect_stderr(errors):
            self.assertEqual(main(["--config", str(self.path), "--check-config"]), 2)
        self.assertNotIn("Injected", errors.getvalue())
        self.assertNotIn("unsafe", errors.getvalue())

    def test_wrong_platform_and_quarantined_host_never_start(self):
        config = load_config(self.path)
        with patch("home_shared.service.sys.platform", "win32"):
            with self.assertRaises(ValueError): validate_host(config)
        self.value["expected_hostname"] = "home-app"
        self.path.write_text(json.dumps(self.value))
        with self.assertRaises(ValueError): load_config(self.path)


class ServiceLifecycleTests(unittest.TestCase):
    def test_shutdown_retains_store_and_journal_until_runtime_worker_exits(self):
        service = VictoriaService.__new__(VictoriaService)
        service.health, service.timing = MagicMock(), MagicMock()
        service.runtime, service.store, service.journal = MagicMock(), MagicMock(), MagicMock()
        store, journal = service.store, service.journal
        service.runtime.close.side_effect = [False, True]
        self.assertFalse(service.close())
        store.close.assert_not_called()
        journal.close.assert_not_called()
        self.assertTrue(service.close())
        store.close.assert_called_once()
        journal.close.assert_called_once()

    def test_constructor_composes_actual_stores_and_fixed_authority_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = provision(root)
            state = root / "state"
            state.mkdir()
            config = load_config(path)
            # Bypass only workstation host/provisioning gates and dependency
            # lookup. Real constructors run, but no poll/start/network call.
            versions = {"paho-mqtt": "2.1.0", "Pillow": "12.3.0"}
            with patch("home_shared.service.validate_host", return_value="current-kernel-boot"), \
                    patch("home_shared.service._state_directory", return_value=state), \
                    patch("home_shared.service.importlib.metadata.version", side_effect=versions.__getitem__), \
                    patch("socket.create_connection", side_effect=AssertionError("no network")):
                service = VictoriaService(config)
            try:
                self.assertEqual(service.runtime.mqtt.adapter_session, service.runtime.adapter.session)
                self.assertEqual(service.journal.gateway_identity, service.runtime.gateway.gateway_identity)
                self.assertIs(service.health._provider, service.timing)
                self.assertIs(service.health._stats_provider, service.timing)
                self.assertEqual(service.health.config.camera_boot_id, "current-kernel-boot")
                self.assertEqual(service.health.config.pi_boot_id, config.health.pi_boot_id)
                self.assertEqual(service.health.config.la_boot_id, config.health.la_boot_id)
                self.assertTrue((state / "submissions.sqlite").exists())
                self.assertTrue((state / "observations.sqlite").exists())
                self.assertIsNone(service.health.read())
            finally:
                self.assertTrue(service.close())

    def test_retention_runtime_still_steps_while_timing_evidence_unavailable(self):
        service = VictoriaService.__new__(VictoriaService)
        service._stopped, service._last_step_end = False, None
        service.timing, service.health, service.runtime = MagicMock(), MagicMock(), MagicMock()
        service.health.read.return_value = None
        service.step()
        service.timing.poll.assert_called_once()
        service.health.poll.assert_called_once()
        service.runtime.step.assert_called_once()

    def test_delayed_tick_invalidates_context(self):
        service = VictoriaService.__new__(VictoriaService)
        service._stopped, service._last_step_end = False, 1
        service.timing, service.health, service.runtime = MagicMock(), MagicMock(), MagicMock()
        with patch("home_shared.service.time.monotonic", side_effect=[4, 6]):
            service.step()
        self.assertEqual(service.runtime.adapter.unavailable.call_count, 2)

    def test_actual_boot_identity_is_read_only_after_expected_host_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = provision(Path(directory))
            config = load_config(path)
            def read(path):
                if str(path).endswith("model"):
                    return "NVIDIA Jetson Orin Nano\x00"
                return "actual-kernel-boot\n"
            with patch("home_shared.service.sys.platform", "linux"), \
                    patch("home_shared.service.platform.machine", return_value="aarch64"), \
                    patch("home_shared.service.socket.gethostname", return_value="victoria-den-jetson"), \
                    patch.object(Path, "read_text", read):
                self.assertEqual(validate_host(config), "actual-kernel-boot")
