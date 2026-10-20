"""Authenticated metadata binding between a trusted gateway and engine hook.

The gateway issues tokens only AFTER committing its resource reservation. The
engine verifies the exact upstream bytes before durable engine registration.
External request-ID headers convey no authority. Tokens can be replayed: the
engine receipt ledger must enforce its one-shot dispatch decision.
Preparation tokens authorize bounded input preparation only. Every generation
entry point must require purpose='execute' and enforce input_bound_tokens before
dispatch. A valid prepare token never authorizes generation.

Keys come from protected deployment configuration and must never enter logs.
This local deadline check does not establish cross-host clock accuracy; the
deployment clock gate remains mandatory. No token proves engine completion.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import re
import time

MAX_TOKEN_BYTES = 2048
MAX_BODY_BYTES = 16 * 1024 * 1024
_DOMAIN = b"home-shared-dispatch-v2\x00"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_BASE64 = re.compile(r"[A-Za-z0-9_-]+")


class DispatchTokenError(ValueError):
    """Binding rejected; no upstream execution is authorized."""


@dataclass(frozen=True)
class DispatchBinding:
    manifest_digest: str
    engine_epoch: str
    ledger_id: str
    request_digest: str
    upstream_body_sha256: str
    request_deadline: float
    purpose: str
    input_bound_tokens: int | None


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _matched(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(encoded):
    if not _matched(_BASE64, encoded):
        raise DispatchTokenError("invalid dispatch token")
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise DispatchTokenError("invalid dispatch token") from None
    if _encode(raw) != encoded:
        raise DispatchTokenError("invalid dispatch token")
    return raw


def _payload(binding):
    return json.dumps({"version": 2, **binding.__dict__}, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


class DispatchTokenAuthority:
    def __init__(self, manifest_digest, engine_epoch, key, *, max_deadline_seconds, clock=time.time):
        if (not _matched(_DIGEST, manifest_digest) or not _matched(_ID, engine_epoch)
                or type(key) is not bytes or not 32 <= len(key) <= 4096
                or not _number(max_deadline_seconds) or not 0 < max_deadline_seconds <= 600
                or not callable(clock)):
            raise DispatchTokenError("invalid dispatch authority configuration")
        self._manifest_digest = manifest_digest
        self._engine_epoch = engine_epoch
        self._key = key
        self._max_deadline_seconds = float(max_deadline_seconds)
        self._clock = clock

    @property
    def manifest_digest(self):
        return self._manifest_digest

    @property
    def engine_epoch(self):
        return self._engine_epoch

    def _now(self):
        try:
            now = self._clock()
        except Exception:
            raise DispatchTokenError("dispatch clock unavailable") from None
        if not _number(now):
            raise DispatchTokenError("dispatch clock unavailable")
        return float(now)

    def _validate(self, binding, now):
        if (binding.manifest_digest != self._manifest_digest or binding.engine_epoch != self._engine_epoch
                or not _matched(_ID, binding.ledger_id) or not _matched(_DIGEST, binding.request_digest)
                or not _matched(_DIGEST, binding.upstream_body_sha256)
                or type(binding.request_deadline) is not float or not _number(binding.request_deadline)
                or type(binding.purpose) is not str or binding.purpose not in {"prepare", "execute"}
                or (binding.purpose == "prepare" and binding.input_bound_tokens is not None)
                or (binding.purpose == "execute" and (type(binding.input_bound_tokens) is not int
                    or not 1 <= binding.input_bound_tokens <= 1_000_000))
                or not 0 < binding.request_deadline - now <= self._max_deadline_seconds):
            raise DispatchTokenError("dispatch binding rejected")

    def issue(self, ledger_id, request_digest, upstream_body_sha256, request_deadline, *,
              purpose="execute", input_bound_tokens=None):
        """Trusted gateway only, after reservation; never issue from model text.

        Execute requires an explicit positive bound; omitted bounds fail closed.
        Prepare requires None and conveys no model-generation permission.
        """
        if not _number(request_deadline):
            raise DispatchTokenError("dispatch binding rejected")
        binding = DispatchBinding(self._manifest_digest, self._engine_epoch, ledger_id,
                                  request_digest, upstream_body_sha256, float(request_deadline),
                                  purpose, input_bound_tokens)
        before = self._now()
        self._validate(binding, before)
        encoded = _encode(_payload(binding))
        signature = hmac.digest(self._key, _DOMAIN + encoded.encode("ascii"), "sha256")
        token = encoded + "." + _encode(signature)
        after = self._now()
        self._validate(binding, after)
        if after < before or len(token) > MAX_TOKEN_BYTES:
            raise DispatchTokenError("dispatch binding rejected")
        return token

    def verify(self, token, body: bytes):
        """Verify bytes/binding; caller must enforce purpose for its operation."""
        if (type(token) is not str or not 1 <= len(token) <= MAX_TOKEN_BYTES
                or type(body) is not bytes or not 1 <= len(body) <= MAX_BODY_BYTES
                or token.count(".") != 1):
            raise DispatchTokenError("invalid dispatch token")
        before = self._now()
        encoded, supplied = token.split(".")
        if len(supplied) != 43:
            raise DispatchTokenError("invalid dispatch token")
        signature = _decode(supplied)
        # Reject non-ASCII input before attempting the MAC, without echoing it.
        if not _matched(_BASE64, encoded):
            raise DispatchTokenError("invalid dispatch token")
        expected = hmac.digest(self._key, _DOMAIN + encoded.encode("ascii"), "sha256")
        if not hmac.compare_digest(signature, expected):
            raise DispatchTokenError("invalid dispatch token")
        raw = _decode(encoded)
        try:
            obj = json.loads(raw.decode("ascii"))
            if (type(obj) is not dict or set(obj) != {"version", *DispatchBinding.__dataclass_fields__}
                    or type(obj["version"]) is not int or obj.pop("version") != 2):
                raise ValueError
            binding = DispatchBinding(**obj)
            self._validate(binding, before)
            if raw != _payload(binding):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise DispatchTokenError("invalid dispatch token") from None
        if not hmac.compare_digest(hashlib.sha256(body).hexdigest(), binding.upstream_body_sha256):
            raise DispatchTokenError("dispatch body mismatch")
        after = self._now()
        self._validate(binding, after)
        if after < before:
            raise DispatchTokenError("dispatch clock unavailable")
        return binding
