"""Victoria-side mTLS client for the LA saved-activity admission consumer.

Mirrors the camera refresh GatewayClient and the Lab remote transport: one
provisioned DNS hostname verified against an explicit CA, a client certificate,
an optional private bearer file, one operation at a time, no redirects, proxies
or retries of a submission, and an absolute deadline. Only exact refusals that
the consumer emits before touching its admission ledger prove non-execution.
Every timeout, other status or connection loss stays unproven; recovery is an
identity-only lookup carrying digests, never the inference body.

Keep-alive (speed plan S1.5; config keep_alive, default true): one connection
per transport carries successive operations. Each response is read to its end
and released before the connection is reused (Python 3.10's read1() never
marks a Content-Length response complete). The kept connection is replaced
before a send when it has been idle more than KEEP_ALIVE_IDLE_SECONDS, when its
socket is readable, or after a response that carried Connection: close, and it
is closed after any transport error and by close(). Only a failed connect of a
FRESH connection proves non-execution; a failure after the request bytes on a
reused one is unproven like any other.
"""
import base64
import binascii
import http.client
import json
import math
import os
from pathlib import Path
import re
import select
import ssl
import threading
import time
from analysis import NotDispatched
from reasoning import canonical, digest

SUBMIT_PATH = "/v1/lab/saved-activity"
LOOKUP_PATH = "/v1/lab/saved-activity/lookup"
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 256*1024
MAX_ENVELOPE_BYTES = 2*1024**2
MAX_REFERENCE_BYTES = 16384
MAX_RESPONSE_BYTES = 128*1024
OPERATION_SECONDS = 10
ENVELOPE_FIELDS = frozenset({"request_id", "site_id", "session_id", "profile", "body", "body_sha256",
                             "prompt_version", "task", "deadline"})
STATES = frozenset({"queued", "dispatching", "running", "indeterminate", "not_found", "completed",
                    "failed", "rejected", "cancelled", "queue_expired", "not_admitted"})
TERMINAL_STATES = frozenset({"completed", "failed", "rejected", "cancelled", "queue_expired", "not_admitted"})
PROOF_FIELDS = frozenset({"version", "request_id", "body_sha256", "terminal", "state", "engine_epoch",
                          "artifact_sha256", "reason", "response", "usage"})
# Exact refusals the LA consumer (home_shared/lab_saved_activity.py) returns
# before any admission ledger read or write. Nothing else proves non-execution.
PRE_EXECUTION = frozenset({(400, "invalid_request"), (400, "labels_rejected"), (400, "profile_mismatch"),
                           (400, "prompt_version_unsupported"), (400, "input_bound_exceeded"),
                           (403, "consumer_not_registered"), (411, "length_required"), (413, "body_too_large")})
CONFIG_FIELDS = frozenset({"hostname", "port", "ca_file", "client_certificate", "client_key",
                           "deadline_seconds", "response_grace_seconds", "poll_seconds"})
CONFIG_OPTIONAL = frozenset({"token_file", "keep_alive"})
# LA's ingress closes a connection idle for 20 s; reconnect well before that.
KEEP_ALIVE_IDLE_SECONDS = 15
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}")  # the LA admission identifier
_REASON = re.compile(r"[a-z0-9_]{1,64}")


class QwenNotExecuted(NotDispatched):
    """Local failure before connecting, or an exact pre-admission refusal."""


class CompletionUnproven(ValueError):
    """No trustworthy terminal proof; the admitted request may still run."""


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _constant(_):
    raise ValueError("nonfinite JSON")


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _protected(path, *, secret):
    """Operator-provisioned file; secrets must be owner-only (Lab convention)."""
    file = Path(path)
    if not file.is_absolute() or file.is_symlink() or not file.is_file():
        raise ValueError("protected Qwen transport file required")
    if secret:
        if not hasattr(os, "geteuid"):
            raise ValueError("owner-only credential checks require POSIX ownership")
        info = file.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("private Qwen transport credential required")
    return str(file)


def close_quietly(connection):
    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass


