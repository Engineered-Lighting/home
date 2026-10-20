"""Real loopback HTTP/TLS and JPEG tests; completion authority is synthetic."""
import base64
from contextlib import contextmanager
from dataclasses import replace
import io
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from home_shared.admission import AdmissionController, AdmissionLedger, ResourceManifest
from home_shared.gateway import Gateway, validate_inference_request
from home_shared.openai_backend import (BackendBusy, BackendDeadline, BackendUnknown,
                                       InputTokenBound, OpenAISnapshotBackend, PROMPT, compose_snapshot_gateway)
from test_admission import FakeVerifier, manifest_fixture
from test_gateway import binding_fixture, header_fixture, request_fixture

CERTS = Path(__file__).parent / "fixtures" / "gateway_client"


def fixture_connect(address, timeout):
    # Fixture-only DNS remap: preserve actual TLS hostname and CA verification.
    return socket.create_connection(("127.0.0.1", address[1]), timeout=timeout)


def completion():
    return {"id": "chatcmpl-fixture-123", "object": "chat.completion", "created": int(time.time()),
            "model": "fake-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": "A chair is visible."},
                                               "finish_reason": "stop", "logprobs": None}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 8, "total_tokens": 50}}


def response(value=None, *, status=200, extra=b"", length=None):
    body = json.dumps(value if value is not None else completion()).encode()
    return (f"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\nContent-Length: {len(body) if length is None else length}\r\n".encode()
            + extra + b"\r\n" + body)


def payload():
    output = io.BytesIO()
    Image.new("RGB", (640, 360), "black").save(output, format="JPEG", quality=85)
    value = request_fixture(time.time())
    value["image_base64"] = base64.b64encode(output.getvalue()).decode()
    return value


def manifest(port, *, tls=False, hostname="127.0.0.1"):
    value = manifest_fixture()
    value["upstream"] = f"{'https' if tls else 'http'}://{hostname}:{port}/v1"
    return ResourceManifest.from_dict(value)


class SyntheticAttestor:
    def __init__(self, subject):
        self.manifest_digest, self.engine_epoch = subject.digest, "fixture-epoch"
        self.observations = []
        self.deny = False
        self.callback = None

    def attest(self, observation, deadline):
        self.observations.append(observation)
        if self.callback:
            self.callback()
        return None if self.deny else (self.engine_epoch, observation.ledger_id, "finished")


class SyntheticInputAuthority:
    """Synthetic fixture count only; never a real model token calculation."""
    def __init__(self, subject):
        self.manifest_digest, self.engine_epoch = subject.digest, "fixture-epoch"
        self.transform = lambda value: value
        self.snapshots = []

    def assess(self, snapshot, deadline):
        self.snapshots.append(snapshot)
        return self.transform(InputTokenBound(snapshot.manifest_digest, snapshot.engine_epoch,
            snapshot.model_revision, snapshot.upstream_body_sha256, 42, deadline))


class IsolatedTransportBackend(OpenAISnapshotBackend):
    """Test-only socket/parser harness; never imported by production composition."""
    def _validate_admission(self, request):
        pass


@contextmanager
def model_server(wire=None, *, tls=False, hold=None):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    requests, errors = [], []
    done = threading.Event()
    def serve():
        connection = None
        try:
            connection, _ = listener.accept()
            connection.settimeout(1)
            if tls:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(CERTS / "server.pem", CERTS / "server-test-only.key")
                connection = context.wrap_socket(connection, server_side=True)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                data += chunk
            head, body = data.split(b"\r\n\r\n", 1)
            headers = dict(line.split(b": ", 1) for line in head.split(b"\r\n")[1:])
            size = int(headers[b"Content-Length"])
            if size > 384 * 1024:
                raise AssertionError("fixture request exceeds bound")
            while len(body) < size:
                chunk = connection.recv(min(4096, size - len(body)))
                if not chunk:
                    return
                body += chunk
            requests.append((head, json.loads(body)))
            if hold:
                hold.wait(1)
            output = wire if wire is not None else response()
            if callable(output):
                output(connection)
            else:
                connection.sendall(output)
        except (OSError, ssl.SSLError):
            pass  # Expected refused trust, client deadline or shutdown.
        except Exception as exc:
            errors.append(exc)
        finally:
            if connection:
                connection.close()
            done.set()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], requests
    finally:
        if hold:
            hold.set()
        listener.close()
        thread.join(1.5)
        if thread.is_alive() or errors:
            raise AssertionError("local model fixture did not exit cleanly")


