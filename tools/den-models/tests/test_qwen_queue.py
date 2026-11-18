"""Synthetic Docker/HTTP/receipt protocol tests. Never access a live daemon."""
from contextlib import ExitStack, contextmanager
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from den_models.contracts import ContractError, canonical
from den_models import qwen_queue as qq
from den_models import qwen_observer as qo

BOOT = "12345678-1234-1234-1234-123456789abc"


def identity(pid):
    return BOOT + ":" + str(pid)


class Clock:
    now = 100.0
    def __call__(self):
        return self.now


def policy():
    return {"daemon_id": "qualified-daemon", "container_id": "1" * 64, "image_id": "sha256:" + "2" * 64,
            "compose_project": "home", "compose_service": "vllm", "started_at": "2026-11-17T01:02:03.123456789Z",
            "restart_count": 0, "init_pid": 1234, "init_process_start_identity": identity(1234),
            "network_name": "homeai-net", "network_id": "3" * 64, "endpoint_id": "4" * 64,
            "expected_series": [{"model_name": "qualified-qwen", "engine": "0"}]}


def binding():
    value = {key: data for key, data in policy().items() if key != "expected_series"}
    value.update(ip_address="172.18.0.6", subnet="172.18.0.0/16", network_driver="bridge", network_scope="local", health="healthy")
    return value


def metrics(running=0, waiting=0, engine="0", model="qualified-qwen"):
    return (f'vllm:num_requests_running{{engine="{engine}",model_name="{model}"}} {running}\n'
            f'vllm:num_requests_waiting{{engine="{engine}",model_name="{model}"}} {waiting}\n').encode()


class QueueFixture:
    def __init__(self, root):
        self.clock = Clock()
        self.path = str(Path(root) / "root-queue.json")
        self.config = {"schema": "den.qwen-observer-config/v1", "daemon_endpoint": qo.ENDPOINT,
                       "policy": policy(), "receipt_file": self.path, "receipt_gid": 999,
                       "poll_seconds": .5, "cycle_timeout_seconds": 1.0, "receipt_ttl_seconds": 1.0,
                       "runtime_seconds": 1800}
        self.inspector = Mock(inspect=Mock(side_effect=lambda p, d: copy.deepcopy(binding())))
        self.fetch = Mock(side_effect=lambda b, d: (metrics(), self.clock(), self.clock()))
        self.observer = qo.Observer(self.config, self.inspector, fetch=self.fetch, clock=self.clock,
                                    boot=lambda: BOOT, identity=identity)
        self.receipt = self.observer.sample()

    @property
    def config_sha(self):
        return qq.digest(self.config)

    @property
    def policy_sha(self):
        return qq.digest(self.config["policy"])

    @contextmanager
    def root_io(self):
        # Only OS/file evidence is faked. The production receipt validator and
        # model reader both still execute, including hashes, bindings and times.
        with ExitStack() as stack:
            stack.enter_context(patch.object(qq, "root_owned_json", side_effect=lambda *a, **k: copy.deepcopy(self.receipt)))
            stack.enter_context(patch("den_models.resources.current_boot_id", return_value=BOOT))
            stack.enter_context(patch("den_models.resources.process_start_identity", side_effect=identity))
            stack.enter_context(patch.object(qq, "process_uid", return_value=0))
            yield

    def reader(self):
        return qq.QueueReader(self.path, shared_gid=999, expected_config_sha256=self.config_sha,
                              expected_policy_sha256=self.policy_sha)


