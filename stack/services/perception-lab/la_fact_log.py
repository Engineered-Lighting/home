"""la-fact-log-1: the always-on LA perceiver's words-only fact log.

Speed plan section 15 (#11): the always-on Qwen perceiver (la_live_perceiver)
runs outside Lab recordings, so its LA facts go to this log instead of a
recording's ledger. It is the perceiver pump's ledger: FactLog.terminal()
receives every LA terminal outcome of a sent request (qwen_live._receipt and
_outcome) and writes one line for it.

Format, exactly as plan section 15 defines it. One JSON object per line, keys
sorted, compact separators, ASCII, no NaN:
- schema: "la-fact-log-1";
- source_id: "la_qwen_den_oak";
- request_id and session_id: the LA request id (64 hex) and the perceiver's
  synthetic per-day session id (32 hex);
- observed_at (the newest frame of the request), sent_at and available_at:
  ISO-8601 UTC with milliseconds and a "Z" suffix, on the Jetson clock. They
  are written as whole milliseconds (floored) and kept in the order
  observed_at <= sent_at <= available_at (a wall-clock step back is the only
  way the raw times could disagree; the later time is then raised to the
  earlier);
- latency_s: available_at - observed_at, in seconds, from those milliseconds;
- engine_epoch, prompt_version and outcome: the engine epoch of the answer
  (else the one the request was sent under), the request's prompt version and
  LA's terminal outcome word (completed, failed, rejected, cancelled,
  queue_expired, not_admitted or unknown_resolved);
- observation: for a completed request, the qwen-observation-1 receipt object
  (qwen_scene.build_observation plus first_receipt: identifiers, times,
  digests, status and the closed-vocabulary facts, words only); null for an
  outcome without an answer.
Nothing else is written: no image, no base64, no reply text or other free
text beyond what qwen-observation-1 already carries. A receipt whose key set
differs from qwen-observation-1, or that exceeds MAX_RECEIPT_BYTES, is refused.

Files: one per UTC day of available_at, la-fact-log-1-YYYY-MM-DD.jsonl, in a
private (owner-only) directory, created 0600. Appends are buffered: each line
is one write that is flushed to the OS at once (a controller crash loses
nothing written), and the file is fsynced at most every fsync_seconds (default
10 s, so a power loss loses at most that much), at a day change and on close,
never once per line. A torn last line left by a power loss is cut off when the
file is reopened. Retention: a day file is deleted once it is retention_days
(default 14) days old, checked when the log opens and once per UTC day; only
files named exactly like this log's are ever touched. A day file stops growing
at max_day_bytes (default 64 MiB): later lines that day are refused and
counted, never written elsewhere.

Writable, or no inference: check_writable() (the perceiver's build, at
controller start) opens today's file and refuses a read-only file system, so a
directory the controller unit cannot write (outside its ReadWritePaths) fails
the start instead of dropping every fact. At run time, writable() is false
after MAX_WRITE_FAILURES failed writes in a row (until RETRY_SECONDS after the
last, when one more request may try) and while today's file is full; the
perceiver then neither captures nor sends. A failed write closes the file (the
next one reopens it) and keeps a content-free failure label (last_failure).
"""
import errno
from copy import deepcopy
import json
import logging
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from collections import deque
from datetime import date, datetime, timezone

import qwen_scene as scene

_log = logging.getLogger("perception_lab.la_live")

SCHEMA = "la-fact-log-1"
SOURCE_ID = "la_qwen_den_oak"
KEYS = ("available_at", "engine_epoch", "latency_s", "observation", "observed_at", "outcome", "prompt_version",
        "request_id", "schema", "sent_at", "session_id", "source_id")
# qwen_scene.build_observation's draft keys plus first_receipt's.
RECEIPT_KEYS = frozenset({"schema", "result_schema", "site_id", "camera_id", "session_id", "request_id", "frame_ids",
                          "media_ids", "capture_epoch", "window_start", "observed_at", "generated_at", "valid_until",
                          "original_valid_until", "max_age_seconds", "prompt_version", "context_digest", "body_sha256",
                          "response_sha256", "engine_epoch", "artifact_sha256", "status", "invalid_reason", "facts",
                          "prior_supplied", "origin", "evidence_roots", "text_scope", "available_at",
                          "first_received_at", "fingerprint"})