class OpenAIBackendTests(unittest.TestCase):
    def test_actual_vllm_0202_snapshot_response_and_null_only_extensions(self):
        # Captured from the resident server using a generated blue/red JPEG;
        # no private camera image or conversation is represented in this fixture.
        wire = json.loads((Path(__file__).parent / 'fixtures' /
                           'vllm-0.20.2-synthetic-snapshot.json').read_text())
        backend, _, request = self.make(1)
        backend.manifest = replace(backend.manifest, model_alias='qwen3-vl-30b')
        request = replace(request, max_output_tokens=128)
        parsed = backend._completion(json.dumps(wire).encode(), request)
        self.assertIn('red square', parsed[0])
        self.assertEqual(parsed[3:], (301, 42))
        for location, field in [('root', 'prompt_token_ids'), ('choice', 'token_ids'),
                                ('message', 'annotations'), ('message', 'audio')]:
            with self.subTest(field=field):
                changed = json.loads(json.dumps(wire))
                parent = changed if location == 'root' else changed['choices'][0]
                if location == 'message':
                    parent = parent['message']
                parent[field] = []
                with self.assertRaises(BackendUnknown):
                    backend._completion(json.dumps(changed).encode(), request)

    def make(self, port, *, tls=False, hostname="127.0.0.1", production=False, **kwargs):
        subject = manifest(port, tls=tls, hostname=hostname)
        authority = SyntheticAttestor(subject)
        kwargs.setdefault("_connect", fixture_connect)
        backend_type = OpenAISnapshotBackend if production else IsolatedTransportBackend
        backend = backend_type(subject, authority.engine_epoch, attestor=authority,
            input_authority=SyntheticInputAuthority(subject),
            ca_file=CERTS / "ca.pem" if tls else None, **kwargs)
        request = validate_inference_request(payload(), binding_fixture(), time.time())
        return backend, authority, request

    def test_real_http_jpeg_fixed_request_and_independent_evidence(self):
        with model_server() as (port, calls):
            backend, authority, request = self.make(port, bearer_token="fixture-secret")
            result = backend.execute(request, request.request_deadline)
        self.assertEqual(result.observation_text, "A chair is visible.")
        self.assertEqual(result.terminal_evidence, ("fixture-epoch", request.ledger_id, "finished"))
        head, body = calls[0]
        self.assertTrue(head.startswith(b"POST /v1/chat/completions HTTP/1.1\r\n"))
        self.assertIn(b"Authorization: Bearer fixture-secret", head)
        self.assertEqual(set(body), {"model", "stream", "n", "max_tokens", "messages"})
        self.assertFalse(body["stream"])
        self.assertEqual(body["max_tokens"], request.max_output_tokens)
        content = body["messages"][0]["content"]
        self.assertEqual(content[0]["text"], PROMPT)
        self.assertEqual(base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1]), request.image)
        receipt = authority.observations[0]
        self.assertEqual(receipt.request_digest, request.digest)
        self.assertEqual(receipt.manifest_digest, backend.manifest.digest)
        self.assertEqual(receipt.completion_id, "chatcmpl-fixture-123")
        self.assertNotIn("chair", repr(receipt))
        self.assertNotIn("fixture-secret", repr(receipt))

    def test_real_tls_verifies_explicit_ca_and_hostname(self):
        with model_server(tls=True) as (port, calls):
            backend, _, request = self.make(port, tls=True, hostname="localhost")
            self.assertEqual(backend.execute(request, request.request_deadline).observation_text, "A chair is visible.")
        self.assertEqual(len(calls), 1)

    def test_wrong_tls_ca_never_sends_model_request(self):
        with model_server(tls=True) as (port, calls):
            subject = manifest(port, tls=True, hostname="localhost")
            backend = IsolatedTransportBackend(subject, "fixture-epoch", attestor=SyntheticAttestor(subject),
                input_authority=SyntheticInputAuthority(subject), ca_file=CERTS / "client.pem", _connect=fixture_connect)
            request = validate_inference_request(payload(), binding_fixture(), time.time())
            with self.assertRaises(BackendUnknown): backend.execute(request, request.request_deadline)
        self.assertEqual(calls, [])

    def test_missing_or_wrong_attestor_prevents_construction(self):
        subject = manifest(19999)
        with self.assertRaises(TypeError): OpenAISnapshotBackend(subject, "fixture-epoch")
        with self.assertRaises(ValueError): OpenAISnapshotBackend(subject, "fixture-epoch", attestor=None,
            input_authority=SyntheticInputAuthority(subject))
        authority = SyntheticAttestor(subject)
        authority.engine_epoch = "other-epoch"
        with self.assertRaises(ValueError): OpenAISnapshotBackend(subject, "fixture-epoch", attestor=authority,
            input_authority=SyntheticInputAuthority(subject))
        with self.assertRaises(TypeError): OpenAISnapshotBackend(subject, "fixture-epoch", attestor=SyntheticAttestor(subject))
        with self.assertRaises(ValueError): OpenAISnapshotBackend(subject, "fixture-epoch", attestor=SyntheticAttestor(subject),
            input_authority=None)

    def test_bad_url_paths_and_plaintext_hosts_rejected_without_io(self):
        subject = manifest(19999)
        for url in ("http://example.com/v1", "http://127.0.0.1/other", "http://127.0.0.1/v1?key=x",
                    "https://user:secret@localhost/v1", "http://127.0.0.1:0/v1"):
            changed = replace(subject, upstream=url)
            with self.subTest(url=url), self.assertRaises(ValueError):
                OpenAISnapshotBackend(changed, "fixture-epoch", attestor=SyntheticAttestor(changed),
                    input_authority=SyntheticInputAuthority(changed))

    def test_input_budget_missing_over_limit_stale_or_wrong_body_prevents_network(self):
        for transform in (lambda v: None, lambda v: replace(v, upper_bound_tokens=1001),
                          lambda v: replace(v, upper_bound_tokens=True),
                          lambda v: replace(v, valid_until=v.valid_until - 1),
                          lambda v: replace(v, upstream_body_sha256="0" * 64),
                          lambda v: replace(v, model_revision="0" * 64),
                          lambda v: replace(v, engine_epoch="wrong")):
            with self.subTest(transform=transform):
                connections = []
                backend, _, request = self.make(19999, _connect=lambda *a, **_k: connections.append(a))
                backend._input_authority.transform = transform
                with self.assertRaises(BackendUnknown): backend.execute(request, request.request_deadline)
                self.assertEqual(connections, [])

    def test_input_authority_binding_change_prevents_network(self):
        connections = []
        backend, _, request = self.make(19999, _connect=lambda *a, **_k: connections.append(a))
        def change(value):
            backend._input_authority.manifest_digest = "0" * 64
            return value
        backend._input_authority.transform = change
        with self.assertRaises(BackendUnknown): backend.execute(request, request.request_deadline)
        self.assertEqual(connections, [])

    def test_usage_over_preflight_bound_fails_even_below_manifest_limit(self):
        value = completion(); value["usage"].update(prompt_tokens=43, total_tokens=51)
        with model_server(response(value)) as (port, _):
            backend, authority, request = self.make(port)
            with self.assertRaises(BackendUnknown): backend.execute(request, request.request_deadline)
            self.assertEqual(authority.observations, [])

    def test_forward_wall_jump_over_one_second_rejects_completion(self):
        shift = [0]
        with model_server() as (port, _):
            backend, authority, request = self.make(port, clock=lambda: time.time() + shift[0])
            authority.callback = lambda: shift.__setitem__(0, 2)
            with self.assertRaises(BackendDeadline): backend.execute(request, request.request_deadline)

    def test_real_decoder_rejects_header_only_image_before_network(self):
        connections = []
        backend, authority, request = self.make(19999, _connect=lambda *a, **_k: connections.append(a))
        request = replace(request, image=header_fixture())
        with self.assertRaises(BackendUnknown): backend.execute(request, request.request_deadline)
        self.assertEqual(authority.observations, [])
        self.assertEqual(connections, [])

    def test_wrong_model_identity_choice_tools_and_finish_fail_before_attestation(self):
        cases = []
        for field, bad in (("id", "other"), ("model", "other-model"), ("object", "chat.completion.chunk"), ("created", True)):
            value = completion(); value[field] = bad; cases.append(value)
        value = completion(); value["choices"][0]["finish_reason"] = "length"; cases.append(value)
        value = completion(); value["choices"][0]["message"]["tool_calls"] = [{"function": "light"}]; cases.append(value)
        value = completion(); value["choices"].append(value["choices"][0]); cases.append(value)
        value = completion(); value["usage"]["prompt_tokens"] = 1001; cases.append(value)
        value = completion(); value["choices"][0]["message"]["content"] = "x" * 4097; cases.append(value)
        for value in cases:
            with self.subTest(value=value), model_server(response(value)) as (port, calls):
                backend, authority, request = self.make(port)
                with self.assertRaises(BackendUnknown): backend.execute(request, request.request_deadline)
                self.assertEqual(authority.observations, [])
                self.assertEqual(len(calls), 1)

    def test_oversized_truncated_ambiguous_and_redirect_responses_fail(self):
        for wire in (response(length=65537), response()[:-20], response(extra=b"Content-Length: 3\r\n"),
                     response(status=302, extra=b"Location: http://example.com/\r\n"),
                     response(extra=b"Transfer-Encoding: chunked\r\n")):
            with self.subTest(wire=wire[:80]), model_server(wire) as (port, calls):
                backend, authority, request = self.make(port)
                with self.assertRaises(BackendUnknown): backend.execute(request, request.request_deadline)
                self.assertEqual(authority.observations, [])
                self.assertEqual(len(calls), 1)

    def test_http_success_without_attestation_is_unknown(self):
        with model_server() as (port, _):
            backend, authority, request = self.make(port)
            authority.deny = True
            with self.assertRaises(BackendUnknown): backend.execute(request, request.request_deadline)

    def test_changed_attestor_binding_after_send_or_during_attestation_fails(self):
        with model_server() as (port, _):
            backend, authority, request = self.make(port)
            authority.callback = lambda: setattr(authority, "manifest_digest", "0" * 64)
            with self.assertRaises(BackendUnknown): backend.execute(request, request.request_deadline)

    def test_attestor_exception_cannot_leak_peer_or_private_text(self):
        with model_server() as (port, _):
            backend, authority, request = self.make(port)
            def fail(): raise BackendUnknown("private token and room image")
            authority.callback = fail
            with self.assertRaises(BackendUnknown) as caught: backend.execute(request, request.request_deadline)
            self.assertNotIn("private", str(caught.exception))

    def test_total_deadline_covers_waiting_response(self):
        hold = threading.Event()
        with model_server(hold=hold) as (port, calls):
            backend, _, request = self.make(port)
            request = replace(request, request_deadline=time.time() + .15)
            started = time.monotonic()
            with self.assertRaises(BackendDeadline): backend.execute(request, request.request_deadline)
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(len(calls), 1)

    def test_attestation_timeout_keeps_worker_permit_until_actual_exit(self):
        entered, release = threading.Event(), threading.Event()
        with model_server() as (port, _):
            backend, authority, request = self.make(port)
            authority.callback = lambda: (entered.set(), release.wait(1))
            request = replace(request, request_deadline=time.time() + .15)
            try:
                with self.assertRaises(BackendDeadline): backend.execute(request, request.request_deadline)
                self.assertTrue(entered.is_set())
                later = replace(request, request_deadline=time.time() + 1)
                with self.assertRaises(BackendBusy): backend.execute(later, later.request_deadline)
            finally:
                release.set()

    def test_gateway_keeps_reservation_when_attestor_denies_http_completion(self):
        with tempfile.TemporaryDirectory() as directory, model_server() as (port, _):
            backend, authority, _ = self.make(port, production=True)
            authority.deny = True
            ledger = AdmissionLedger(Path(directory) / "ledger.sqlite")
            try:
                controller = AdmissionController(ledger, backend.manifest, FakeVerifier())
                controller.enable("fixture-epoch")
                gateway = compose_snapshot_gateway(controller, backend)
                value = payload()
                request = validate_inference_request(value, binding_fixture(), time.time())
                self.assertEqual(gateway.submit(value, binding_fixture())["status"], "queued")
                self.assertEqual(gateway.dispatch_once()["status"], "indeterminate")
                record = ledger.get(request.ledger_id)
                self.assertTrue(record.reservation)
                self.assertEqual(record.state, "indeterminate")
            finally:
                ledger.close()

    def test_composed_gateway_verifies_completion_and_releases_reservation(self):
        with tempfile.TemporaryDirectory() as directory, model_server() as (port, calls):
            backend, _, _ = self.make(port, production=True)
            ledger = AdmissionLedger(Path(directory) / "ledger.sqlite")
            try:
                controller = AdmissionController(ledger, backend.manifest, FakeVerifier())
                controller.enable("fixture-epoch")
                gateway = compose_snapshot_gateway(controller, backend)
                value = payload()
                request = validate_inference_request(value, binding_fixture(), time.time())
                self.assertEqual(gateway.submit(value, binding_fixture())["status"], "queued")
                self.assertEqual(gateway.dispatch_once()["status"], "terminal")
                record = ledger.get(request.ledger_id)
                self.assertEqual(record.state, "terminal")
                self.assertFalse(record.reservation)
                self.assertEqual(len(calls), 1)
            finally:
                ledger.close()

    def test_composition_rejects_wrong_manifest_epoch_and_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            backend, _, _ = self.make(19999, production=True)
            ledger = AdmissionLedger(Path(directory) / "ledger.sqlite")
            try:
                controller = AdmissionController(ledger, backend.manifest, FakeVerifier())
                controller.enable("other-epoch")
                with self.assertRaises(ValueError): compose_snapshot_gateway(controller, backend)
                controller.enable("fixture-epoch")
                controller.manifest = replace(backend.manifest, upstream="http://127.0.0.1:19998/v1")
                with self.assertRaises(ValueError): compose_snapshot_gateway(controller, backend)
                controller.manifest = backend.manifest
                compose_snapshot_gateway(controller, backend)
                with self.assertRaises(ValueError): compose_snapshot_gateway(controller, backend)
            finally:
                ledger.close()

    def test_controller_epoch_change_during_input_admission_prevents_network(self):
        with tempfile.TemporaryDirectory() as directory:
            connections = []
            backend, _, _ = self.make(19999, production=True, _connect=lambda *a, **_k: connections.append(a))
            ledger = AdmissionLedger(Path(directory) / "ledger.sqlite")
            try:
                controller = AdmissionController(ledger, backend.manifest, FakeVerifier())
                controller.enable("fixture-epoch")
                gateway = compose_snapshot_gateway(controller, backend)
                def changed(bound):
                    controller.engine_epoch = "other-epoch"
                    return bound
                backend._input_authority.transform = changed
                value = payload()
                gateway.submit(value, binding_fixture())
                self.assertEqual(gateway.dispatch_once()["status"], "indeterminate")
                self.assertEqual(connections, [])
            finally:
                ledger.close()

    def test_production_backend_without_controller_never_connects(self):
        connections = []
        backend, authority, request = self.make(19999, production=True,
            _connect=lambda *a, **_k: connections.append(a))
        with self.assertRaises(BackendUnknown):
            backend.execute(request, request.request_deadline)
        self.assertEqual(connections, [])
        self.assertEqual(authority.observations, [])
