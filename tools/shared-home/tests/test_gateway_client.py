"""Ephemeral localhost TLS contracts; no production endpoints or model work."""
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from home_shared.gateway_client import (GatewayBusy, GatewayClient as ProductionGatewayClient, GatewayClientConfig,
                                        GatewayDeadline, GatewayNotSent, GatewayTransportError, MAX_HEADER_BYTES)
from home_shared.admission import canonical_digest
from test_gateway import request_fixture


FIXTURES = Path(__file__).parent / "fixtures" / "gateway_client"


def GatewayClient(config, **kwargs):
    # Windows localhost name resolution can exceed this protocol's deadline.
    # Pin only the fixture dial address; TLS still verifies the provisioned
    # hostname and trust chain using real sockets and the production SSL context.
    def connect(address, timeout):
        if address != (config.hostname, config.port):
            raise AssertionError("client changed the provisioned endpoint")
        return socket.create_connection(("127.0.0.1", address[1]), timeout=timeout)
    kwargs.setdefault("_connect", connect)
    return ProductionGatewayClient(config, **kwargs)


def result(**changes):
    value = {"version": 1, "request_id": "request-1", "site_id": "victoria",
             "status": "queued", "outcome": None}
    value.update(changes)
    return value


def reply(value=None, *, body=None, status="200 OK", extra="", content_type="application/json"):
    body = json.dumps(result() if value is None else value).encode() if body is None else body
    return (f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n{extra}\r\n").encode() + body


class LocalTLS:
    def __init__(self, response, *, mutual=False, after_request=None):
        self.response, self.after_request = response, after_request
        self.requests = []
        self.peer = None
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(2)
        self.port = self.listener.getsockname()[1]
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(FIXTURES / "server.pem"), str(FIXTURES / "server-test-only.key"))
        if mutual:
            self.context.verify_mode = ssl.CERT_REQUIRED
            self.context.load_verify_locations(str(FIXTURES / "ca.pem"))
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.listener.close()
        self.thread.join(3)
        if self.thread.is_alive():
            raise AssertionError("local TLS server did not exit")

    def run(self):
        try:
            raw, _ = self.listener.accept()
            raw.settimeout(2)
            with raw, self.context.wrap_socket(raw, server_side=True) as connection:
                self.peer = connection.getpeercert()
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                head, body = data.split(b"\r\n\r\n", 1)
                headers = dict(line.split(": ", 1) for line in head.decode().split("\r\n")[1:])
                length = int(headers["Content-Length"])
                while len(body) < length:
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    body += chunk
                self.requests.append((head.decode().split("\r\n")[0], headers, body))
                if self.after_request:
                    self.after_request()
                if callable(self.response):
                    self.response(connection)
                else:
                    connection.sendall(self.response)
        except (OSError, ssl.SSLError):
            # Cancellation, wrong trust and absent client certificate are
            # intentional fixtures; request receipts establish any acceptance.
            pass


def config(port=443, **overrides):
    values = dict(hostname="localhost", port=port, ca_file=FIXTURES / "ca.pem",
                  bearer_token="test-device-secret", device_id="den-adapter",
                  cameras={"den_oak": "den"}, configuration_revision="rev-1")
    values.update(overrides)
    return GatewayClientConfig(**values)


