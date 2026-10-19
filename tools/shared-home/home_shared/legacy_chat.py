"""Admitted LA chat transport. One shared dispatcher owns all ledger transitions.

No production token/lifecycle authority is supplied. SSE bytes are observable
output, never terminal engine evidence. Client cancellation retains reservations.
"""
from __future__ import annotations
import base64
from dataclasses import dataclass
import hashlib
import http.client
import io
import json
import math
import queue
import re
import socket
import threading
from typing import Any

from .admission import AdmissionError, canonical_digest
from .openai_backend import (OpenAISnapshotBackend, BackendUnknown, BackendDeadline,
                             BackendBusy, _object, _constant)


@dataclass(frozen=True)
class LegacyChatPolicy:
    max_body_bytes: int
    max_images: int
    max_image_pixels: int
    max_response_bytes: int
    max_event_bytes: int
    allowed_options: frozenset[str]

    def __post_init__(self):
        for name, limit in (("max_body_bytes", 8*1024*1024), ("max_images", 16),
                            ("max_image_pixels", 16*1024*1024), ("max_response_bytes", 16*1024*1024),
                            ("max_event_bytes", 1024*1024)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError("explicit bounded chat policy required")
        safe = {"temperature", "top_p", "top_k", "min_p", "seed", "stop", "presence_penalty",
                "frequency_penalty", "repetition_penalty", "tools", "tool_choice", "parallel_tool_calls",
                "response_format", "stream_options", "logprobs", "top_logprobs", "chat_template_kwargs"}
        if not isinstance(self.allowed_options, frozenset) or not self.allowed_options <= safe:
            raise ValueError("unsupported chat options")

    @property
    def digest(self):
        return canonical_digest({"max_body_bytes": self.max_body_bytes, "max_images": self.max_images,
            "max_image_pixels": self.max_image_pixels, "max_response_bytes": self.max_response_bytes,
            "max_event_bytes": self.max_event_bytes, "allowed_options": sorted(self.allowed_options)})


@dataclass(frozen=True)
class LegacyChatRequest:
    request_id: str
    ledger_id: str
    site_id: str
    consumer_id: str
    request_deadline: float
    task_class: str
    max_output_tokens: int
    stream: bool
    body: bytes
    digest: str


@dataclass(frozen=True)
class ChatInput:
    manifest_digest: str
    engine_epoch: str
    model_revision: str
    upstream_body_sha256: str
    body: bytes
    max_output_tokens: int
    ledger_id: str | None = None
    request_digest: str | None = None


@dataclass(frozen=True)
class ChatCompletionObservation:
    manifest_digest: str
    engine_epoch: str
    ledger_id: str
    request_digest: str
    upstream_body_sha256: str
    response_sha256: str
    http_status: int
    stream: bool
    request_deadline: float


@dataclass(frozen=True)
class LegacyChatResult:
    status: int
    content_type: str
    body: bytes
    terminal_evidence: Any


def prepare_chat_request(payload, *, consumer_id, request_id, deadline, manifest, policy,
                         task_class="interactive"):
    """consumer_id is provisioned ingress identity, NEVER a caller site/header claim."""
    if (manifest.consumer_routes.get(consumer_id) != "echo" or
            not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", request_id) is None or
            type(deadline) not in (float, int) or not math.isfinite(deadline) or
            task_class not in {"interactive", "background"}):
        raise AdmissionError("invalid trusted echo request binding")
    if getattr(manifest, "consumer_policy_sha256", {}).get(consumer_id) != policy.digest:
        raise AdmissionError("chat policy lacks exact resource-manifest binding")
    required = {"model", "messages", "max_tokens"}
    if (type(payload) is not dict or not required <= payload.keys() or
            not payload.keys() <= required | {"stream", "n"} | policy.allowed_options or
            payload["model"] != manifest.model_alias or type(payload.get("stream", False)) is not bool or
            type(payload.get("n", 1)) is not int or payload.get("n", 1) != 1 or
            type(payload["max_tokens"]) is not int or not 1 <= payload["max_tokens"] <= manifest.max_output_tokens):
        raise AdmissionError("chat request outside explicit policy")
    try:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise AdmissionError("invalid JSON chat request") from None
    if len(body) > policy.max_body_bytes:
        raise AdmissionError("chat body too large")
    messages = payload["messages"]
    if not isinstance(messages, list) or not 1 <= len(messages) <= 1024:
        raise AdmissionError("bounded messages required")
    images = 0
    for message in messages:
        if (not isinstance(message, dict) or message.get("role") not in {"system", "developer", "user", "assistant", "tool"}
                or not message.keys() <= {"role", "content", "name", "tool_calls", "tool_call_id"}):
            raise AdmissionError("unsupported message")
        content = message.get("content")
        if content is None or isinstance(content, str):
            continue
        if not isinstance(content, list):
            raise AdmissionError("unsupported message content")
        for part in content:
            if isinstance(part, dict) and set(part) == {"type", "text"} and part["type"] == "text" and isinstance(part["text"], str):
                continue
            if not isinstance(part, dict) or set(part) != {"type", "image_url"} or part["type"] != "image_url":
                raise AdmissionError("unsupported multimodal input")
            image = part["image_url"]
            if not isinstance(image, dict) or not {"url"} <= image.keys() <= {"url", "detail"}:
                raise AdmissionError("invalid image input")
            match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", image["url"]) if isinstance(image.get("url"), str) else None
            if not match:
                raise AdmissionError("only inline bounded images admitted")
            images += 1
            if images > policy.max_images:
                raise AdmissionError("too many images")
            try:
                raw = base64.b64decode(match[2], validate=True)
                if not 1 <= len(raw) <= manifest.max_image_bytes:
                    raise ValueError()
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as picture:
                    if (picture.format != {"jpeg":"JPEG", "png":"PNG", "webp":"WEBP"}[match[1]] or
                            picture.width*picture.height > policy.max_image_pixels or getattr(picture, "n_frames", 1) != 1):
                        raise ValueError()
                    picture.load()
            except Exception:
                raise AdmissionError("invalid bounded inline image") from None
    digest = hashlib.sha256(body).hexdigest()
    ledger_id = "chat-" + hashlib.sha256(("echo\0"+consumer_id+"\0"+request_id).encode()).hexdigest()
    return LegacyChatRequest(request_id, ledger_id, "echo", consumer_id, float(deadline), task_class,
                             payload["max_tokens"], payload.get("stream", False), body, digest)


class OpenAIChatBackend(OpenAISnapshotBackend):
    """Actual HTTP JSON/SSE, fixed upstream, independent authorities, no scheduler.

    on_event executes in the bounded worker and must be a bounded sink. A blocked
    sink/authority retains the worker slot even when execute times out.
    """
    def __init__(self, controller, engine_epoch, *, policy, **kwargs):
        super().__init__(controller.manifest, engine_epoch, **kwargs)
        self._controller = controller
        self.policy = policy

    def execute(self, request, deadline, on_event=None):
        if not isinstance(request, LegacyChatRequest) or deadline != request.request_deadline:
            raise BackendUnknown("invalid chat request")
        # Revalidate immutable bytes, including policies and body digest.
        try:
            payload = json.loads(request.body, object_pairs_hook=_object, parse_constant=_constant)
            checked = prepare_chat_request(payload, consumer_id=request.consumer_id, request_id=request.request_id,
                deadline=deadline, manifest=self.manifest, policy=self.policy, task_class=request.task_class)
            if checked != request:
                raise ValueError()
        except Exception:
            raise BackendUnknown("chat request binding invalid") from None
        started, wall = self._mono(), self._clock()
        budget = min(deadline-wall, self.manifest.max_deadline_seconds)
        cancelled = threading.Event()
        def remaining():
            mono, now = self._mono(), self._clock()
            left = min(started+budget-mono, wall+budget-now)
            if (cancelled.is_set() or not math.isfinite(left) or mono < started or now < wall or
                    abs((mono-started)-(now-wall)) > 1 or left <= 0):
                raise BackendDeadline("chat deadline elapsed")
            return left
        remaining()
        if not self._inflight.acquire(False):
            raise BackendBusy("chat worker outstanding")
        outcomes = queue.Queue(1)
        sockets = []
        guard = threading.Lock()
        def register(sock):
            with guard:
                if cancelled.is_set():
                    sock.close()
                    raise BackendDeadline("chat cancelled")
                sockets[:] = [sock]
        def worker():
            sock = None
            response = None
            try:
                self._validate_admission(request)
                bound = self._input_authority.assess(ChatInput(self.manifest.digest, self.engine_epoch,
                    self.manifest.model_revision, request.digest, request.body, request.max_output_tokens,
                    request.ledger_id, request.digest), deadline)
                self._validate_input_bound(bound, request.digest, deadline)
                self._validate_admission(request)
                if not self._attestor_bound():
                    raise BackendUnknown("attestor binding changed")
                sock = self._connect((self._dial_host, self._port), timeout=remaining())
                register(sock)
                if self._tls:
                    sock = self._tls.wrap_socket(sock, server_hostname=self._hostname)
                    register(sock)
                self._validate_admission(request)
                self._validate_input_bound(bound, request.digest, deadline)
                host = f"[{self._hostname}]" if ":" in self._hostname else self._hostname
                head = ("POST /v1/chat/completions HTTP/1.1\r\n" + f"Host: {host}:{self._port}\r\n"
                    + f"X-Request-ID: {request.ledger_id}\r\nContent-Type: application/json\r\n"
                    + "Accept-Encoding: identity\r\nConnection: close\r\n"
                    + (f"Authorization: Bearer {self._bearer}\r\n" if self._bearer else "")
                    + self._dispatch_header(request, request.digest, bound.upper_bound_tokens)
                    + f"Content-Length: {len(request.body)}\r\n\r\n")
                if not self._attestor_bound():
                    raise BackendUnknown("attestor binding changed before upload")
                sock.settimeout(remaining())
                sock.sendall(head.encode("ascii")+request.body)
                response = http.client.HTTPResponse(sock)
                response.begin()
                headers = response.getheaders()
                if (sum(len(k)+len(v) for k,v in headers) > 16384 or
                    any(sum(k.lower()==name for k,v in headers)>1 for name in
                        ("content-length", "transfer-encoding", "content-type", "content-encoding")) or
                    response.getheader("Content-Encoding", "identity") != "identity" or
                    (response.getheader("Transfer-Encoding") is not None and response.getheader("Transfer-Encoding") != "chunked") or
                    (response.getheader("Transfer-Encoding") and response.getheader("Content-Length"))):
                    raise BackendUnknown("invalid response framing")
                if response.getheader("Content-Length") is not None and (response.length is None or response.length > self.policy.max_response_bytes):
                    raise BackendUnknown("invalid response length")
                expected_length = response.length
                content_type = response.getheader("Content-Type", "").split(";",1)[0].strip()
                streaming = request.stream and response.status == 200
                if content_type != ("text/event-stream" if streaming else "application/json") or 300 <= response.status < 400:
                    raise BackendUnknown("unsupported upstream response")
                data = bytearray()
                event = bytearray()
                done = False
                while True:
                    sock.settimeout(remaining())
                    part = response.readline(min(self.policy.max_event_bytes+1, self.policy.max_response_bytes-len(data)+1)) if streaming else response.read1(min(65536, self.policy.max_response_bytes-len(data)+1))
                    remaining()
                    if not part:
                        break
                    data.extend(part)
                    if len(data) > self.policy.max_response_bytes:
                        raise BackendUnknown("response exceeds bound")
                    if streaming:
                        event.extend(part)
                        if len(event) > self.policy.max_event_bytes:
                            raise BackendUnknown("SSE event exceeds bound")
                        if part in (b"\n", b"\r\n"):
                            if done:
                                raise BackendUnknown("SSE after DONE")
                            lines = [line[5:].strip() for line in event.splitlines() if line.startswith(b"data:")]
                            if lines:
                                value = b"\n".join(lines)
                                done = value == b"[DONE]"
                                if not done:
                                    json.loads(value, object_pairs_hook=_object, parse_constant=_constant)
                            if on_event:
                                on_event(bytes(event))
                            event.clear()
                if expected_length is not None and len(data) != expected_length:
                    raise BackendUnknown("truncated response body")
                if streaming:
                    if event or not done:
                        raise BackendUnknown("incomplete SSE")
                else:
                    json.loads(data, object_pairs_hook=_object, parse_constant=_constant)
                if not self._attestor_bound():
                    raise BackendUnknown("attestor binding changed")
                observation = ChatCompletionObservation(self.manifest.digest, self.engine_epoch, request.ledger_id,
                    request.digest, request.digest, hashlib.sha256(data).hexdigest(), response.status, request.stream, deadline)
                evidence = self._attestor.attest(observation, deadline)
                remaining()
                if evidence is None or not self._attestor_bound():
                    raise BackendUnknown("independent terminal evidence unavailable")
                outcome = LegacyChatResult(response.status, content_type, bytes(data), evidence)
            except BackendUnknown as exc:
                outcome = exc
            except Exception:
                outcome = BackendUnknown("chat transport or authority failed")
            finally:
                if response:
                    response.close()
                if sock:
                    sock.close()
                with guard:
                    sockets.clear()
                self._inflight.release()
            outcomes.put(outcome)
        try:
            threading.Thread(target=worker, daemon=True, name="admitted-chat-http").start()
        except Exception:
            self._inflight.release()
            raise BackendUnknown("chat worker unavailable") from None
        try:
            try:
                result = outcomes.get(timeout=remaining())
            except queue.Empty:
                raise BackendDeadline("chat deadline elapsed") from None
            remaining()
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            cancelled.set()
            with guard:
                for sock in sockets:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
