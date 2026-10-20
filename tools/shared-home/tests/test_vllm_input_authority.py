"""Real bounded HTTP/TLS fixtures; no vLLM installation or GPU is used."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import threading
import time
import unittest
from unittest.mock import patch

from home_shared.admission import ResourceManifest
from home_shared.dispatch_token import DispatchTokenAuthority
from home_shared.legacy_chat import ChatInput
from home_shared.openai_backend import BackendBusy, BackendDeadline, BackendUnknown, SnapshotInput
from home_shared.vllm_input_authority import FixedInputAuthority, MAX_BODY
from test_admission import manifest_fixture


CERTS = Path(__file__).parent / "fixtures" / "gateway_client"


def manifest(port=1, *, tls=False, hostname="127.0.0.1"):
    value = manifest_fixture()
    value["upstream"] = f"{'https' if tls else 'http'}://{hostname}:{port}/v1/chat/completions"
    return ResourceManifest.from_dict(value)


def authority(subject, clock=time.time):
    return DispatchTokenAuthority(subject.digest, "fixture-epoch", b"fixture-secret-not-a-deployed-key!",
                                  max_deadline_seconds=subject.max_deadline_seconds, clock=clock)


def input_value(subject, *, snapshot=False):
    body = json.dumps({"model": subject.model_alias, "messages": [{"role": "user", "content": "fixture"}],
                       "max_tokens": 16}, separators=(",", ":")).encode()
    common = dict(manifest_digest=subject.digest, engine_epoch="fixture-epoch", model_revision=subject.model_revision,
                  upstream_body_sha256=hashlib.sha256(body).hexdigest(), body=body, max_output_tokens=16,
                  ledger_id="fixture-ledger", request_digest="b" * 64)
    return SnapshotInput(**common, image=b"fixture-image", prompt="fixture") if snapshot else ChatInput(**common)


def wire(value, *, status=200, extra=b"", length=None, raw=None):
    body = raw if raw is not None else json.dumps(value, separators=(",", ":")).encode()
    return (f"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\nContent-Length: "
            f"{len(body) if length is None else length}\r\nConnection: close\r\n".encode()
            + extra + b"\r\n" + body)


class Fixture:
    def __init__(self, tls=False):
        self.tls = tls
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(1)
        self.port = self.listener.getsockname()[1]
        self.manifest = manifest(self.port, tls=tls, hostname="localhost" if tls else "127.0.0.1")
        self.authority = authority(self.manifest)
        self.calls, self.errors = [], []
        self.rewrite = lambda value: wire(value)
        self.hold = None
        self.arrived = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def serve(self):
        try:
            connection, _ = self.listener.accept()
            with connection:
                connection.settimeout(1)
                if self.tls:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.load_cert_chain(CERTS / "server.pem", CERTS / "server-test-only.key")
                    connection = context.wrap_socket(connection, server_side=True)
                with connection:
                    stream = connection.makefile("rb")
                    with stream:
                        line = stream.readline(4096)
                        headers = {}
                        while True:
                            field = stream.readline(8192)
                            if field == b"\r\n":
                                break
                            if not field:
                                return
                            name, value = field.decode("ascii").rstrip("\r\n").split(":", 1)
                            if name.lower() in headers:
                                raise AssertionError("duplicate outbound header")
                            headers[name.lower()] = value.strip()
                        size = int(headers["content-length"])
                        if not 1 <= size <= MAX_BODY:
                            raise AssertionError("unbounded request")
                        body = stream.read(size)
                        binding = self.authority.verify(headers["x-shared-home-dispatch"], body)
                        if binding.purpose != "prepare" or binding.input_bound_tokens is not None:
                            raise AssertionError("preflight sent generation authority")
                        if headers["x-request-id"] != binding.ledger_id:
                            raise AssertionError("request ID differs from signed ledger")
                        self.calls.append((line, headers, body, binding))
                        self.arrived.set()
                        if self.hold is not None:
                            self.hold.wait(1)
                        result = {"version": 1, "manifest_digest": binding.manifest_digest,
                                  "engine_epoch": binding.engine_epoch, "model_revision": self.manifest.model_revision,
                                  "upstream_body_sha256": binding.upstream_body_sha256, "upper_bound_tokens": 42,
                                  "valid_until": binding.request_deadline, "ledger_id": binding.ledger_id,
                                  "request_digest": binding.request_digest}
                        output = self.rewrite(result)
                        if callable(output):
                            output(connection)
                        else:
                            connection.sendall(output)
        except (OSError, ssl.SSLError):
            pass  # Expected client shutdown, refused TLS or timeout.
        except BaseException as exc:
            self.errors.append(exc)


@contextmanager
def server(*, tls=False):
    fixture = Fixture(tls)
    fixture.thread.start()
    try:
        yield fixture
    finally:
        if fixture.hold:
            fixture.hold.set()
        fixture.listener.close()
        fixture.thread.join(1.5)
        if fixture.thread.is_alive():
            raise AssertionError("HTTP fixture leaked a worker")
        if fixture.errors:
            raise fixture.errors[0]


class FixedInputAuthorityTests(unittest.TestCase):
    def provider(self, fixture, **options):
        return FixedInputAuthority(fixture.manifest, fixture.authority,
                                   ca_file=CERTS / "ca.pem" if fixture.tls else None, **options)

    def test_actual_snapshot_and_chat_body_are_signed_for_preparation_only(self):
        for snapshot in (False, True):
            with self.subTest(snapshot=snapshot), server() as fixture:
                value = input_value(fixture.manifest, snapshot=snapshot)
                provider = self.provider(fixture, bearer_token="fixture-only-token")
                deadline = time.time() + 2
                with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1"}):
                    result = provider.assess(value, deadline)
                self.assertEqual(result.upper_bound_tokens, 42)
                self.assertEqual(result.valid_until, deadline)
                self.assertEqual(result.upstream_body_sha256, value.upstream_body_sha256)
                self.assertEqual(len(fixture.calls), 1)
                line, headers, body, binding = fixture.calls[0]
                self.assertEqual(line, b"POST /shared-home/input-bound HTTP/1.1\r\n")
                self.assertEqual(body, value.body)
                self.assertEqual(headers["authorization"], "Bearer fixture-only-token")
                self.assertEqual(binding.request_digest, value.request_digest)
                with self.assertRaises(AttributeError):
                    provider.engine_epoch = "other"

    def test_real_tls_requires_ca_and_hostname_validation(self):
        dial = socket.create_connection
        def fixture_dial(address, *args, **kwargs):
            # Keep real TLS hostname/CA checks; only fixture DNS routes locally.
            return dial(("127.0.0.1", address[1]), *args, **kwargs)
        with server(tls=True) as fixture, patch("http.client.socket.create_connection", fixture_dial):
            result = self.provider(fixture).assess(input_value(fixture.manifest), time.time() + 2)
            self.assertEqual(result.upper_bound_tokens, 42)
        with server(tls=True) as fixture, patch("http.client.socket.create_connection", fixture_dial):
            wrong = manifest(fixture.port, tls=True, hostname="wrong-host.fixture")
            provider = FixedInputAuthority(wrong, authority(wrong), ca_file=CERTS / "ca.pem")
            with self.assertRaises(BackendUnknown):
                provider.assess(input_value(wrong), time.time() + 2)
            self.assertEqual(fixture.calls, [])

    def test_response_binding_count_and_original_deadline_are_exact(self):
        changes = [{"manifest_digest": "0" * 64}, {"engine_epoch": "other"}, {"model_revision": "0" * 64},
                   {"upstream_body_sha256": "0" * 64}, {"ledger_id": "other"}, {"request_digest": "0" * 64},
                   {"upper_bound_tokens": 0}, {"upper_bound_tokens": 1001}, {"upper_bound_tokens": True},
                   {"version": True}, {"extra": 1}, {"valid_until": 1}]
        for changeset in changes:
            with self.subTest(changes=changeset), server() as fixture:
                fixture.rewrite = lambda value: wire(dict(value, **changeset))
                with self.assertRaises(BackendUnknown):
                    self.provider(fixture).assess(input_value(fixture.manifest), time.time() + 2)
                self.assertEqual(len(fixture.calls), 1)
        with server() as fixture:
            fixture.rewrite = lambda value: wire(dict(value, valid_until=value["valid_until"] + 1))
            with self.assertRaises(BackendUnknown):
                self.provider(fixture).assess(input_value(fixture.manifest), time.time() + 2)

    def test_duplicate_json_nonfinite_malformed_and_oversized_responses_fail(self):
        variants = [lambda value: wire(value, raw=b'{"version":1,"version":1}'),
                    lambda value: wire(value, raw=b'{"valid_until":NaN}'),
                    lambda value: wire(value, raw=b"not-json"),
                    lambda value: wire(value, length=4097),
                    lambda value: wire(value, length=0),
                    lambda value: wire(value, extra=b"Content-Length: 2\r\n"),
                    lambda value: wire(value, extra=b"Content-Encoding: gzip\r\n"),
                    lambda value: wire(value, extra=b"Transfer-Encoding: chunked\r\n"),
                    lambda value: wire(value, extra=b"X-Thing: a\r\nx-thing: b\r\n")]
        for variant in variants:
            with self.subTest(variant=variant), server() as fixture:
                fixture.rewrite = variant
                with self.assertRaises(BackendUnknown):
                    self.provider(fixture).assess(input_value(fixture.manifest), time.time() + 2)
                self.assertEqual(len(fixture.calls), 1)

    def test_redirect_and_error_status_never_follow_or_retry(self):
        for status in (301, 302, 307, 308, 401, 429, 500):
            with self.subTest(status=status), server() as fixture:
                fixture.rewrite = lambda value: wire(value, status=status, extra=b"Location: http://127.0.0.1:1/\r\n")
                with self.assertRaises(BackendUnknown):
                    self.provider(fixture).assess(input_value(fixture.manifest), time.time() + 2)
                self.assertEqual(len(fixture.calls), 1)

    def test_invalid_binding_and_body_never_reach_network(self):
        subject = manifest()
        provider = FixedInputAuthority(subject, authority(subject))
        value = input_value(subject)
        changes = [{"manifest_digest": "0" * 64}, {"model_revision": "0" * 64}, {"engine_epoch": "other"},
                   {"upstream_body_sha256": "0" * 64}, {"ledger_id": "bad\r\nheader"}, {"ledger_id": None},
                   {"request_digest": None}, {"request_digest": "not-a-digest"}, {"body": None},
                   {"body": bytearray(value.body)}, {"body": b"x" * (MAX_BODY + 1)},
                   {"max_output_tokens": True}, {"max_output_tokens": 129}, {"max_output_tokens": 17}]
        with patch("home_shared.vllm_input_authority.http.client.HTTPConnection") as connect:
            for change in changes:
                with self.subTest(change=list(change)), self.assertRaises(BackendUnknown):
                    provider.assess(replace(value, **change), time.time() + 2)
            for body in (b'{"model":"fake-model","model":"fake-model","max_tokens":16}',
                         b'{"model":"other","max_tokens":16}', b'{"model":"fake-model","max_tokens":NaN}'):
                with self.assertRaises(BackendUnknown):
                    provider.assess(replace(value, body=body, upstream_body_sha256=hashlib.sha256(body).hexdigest()),
                                    time.time() + 2)
            connect.assert_not_called()

    def test_absolute_deadline_cancels_slow_headers_and_keeps_count_unusable(self):
        with server() as fixture:
            fixture.hold = threading.Event()
            started = time.monotonic()
            with self.assertRaises(BackendDeadline):
                self.provider(fixture).assess(input_value(fixture.manifest), time.time() + .06)
            self.assertLess(time.monotonic() - started, .5)
            self.assertTrue(fixture.arrived.is_set())

    def test_absolute_deadline_cancels_drip_body(self):
        with server() as fixture:
            def rewrite(value):
                def drip(connection):
                    packet = wire(value)
                    head, body = packet.split(b"\r\n\r\n", 1)
                    connection.sendall(head + b"\r\n\r\n")
                    for byte in body:
                        connection.sendall(bytes([byte]))
                        time.sleep(.01)
                return drip
            fixture.rewrite = rewrite
            started = time.monotonic()
            with self.assertRaises(BackendDeadline):
                self.provider(fixture).assess(input_value(fixture.manifest), time.time() + .06)
            self.assertLess(time.monotonic() - started, .5)

    def test_stalled_connect_retains_permit_and_sends_nothing_when_late(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = http.client.HTTPConnection.connect
        def held_connect(connection):
            entered.set()
            release.wait(1)
            try:
                original(connection)
            finally:
                finished.set()
        with server() as fixture, patch.object(http.client.HTTPConnection, "connect", held_connect):
            provider = self.provider(fixture)
            with self.assertRaises(BackendDeadline):
                provider.assess(input_value(fixture.manifest), time.time() + .06)
            self.assertTrue(entered.is_set())
            with self.assertRaises(BackendBusy):
                provider.assess(input_value(fixture.manifest), time.time() + 1)
            release.set()
            self.assertTrue(finished.wait(1))
            self.assertTrue(provider._permit.acquire(timeout=1))
            provider._permit.release()
            self.assertEqual(fixture.calls, [])

    def test_wall_clock_rollback_cannot_extend_the_original_deadline(self):
        with server() as fixture:
            wall = [time.time()]
            fixture.authority = authority(fixture.manifest, clock=lambda: wall[0])
            provider = self.provider(fixture, clock=lambda: wall[0])
            deadline = wall[0] + 2
            def rewrite(value):
                wall[0] -= 1
                return wire(value)
            fixture.rewrite = rewrite
            with self.assertRaises(BackendDeadline):
                provider.assess(input_value(fixture.manifest), deadline)

    def test_expired_deadline_and_authority_rebinding_fail_closed(self):
        subject = manifest()
        signer = authority(subject)
        provider = FixedInputAuthority(subject, signer)
        for deadline in (time.time() - 1, time.time() + 100, float("nan"), True, 10**400):
            with self.assertRaises(BackendDeadline):
                provider.assess(input_value(subject), deadline)
        signer._engine_epoch = "replacement"
        with self.assertRaises(BackendUnknown):
            provider.assess(input_value(subject), time.time() + 2)

    def test_bad_origin_trust_and_bearer_are_rejected_at_construction(self):
        subject = manifest()
        for url in ("http://10.1.2.3/v1", "http://127.0.0.1/v1?x=1", "http://u:p@127.0.0.1/v1",
                    "http://127.0.0.1/other", "https://0.0.0.0/v1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                FixedInputAuthority(replace(subject, upstream=url), authority(subject))
        with self.assertRaises(ValueError):
            FixedInputAuthority(subject, authority(subject), bearer_token="secret\r\nX-Other: yes")
        with self.assertRaises(ValueError):
            FixedInputAuthority(replace(subject, upstream="https://localhost/v1"), authority(subject))


if __name__ == "__main__":
    unittest.main()
