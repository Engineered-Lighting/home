"""Long-lived model transport for the LA camera pilot (speed plan S1.1).

One `docker exec -i <pinned container> python3 -I -S -u -c WORKER` per consumer
(camera chat, live scene) replaces the fresh `docker exec` interpreter per
request. WORKER is fixed text. It reads length-prefixed JSON frames on stdin
and, for each frame, opens a FRESH http.client connection to 127.0.0.1:8000,
makes one fixed call and closes it: camera requests are at least 30 s apart,
longer than vLLM's idle keep-alive, so a pooled socket would go stale and a
post-send failure is indeterminate. There is still no published model port,
shell, dynamic command or retry, and a chat frame forwards its body bytes
exactly.

Frames are a 4-byte big-endian length, then that many bytes of ASCII JSON:
  worker -> host once at start: {"ready": 1}
  host -> worker: {"op": "chat", "body": <text>, "timeout": <s>, "limit": <bytes>}
                  {"op": "metrics", "timeout": <s>}   (vLLM running/waiting)
  worker -> host: {"ok": 1, "body": <text>} | {"ok": 1, "running": n, "waiting": n}
                  | {"ok": 0, "error": <exception class name>}

Delivery boundary: a request is "sent" once its frame is completely written to
the worker's stdin, because the worker acts only on a complete frame. Before
that, WorkerNotSent proves nothing reached the model (no worker could be
started or it never said ready, the frame write did not complete, the worker
is busy). After it:
- WorkerFailed: an error frame; the worker's own model connection ended
  without a usable answer (the evidence today's transport process gave by
  exiting non-zero);
- WorkerExited: the worker exited before replying (today's `docker exec`
  exiting);
- WorkerUnproven: no valid reply by the deadline; the worker is killed and
  the outcome is unknown (today's `docker exec` timeout).
Each consumer maps these exactly as it mapped the per-request transport. No
request frame is ever written twice.

Watchdog: a worker that died, was killed, failed or names another container
is discarded between requests; the next request starts a new one, and waits
for its ready frame, before its own frame is written. A start first runs the
caller's before_start engine check (a fresh health verdict), because a vLLM
restart is what usually kills a worker. One pump thread per running worker
owns the pipes; it idles on a queue (no polling).

Linger (speed plan S1.8, live consumer only; camera chat never lingers): with
chat(linger=s), a request whose frame was written but got no reply by the
deadline is still WorkerUnproven, but the worker is kept (busy) for up to s
more seconds instead of being killed at once, and the exception carries a
Linger. Linger.wait() is True only when the exchange is then proven ended by a
reply frame, ok or error (the worker's own model call returned and its
connection closed). A worker exit with any status (the docker CLI's status may
be its own, e.g. after losing dockerd, while the exec'd call still runs), our
own kill, or silence until the bound proves nothing. Either way the worker is
then discarded.

Cold start (speed plan S1.4 yield): a worker start that one exchange cannot
wait out (a metrics exchange waits only while its own call timeout still fits)
is kept, within its own START_SECONDS bound, and the next exchange adopts it
once ready; that exchange is WorkerNotSent ("worker still starting"). A start
that fails or passes its bound is stopped, as before.
"""
from __future__ import annotations

import json
import queue
import re
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from .openai_backend import _constant, _object