class GaugeTests(unittest.TestCase):
    def test_matching_approved_series_are_required_for_both_gauges(self):
        series = policy()["expected_series"] + [{"model_name": "qualified-qwen", "engine": "1"}]
        found = qq.parse_reported_gauges(metrics(2, 1) + metrics(3, 4, engine="1"), series)
        self.assertEqual((found["running"], found["waiting"]), (5, 5))
        self.assertEqual(found["semantics"], qq.SEMANTICS)
        bad = [metrics(engine="other"), metrics(model="another-qwen"), metrics() + metrics(),
               metrics().splitlines()[0] + b"\n", b'{"model":"qwen","tps":0}',
               metrics().replace(b" 0\n", b" NaN\n", 1), metrics().replace(b" 0\n", b" -1\n", 1),
               metrics().replace(b" 0\n", b" 0 1700000000000\n", 1),
               metrics().replace(b" 0\n", b" 0.5\n", 1)]
        bad.append(metrics().replace(b" 0\n", b" 1e-400\n", 1))
        for raw in bad:
            with self.subTest(raw=raw):
                with self.assertRaises(ContractError):
                    qq.parse_reported_gauges(raw, policy()["expected_series"])
        mismatched = metrics().splitlines()[0] + b"\n" + metrics(engine="1").splitlines()[1] + b"\n"
        with self.assertRaises(ContractError):
            qq.parse_reported_gauges(mismatched, series)

    def test_reordered_duplicate_labels_and_ambiguous_labels_are_refused(self):
        duplicates = metrics() + b'vllm:num_requests_running{model_name="qualified-qwen",engine="0"} 0\n'
        with self.assertRaises(ContractError):
            qq.parse_reported_gauges(duplicates, policy()["expected_series"])
        for raw in (metrics().replace(b'engine="0"', b'engine="0",engine="0"'),
                    metrics().replace(b'engine="0"', b'engine="0",unapproved="value"'),
                    b"vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n"):
            with self.assertRaises(ContractError):
                qq.parse_reported_gauges(raw, policy()["expected_series"])


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = QueueFixture(self.temp.name)

    def test_equal_inspection_fence_yields_original_times_and_small_metadata_only_receipt(self):
        doc = self.f.receipt
        self.assertEqual(doc["status"], "observed")
        self.assertEqual(self.f.inspector.inspect.call_count, 2)
        self.assertEqual(doc["observed_at"], 100)
        self.assertEqual(doc["valid_until"], 101)
        self.assertNotIn("raw_metrics", doc)
        self.assertLess(len(canonical(doc).encode()), qq.MAX_RECEIPT_BYTES)
        with self.f.root_io():
            self.assertEqual(self.f.reader().read(100.4), doc)
        source = qq.source_from_receipt(doc)
        self.assertEqual(source["observed_at"], 100)
        self.assertFalse(source["busy"])

    def test_restart_lost_binding_ip_reuse_and_new_endpoint_all_publish_unknown(self):
        changes = ({"container_id": "5" * 64}, {"init_pid": 1235}, {"restart_count": 1},
                   {"started_at": "2026-11-17T01:02:04Z"}, {"endpoint_id": "6" * 64},
                   {"daemon_id": "other-daemon"}, {"ip_address": "172.18.0.8"})
        for change in changes:
            with self.subTest(change=change):
                second = {**binding(), **change}
                self.f.inspector.inspect.side_effect = [binding(), second]
                value = self.f.observer.sample()
                self.assertEqual(value["status"], "unknown")
                self.assertNotIn("gauges", value)
        self.f.inspector.inspect.side_effect = OSError("lost daemon")
        self.assertEqual(self.f.observer.sample()["status"], "unknown")

    def test_private_ip_is_dynamic_but_must_belong_to_the_approved_bridge(self):
        for address in ("172.18.0.8", "172.18.0.99"):
            new = {**binding(), "ip_address": address}
            self.assertEqual(qq.validate_binding(new, policy())["ip_address"], address)
        for address in ("127.0.0.1", "8.8.8.8", "172.19.0.6", "172.18.255.255"):
            with self.assertRaises(ContractError):
                qq.validate_binding({**binding(), "ip_address": address}, policy())

    def test_slow_or_expired_cycle_never_publishes_a_reusable_healthy_sample(self):
        def late_fetch(b, d):
            self.f.clock.now += 2
            return metrics(), 100, 102
        self.f.fetch.side_effect = late_fetch
        self.assertEqual(self.f.observer.sample()["status"], "unknown")
        with self.f.root_io():
            with self.assertRaises(ContractError):
                self.f.reader().read(101)

    def test_oldest_get_time_remains_the_freshness_fence_at_consumption(self):
        doc = copy.deepcopy(self.f.receipt)
        doc.update(request_started_at=99, observed_at=100, postcheck_at=100, published_at=100, valid_until=101.5)
        self.f.receipt = doc
        with self.f.root_io():
            self.assertEqual(self.f.reader().read(100.4)["observed_at"], 100)
            with self.assertRaises(ContractError):
                self.f.reader().read(101.4)

    def test_running_observer_never_attests_a_later_on_disk_code_version(self):
        original = self.f.observer.code_sha256
        with patch.object(qo, "observer_code_digest", return_value="f" * 64), patch.object(Path, "read_bytes", side_effect=AssertionError("per-sample disk hash")):
            observed = self.f.observer.sample()
        self.assertEqual(observed["observer"]["code_sha256"], original)
        with self.assertRaises(ContractError):
            qq.validate_receipt(observed, now=100, boot_id=BOOT, expected_config_sha256=self.f.config_sha,
                                expected_policy_sha256=self.f.policy_sha, observer_identity=identity,
                                observer_uid=lambda pid: 0, expected_code_sha256="f" * 64)

    def test_attestation_binding_policy_and_process_failures_are_not_accepted(self):
        for mutate in (lambda d: d["observer"].update(config_sha256="0" * 64),
                       lambda d: d["observer"].update(code_sha256="0" * 64),
                       lambda d: d["observer"].update(process_start_identity="old-process"),
                       lambda d: d.update(host_boot_id="another-host-boot"),
                       lambda d: d.update(policy_sha256="0" * 64),
                       lambda d: d.update(binding_sha256="0" * 64),
                       lambda d: d.update(valid_until=102)):
            with self.subTest(mutate=mutate):
                original = self.f.receipt
                self.f.receipt = copy.deepcopy(original)
                mutate(self.f.receipt)
                with self.f.root_io():
                    with self.assertRaises(ContractError):
                        self.f.reader().read(100)
                self.f.receipt = original
        with self.f.root_io(), patch.object(qq, "process_uid", return_value=1000):
            with self.assertRaises(ContractError):
                self.f.reader().read(100)

    def test_check_default_does_not_probe_or_require_root(self):
        path = Path(self.temp.name) / "config.json"
        path.write_text(canonical(self.f.config))
        with patch.object(qo, "DockerInspector", side_effect=AssertionError("probe")), patch("sys.stdout", io.StringIO()) as output:
            self.assertEqual(qo.main(["--config", str(path)]), 0)
        self.assertFalse(json.loads(output.getvalue())["probes_performed"])
        template = {**self.f.config, "policy": None}
        path.write_text(canonical(template))
        with patch.object(qo, "DockerInspector", side_effect=AssertionError("probe")), patch("sys.stdout", io.StringIO()):
            self.assertEqual(qo.main(["--config", str(path)]), 2)

    def test_unprivileged_or_unbounded_actor_cannot_reach_docker(self):
        with patch.object(qo, "require_bounded_root", side_effect=ContractError("no root unit")), patch.object(qo.subprocess, "run") as run:
            with self.assertRaises(ContractError):
                qo.DockerInspector(self.f.config)
            run.assert_not_called()

    def test_plain_or_model_owned_file_is_not_a_root_authority_receipt(self):
        path = Path(self.temp.name) / "queue.json"
        path.write_text(canonical(self.f.receipt))
        with self.assertRaises(ContractError):
            qq.root_owned_json(path, shared_gid=None)


