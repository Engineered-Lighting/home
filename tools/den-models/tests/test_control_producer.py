import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from den_models import control_producer as cp
from den_models.contracts import ContractError, canonical
from den_models.resources import ControlState, FileExecutionLease, file_permissions, parent_permissions, private_json
import test_runtime as fixtures
import test_qwen_queue as queue_fixtures

BOOT = "12345678-1234-1234-1234-123456789abc"


class Clock:
    now = 100.0
    def __call__(self):
        return self.now


def identity(pid):
    return "test-process-start:" + str(pid)


class Fixture:
    def __init__(self, root):
        self.root, self.clock = Path(root), Clock()
        self.queue = queue_fixtures.QueueFixture(root)
        self.config = {"schema": "den.model-control-config/v2", "vllm_port": None,
                       "ops_status_file": str(self.root / "ops.json"), "ops_host": "la-host",
                       "dependencies_file": str(self.root / "source.json"),
                       "control_file": str(self.root / "control.json"),
                       "execution_lease_file": str(self.root / "execution.lease"), "gpu_selector": "0",
                       "shared_gid": None, "trusted_source_uids": None, "ops_owner_uid": None,
                       "queue_receipt_file": self.queue.path, "queue_receipt_gid": 999,
                       "qwen_observer_config_sha256": self.queue.config_sha,
                       "qwen_binding_policy_sha256": self.queue.policy_sha,
                       "poll_seconds": .25,
                       "ops_max_age_seconds": 90}
        self.docs = {self.config["ops_status_file"]: {"schema": "den-ops-status-1", "host": "la-host",
                     "level": 0, "step": "normal", "latched": False, "checked_at": 999,
                     "report": {"evidence": {"ok": True}}}, self.config["dependencies_file"]:
                     {"schema": "den.intent-source/v1", "observed_at": 99.9, "valid_until": 101.4,
                      "dependencies": [fixtures.dependencies()]}}
        self.gpu = Mock(free_bytes=Mock(return_value=50 * 1024**3))
        self.host = Mock(available_bytes=Mock(return_value=40 * 1024**3))
        self.broker = Mock(return_value={"scope": cp.BROKER_SCOPE, "state": "free", "global_enforcement": False,
                                         "unallocated_reserve_bytes": 0})
        self.producer = cp.Producer(self.config, clock=self.clock, wall=lambda: 1000,
                                    read_json=lambda path, **kw: copy.deepcopy(self.docs[path]), gpu=self.gpu,
                                    host=self.host, identity=identity, boot=lambda: BOOT, broker=self.broker)

    def reader(self):
        raw = ControlState(self.config["control_file"], expected_lease_file=self.config["execution_lease_file"],
                           identity=identity, boot=lambda: BOOT, wall=lambda: 1000,
                           queue_receipt_file=self.queue.path, queue_receipt_gid=999,
                           expected_qwen_config_sha256=self.queue.config_sha, expected_qwen_policy_sha256=self.queue.policy_sha)
        fixture = self
        class Reader:
            def read(self, now):
                with fixture.queue.root_io():
                    return raw.read(now)
        return Reader()

    def publish(self):
        with self.queue.root_io():
            state = self.producer.sample()
        cp.write_atomic(self.config["control_file"], state)
        return state


