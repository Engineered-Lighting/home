"""Deterministic local IPC contracts; Linux peer checks are tested explicitly."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from home_shared.timing_client import MAX_MESSAGE, TimingClient, TimingClientConfig
from home_shared.health import HealthProbe
from test_health import ClockProvider, SyntheticTimingAuthority, config


class FakeSocket:
    def __init__(self, mutation=None):
        self.response = b""
        self.mutation = mutation
        self.sent = []
    def settimeout(self, timeout): pass
    def sendall(self, message):
        request = json.loads(message[4:])
        self.sent.append(request)
        evidence = (ClockProvider(lambda: 100, lambda: 1000).read() if request["kind"] == "clock"
                    else SyntheticTimingAuthority(lambda: 100).read_for(request["snapshot_digest"]))
        value = dict(request, evidence=asdict(evidence))
        if self.mutation:
            self.mutation(value)
        body = json.dumps(value).encode()
        self.response = struct.pack("!I", len(body)) + body
    def recv(self, count):
        chunk, self.response = self.response[:count], self.response[count:]
        return chunk
    def close(self): pass
    def shutdown(self, how): pass


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.client = TimingClient(TimingClientConfig("timing-authority", 123, 456), monotonic=lambda: self.now)
        self.health = HealthProbe(config(), clock_provider=self.client, stats_timing_provider=self.client,
                                  monotonic=lambda: self.now, clock=lambda: self.now + 900)
        self.addCleanup(self.client.stop)
        self.addCleanup(self.health.stop)
        self.sockets = []
        def dial(deadline):
            stream = FakeSocket()
            self.sockets.append(stream)
            return stream
        self.client._dial = dial

    def collect(self):
        self.assertTrue(self.client.poll())
        self.client._thread.join(1)
        self.assertFalse(self.client._thread.is_alive())

    def test_cache_read_performs_no_io_and_only_latest_digest_is_queued(self):
        self.assertIsNone(self.client.read())
        self.assertIsNone(self.client.read_for("a" * 64))
        self.assertIsNone(self.client.read_for("b" * 64))
        self.assertEqual(self.sockets, [])
        self.collect()
        self.assertEqual(len(self.sockets), 2)
        self.assertEqual(self.client.read().camera_boot_id, "boot-1")
        self.assertIsNone(self.client.read_for("a" * 64))
        self.assertEqual(self.client.read_for("b" * 64).snapshot_digest, "b" * 64)
        self.assertEqual(self.sockets[1].sent[0]["snapshot_digest"], "b" * 64)

    def test_receipt_validator_cannot_be_replaced(self):
        with self.assertRaises(RuntimeError):
            self.client.bind_receipt_validator(lambda *_: True)
        self.collect()
        with self.assertRaises(RuntimeError):
            self.client.bind_receipt_validator(lambda *_: True)

    def test_poll_without_health_policy_fails_and_late_install_is_rejected(self):
        client = TimingClient(TimingClientConfig("timing-authority", 123, 456))
        self.addCleanup(client.stop)
        with self.assertRaises(RuntimeError):
            client.poll()
        self.assertIsNone(client._thread)
        with self.assertRaises(RuntimeError):
            client.bind_receipt_validator(lambda *_: True)

    def test_wrong_response_authority_request_or_snapshot_fails_closed(self):
        for key, value in (("authority_id", "other"), ("request_id", "wrong"), ("kind", "unknown"), ("version", True)):
            self.client._dial = lambda deadline: FakeSocket(lambda body: body.update({key: value}))
            self.collect()
            self.assertIsNone(self.client.read())
            self.now += 1
        self.client.read_for("a" * 64)
        def corrupt(body):
            if body["kind"] == "stats":
                body["evidence"]["snapshot_digest"] = "b" * 64
        self.client._dial = lambda deadline: FakeSocket(corrupt)
        self.collect()
        self.assertIsNone(self.client.read())

    def test_bounded_framing_and_exact_evidence_fields(self):
        class Oversized(FakeSocket):
            def sendall(self, data): self.response = struct.pack("!I", MAX_MESSAGE + 1)
        self.client._dial = lambda deadline: Oversized()
        self.collect()
        self.assertIsNone(self.client.read())

    def test_version2_clock_uses_exact_host_only_schema(self):
        def upgrade(body):
            evidence = body["evidence"]
            evidence["version"] = 2
            evidence["camera_host_uncertainty_seconds"] = evidence.pop("camera_source_uncertainty_seconds")
        self.client._dial = lambda deadline: FakeSocket(upgrade)
        self.collect()
        self.assertEqual(self.client.read().version, 2)
        self.assertEqual(self.client.read().camera_host_uncertainty_seconds, .1)
        self.assertFalse(hasattr(self.client.read(), "camera_source_uncertainty_seconds"))
        self.now += 1
        def mixed(body):
            upgrade(body)
            body["evidence"]["camera_source_uncertainty_seconds"] = .1
        self.client._dial = lambda deadline: FakeSocket(mixed)
        self.collect()
        self.assertIsNone(self.client.read())
        self.now += 1
        self.client._dial = lambda deadline: FakeSocket(lambda body: body["evidence"].update(unreviewed=True))
        self.collect()
        self.assertIsNone(self.client.read())

    def test_blocked_worker_keeps_permit_and_rejects_late_result(self):
        entered, release = threading.Event(), threading.Event()
        def dial(deadline):
            entered.set()
            release.wait(2)
            return FakeSocket()
        self.client._dial = dial
        self.client.poll()
        try:
            self.assertTrue(entered.wait(1))
            self.now += 3
            self.assertFalse(self.client.poll())
            self.assertIsNone(self.client.read())
            self.assertEqual(self.client.last_error, "timing_authority_deadline")
        finally:
            release.set()
            self.client._thread.join(1)
        self.assertIsNone(self.client.read())

    def test_missing_server_and_stop_do_not_fabricate_evidence(self):
        self.client._dial = lambda deadline: (_ for _ in ()).throw(FileNotFoundError())
        self.collect()
        self.assertIsNone(self.client.read())
        self.client.stop()
        self.assertFalse(self.client.poll())
        self.assertIsNone(self.client.read_for("a" * 64))

    def test_windows_actual_ipc_is_unavailable(self):
        with patch("home_shared.timing_client.sys.platform", "win32"):
            with self.assertRaises(RuntimeError): self.client._socket_identity()

    def test_linux_directory_and_socket_permissions(self):
        class Info:
            def __init__(self, mode, uid, gid):
                self.st_mode, self.st_uid, self.st_gid = mode, uid, gid
                self.st_dev, self.st_ino = 1, 2
        def lstat(path):
            return Info(stat.S_IFSOCK | 0o660, 123, 456) if path.name == "authority.sock" else Info(stat.S_IFDIR | 0o755, 0, 0)
        with patch("home_shared.timing_client.sys.platform", "linux"), \
                patch.object(socket, "SO_PEERCRED", 17, create=True), patch.object(Path, "lstat", lstat):
            self.assertEqual(self.client._socket_identity(), (1, 2))
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFDIR | 0o777):
            with patch("home_shared.timing_client.sys.platform", "linux"), \
                    patch.object(socket, "SO_PEERCRED", 17, create=True), \
                    patch.object(Path, "lstat", lambda path: Info(mode, 0, 0)):
                with self.assertRaises(ValueError): self.client._socket_identity()

    def test_kernel_peer_credentials_must_match_uid_and_gid(self):
        class PeerSocket(FakeSocket):
            def connect(self, path): pass
            def getsockopt(self, *args): return struct.pack("3i", 900, 123, 999)
        real = TimingClient(TimingClientConfig("timing-authority", 123, 456), monotonic=lambda: 100)
        self.addCleanup(real.stop)
        with patch.object(real, "_socket_identity", return_value=(1, 2)), \
                patch.object(socket, "SO_PEERCRED", 17, create=True), \
                patch.object(socket, "AF_UNIX", 1, create=True), \
                patch("home_shared.timing_client.socket.socket", return_value=PeerSocket()):
            with self.assertRaises(ValueError): real._dial(102)

    @unittest.skipUnless(sys.platform == "linux", "actual AF_UNIX peer acceptance requires Linux")
    def test_actual_linux_unix_socket_and_kernel_peer_credentials(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            endpoint = Path(directory) / "authority.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(endpoint))
            endpoint.chmod(0o660)
            server.listen(1)
            server.settimeout(2)
            def serve():
                with server:
                    connection, _ = server.accept()
                    with connection:
                        def receive(count):
                            data = b""
                            while len(data) < count:
                                data += connection.recv(count - len(data))
                            return data
                        request = json.loads(receive(struct.unpack("!I", receive(4))[0]))
                        body = json.dumps(dict(request, evidence=asdict(ClockProvider(lambda: 100, lambda: 1000).read()))).encode()
                        connection.sendall(struct.pack("!I", len(body)) + body)
            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            client = TimingClient(TimingClientConfig("timing-authority", os.getuid(), os.getgid()), monotonic=lambda: 100)
            health = HealthProbe(config(), clock_provider=client, stats_timing_provider=client,
                                 monotonic=lambda: 100, clock=lambda: 1000)
            try:
                with patch("home_shared.timing_client.SOCKET_PATH", endpoint):
                    client.poll()
                    client._thread.join(2)
                    self.assertIsNotNone(client.read())
            finally:
                health.stop()
                client.stop()
                server.close()
                worker.join(2)