class DirectHttpTests(unittest.TestCase):
    def connection(self, *, status=200, content_type="text/plain", raw=None, age=None):
        raw = metrics() if raw is None else raw
        response = Mock(status=status, read=Mock(return_value=raw))
        response.getheader.side_effect = lambda name, default=None: {"Content-Type": content_type,
            "Content-Length": str(len(raw)), "Age": age}.get(name, default)
        connection = Mock(sock=None, getresponse=Mock(return_value=response))
        return connection

    def test_only_direct_private_origin_get_is_used_no_proxy_or_redirect(self):
        clock = Clock()
        connection = self.connection()
        factory = Mock(return_value=connection)
        raw, start, received = qo.direct_metrics(binding(), 101, clock=clock, connect=factory)
        factory.assert_called_once_with("172.18.0.6", 8000, timeout=.5)
        self.assertEqual(connection.request.call_args.args, ("GET", "/metrics"))
        self.assertEqual(raw, metrics())
        self.assertEqual((start, received), (100, 100))
        for kwargs in ({"status": 302}, {"content_type": "application/json"}, {"age": "1"}):
            with self.assertRaises(ContractError):
                qo.direct_metrics(binding(), 101, clock=clock, connect=Mock(return_value=self.connection(**kwargs)))

    def test_deadline_shutdown_retains_socket_after_connection_close_response_detaches_it(self):
        clock, active_socket, timers = Clock(), Mock(), []
        connection = self.connection()
        connection.sock = active_socket
        response = connection.getresponse.return_value
        def detach():
            connection.sock = None  # HTTPConnection.getresponse will_close path.
            return response
        connection.getresponse.side_effect = detach
        class Timer:
            def __init__(self, duration, callback):
                self.callback = callback
                timers.append(self)
            def start(self):
                pass
            def cancel(self):
                pass
        def slow_read(limit):
            clock.now = 101
            timers[0].callback()
            return metrics()
        response.read.side_effect = slow_read
        with patch.object(qo.threading, "Timer", Timer):
            with self.assertRaises(ContractError):
                qo.direct_metrics(binding(), 101, clock=clock, connect=Mock(return_value=connection))
        active_socket.shutdown.assert_called_once_with(qo.socket.SHUT_RDWR)


if __name__ == "__main__":
    unittest.main()