def kept_connection_unusable(connection, last_used, now, *, idle_seconds=KEEP_ALIVE_IDLE_SECONDS):
    """True when a kept connection must not carry the next request: it has no
    socket (closed, e.g. after a Connection: close response), it has been idle
    longer than idle_seconds, or its socket is readable before a send (the peer
    closed it or sent bytes nobody asked for). Never raises; any doubt is True."""
    sock = getattr(connection, "sock", None)
    if sock is None or not _finite(last_used) or not _finite(now) or not 0 <= now - last_used <= idle_seconds:
        return True
    try:
        pending = getattr(sock, "pending", None)  # TLS bytes already decrypted
        if callable(pending) and pending():
            return True
        return bool(select.select([sock], [], [], 0)[0])
    except Exception:
        return True


def response_released(connection, response):
    """True when an exchange left its connection reusable: the response was
    read to its end and released, the peer did not ask to close (Connection:
    close, or an HTTP/1.0 reply) and the socket is still open. Never raises."""
    try:
        return (getattr(response, "will_close", True) is False and response.isclosed() is True
                and getattr(connection, "sock", None) is not None)
    except Exception:
        return False


def load_config(value, *, check_files=True):
    """Exact provisioning: {hostname, port, ca_file, client_certificate,
    client_key, deadline_seconds, response_grace_seconds, poll_seconds} plus an
    optional owner-only token_file sent as a bearer header and an optional
    keep_alive switch (default true; false restores one connection per request).

    deadline_seconds must cover the LA queue wait plus the consumer's measured
    minimum execution time; the consumer clamps it to its own bound on its own
    clock, so a Victoria clock lead cannot turn into an expired refusal."""
    if not isinstance(value, dict) or set(value) - CONFIG_OPTIONAL != CONFIG_FIELDS:
        raise ValueError("exact Qwen transport provisioning required")
    if "keep_alive" in value and type(value["keep_alive"]) is not bool:
        raise ValueError("keep_alive must be true or false")
    host = value["hostname"]
    if (not isinstance(host, str) or not 1 <= len(host) <= 253 or any(_HOST.fullmatch(p) is None for p in host.split("."))
            or type(value["port"]) is not int or not 1 <= value["port"] <= 65535):
        raise ValueError("a fixed DNS hostname and port are required")
    # The LA route for a Victoria consumer admits at most 15 s (manifest v3).
    if not _finite(value["deadline_seconds"]) or not 5 <= value["deadline_seconds"] <= 15:
        raise ValueError("admission deadline must be 5-15 seconds")
    if not _finite(value["response_grace_seconds"]) or not 1 <= value["response_grace_seconds"] <= 60:
        raise ValueError("bounded response grace required")
    if not _finite(value["poll_seconds"]) or not .1 <= value["poll_seconds"] <= 5:
        raise ValueError("bounded status polling required")
    config = dict(value)
    for key, secret in (("ca_file", False), ("client_certificate", False), ("client_key", True), ("token_file", True)):
        if key not in config:
            continue
        if not isinstance(config[key], str) or not config[key]:
            raise ValueError("Qwen transport file paths required")
        if check_files:
            config[key] = _protected(config[key], secret=secret)
    return config