MAX_RECEIPT_BYTES = 8192
DEFAULTS = {"retention_days": 14, "fsync_seconds": 10.0, "max_day_bytes": 64*1024*1024}
BOUNDS = {"retention_days": (1, 30), "fsync_seconds": (1, 60), "max_day_bytes": (1024*1024, 512*1024*1024)}
FILE = re.compile(r"la-fact-log-1-(\d{4})-(\d{2})-(\d{2})\.jsonl")
# The identifier and outcome patterns the la-fact-log-1 readers accept
# (tools/convert-legacy-den-context.js FACT_ID; outcome words).
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}")
_OUTCOME = re.compile(r"[a-z0-9_]{1,64}")
_REQUEST = re.compile(r"[a-f0-9]{64}")
_SESSION = re.compile(r"[a-f0-9]{32}")
# Request ids already written this run: one line per request.
RECENT_IDS = 512
# Failed writes in a row after which writable() is false, and how long until
# one more request may try.
MAX_WRITE_FAILURES = 3
RETRY_SECONDS = 60


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def failure_label(error):
    """Content-free failure label (no path, no text): the errno name of an
    OSError, else the exception type."""
    code = getattr(error, "errno", None)
    if isinstance(error, OSError) and isinstance(code, int) and code in errno.errorcode:
        return errno.errorcode[code]
    return type(error).__name__


def settings(value=None):
    """Exact optional fact-log settings with bounds; absent keys take DEFAULTS."""
    value = dict(value or {})
    if set(value) - set(DEFAULTS):
        raise ValueError("unknown fact log setting")
    config = {**DEFAULTS, **value}
    for key, (low, high) in BOUNDS.items():
        number = config[key]
        if key in ("retention_days", "max_day_bytes") and type(number) is not int:
            raise ValueError(f"fact log {key} must be an integer")
        if not _finite(number) or not low <= number <= high:
            raise ValueError(f"fact log {key} outside its bound")
    return config


def milliseconds(epoch):
    if not _finite(epoch) or epoch <= 0:
        raise ValueError("finite epoch time required")
    return math.floor(epoch * 1000)


def iso(ms):
    """ISO-8601 UTC with milliseconds and 'Z' from whole milliseconds."""
    seconds, millis = divmod(ms, 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + ".%03dZ" % millis


def day_of(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms // 1000))


def line(*, request_id, session_id, observed_at, sent_at, available_at, engine_epoch, prompt_version, outcome,
         observation):
    """(one la-fact-log-1 line without its newline, available_at in whole
    milliseconds); refuses anything outside the format."""
    if not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id):
        raise ValueError("LA request id required")
    if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
        raise ValueError("synthetic session id required")
    for value in (engine_epoch, prompt_version):
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError("identifier required")
    if not isinstance(outcome, str) or not _OUTCOME.fullmatch(outcome):
        raise ValueError("outcome word required")
    if observation is not None:
        if (not isinstance(observation, dict) or set(observation) != RECEIPT_KEYS
                or observation.get("schema") != scene.RECEIPT_SCHEMA or observation.get("request_id") != request_id
                or observation.get("session_id") != session_id or outcome != "completed"):
            raise ValueError("qwen-observation-1 receipt required")
        if len(json.dumps(observation, sort_keys=True, separators=(",", ":"), allow_nan=False)) > MAX_RECEIPT_BYTES:
            raise ValueError("receipt exceeds bound")
    observed = milliseconds(observed_at)
    sent = max(milliseconds(sent_at), observed)
    available = max(milliseconds(available_at), sent)
    value = {"schema": SCHEMA, "source_id": SOURCE_ID, "request_id": request_id, "session_id": session_id,
             "observed_at": iso(observed), "sent_at": iso(sent), "available_at": iso(available),
             "latency_s": (available - observed) / 1000, "engine_epoch": engine_epoch,
             "prompt_version": prompt_version, "outcome": outcome, "observation": observation}
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False), available