class GatewayClientTests(unittest.TestCase):
    def test_interactive_requires_explicit_client_provisioning(self):
        payload = request_fixture(time.time())
        payload['task_class'] = 'interactive'
        denied = GatewayClient(config())
        with self.assertRaises(GatewayNotSent):
            denied.submit(payload)
        with LocalTLS(reply()) as server:
            enabled = GatewayClient(config(server.port, allow_interactive=True))
            self.assertEqual(enabled.submit(payload), result())
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(json.loads(server.requests[0][2])['task_class'], 'interactive')

    def test_tls_post_fixed_route_bearer_and_exact_payload(self):
        payload = request_fixture(time.time())
        with LocalTLS(reply()) as server:
            client = GatewayClient(config(server.port))
            self.assertEqual(client.submit(payload), result())
        line, headers, body = server.requests[0]
        self.assertEqual(line, "POST /v1/inference HTTP/1.1")
        self.assertEqual(headers["Authorization"], "Bearer test-device-secret")
        self.assertEqual(headers["Host"], f"localhost:{server.port}")
        self.assertEqual(json.loads(body), payload)
        self.assertEqual(len(server.requests), 1)

    def test_ambient_proxy_and_tls_key_logging_cannot_change_private_transport(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://wrong.invalid:8080", "SSLKEYLOGFILE": "must-not-exist.keys"}):
            with LocalTLS(reply()) as server:
                client = GatewayClient(config(server.port))
                self.assertIsNone(client._tls.keylog_filename)
                self.assertEqual(client._tls.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(client._tls.check_hostname)
                self.assertFalse(client._tls.hostname_checks_common_name)
                self.assertEqual(client.status("request-1"), result())
        self.assertEqual(len(server.requests), 1)

    def test_payload_mutation_after_validation_cannot_change_transmitted_bytes(self):
        payload = request_fixture(time.time())
        original = dict(payload)
        with LocalTLS(reply()) as server:
            def connect(address, timeout):
                payload["site_id"] = "echo"
                payload["request_id"] = "other"
                return socket.create_connection(("127.0.0.1", address[1]), timeout=timeout)
            client = GatewayClient(config(server.port), _connect=connect)
            self.assertEqual(client.submit(payload), result())
        self.assertEqual(json.loads(server.requests[0][2]), original)

    def test_mutual_tls_when_provisioned(self):
        with LocalTLS(reply(), mutual=True) as server:
            client = GatewayClient(config(server.port, client_certificate=FIXTURES / "client.pem",
                                           client_key=FIXTURES / "client-test-only.key"))
            self.assertEqual(client.status("request-1"), result())
        self.assertTrue(server.peer)
        self.assertEqual(server.requests[0][0], "GET /v1/inference/request-1 HTTP/1.1")
        self.assertEqual(server.requests[0][2], b"")

    def test_server_requires_client_certificate_without_fallback(self):
        with LocalTLS(reply(), mutual=True) as server:
            with self.assertRaises(GatewayTransportError):
                GatewayClient(config(server.port)).status("request-1")
        self.assertFalse(server.requests)

    def test_hostname_verification_and_untrusted_ca_reject_before_bearer(self):
        for change in ({"hostname": "127.0.0.1"}, {"ca_file": FIXTURES / "client.pem"}):
            with self.subTest(change=change), LocalTLS(reply()) as server:
                with self.assertRaises(GatewayTransportError):
                    GatewayClient(config(server.port, **change)).status("request-1")
            self.assertFalse(server.requests)

    def test_config_rejects_url_header_injection_and_mutable_registry(self):
        for change in ({"hostname": "https://localhost/path"}, {"hostname": "localhost\r\nInjected"},
                       {"port": True}, {"bearer_token": "secret\nleak"}, {"ca_file": ""},
                       {"client_certificate": "cert"}, {"client_certificate": 1, "client_key": 1},
                       {"cameras": []}, {"cameras": {"../camera": "den"}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                config(**change)
        original = {"den_oak": "den"}
        trusted = config(cameras=original)
        original["den_oak"] = "other"
        self.assertEqual(trusted.cameras["den_oak"], "den")
        self.assertNotIn("test-device-secret", repr(trusted))

    def test_gateway_identity_binds_endpoint_trust_and_device_but_not_secrets(self):
        original = GatewayClient(config()).gateway_identity
        self.assertRegex(original, r"^[a-f0-9]{64}$")
        self.assertEqual(GatewayClient(config(bearer_token="rotated-secret")).gateway_identity, original)
        self.assertEqual(GatewayClient(config(client_certificate=FIXTURES / "client.pem",
                                              client_key=FIXTURES / "client-test-only.key")).gateway_identity, original)
        for change in ({"hostname": "other.private"}, {"port": 8443}, {"device_id": "different"},
                       {"cameras": {"den_oak": "different-room"}}, {"configuration_revision": "rev-2"}):
            with self.subTest(change=change):
                self.assertNotEqual(GatewayClient(config(**change)).gateway_identity, original)
        with tempfile.TemporaryDirectory() as directory:
            ca = Path(directory) / "ca.pem"
            ca.write_bytes((FIXTURES / "ca.pem").read_bytes())
            existing = GatewayClient(config(ca_file=ca))
            self.assertEqual(existing.gateway_identity, original)
            ca.write_bytes(ca.read_bytes() + b"\n")
            self.assertNotEqual(GatewayClient(config(ca_file=ca)).gateway_identity, original)
            self.assertEqual(existing.gateway_identity, original)

    def test_gateway_identity_camera_order_is_stable(self):
        first = GatewayClient(config(cameras={"one": "den", "two": "kitchen"}))
        second = GatewayClient(config(cameras={"two": "kitchen", "one": "den"}))
        self.assertEqual(first.gateway_identity, second.gateway_identity)

    def test_local_invalid_payload_and_serialization_failures_are_not_sent(self):
        attempts = []
        client = GatewayClient(config(), _connect=lambda *a, **kw: attempts.append(a))
        for payload in (request_fixture(time.time() - 16), {"unsupported": object()}, {"site_id": "echo"}):
            with self.subTest(payload=payload), self.assertRaises(GatewayNotSent):
                client.submit(payload)
        self.assertFalse(attempts)

    def test_post_busy_and_initial_deadline_are_not_sent_status_remains_ambiguous(self):
        attempts = []
        client = GatewayClient(config(), _connect=lambda *a, **kw: attempts.append(a))
        client._inflight.acquire()
        try:
            with self.assertRaises(GatewayNotSent):
                client.submit(request_fixture(time.time()))
            with self.assertRaises(GatewayBusy) as caught:
                client.status("request-1")
            self.assertNotIsInstance(caught.exception, GatewayNotSent)
        finally:
            client._inflight.release()
        moments = iter((100., 116.))
        expired = GatewayClient(config(), monotonic=lambda: next(moments), clock=lambda: 1000,
                                _connect=lambda *a, **kw: attempts.append(a))
        with self.assertRaises(GatewayNotSent):
            expired.submit(request_fixture(1000))
        self.assertFalse(attempts)

    def test_worker_launch_failure_is_not_sent_only_for_post(self):
        attempts = []
        client = GatewayClient(config(), _connect=lambda *a, **kw: attempts.append(a))
        with patch("home_shared.gateway_client.threading.Thread.start", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(GatewayNotSent):
                client.submit(request_fixture(time.time()))
            with self.assertRaises(GatewayTransportError) as caught:
                client.status("request-1")
            self.assertNotIsInstance(caught.exception, GatewayNotSent)
        self.assertFalse(client._inflight.locked())
        self.assertFalse(attempts)

    def test_worker_failure_after_launch_is_ambiguous_even_when_dial_fails(self):
        def fail(*args, **kwargs):
            raise OSError("failed dial")
        with self.assertRaises(GatewayTransportError) as caught:
            GatewayClient(config(), _connect=fail).submit(request_fixture(time.time()))
        self.assertNotIsInstance(caught.exception, GatewayNotSent)

    def test_cross_site_device_camera_config_and_interactive_requests_do_not_connect(self):
        attempts = []
        client = GatewayClient(config(), _connect=lambda *a, **kw: attempts.append(a))
        for key, value in (("site_id", "echo"), ("device_id", "other"), ("camera_id", "other"),
                           ("room_id", "other"), ("configuration_revision", "other"), ("task_class", "interactive"),
                           ("request_id", "../escape"), ("request_deadline", float("inf"))):
            payload = request_fixture(time.time())
            payload[key] = value
            with self.subTest(key=key), self.assertRaises(GatewayTransportError):
                client.submit(payload)
        self.assertFalse(attempts)

    def test_status_path_cannot_be_selected_by_caller(self):
        attempts = []
        client = GatewayClient(config(), _connect=lambda *a, **kw: attempts.append(a))
        for identity in ("../request", "https://other/", "r?query", "r#fragment", "r\r\nHost: other", ""):
            with self.subTest(identity=identity), self.assertRaises(GatewayTransportError):
                client.status(identity)
        self.assertFalse(attempts)

    def test_redirect_error_and_404_are_ambiguous_and_never_retried(self):
        for status in ("302 Found", "404 Not Found", "429 Too Many Requests", "503 Unavailable"):
            with self.subTest(status=status), LocalTLS(reply(status=status, extra="Location: https://other.invalid/\r\n")) as server:
                with self.assertRaises(GatewayTransportError) as caught:
                    GatewayClient(config(server.port)).submit(request_fixture(time.time()))
                self.assertNotIn("test-device-secret", str(caught.exception))
                self.assertNotIn("other.invalid", str(caught.exception))
                self.assertNotIsInstance(caught.exception, GatewayNotSent)
            self.assertEqual(len(server.requests), 1)

    def test_invalid_json_duplicate_nested_nonfinite_and_oversized_values_rejected(self):
        bodies = (b'{"version":1,"version":1}', b'{"x":NaN}', b'{"x":1e9999}',
                  b'{"x":{"a":1,"a":2}}', b'[]', b'\xff', b'{')
        for body in bodies:
            with self.subTest(body=body), LocalTLS(reply(body=body)) as server:
                with self.assertRaises(GatewayTransportError):
                    GatewayClient(config(server.port)).status("request-1")

    def test_identity_state_and_outcome_contract(self):
        invalid = (result(site_id="echo"), result(request_id="other"), result(version=True),
                   result(status="done"), result(status="running", outcome="completed"),
                   result(status="terminal", outcome=None), result(status="terminal", outcome="invented"),
                   result(outcome=[]), result(observation={}), result(extra="private-data"))
        for value in invalid:
            with self.subTest(value=value), LocalTLS(reply(value)) as server:
                with self.assertRaises(GatewayTransportError):
                    GatewayClient(config(server.port)).status("request-1")

    def test_terminal_proof_without_observation_is_valid_for_reconciliation(self):
        for outcome in ("completed", "completed_after_deadline", "queue_expired", "restart_before_dispatch", "engine_stopped", "preflight_rejected"):
            expected = result(status="terminal", outcome=outcome)
            with self.subTest(outcome=outcome), LocalTLS(reply(expected)) as server:
                self.assertEqual(GatewayClient(config(server.port)).status("request-1"), expected)

    def test_nonterminal_states_remain_nonterminal(self):
        for state, outcome in (("queued", None), ("dispatching", None), ("running", None),
                               ("indeterminate", "upstream_timeout"), ("indeterminate", "dispatcher_restart")):
            expected = result(status=state, outcome=outcome)
            with self.subTest(state=state, outcome=outcome), LocalTLS(reply(expected)) as server:
                self.assertEqual(GatewayClient(config(server.port)).status("request-1"), expected)

    def test_durable_rejection_receipt_accepts_matching_submit_digest_and_status_lookup(self):
        payload = request_fixture(time.time())
        receipt = {"version": 1, "device_id": "den-adapter", "request_digest": canonical_digest(payload), "reason": "capacity"}
        expected = result(status="terminal", outcome="rejected_capacity", rejection=receipt)
        with LocalTLS(reply(expected)) as server:
            self.assertEqual(GatewayClient(config(server.port)).submit(payload), expected)
        with LocalTLS(reply(expected)) as server:
            self.assertEqual(GatewayClient(config(server.port)).status("request-1"), expected)

    def test_rejection_wrong_digest_after_post_remains_ambiguous(self):
        receipt = {"version": 1, "device_id": "den-adapter", "request_digest": "f" * 64, "reason": "closed"}
        expected = result(status="terminal", outcome="rejected_closed", rejection=receipt)
        with LocalTLS(reply(expected)) as server:
            with self.assertRaises(GatewayTransportError) as caught:
                GatewayClient(config(server.port)).submit(request_fixture(time.time()))
            self.assertNotIsInstance(caught.exception, GatewayNotSent)

    def test_missing_or_forged_rejection_receipts_are_never_terminal_proof(self):
        receipt = {"version": 1, "device_id": "den-adapter", "request_digest": "f" * 64, "reason": "closed"}
        valid = result(status="terminal", outcome="rejected_closed", rejection=receipt)
        variants = [result(status="terminal", outcome="rejected_closed"), {**valid, "observation": {}},
                    {**valid, "status": "running"}, {**valid, "outcome": "completed"}]
        for key, value in (("version", True), ("device_id", "other"), ("request_digest", "not-a-digest"),
                           ("reason", "capacity"), ("extra", "unchecked")):
            variants.append({**valid, "rejection": {**receipt, key: value}})
        for value in variants:
            with self.subTest(value=value), LocalTLS(reply(value)) as server:
                with self.assertRaises(GatewayTransportError):
                    GatewayClient(config(server.port)).status("request-1")

    def test_observation_bound_to_provisioned_camera_room_and_revision(self):
        observation = {"site_id": "victoria", "device_id": "den-adapter", "camera_id": "den_oak", "room_id": "den", "configuration_revision": "rev-1"}
        for changes in ({}, {"site_id": "echo"}, {"device_id": "other"}, {"device_id": None}, {"camera_id": "other"}, {"room_id": "other"},
                        {"configuration_revision": "other"}):
            value = result(status="terminal", outcome="completed", observation={**observation, **changes})
            with self.subTest(changes=changes), LocalTLS(reply(value)) as server:
                client = GatewayClient(config(server.port))
                if changes:
                    with self.assertRaises(GatewayTransportError):
                        client.status("request-1")
                else:
                    self.assertEqual(client.status("request-1"), value)

    def test_header_and_body_bounds_framing_and_truncation(self):
        responses = (
            b"HTTP/1.1 200 OK\r\nX-Large: " + b"x" * MAX_HEADER_BYTES,
            reply(extra="Content-Length: 1\r\n"), reply(extra="Transfer-Encoding: chunked\r\n"),
            reply(extra="Content-Encoding: gzip\r\n"), reply(content_type="text/html"),
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 999999\r\n\r\n",
            reply()[:-5], reply(extra=" folded: value\r\n"),
        )
        for response in responses:
            with self.subTest(response=response[:60]), LocalTLS(response) as server:
                with self.assertRaises(GatewayTransportError):
                    GatewayClient(config(server.port)).status("request-1")

    def test_expired_submission_does_not_connect_but_lookup_can_reconcile(self):
        attempts = []
        client = GatewayClient(config(), _connect=lambda *a, **kw: attempts.append(a))
        with self.assertRaises(GatewayTransportError):
            client.submit(request_fixture(time.time() - 16))
        self.assertFalse(attempts)
        with LocalTLS(reply(result(status="running"))) as server:
            self.assertEqual(GatewayClient(config(server.port)).status("request-1")["status"], "running")

    def test_elapsed_absolute_deadline_rejects_response_after_headers(self):
        now = [1000.0]
        with LocalTLS(reply(), after_request=lambda: now.__setitem__(0, 1016)) as server:
            client = GatewayClient(config(server.port), clock=lambda: now[0])
            with self.assertRaises(GatewayDeadline):
                client.submit(request_fixture(1000))

    def test_clock_rollback_rejects_response(self):
        now = [1000.0]
        with LocalTLS(reply(), after_request=lambda: now.__setitem__(0, 999)) as server:
            client = GatewayClient(config(server.port), clock=lambda: now[0])
            with self.assertRaises(GatewayDeadline):
                client.status("request-1")

    def test_stuck_dns_retains_single_permit_after_deadline(self):
        entered, release = threading.Event(), threading.Event()
        attempts = []

        def stuck(*args, **kwargs):
            attempts.append(args)
            entered.set()
            release.wait(2)
            raise OSError("peer-data-and-secret-must-not-escape")

        client = GatewayClient(config(), _connect=stuck)
        payload = request_fixture(time.time())
        payload["scheduled_at"] -= 14.8
        payload["request_deadline"] -= 14.8
        try:
            with self.assertRaises(GatewayDeadline):
                client.submit(payload)
            self.assertTrue(entered.is_set())
            with self.assertRaises(GatewayBusy):
                client.status("request-1")
            self.assertEqual(len(attempts), 1)
        finally:
            release.set()
            until = time.monotonic() + 2
            while client._inflight.locked() and time.monotonic() < until:
                time.sleep(.005)
        self.assertFalse(client._inflight.locked())

    def test_slow_trickle_cannot_extend_absolute_deadline(self):
        def trickle(connection):
            for byte in reply():
                connection.sendall(bytes([byte]))
                time.sleep(.025)
        payload = request_fixture(time.time())
        payload["scheduled_at"] -= 14.8
        payload["request_deadline"] -= 14.8
        started = time.monotonic()
        with LocalTLS(trickle) as server:
            with self.assertRaises(GatewayDeadline):
                GatewayClient(config(server.port)).submit(payload)
        self.assertLess(time.monotonic() - started, 1.5)


if __name__ == "__main__":
    unittest.main()