class QwenTransport:
    """Callable admitted transport: submit once, then poll by identity.

    __call__(envelope) returns a terminal proof or raises QwenNotExecuted
    (proven never admitted) or CompletionUnproven (outcome unknown).
    lookup(reference) returns the consumer's current proof for that identity.
    """
    # Proof states this transport accepts (LiveSceneTransport adds the live
    # consumer's reconcile outcome).
    STATES, TERMINAL_STATES = STATES, TERMINAL_STATES

    def __init__(self, config, *, connection_factory=None, clock=time.time, monotonic=time.monotonic, sleep=None):
        self.config = config
        self.host, self.port = config["hostname"], config["port"]
        self.keep_alive = config.get("keep_alive", True)
        if type(self.keep_alive) is not bool:
            raise ValueError("keep_alive must be true or false")
        self._clock, self._monotonic = clock, monotonic
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        # The one kept connection and when its last exchange ended (monotonic);
        # touched only while _inflight is held.
        self._kept, self._last_used = None, None
        if connection_factory is None:
            try:
                with open(config["ca_file"], "rb") as source:
                    ca = source.read(1024*1024+1)
                if not ca or len(ca) > 1024*1024:
                    raise ValueError()
                # Explicit context: no system trust additions or key logging.
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.hostname_checks_common_name = False
                context.load_verify_locations(cadata=ca.decode("ascii"))
                context.load_cert_chain(config["client_certificate"], config["client_key"])
            except Exception:
                raise ValueError("Qwen transport trust configuration unavailable") from None
            connection_factory = lambda timeout: http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=context)
        self._connection = connection_factory
        self._inflight = threading.Lock()

    def close(self):
        """Stop polling, refuse new operations and close the kept connection
        (also the step for a certificate rotation: close, then build a new
        transport, which reloads the trust files). An in-flight operation ends
        within its bound and then closes its own connection."""
        self._stop.set()
        if self._inflight.acquire(blocking=False):
            try:
                connection, self._kept = self._kept, None
                close_quietly(connection)
            finally:
                self._inflight.release()

    def _token(self):
        path = self.config.get("token_file")
        if path is None:
            return None
        token = Path(path).read_text(encoding="ascii").strip()
        if not 32 <= len(token) <= 512 or re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token) is None:
            raise ValueError("invalid Qwen transport credential")
        return token

    def _check_envelope(self, envelope):
        now = self._clock()
        if (not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS
                or not isinstance(envelope["request_id"], str) or not re.fullmatch(r"[a-f0-9]{64}", envelope["request_id"])
                or envelope["body_sha256"] != digest(envelope["body"])
                or not _finite(envelope["deadline"]) or not _finite(now)
                or not now < envelope["deadline"] <= now + self.config["deadline_seconds"] + 1):
            raise QwenNotExecuted("envelope_invalid")
        try:
            content = envelope["body"]["messages"][1]["content"]
            images = [part["image_url"]["url"] for part in content if part.get("type") == "image_url"]
            if not 1 <= len(images) <= MAX_IMAGES:
                raise ValueError()
            for url in images:
                prefix = "data:image/jpeg;base64,"
                if not url.startswith(prefix) or len(url) - len(prefix) > 4*((MAX_IMAGE_BYTES+2)//3):
                    raise ValueError()
                if not 0 < len(base64.b64decode(url[len(prefix):], validate=True)) <= MAX_IMAGE_BYTES:
                    raise ValueError()
        except (KeyError, IndexError, TypeError, AttributeError, ValueError, binascii.Error):
            raise QwenNotExecuted("envelope_images_invalid") from None
        body = canonical(envelope).encode()
        if len(body) > MAX_ENVELOPE_BYTES:
            raise QwenNotExecuted("envelope_exceeds_bound")
        return body

    def _post(self, path, body, budget, *, submission):
        """One request; returns (status, value). Raises QwenNotExecuted only
        when nothing could have reached the consumer."""
        if self._stop.is_set():
            if submission:
                raise QwenNotExecuted("transport_closed")
            raise CompletionUnproven("transport_closed")
        if not self._inflight.acquire(blocking=False):
            if submission:
                raise QwenNotExecuted("transport_busy")
            raise CompletionUnproven("transport_busy")
        connection, reusable = None, False
        try:
            started = self._monotonic()
            def remaining():
                left = started + budget - self._monotonic()
                if left <= 0:
                    raise TimeoutError()
                return left
            # The kept connection, unless idle too long, readable or closed.
            connection, self._kept = self._kept, None
            if connection is not None and kept_connection_unusable(connection, self._last_used, self._monotonic()):
                close_quietly(connection)
                connection = None
            try:
                token = self._token()
                if connection is None:
                    connection = self._connection(min(remaining(), OPERATION_SECONDS))
                    # TCP and TLS complete here, before any request byte is sent:
                    # a failure proves the consumer never received this request.
                    connection.connect()
                    # Never a hidden reconnect inside request(): every connect
                    # is this explicit one.
                    connection.auto_open = 0
                else:
                    # A reused connection: this operation's bound applies. A
                    # failure from here on is unproven, as on a fresh one.
                    connection.timeout = min(remaining(), OPERATION_SECONDS)
                    connection.sock.settimeout(connection.timeout)
            except Exception:
                if submission:
                    raise QwenNotExecuted("transport_not_connected") from None
                raise CompletionUnproven("lookup_not_connected") from None
            headers = {"Content-Type": "application/json", "Accept": "application/json",
                       "Accept-Encoding": "identity"}
            if not self.keep_alive:
                headers["Connection"] = "close"
            if token:
                headers["Authorization"] = "Bearer " + token
            partial = False
            try:
                connection.request("POST", path, body=body, headers=headers)
            except OSError:
                # A consumer may refuse and close before reading the body. Read
                # that response once; only an exact pre-admission refusal counts.
                partial = True
            response = connection.getresponse()
            if (response.getheader("Content-Type", "").split(";")[0].strip() != "application/json"
                    or response.getheader("Content-Encoding", "identity") != "identity"):
                raise ValueError()
            raw = bytearray()
            while True:
                if connection.sock is not None:
                    connection.sock.settimeout(remaining())
                chunk = response.read1(min(8192, MAX_RESPONSE_BYTES+1-len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError()
            # Python 3.10's read1() never marks a fully read Content-Length
            # response complete, so a reused connection would refuse the next
            # request (ResponseNotReady). read() finds nothing left and
            # releases the response; anything left over is a malformed reply.
            if response.read():
                raise ValueError()
            value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
            if not isinstance(value, dict):
                raise ValueError()
            if partial and response.status in {200, 202}:
                raise ValueError()
            reusable = self.keep_alive and not partial and response_released(connection, response)
            return response.status, value
        except (QwenNotExecuted, CompletionUnproven):
            raise
        except Exception:
            raise CompletionUnproven("analysis_completion_unproven") from None
        finally:
            # Kept only after a clean, fully released exchange; closed after any
            # error, a Connection: close reply, or once the transport is closed.
            if reusable and not self._stop.is_set():
                self._kept, self._last_used = connection, self._monotonic()
            else:
                close_quietly(connection)
            self._inflight.release()

    def _proof(self, status, value, reference, *, submission):
        if submission and status in {s for s, _ in PRE_EXECUTION}:
            if set(value) == {"reason"} and (status, value["reason"]) in PRE_EXECUTION:
                raise QwenNotExecuted(value["reason"])
            raise CompletionUnproven("analysis_completion_unproven")
        if status not in ({202, 200} if submission else {200}):
            raise CompletionUnproven("analysis_completion_unproven")
        state = value.get("state")
        response, usage, reason = value.get("response"), value.get("usage"), value.get("reason")
        if (not set(value) <= PROOF_FIELDS or not {"version", "request_id", "body_sha256", "terminal", "state",
                "engine_epoch", "artifact_sha256"} <= set(value)
                or type(value["version"]) is not int or value["version"] != 1
                or value["request_id"] != reference["request_id"] or value["body_sha256"] != reference["body_sha256"]
                or state not in self.STATES or value["terminal"] is not (state in self.TERMINAL_STATES)
                or (value["engine_epoch"] is not None and (not isinstance(value["engine_epoch"], str) or not _ID.fullmatch(value["engine_epoch"])))
                or (value["artifact_sha256"] is not None and (not isinstance(value["artifact_sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", value["artifact_sha256"])))
                or (reason is not None and (not isinstance(reason, str) or not _REASON.fullmatch(reason)))
                or (response is not None and (state != "completed" or not isinstance(response, dict)))
                or (usage is not None and (state != "completed" or not isinstance(usage, dict)))):
            raise CompletionUnproven("analysis_completion_unproven")
        return value

    def submit(self, envelope, *, budget=OPERATION_SECONDS):
        body = self._check_envelope(envelope)
        status, value = self._post(SUBMIT_PATH, body, budget, submission=True)
        return self._proof(status, value, envelope, submission=True)

    def lookup(self, reference, *, budget=OPERATION_SECONDS):
        if not isinstance(reference, dict) or "body" in reference or set(reference) != ENVELOPE_FIELDS - {"body"}:
            raise CompletionUnproven("lookup_reference_invalid")
        body = canonical(reference).encode()
        if len(body) > MAX_REFERENCE_BYTES:
            raise CompletionUnproven("lookup_reference_invalid")
        status, value = self._post(LOOKUP_PATH, body, budget, submission=False)
        return self._proof(status, value, reference, submission=False)

    def __call__(self, envelope):
        # Deadline/grace are in wall seconds; the loop measures them monotonically.
        started = self._monotonic()
        # Wait past the admission deadline so a late but proven completion can
        # still be collected; afterwards the step stays unproven.
        budget = envelope.get("deadline", 0) - self._clock() + self.config["response_grace_seconds"] if isinstance(envelope, dict) else 0
        try:
            proof = self.submit(envelope, budget=max(.001, min(OPERATION_SECONDS, budget)) if _finite(budget) else .001)
        except QwenNotExecuted:
            raise
        except CompletionUnproven:
            # The request may have been admitted (a lost 202, a read timeout or
            # a reset after upload): recover by identity, never by resending.
            proof = None
        reference = {k: v for k, v in envelope.items() if k != "body"}
        while proof is None or not proof["terminal"]:
            left = started + budget - self._monotonic()
            if left <= 0 or self._stop.is_set():
                raise CompletionUnproven("analysis_completion_unproven")
            self._sleep(min(self.config["poll_seconds"], left))
            try:
                proof = self.lookup(reference, budget=max(.001, min(OPERATION_SECONDS, started + budget - self._monotonic())))
            except CompletionUnproven:
                continue  # Identity-only lookups are safe to repeat until the bound.
        return proof


def saved_analysis(store, qwen_profile=None, qwen_transport=None, *, transport_factory=None):
    """Controller-lifetime sequential Qwen analysis from protected provisioning.

    qwen_profile and qwen_transport are the managed provisioning keys: each is
    an object or the path of a private operator JSON file. Absent keys mean no
    saved analysis (None, reported as qwen_admission_not_provisioned); a partial
    or invalid pair fails closed at startup. Construction sends nothing; the
    owner's start_reconcile performs identity lookups only.

    Not yet reachable in production: live_detector_app.validate_config accepts
    an exact key set without these keys and managed_detector_app builds
    ReadinessPair without analysis_jobs. Wiring both is a composition change
    for that owner; until then saved analysis reports qwen_admission_not_provisioned.
    """
    if qwen_profile is None and qwen_transport is None:
        return None
    if qwen_profile is None or qwen_transport is None:
        raise ValueError("Qwen profile and admission transport must be provisioned together")
    from live_detector_app import private_json
    from analysis import SequentialAnalysis
    from analysis_inputs import RegisteredAnalysisInputs
    from analysis_jobs import AnalysisJobs
    from qwen_adapter import QwenAdapter, RegisteredAnalysisMedia
    profile = qwen_profile if isinstance(qwen_profile, dict) else private_json(qwen_profile)
    # An injected factory owns its connections, so it reads no trust files.
    config = load_config(qwen_transport if isinstance(qwen_transport, dict) else private_json(qwen_transport),
                         check_files=transport_factory is None)
    transport = (transport_factory or QwenTransport)(config)
    adapter = QwenAdapter(profile, RegisteredAnalysisMedia(store), transport, deadline_seconds=config["deadline_seconds"])
    analysis = SequentialAnalysis(store, profile, adapter)

    def lookup(request, reference):
        # Bound to the step's recorded profile, so an engine restart resolves
        # to ended_unknown instead of blocking all later analysis.
        return adapter.reconcile(request, transport.lookup, reference)

    return AnalysisJobs(store, analysis, RegisteredAnalysisInputs(store), lookup=lookup, stop=getattr(transport, "close", None))