def private_directory(path):
    """The fact log directory: absolute, a real directory owned by this user
    with no group/other access, under parents nobody else can write."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("absolute fact log directory required")
    if not hasattr(os, "geteuid"):
        raise ValueError("owner-only fact log checks require POSIX ownership")
    for parent in (*reversed(path.parents), path):
        info = os.lstat(parent)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            raise ValueError("protected fact log directory required")
    info = os.lstat(path)
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("owner-only fact log directory required")
    return path


class FactLog:
    """Writer of one la-fact-log-1 directory (see the module docstring)."""

    def __init__(self, directory, *, retention_days=DEFAULTS["retention_days"],
                 fsync_seconds=DEFAULTS["fsync_seconds"], max_day_bytes=DEFAULTS["max_day_bytes"],
                 clock=time.time, monotonic=time.monotonic, private=True):
        config = settings({"retention_days": retention_days, "fsync_seconds": fsync_seconds,
                           "max_day_bytes": max_day_bytes})
        self.directory = private_directory(directory) if private else Path(directory)
        if not self.directory.is_absolute() or not self.directory.is_dir():
            raise ValueError("fact log directory required")
        self.retention_days, self.fsync_seconds = config["retention_days"], config["fsync_seconds"]
        self.max_day_bytes = config["max_day_bytes"]
        self.private = private
        self.clock, self.monotonic = clock, monotonic
        self.lock = threading.Lock()
        self.file, self.day, self.size = None, None, 0
        self.dirty = False
        self.last_sync = monotonic()
        self.pruned_day = None
        self.full_day = None
        self.recent, self.recent_ids = deque(), set()
        self.closed = False
        self.failures, self.failed_at, self.last_failure = 0, None, None
        self.counts = {"written": 0, "duplicate": 0, "refused_full": 0, "failed": 0, "fsync": 0, "pruned": 0}
        self.receipt_subscribers = set()

    def subscribe_receipts(self, callback):
        """Observe new successfully written receipts in memory, without replay.

        Observers run outside the file lock and cannot change pump admission or
        stored receipt identity. No observer starts acquisition or reads a log.
        """
        if not callable(callback):
            raise ValueError("receipt callback required")
        with self.lock:
            if self.closed or len(self.receipt_subscribers) >= 8 and callback not in self.receipt_subscribers:
                raise ValueError("bounded active receipt subscribers required")
            self.receipt_subscribers.add(callback)

    def unsubscribe_receipts(self, callback):
        with self.lock:
            self.receipt_subscribers.discard(callback)

    def check_writable(self):
        """At controller start: open (creating if needed) today's day file now
        and refuse a read-only file system (see the module docstring)."""
        try:
            statvfs = getattr(os, "statvfs", None)
            if statvfs is not None and statvfs(self.directory).f_flag & getattr(os, "ST_RDONLY", 1):
                raise OSError(errno.EROFS, "read-only file system")
            with self.lock:
                if self.closed:
                    raise ValueError("fact log closed")
                self._rotate(self._today())
        except Exception as error:
            raise ValueError("writable fact log directory required (%s); it must be inside the controller "
                             "unit's ReadWritePaths" % failure_label(error)) from None

    def writable(self):
        """Whether another request's answer could be recorded: false once
        closed, while today's file is full, and after MAX_WRITE_FAILURES failed
        writes in a row until RETRY_SECONDS after the last."""
        try:
            today = self._today()
        except Exception:
            return False
        with self.lock:
            if self.closed or self.full_day == today:
                return False
            return self.failures < MAX_WRITE_FAILURES or self.monotonic() - self.failed_at >= RETRY_SECONDS

    def _today(self):
        return day_of(milliseconds(self.clock()))

    # --- the pump's ledger ---------------------------------------------
    def terminal(self, request, outcome, draft):
        """Write one terminal outcome of a sent request; returns the pump's
        receipt state (available, expired_on_receipt, duplicate or log_full).
        draft: the completed request's qwen.observation draft, else None."""
        if self.closed:
            raise ValueError("fact log closed")
        now = self.clock()
        reference, frames = request["reference"], request["frames"]
        receipt = scene.first_receipt(draft, now) if draft is not None else None
        epoch = draft.get("engine_epoch") if draft is not None else None
        if not isinstance(epoch, str):
            epoch = request["profile"]["engine_epoch"]
        text, available = line(request_id=reference["request_id"], session_id=reference["session_id"],
                               observed_at=frames[-1]["observed_at"], sent_at=request["sent_at"], available_at=now,
                               engine_epoch=epoch, prompt_version=reference["prompt_version"], outcome=outcome,
                               observation=receipt)
        state = self._append(reference["request_id"], text, available)
        if state == "available" and receipt is not None:
            with self.lock:
                observers = tuple(self.receipt_subscribers)
            # The pump request also contains an image-bearing envelope. Observers
            # receive only this closed metadata projection, never that envelope.
            try:
                metadata = {
                    "reference": {key: reference[key] for key in
                                  ("request_id", "session_id", "site_id", "body_sha256", "prompt_version")},
                    "frames": [{key: frame[key] for key in
                                ("media_id", "frame_id", "capture_epoch", "observed_at")} for frame in frames],
                    "sent_at": request["sent_at"], "profile": {"engine_epoch": epoch},
                } if observers else None
            except Exception:
                metadata = None  # Do not tighten the historical log's input API.
            for callback in observers:
                try:
                    if metadata is not None:
                        callback(deepcopy(metadata), deepcopy(receipt))
                except Exception:
                    pass  # Optional consumers cannot disrupt the relied-on pump.
        if state == "available" and receipt is not None and receipt["valid_until"] <= now:
            return {"state": "expired_on_receipt", "event_id": None}
        return {"state": state, "event_id": None}

    # --- files ---------------------------------------------------------
    def _append(self, request_id, text, available_ms):
        data = (text + "\n").encode("ascii")
        day = day_of(available_ms)
        with self.lock:
            if self.closed:
                raise ValueError("fact log closed")
            if request_id in self.recent_ids:
                self.counts["duplicate"] += 1
                return "duplicate"
            try:
                self._rotate(day)
                if self.size + len(data) > self.max_day_bytes:
                    self.counts["refused_full"] += 1
                    if self.full_day != day:
                        self.full_day = day
                        _log.warning("la-fact-log full for %s: further lines today are refused and the LA perceiver "
                                 "captures and sends nothing until the next UTC day (max_day_bytes=%d)",
                                 day, self.max_day_bytes)
                    return "log_full"
                self.file.write(data)
                self.file.flush()
            except Exception as error:
                self.counts["failed"] += 1
                self.failures, self.failed_at = self.failures + 1, self.monotonic()
                self.last_failure = failure_label(error)
                self._drop_file()
                raise
            self.size += len(data)
            self.dirty = True
            self.failures = 0
            self.counts["written"] += 1
            self.recent.append(request_id)
            self.recent_ids.add(request_id)
            while len(self.recent) > RECENT_IDS:
                self.recent_ids.discard(self.recent.popleft())
            self._sync(due_only=True)
            return "available"

    def _rotate(self, day):
        """The open file is the given day's (caller holds the lock)."""
        if self.file is not None and self.day == day:
            return
        self._close_file()
        self._prune(day)
        self.file, self.size = self._open(day)
        self.day = day

    def _open(self, day):
        path = self.directory / f"{SCHEMA}-{day}.jsonl"
        created = not os.path.lexists(path)
        flags = (os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_BINARY", 0))
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("regular fact log file required")
            if self.private and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise ValueError("owner-only fact log file required")
            size = self._repair(fd, info.st_size)
            file = os.fdopen(fd, "ab")
        except Exception:
            os.close(fd)
            raise
        if created:
            self._sync_directory()
        return file, size

    @staticmethod
    def _repair(fd, size):
        """Cut a torn last line (no trailing newline) off; returns the size."""
        if size == 0:
            return 0
        os.lseek(fd, size - 1, os.SEEK_SET)
        if os.read(fd, 1) == b"\n":
            return size
        end = size
        while end > 0:
            start = max(0, end - 4096)
            os.lseek(fd, start, os.SEEK_SET)
            chunk = os.read(fd, end - start)
            cut = chunk.rfind(b"\n")
            if cut >= 0:
                keep = start + cut + 1
                break
            end = start
        else:
            keep = 0
        os.ftruncate(fd, keep)
        _log.warning("la-fact-log: cut a torn last line (%d bytes) on reopen", size - keep)
        return keep

    def _sync_directory(self):
        if not hasattr(os, "O_DIRECTORY"):
            return
        try:
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _sync(self, *, due_only):
        """fsync the open file when it has unsynced lines (and, with due_only,
        only once fsync_seconds have passed since the last one)."""
        if self.file is None or not self.dirty:
            return
        now = self.monotonic()
        if due_only and now - self.last_sync < self.fsync_seconds:
            return
        self.file.flush()
        os.fsync(self.file.fileno())
        self.dirty, self.last_sync = False, now
        self.counts["fsync"] += 1

    def _close_file(self):
        if self.file is None:
            return
        try:
            self._sync(due_only=False)
        finally:
            self.file.close()
            self.file, self.day, self.size = None, None, 0

    def _drop_file(self):
        """After a failed write: close without raising; the next write
        reopens the day file (caller holds the lock)."""
        try:
            self._close_file()
        except Exception:
            pass
        self.file, self.day, self.size, self.dirty = None, None, 0, False

    def _prune(self, today):
        """Delete this log's day files retention_days old or older (once a day)."""
        if self.pruned_day == today:
            return
        self.pruned_day = today
        current = date.fromisoformat(today)
        with os.scandir(self.directory) as entries:
            for entry in entries:
                match = FILE.fullmatch(entry.name)
                if match is None or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    day = date(*(int(part) for part in match.groups()))
                except ValueError:
                    continue
                if (current - day).days >= self.retention_days:
                    os.unlink(entry.path)
                    self.counts["pruned"] += 1

    # --- maintenance ---------------------------------------------------
    def maintain(self):
        """Called after every pump tick: the periodic fsync, and the daily
        prune once the UTC day has changed. Never raises."""
        try:
            today = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")
            with self.lock:
                if self.closed:
                    return
                self._sync(due_only=True)
                self._prune(today)
        except Exception:
            with self.lock:
                self.counts["failed"] += 1

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.receipt_subscribers.clear()
            self._close_file()

    def snapshot(self):
        with self.lock:
            return {"day": self.day, "bytes": self.size, "counts": dict(self.counts),
                    "failures_in_a_row": self.failures, "last_failure": self.last_failure}