class QueueSourceTests(unittest.TestCase):
    def test_v1_and_loopback_port_configs_cannot_be_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(directory)
            path = Path(directory) / "config.json"
            for change in ({"schema": "den.model-control-config/v1"}, {"vllm_port": 8000}):
                cfg = {**fixture.config, **change}
                cp.write_atomic(path, cfg)
                with self.assertRaises(ContractError):
                    cp.load_config(path)
                with self.assertRaises(ContractError):
                    cp.Producer(cfg)

    def test_unapproved_root_config_digest_blocks_even_well_formed_zero_gauges(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(directory)
            fixture.producer.queue_reader.expected_config_sha256 = "0" * 64
            self.assertTrue(fixture.publish()["admission_blocked"])


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(self.temp.name)

    def test_observed_receipt_round_trips_with_exact_bindings(self):
        state = self.f.publish()
        self.assertFalse(state["admission_blocked"])
        self.assertFalse(state["qwen_busy"])
        self.assertEqual(state["dependencies"], [fixtures.dependencies()])
        self.assertEqual(state["sources"]["gpu"]["free_bytes"], 50 * 1024**3)
        self.assertEqual(state["sources"]["host"]["available_bytes"], 40 * 1024**3)
        self.assertEqual(state["sources"]["den_ops"]["checked_at_utc"], 999)
        self.assertEqual(state["producer"]["clock_domain"], cp.CLOCK_DOMAIN)
        verified = self.f.reader().read(100)
        self.assertIn("current_verified_observation", verified["sources"]["qwen"])
        del verified["sources"]["qwen"]["current_verified_observation"]
        self.assertEqual(verified, state)
        wrong = ControlState(self.f.config["control_file"], expected_lease_file=self.f.root / "other.lease",
                             identity=identity, boot=lambda: BOOT, wall=lambda: 1000)
        with self.assertRaises(ContractError):
            wrong.read(100)

    def test_backoff_three_is_preserved_without_falsifying_qwen_queue(self):
        ops = self.f.docs[self.f.config["ops_status_file"]]
        ops.update(level=3, step="loop_stopped", latched=True)
        state = self.f.publish()
        self.assertTrue(state["admission_blocked"])
        self.assertFalse(state["qwen_busy"])
        self.assertEqual(state["sources"]["den_ops"]["level"], 3)
        self.assertTrue(state["sources"]["den_ops"]["latched"])
        self.assertIn("den_ops_backoff", state["blocked_reasons"])
        with self.assertRaises(ContractError):
            self.f.reader().read(100)
        del self.f.docs[self.f.config["ops_status_file"]]
        state = self.f.publish()
        self.assertTrue(state["admission_blocked"])
        self.assertEqual(state["sources"]["den_ops"]["last_known"]["level"], 3)

    def test_queue_busy_is_an_actual_queue_observation(self):
        self.f.queue.fetch.side_effect = lambda b, d: (queue_fixtures.metrics(2), 100, 100)
        self.f.queue.receipt = self.f.queue.observer.sample()
        state = self.f.publish()
        self.assertTrue(state["qwen_busy"])
        self.assertFalse(state["admission_blocked"])
        self.assertTrue(self.f.reader().read(100)["qwen_busy"])

    def test_new_root_busy_sample_cannot_be_hidden_by_older_control_zero(self):
        state = self.f.publish()
        self.assertFalse(state["qwen_busy"])
        self.f.queue.clock.now = 100.2
        self.f.queue.fetch.side_effect = lambda b, d: (queue_fixtures.metrics(1), 100.2, 100.2)
        self.f.queue.receipt = self.f.queue.observer.sample()
        current = self.f.reader().read(100.3)
        self.assertTrue(current["qwen_busy"])
        self.assertEqual(current["sources"]["qwen"]["current_verified_observation"]["observed_at"], 100.2)

    def test_background_control_publish_never_freshens_root_queue_timestamp(self):
        self.f.publish()
        self.f.clock.now = 100.5
        state = self.f.publish()
        self.assertEqual(state["observed_at"], 100.5)
        self.assertEqual(state["sources"]["qwen"]["observed_at"], 100)
        self.assertEqual(state["sources"]["qwen"]["valid_until"], 101)
        self.f.clock.now = 101
        self.assertTrue(self.f.publish()["admission_blocked"])

    def test_copied_root_looking_json_does_not_replace_the_real_root_file(self):
        self.f.publish()
        self.f.queue.receipt = {"schema": "den.qwen-queue-observation/v1", "status": "unknown"}
        with self.assertRaises(ContractError):
            self.f.reader().read(100)

    def test_blocked_control_never_releases_active_native_lease_before_reap(self):
        runtime_fixture = fixtures.RuntimeTests()
        runtime_fixture.setUp()
        runtime = runtime_fixture.runtime
        runtime.control, runtime.clock = self.f.reader(), self.f.clock
        self.f.publish()
        runtime.submit(fixtures.request())
        runtime.tick()
        self.assertEqual(runtime_fixture.lease.held, "job-1")
        self.f.docs[self.f.config["ops_status_file"]].update(level=3, latched=True, step="loop_stopped")
        self.f.publish()
        runtime.tick()
        self.assertEqual(runtime.get("job-1")["status"], "stopping")
        self.assertEqual(runtime_fixture.lease.held, "job-1")
        self.assertIsNone(runtime.get("job-1")["hint"])
        runtime_fixture.workers[0].alive = False
        runtime.tick()
        self.assertEqual(runtime.get("job-1")["status"], "invalidated")
        self.assertEqual(runtime_fixture.lease.releases, ["process_reaped"])

    def test_unknown_sources_never_become_idle_or_normal(self):
        failures = (lambda: self.f.queue.receipt.update(status="unknown"),
                    lambda: self.f.gpu.free_bytes.configure_mock(side_effect=OSError()),
                    lambda: self.f.host.available_bytes.configure_mock(side_effect=OSError()),
                    lambda: self.f.broker.configure_mock(side_effect=OSError()))
        for fail in failures:
            with self.subTest(fail=fail):
                self.f = Fixture(self.temp.name)
                fail()
                state = self.f.publish()
                self.assertTrue(state["admission_blocked"])
                with self.assertRaises(ContractError):
                    self.f.reader().read(100)
        self.f = Fixture(self.temp.name)
        self.f.producer.queue_reader.path = None
        for key in ("gpu_selector", "ops_status_file", "dependencies_file", "execution_lease_file"):
            self.f.config[key] = None
        state = self.f.publish()
        self.assertTrue(state["admission_blocked"])
        self.assertTrue(state["qwen_busy"])
        self.assertEqual(state["unallocated_reserve_bytes"], 1024**4)

    def test_source_expiry_is_not_refreshed_by_background_sampling(self):
        self.f.publish()
        self.f.clock.now = 101.4
        state = self.f.publish()
        self.assertEqual(state["observed_at"], 101.4)
        self.assertTrue(state["admission_blocked"])
        self.assertEqual(state["dependencies"], [])
        with self.assertRaises(ContractError):
            self.f.reader().read(101.4)

    def test_source_expiring_during_probe_blocks_and_reports_independent_times(self):
        def slow_gpu():
            self.f.clock.now += 2
            return 50 * 1024**3
        self.f.gpu.free_bytes.side_effect = slow_gpu
        state = self.f.publish()
        self.assertEqual(state["sources"]["qwen"]["observed_at"], 100)
        self.assertEqual(state["sources"]["gpu"]["observed_at"], 102)
        self.assertTrue(state["admission_blocked"])
        self.assertIn("qwen_observation_stale", state["blocked_reasons"])

    def test_fresh_empty_source_can_support_warmup_without_inventing_person(self):
        self.f.docs[self.f.config["dependencies_file"]]["dependencies"] = []
        state = self.f.publish()
        self.assertFalse(state["admission_blocked"])
        self.assertEqual(self.f.reader().read(100)["dependencies"], [])

    def test_ops_freshness_evidence_and_real_lineage_are_required(self):
        for mutate in (lambda: self.f.docs[self.f.config["ops_status_file"]].update(checked_at=909),
                       lambda: self.f.docs[self.f.config["ops_status_file"]].update(checked_at=1001),
                       lambda: self.f.docs[self.f.config["ops_status_file"]].pop("report"),
                       lambda: self.f.docs[self.f.config["dependencies_file"]]["dependencies"][0].update(source_generation="d" * 64)):
            with self.subTest(mutate=mutate):
                self.f = Fixture(self.temp.name)
                mutate()
                self.assertTrue(self.f.publish()["admission_blocked"])

    def test_old_manual_json_and_dead_wrong_code_or_other_clock_are_refused(self):
        mutations = (lambda s: s["producer"].update(code_sha256="0" * 64),
                     lambda s: s["producer"].update(process_start_identity="old-process"),
                     lambda s: s["producer"].update(boot_id="other-boot"),
                     lambda s: s["producer"].update(clock_domain="browser-wall-clock"),
                     lambda s: s["sources"]["den_ops"].update(level=3),
                     lambda s: s["sources"]["qwen"].update(running=1),
                     lambda s: s["sources"]["dependencies"].update(valid_until=99))
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                state = self.f.publish()
                mutate(state)
                cp.write_atomic(self.f.config["control_file"], state)
                with self.assertRaises(ContractError):
                    self.f.reader().read(100)
        cp.write_atomic(self.f.config["control_file"], {"schema": "den.model-control/v1", "observed_at": 100,
                        "qwen_busy": False, "unallocated_reserve_bytes": 0, "dependencies": []})
        with self.assertRaises(ContractError):
            self.f.reader().read(100)


class BrokerAndFileTests(unittest.TestCase):
    def test_cross_uid_permission_policy_needs_exact_writer_and_shared_scope(self):
        args = {"current_uid": 1001, "shared_gid": 2000, "trusted_owner_uids": (1002,)}
        self.assertTrue(parent_permissions(0, 2000, 0o2770, allow_shared_parent_write=True, **args))
        self.assertFalse(parent_permissions(0, 2000, 0o2770, **args))  # Not a control/broker directory.
        self.assertFalse(parent_permissions(0, 2001, 0o2770, allow_shared_parent_write=True, **args))
        self.assertFalse(parent_permissions(1002, 2000, 0o2770, allow_shared_parent_write=True, **args))
        self.assertTrue(file_permissions(1002, 2000, 0o640, **args))
        self.assertFalse(file_permissions(1003, 2000, 0o640, **args))  # GID membership alone is insufficient.
        self.assertFalse(file_permissions(1002, 2001, 0o640, **args))
        self.assertFalse(file_permissions(1002, 2000, 0o644, **args))

    def test_public_ops_policy_does_not_widen_dependency_or_control_policy(self):
        args = {"current_uid": 1001, "trusted_owner_uids": (2002,)}
        self.assertTrue(parent_permissions(0, 0, 0o755, protected_parent=True, **args))
        self.assertTrue(file_permissions(2002, 0, 0o644, allow_public_file=True, **args))
        self.assertFalse(file_permissions(2002, 0, 0o644, **args))
        self.assertFalse(file_permissions(2003, 0, 0o644, allow_public_file=True, **args))
        self.assertFalse(file_permissions(2002, 0, 0o666, allow_public_file=True, **args))
        self.assertFalse(parent_permissions(0, 0, 0o777, protected_parent=True, **args))

    def test_config_cannot_overwrite_its_sources_broker_or_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(directory)
            path = Path(directory) / "config.json"
            cp.write_atomic(path, fixture.config)
            self.assertEqual(cp.load_config(path), fixture.config)
            fixture.config["control_file"] = fixture.config["execution_lease_file"]
            cp.write_atomic(path, fixture.config)
            with self.assertRaises(ContractError):
                cp.load_config(path)

    def test_actual_empty_private_scope_and_live_lease_owner_are_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "execution.lease"
            self.assertEqual(cp.broker_observation(path)["state"], "free")
            lease = FileExecutionLease(path)
            token = lease.try_acquire("test-job")
            observation = cp.broker_observation(path)
            self.assertEqual(observation["state"], "held")
            self.assertEqual(observation["unallocated_reserve_bytes"], 0)
            self.assertFalse(observation["global_enforcement"])
            with self.assertRaises(ContractError):
                cp.broker_observation(path, identity=lambda pid: "different-start")
            self.assertTrue(lease.release(token, "no_native_context"))
            with self.assertRaises(ContractError):
                cp.broker_observation(None)

    def test_atomic_private_publication_is_complete_and_does_not_follow_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            cp.write_atomic(path, {"generation": 1})
            cp.write_atomic(path, {"generation": 2})
            self.assertEqual(private_json(path), {"generation": 2})
            self.assertFalse(list(Path(directory).glob("*.tmp")))
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                path.chmod(0o644)
                with self.assertRaises(ContractError):
                    private_json(path)


if __name__ == "__main__":
    unittest.main()