WORKER = r'''
import sys,json,struct,http.client
read,write=sys.stdin.buffer.read,sys.stdout.buffer.write
def take(size):
 data=b''
 while len(data)<size:
  chunk=read(size-len(data))
  if not chunk:raise SystemExit(0)
  data+=chunk
 return data
def send(value):
 data=json.dumps(value).encode()
 data=struct.pack('>I',len(data))+data
 while data:data=data[write(data):]
 sys.stdout.buffer.flush()
def call(method,path,body,timeout,limit):
 connection=http.client.HTTPConnection('127.0.0.1',8000,timeout=timeout)
 try:
  connection.request(method,path,body=body,headers={'Content-Type':'application/json'} if body is not None else {})
  response=connection.getresponse()
  raw=response.read(limit+1)
  if response.status!=200 or len(raw)>limit:raise ValueError('model response unavailable')
  return raw.decode()
 finally:connection.close()
def gauge(text,name):
 total=None
 for line in text.splitlines():
  if line.startswith(name+'{') or line.startswith(name+' '):total=(total or 0.0)+float(line.rsplit(' ',1)[1])
 if total is None:raise ValueError('metric unavailable')
 return total
send({'ready':1})
while 1:
 size=struct.unpack('>I',take(4))[0]
 if size>8388608:raise SystemExit(2)
 value=json.loads(take(size).decode())
 try:
  timeout=value['timeout']
  if type(timeout) not in (int,float) or not 0<timeout<=120:raise ValueError('timeout')
  if value['op']=='chat':
   limit=value['limit']
   if type(limit) is not int or not 0<limit<=131072:raise ValueError('limit')
   result={'ok':1,'body':call('POST','/v1/chat/completions',value['body'].encode(),timeout,limit)}
  elif value['op']=='metrics':
   text=call('GET','/metrics',None,timeout,4194304)
   result={'ok':1,'running':gauge(text,'vllm:num_requests_running'),'waiting':gauge(text,'vllm:num_requests_waiting')}
  else:raise ValueError('operation')
 except Exception as error:result={'ok':0,'error':type(error).__name__}
 send(result)
'''

MAX_REQUEST_FRAME = 8 * 1024 * 1024
MAX_REPLY_FRAME = 1024 * 1024
MAX_CHAT_LIMIT = 131072
START_SECONDS = 10.0     # bound on a worker start, ready frame included
SETTLE_SECONDS = 2.0     # after a kill, how long to wait to learn whether the frame was written
MAX_LINGER_SECONDS = 120.0   # bound on chat(linger=...), the worker's own per-call timeout cap
_CONTAINER = re.compile(r"[a-f0-9]{64}")


class ModelWorkerError(RuntimeError):
    """Base class; none of these is a ValueError (the live consumer maps
    ValueError to a terminal invalid completion)."""


class WorkerNotSent(ModelWorkerError):
    """Proven: no complete request frame reached the worker. engine_check is
    True when the caller's before_start engine check refused a worker start
    (its exception is the __cause__); the type name stays WorkerNotSent."""

    engine_check = False


class WorkerFailed(ModelWorkerError):
    """Error frame: the worker's model connection ended without an answer."""


class WorkerExited(ModelWorkerError):
    """The worker exited after the request frame, before replying."""


class WorkerUnproven(ModelWorkerError):
    """No valid reply by the deadline; the worker was killed; outcome unknown.

    ended is None, or (chat with linger) the Linger of an exchange whose frame
    was written and whose worker was kept to learn whether it ends."""

    def __init__(self, message: str, ended: "Linger | None" = None):
        super().__init__(message)
        self.ended = ended


def docker_command(container: str) -> list[str]:
    """The fixed argv: the pinned container, the fixed WORKER text, no shell."""
    return ["docker", "exec", "-i", container, "python3", "-I", "-S", "-u", "-c", WORKER]


def encode_frame(value: Any, limit: int = MAX_REQUEST_FRAME) -> bytes:
    data = json.dumps(value, allow_nan=False).encode("ascii")
    if len(data) > limit:
        raise ValueError("frame exceeds limit")
    return struct.pack(">I", len(data)) + data


def write_all(stream: Any, data: bytes) -> None:
    """Every byte or an exception; a raw pipe may take a write in parts."""
    view = memoryview(data)
    while view:
        written = stream.write(view)
        if not written:
            raise OSError("worker pipe accepted no bytes")
        view = view[written:]
    flush = getattr(stream, "flush", None)
    if flush is not None:
        flush()


