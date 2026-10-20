"""The LA camera pilot's single vLLM engine identity: private/model-pin.json.

One operator-provisioned file names the served engine incarnation (container
name and id, image digest, StartedAt) and the labels the live consumer quotes
for it (engine_epoch, artifact_sha256). The pilot reads it at start, and an
enabled private/live-scene.json must quote the same engine_epoch and
artifact_sha256 or the service refuses to start. camera_pilot_service's
MODEL_REVISION is a frozen ledger label, not part of this identity.

    {"version": 1, "container_name": "vllm", "container_id": "<64 hex>",
     "image": "sha256:<64 hex>", "started_at": "2026-09-27T21:42:03.337370461Z",
     "engine_epoch": "<id>", "artifact_sha256": "<64 hex>", "auto_repin": false}

Any vLLM restart changes StartedAt (restart: unless-stopped, a Docker daemon
restart, an LA reboot). Without "auto_repin": true (absent means false) the
pilot refuses, as before, and logs one "vLLM StartedAt changed" line per
change. With it, a health check that finds the same container name and image
digest with a new container id or StartedAt, and every other health check
passing, accepts the new incarnation: the file is rewritten atomically with a
new engine_epoch and one "engine re-pinned" line is logged. Camera chat then
continues on the new incarnation; the live consumer stays closed
(engine_changed) until live-scene.json, and Victoria's runtime_revision, are
bumped to the new epoch (speed plan section 15 #4).

The rewrite also records the epochs re-pins replaced, newest first, in
"repinned_from" (at most MAX_REPINNED_FROM; absent or [] in an operator's
pin). A live-scene.json that still quotes one of them is that pending bump,
not a misconfiguration: later starts keep camera chat and compose the live
consumer closed (engine_changed). Once the lineage is full it may have
dropped the epoch a long-pending bump quotes, so with a full lineage any
other epoch is treated the same way; the size bound never turns a pending
bump into a refused start. Another artifact_sha256, or (lineage not full) an
epoch no re-pin produced, still refuses the start.

load_read_only_engine() is the pin for a standalone health preflight outside
the running pilot: its re-pin rule is off and it never writes the file.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import threading
from typing import Any, Callable

from .openai_backend import _constant, _object
from .service_config import protected_file

PIN_NAME = "model-pin.json"
PIN_FIELDS = frozenset({"version", "container_name", "container_id", "image", "started_at", "engine_epoch",
                        "artifact_sha256"})
OPTIONAL_FIELDS = frozenset({"auto_repin", "repinned_from"})
MAX_PIN_BYTES = 4096
MAX_REPINNED_FROM = 8        # epochs accepted re-pins replaced, kept newest first
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")  # a Docker container name
_HEX64 = re.compile(r"[a-f0-9]{64}")
_IMAGE = re.compile(r"sha256:[a-f0-9]{64}")
_STARTED = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z")
# Same identifier rule as live-scene.json's engine_epoch (lab_saved_activity._ID).
_EPOCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}")
UNHEALTHY = "pinned model incarnation changed or unhealthy"


class EngineIdentityMismatch(ValueError):
    """An enabled live-scene.json names another engine than model-pin.json."""


@dataclass(frozen=True)
class ModelPin:
    container_name: str
    container_id: str
    image: str
    started_at: str
    engine_epoch: str
    artifact_sha256: str
    auto_repin: bool = False
    repinned_from: tuple[str, ...] = ()   # epochs accepted re-pins replaced, newest first

    @property
    def incarnation(self) -> tuple[str, str]:
        return self.container_id, self.started_at


def _valid(pin: ModelPin) -> ModelPin:
    if (not isinstance(pin.container_name, str) or not _NAME.fullmatch(pin.container_name)
            or not isinstance(pin.container_id, str) or not _HEX64.fullmatch(pin.container_id)
            or not isinstance(pin.image, str) or not _IMAGE.fullmatch(pin.image)
            or not isinstance(pin.started_at, str) or not _STARTED.fullmatch(pin.started_at)
            or not isinstance(pin.engine_epoch, str) or not _EPOCH.fullmatch(pin.engine_epoch)
            or not isinstance(pin.artifact_sha256, str) or not _HEX64.fullmatch(pin.artifact_sha256)
            or type(pin.auto_repin) is not bool
            or type(pin.repinned_from) is not tuple or len(pin.repinned_from) > MAX_REPINNED_FROM
            or not all(isinstance(epoch, str) and _EPOCH.fullmatch(epoch) for epoch in pin.repinned_from)
            or len(set(pin.repinned_from)) != len(pin.repinned_from) or pin.engine_epoch in pin.repinned_from):
        raise ValueError("invalid model pin")
    return pin


def load_model_pin(path: str | Path, *, enforce_linux: bool = False) -> ModelPin:
    """Exact provisioning; a missing, malformed or partial pin is an error."""
    path = protected_file(path, enforce_linux=enforce_linux)
    with path.open("rb") as source:
        raw = source.read(MAX_PIN_BYTES + 1)
    if len(raw) > MAX_PIN_BYTES:
        raise ValueError("model pin exceeds limit")
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(value, dict) or set(value) - OPTIONAL_FIELDS != PIN_FIELDS:
        raise ValueError("model pin fields do not match the versioned schema")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unsupported model pin")
    lineage = value.get("repinned_from", [])
    if not isinstance(lineage, list):
        raise ValueError("invalid model pin")
    return _valid(ModelPin(value["container_name"], value["container_id"], value["image"], value["started_at"],
                           value["engine_epoch"], value["artifact_sha256"], value.get("auto_repin", False),
                           tuple(lineage)))


def write_model_pin(path: str | Path, pin: ModelPin) -> None:
    """Atomic replace beside the old file, keeping its mode; never a partial pin."""
    path = Path(path)
    value = {"version": 1, "container_name": pin.container_name, "container_id": pin.container_id,
             "image": pin.image, "started_at": pin.started_at, "engine_epoch": pin.engine_epoch,
             "artifact_sha256": pin.artifact_sha256, "auto_repin": pin.auto_repin}
    if pin.repinned_from:
        value["repinned_from"] = list(pin.repinned_from)
    encoded = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    _valid(pin)
    if len(encoded) > MAX_PIN_BYTES:   # never a pin the next start cannot load
        raise ValueError("model pin exceeds limit")
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(prefix=".model-pin.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    if hasattr(os, "O_DIRECTORY"):  # the rename itself is durable on POSIX
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def repinned_epoch(container_name: str, container_id: str, started_at: str) -> str:
    """A new engine_epoch for an accepted re-pin, unique to that incarnation."""
    return "%s-%s-%s" % (container_name, container_id[:12], started_at.replace("-", "").replace(":", ""))


def check_live_identity(pin: ModelPin, config: Any) -> bool:
    """True when the live config quotes this pin. False when it quotes the
    pin's artifact and an epoch an accepted re-pin replaced: the bump of
    live-scene.json is still pending, so the live consumer starts closed.
    A full lineage may have dropped that epoch (more than MAX_REPINNED_FROM
    re-pins before the bump), so with one any epoch of the pin's artifact
    starts closed too: camera chat never depends on the lineage's size.
    Otherwise raise EngineIdentityMismatch (the service refuses to start)."""
    mismatched = ["%s=%s (model-pin.json %s)" % (name, getattr(config, name), getattr(pin, name))
                  for name in ("engine_epoch", "artifact_sha256") if getattr(config, name) != getattr(pin, name)]
    if not mismatched:
        return True
    if config.artifact_sha256 == pin.artifact_sha256 and (config.engine_epoch in pin.repinned_from
                                                          or len(pin.repinned_from) >= MAX_REPINNED_FROM):
        return False
    raise EngineIdentityMismatch("live-scene.json " + ", ".join(mismatched) + " does not match model-pin.json")


def _label(incarnation: tuple[Any, Any]) -> str:
    return "%s@%s" % (str(incarnation[0])[:12], incarnation[1])


class PinnedEngine:
    """The pinned incarnation in memory, its health check and the re-pin rule.

    `provisioned` is the pin as read at start (the live identity cross-check
    uses it, with its repinned_from lineage); `pin` is current and moves only
    on an accepted re-pin.
    """

    def __init__(self, path: str | Path, pin: ModelPin, *, log: Callable[[str], None] | None = None,
                 write: Callable[[Path, ModelPin], None] = write_model_pin):
        self.path, self.provisioned, self._pin = Path(path), _valid(pin), pin
        self._log = log or (lambda line: print(line, file=sys.stderr, flush=True))
        self._write = write
        self._lock = threading.Lock()
        self._refused: tuple[str, str] | None = None

    @property
    def pin(self) -> ModelPin:
        return self._pin

    @property
    def container_name(self) -> str:
        return self._pin.container_name

    @property
    def container_id(self) -> str:
        return self._pin.container_id

    @property
    def engine_epoch(self) -> str:
        return self._pin.engine_epoch

    def check(self, value: Any, others: Callable[[], None]) -> None:
        """Raise unless `value` (one `docker inspect` object) is the pinned
        incarnation, or an acceptable re-pin of it; others() runs every other
        health check and must pass before a re-pin is accepted."""
        pin = self._pin
        state = value["State"]
        seen = (value["Id"], state["StartedAt"])
        healthy = (value["Name"] == "/" + pin.container_name and value["Image"] == pin.image
                   and bool(state["Running"]) and state.get("Health", {}).get("Status") == "healthy")
        if seen == pin.incarnation:
            if not healthy:
                raise ValueError(UNHEALTHY)
            others()
            return
        try:
            # Only the same container name and image digest, healthy, and with
            # every other check passing, is the same engine restarted.
            if not healthy or not pin.auto_repin:
                raise ValueError(UNHEALTHY)
            others()
            self._repin(pin, seen)
        except Exception:
            self._refusing(pin, seen)
            raise

    def _repin(self, base: ModelPin, seen: tuple[str, str]) -> None:
        with self._lock:
            current = self._pin
            if current.incarnation == seen:
                return  # another worker accepted the same incarnation first
            if current != base:
                raise ValueError("model pin changed during the health check")
            epoch = repinned_epoch(current.container_name, *seen)
            # The replaced epoch joins the lineage a pending live-scene.json
            # bump may still quote (newest first, bounded).
            lineage = tuple(old for old in (current.engine_epoch,) + current.repinned_from if old != epoch)
            new = _valid(replace(current, container_id=seen[0], started_at=seen[1], engine_epoch=epoch,
                                 repinned_from=lineage[:MAX_REPINNED_FROM]))
            self._write(self.path, new)
            self._pin, self._refused = new, None
        self._log("engine re-pinned old=%s new=%s engine_epoch=%s" % (
            _label(current.incarnation), _label(seen), new.engine_epoch))

    def _refusing(self, pin: ModelPin, seen: tuple[str, str]) -> None:
        with self._lock:
            if self._refused == seen:
                return
            self._refused = seen
        self._log("vLLM StartedAt changed; pilot refusing old=%s new=%s" % (_label(pin.incarnation), _label(seen)))


def load_engine(path: str | Path, *, enforce_linux: bool = False,
                log: Callable[[str], None] | None = None) -> PinnedEngine:
    return PinnedEngine(path, load_model_pin(path, enforce_linux=enforce_linux), log=log)


def _read_only(path: Path, pin: ModelPin) -> None:
    raise ValueError("model pin is read-only outside the running pilot")


def load_read_only_engine(path: str | Path, *, enforce_linux: bool = False,
                          log: Callable[[str], None] | None = None) -> PinnedEngine:
    """The pin as provisioned for a standalone preflight (a deploy or recovery
    helper), never the running pilot: auto_repin is off, so a changed
    incarnation fails the check as it did before model-pin.json, and the file
    is never rewritten. Only the pilot's own engine re-pins."""
    pin = load_model_pin(path, enforce_linux=enforce_linux)
    return PinnedEngine(path, replace(pin, auto_repin=False), log=log, write=_read_only)