def read_exact(stream: Any, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError("worker output ended")
        data += chunk
    return data


def read_frame(stream: Any, limit: int = MAX_REPLY_FRAME) -> Any:
    size = struct.unpack(">I", read_exact(stream, 4))[0]
    if size > limit:
        raise ValueError("frame exceeds limit")
    return json.loads(read_exact(stream, size).decode("ascii"), object_pairs_hook=_object, parse_constant=_constant)


class _Job:
    __slots__ = ("frame", "taken", "written", "reply", "error", "done")

    def __init__(self, frame: bytes):
        self.frame, self.taken, self.written, self.reply, self.error = frame, False, False, None, None
        self.done = threading.Event()


class _Running:
    """One spawned worker process and the pump thread that owns its pipes."""

    def __init__(self, process: Any, container: str, name: str):
        self.process, self.container = process, container
        self.ready, self.started, self.failed = threading.Event(), False, False
        # killed: stop() ran (its EOF proves nothing); linger_until: monotonic
        # bound of a lingering exchange, which keeps this worker busy;
        # start_until: monotonic bound of a start not yet seen ready by any
        # exchange (None once it is in use).
        self.killed, self.linger_until, self.start_until = False, None, None
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        self.thread = threading.Thread(target=self._pump, name="model-worker-" + name, daemon=True)

    def alive(self) -> bool:
        return self.started and not self.failed and self.process.poll() is None

    def _pump(self) -> None:
        try:
            try:
                hello = read_frame(self.process.stdout)
            except BaseException:
                hello = None
            self.started = hello == {"ready": 1}
            self.failed = not self.started
            self.ready.set()
            if not self.started:
                return
            while True:
                job = self._jobs.get()
                if job is None:
                    return
                job.taken = True
                try:
                    write_all(self.process.stdin, job.frame)
                    job.written = True   # complete: from here the worker may act on it
                    job.reply = read_frame(self.process.stdout)
                except BaseException as error:
                    job.error, self.failed = error, True
                    job.done.set()
                    return
                job.done.set()
        finally:
            self.ready.set()
            self._close_pipes()   # only this thread reads or writes them

    def _close_pipes(self) -> None:
        for stream in (self.process.stdin, self.process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    def submit(self, frame: bytes) -> _Job:
        job = _Job(frame)
        self._jobs.put(job)
        return job

    def stop(self) -> None:
        """Kill the process; the pump then sees EOF or a broken pipe, or the
        sentinel when idle, and exits closing the pipes."""
        self.failed = self.killed = True
        self._jobs.put(None)
        for action in (self.process.kill, lambda: self.process.wait(timeout=SETTLE_SECONDS)):
            try:
                action()
            except Exception:
                pass
        if self.thread.ident is None:   # never started: nothing else owns the pipes
            self._close_pipes()


class ModelWorker:
    """One consumer's long-lived worker: one request at a time, never retried.

    command(container) -> argv (docker_command in production); spawn is
    subprocess.Popen. clock is monotonic.
    """

    def __init__(self, name: str, *, command: Callable[[str], list] = docker_command,
                 spawn: Callable[..., Any] = subprocess.Popen, clock: Callable[[], float] = time.monotonic,
                 start_seconds: float = START_SECONDS, log: Callable[[str], None] | None = None):
        self.name, self._command, self._spawn, self._clock = name, command, spawn, clock
        self._start_seconds = float(start_seconds)
        self._log = log or (lambda line: print(line, file=sys.stderr, flush=True))
        self._lock = threading.Lock()
        self._running: _Running | None = None
        self.starts = 0

    # --- requests ------------------------------------------------------
    # before_start, when given, runs (under this worker's lock) only before a
    # new worker process is started; it must raise unless the engine is still
    # the pinned one, so a worker never execs into an incarnation that no check
    # has seen (a vLLM restart kills the old worker). Raising is WorkerNotSent.
    def chat(self, container: str, body: str, *, timeout: float, limit: int, seconds: float,
             before_start: Callable[[], None] | None = None, linger: float = 0) -> str:
        """POST body to /v1/chat/completions; the raw 200 response text.

        linger 0 (camera chat) kills a worker with no reply at the deadline.
        linger > 0 (the live consumer, S1.8) keeps it up to that much longer;
        the WorkerUnproven then carries a Linger (see the module text)."""
        if (not isinstance(body, str) or type(limit) is not int or not 0 < limit <= MAX_CHAT_LIMIT
                or type(linger) not in (int, float) or not 0 <= linger <= MAX_LINGER_SECONDS):
            raise WorkerNotSent("invalid chat request")

        def accept(reply: Any) -> Any:
            if (isinstance(reply, dict) and set(reply) == {"ok", "body"} and reply["ok"] == 1
                    and isinstance(reply["body"], str) and len(reply["body"].encode()) <= limit):
                return reply["body"]
            return _failed(reply)
        return self._exchange(container, {"op": "chat", "body": body, "timeout": timeout, "limit": limit},
                              seconds, accept, before_start, linger)

    def metrics(self, container: str, *, timeout: float = 1.0, seconds: float = 2.0,
                before_start: Callable[[], None] | None = None) -> dict:
        """vLLM num_requests_running / num_requests_waiting (summed over labels)."""
        def accept(reply: Any) -> Any:
            if (isinstance(reply, dict) and set(reply) == {"ok", "running", "waiting"} and reply["ok"] == 1
                    and all(type(reply[key]) in (int, float) and reply[key] >= 0 for key in ("running", "waiting"))):
                return {"running": reply["running"], "waiting": reply["waiting"]}
            return _failed(reply)
        return self._exchange(container, {"op": "metrics", "timeout": timeout}, seconds, accept, before_start)

    def _exchange(self, container: str, value: dict, seconds: float, accept: Callable[[Any], Any],
                  before_start: Callable[[], None] | None, linger: float = 0) -> Any:
        if not isinstance(container, str) or not _CONTAINER.fullmatch(container):
            raise WorkerNotSent("pinned container id required")
        if type(seconds) not in (int, float) or not 0 < seconds <= 600 or type(value["timeout"]) not in (int, float) \
                or not 0 < value["timeout"] <= 120:
            raise WorkerNotSent("invalid worker deadline")
        try:
            frame = encode_frame(value)
        except (ValueError, TypeError, UnicodeError) as error:
            raise WorkerNotSent("request frame unavailable") from error
        deadline = self._clock() + seconds
        # A metrics exchange (the S1.4 yield, a short budget) waits for a
        # worker start only while its own call timeout still fits after it.
        start_by = deadline - value["timeout"] if value["op"] == "metrics" else deadline
        if not self._lock.acquire(timeout=seconds):
            raise WorkerNotSent("worker busy")
        try:
            running = self._ready(container, start_by, before_start)
            job = running.submit(frame)
            if not job.done.wait(max(0.0, deadline - self._clock())):
                if linger > 0 and job.written:
                    # Sent and unanswered: keep the worker (busy) so the
                    # caller can learn whether this exchange ends.
                    running.linger_until = self._clock() + linger
                    raise WorkerUnproven("no worker reply by the deadline", Linger(self, running, job))
                self._discard(running, "deadline")
                job.done.wait(SETTLE_SECONDS)   # the kill ends the pump's write or read
                if not job.taken or (job.done.is_set() and not job.written):
                    raise WorkerNotSent("worker did not take the request")
                raise WorkerUnproven("no worker reply by the deadline")
            if job.error is not None:
                self._discard(running, "exited" if isinstance(job.error, EOFError) else "failed")
                if not job.written:
                    raise WorkerNotSent("request frame not written") from None
                if isinstance(job.error, EOFError):
                    raise WorkerExited("worker exited before replying") from None
                raise WorkerUnproven("malformed worker reply") from None
            try:
                return accept(job.reply)
            except WorkerUnproven:
                self._discard(running, "failed")
                raise
        finally:
            self._lock.release()

    # --- lifecycle -----------------------------------------------------
    def _ready(self, container: str, deadline: float, before_start: Callable[[], None] | None) -> _Running:
        """Under the lock: the running worker for container, or a new one;
        deadline bounds only this exchange's wait for a start."""
        running = self._running
        if running is not None and running.linger_until is not None:
            # A lingering exchange owns this worker until it settles; past its
            # bound (nobody settled it) it is discarded like a dead worker.
            if self._clock() < running.linger_until:
                raise WorkerNotSent("worker busy")
            self._discard(running, "linger_expired")
            running = None
        if running is not None and running.start_until is not None:
            # A start an earlier exchange could not wait out.
            if running.container == container and (not running.ready.is_set() or running.alive()):
                return self._await_start(running, deadline)
            self._discard(running, "container_changed" if running.container != container else "start_failed")
            running = None
        if running is not None and (running.container != container or not running.alive()):
            self._discard(running, "container_changed" if running.container != container else "exited")
            running = None
        if running is not None:
            return running
        if before_start is not None:
            try:
                before_start()
            except Exception as error:
                refused = WorkerNotSent("engine check before a worker start failed")
                refused.engine_check = True
                raise refused from error
        try:
            process = self._spawn(self._command(container), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, bufsize=0)
        except Exception as error:
            raise WorkerNotSent("worker unavailable") from error
        running = _Running(process, container, self.name)
        running.start_until = self._clock() + self._start_seconds
        try:
            running.thread.start()
        except Exception as error:
            running.stop()
            raise WorkerNotSent("worker thread unavailable") from error
        return self._await_start(running, deadline)

    def _await_start(self, running: _Running, deadline: float) -> _Running:
        """Under the lock: wait, up to this exchange's deadline, for a start
        to send its ready frame. One that fails, or passes its own start bound
        (start_seconds from its spawn), is stopped. One still within that bound
        is kept for the next exchange to adopt instead of being killed, so a
        start never has to fit inside one short exchange (the S1.4 yield's
        metrics refresh); this exchange is not sent either way."""
        running.ready.wait(max(0.0, min(running.start_until, deadline) - self._clock()))
        if running.ready.is_set() and running.alive():   # started is set before ready
            running.start_until = None
            self._running = running
            self.starts += 1
            self._log("model worker started name=%s starts=%d pid=%s" % (
                self.name, self.starts, getattr(running.process, "pid", None)))
            return running
        failed = running.ready.is_set()
        # This exchange could wait out the whole start bound (camera chat, live
        # chat), or the bound has passed: never kept.
        if failed or deadline >= running.start_until or self._clock() >= running.start_until:
            if self._running is running:
                self._running = None
            running.stop()
            self._log("model worker discarded name=%s reason=%s" % (
                self.name, "start_failed" if failed else "start_expired"))
            raise WorkerNotSent("worker did not start")
        self._running = running
        raise WorkerNotSent("worker still starting")

    def _discard(self, running: _Running, reason: str) -> None:
        if self._running is running:
            self._running = None
        running.stop()
        self._log("model worker discarded name=%s reason=%s" % (self.name, reason))

    def _settle(self, running: _Running, reason: str) -> None:
        """A lingering exchange settled: its worker is discarded (the next
        request starts a fresh one, after its before_start check)."""
        acquired = self._lock.acquire(timeout=SETTLE_SECONDS)
        try:
            if self._running is running:
                self._discard(running, reason)
            else:
                running.stop()   # already discarded or closed
        finally:
            if acquired:
                self._lock.release()

    def close(self, timeout: float = 5) -> None:
        acquired = self._lock.acquire(timeout=timeout)
        try:
            running, self._running = self._running, None
            if running is not None:
                running.stop()
        finally:
            if acquired:
                self._lock.release()


class Linger:
    """A live exchange kept past its deadline by chat(linger=...): its frame
    was written and no reply came in time (see the module text)."""

    def __init__(self, worker: ModelWorker, running: _Running, job: _Job):
        self._worker, self._running, self._job = worker, running, job
        self._lock, self._settled = threading.Lock(), False

    def wait(self) -> bool:
        """Block until the exchange ends or the linger bound passes; True only
        when it is proven ended. The worker is discarded either way."""
        left = self._running.linger_until - self._worker._clock()
        done = self._job.done.wait(max(0.0, left))
        # Only a reply frame (ok or error) is proof: the worker writes one only
        # after its own model call returned and its loopback connection closed.
        # A worker exit is not: the docker CLI's status may be its own (1 when
        # it lost dockerd) while the exec'd call still runs in vLLM.
        ended = done and self._job.error is None
        self._settle("lingered" if ended else "linger_expired" if not done
                     else "exited" if isinstance(self._job.error, EOFError) else "failed")
        return ended

    def abandon(self) -> None:
        """Stop waiting (shutdown): the worker is killed and nothing is proven."""
        self._settle("abandoned")

    def _settle(self, reason: str) -> None:
        with self._lock:
            if self._settled:
                return
            self._settled = True
        self._worker._settle(self._running, reason)


def _failed(reply: Any) -> Any:
    if isinstance(reply, dict) and set(reply) == {"ok", "error"} and reply["ok"] == 0 and isinstance(reply["error"], str):
        raise WorkerFailed("worker model call failed: " + reply["error"][:64])
    raise WorkerUnproven("malformed worker reply")
