"""den-live's FC13 sender (plan 05, step 05-2): fsynced trace rows to the den home's Home Assistant `c12` services.

Offline only. Nothing in the service builds a Sender yet: the desk switch (step 05-4) will map one fixture, and until
then den-live stays virtual. The `c12` integration it calls is the firmware chat's P5, not built; fake_c12_ha.py
stands in for it.

The path (plan 05 §3.1): the preview decides, PreviewTrace encodes, the storage fsyncs the row, and only then does
`take()` see it (`after()` wraps the trace's append to do that). The sender calls `c12.move`, `c12.keep_lit`,
`c12.stop` or `c12.resume` (schema c12-service-1) and records a send row for every call and every drop.

The integration owns the wire `seq`: it reads the board's `last_seq` when it starts and counts on. den-live's trace
`seq` never goes on the air, so a den-live restart can't be refused as stale.

The rules:
- Stop first. Stop and Resume ride their own lane, so a move waiting up to about 1 s for its MOVE_ACK never holds a
  Stop back. A Stop cancels its fixture's unsent keep-lit and move. A move or keep-lit waits while its fixture has a
  Resume unanswered.
- One move or keep-lit in flight per fixture, keep-lit first (unless it renews the move still waiting). A newer one
  replaces an unsent one of its kind.
- A move whose valid_ms ran out before its call is dropped, never sent: the board can only time from its receipt.
- Never retried here: moves (the integration resends once with the same seq) and keep-lits (the next is 1 s behind).
  Stop is, up to STOP_CALLS calls, until the board reads latched.
- A keep-lit's `lit_seq` is the wire seq of the latest accepted lit move, from that move's answer.
- on_release(fixture_id, move_trace_id, reason): a move not accepted, a move dropped as expired, or KEEP_LIT_FAILURES
  keep-lits in a row that failed. The preview should release that row if its move is still the current one.
- light=False is the desk's dark rehearsal (§3.6): every move goes with its spot at level 0 and no lit travel, and no
  keep-lit goes (`dropped_dark`). The replay knows the run is dark from its header's desk mapping.
- on_board_stop(fixture_id, reason), the Stop copy (§3.4): a move refused `stopped`, or `observe()` reading the latch
  more than RESUME_GUARD_S after den-live's own last Resume answer, with none unanswered and no Stop of den-live's
  holding. copy_stop_into() turns it into Home's Stop.
- No send row or error holds the bearer token, the URL or Home Assistant's text.
"""

import collections
import math
import struct
import threading
import time

import ha_link
import preview_trace


DOMAIN = "c12"
SERVICE_SCHEMA = "c12-service-1"
SEND_SCHEMA = preview_trace.SCHEMA   # send rows sit in the same trace as the rows they send
KEEP_LIT_TIMEOUT_S = 1.0
CALL_TIMEOUT_S = 2.0   # c12.move waits up to about 1 s for its MOVE_ACK
STOP_CALLS = 3
KEEP_LIT_FAILURES = 2
RESUME_GUARD_S = 2.0
CONTROL = "control"   # the Stop and Resume lane
OUTCOMES = ("acked", "refused", "no_answer", "dropped_expired", "dropped_superseded", "dropped_dark")


class _Job:
    __slots__ = ("row", "fixture_id", "action", "deadline")

    def __init__(self, row, taken):
        self.row, self.fixture_id, self.action = row, row["fixture_id"], row["action"]
        self.deadline = None
        if self.action == "aim_move":
            effective, at = row["effective"], row.get("at")
            valid_ms = effective.get("effective_valid_ms") or effective["valid_ms"] or 1000
            start = at if type(at) in (int, float) and math.isfinite(at) and at <= taken else taken
            self.deadline = start + valid_ms / 1000


def dark_payload(payload):
    """A MOVE as the dark rehearsal sends it: its spot at level 0 and no lit travel (payload byte 7, and flag bit 0)."""
    dark = bytearray(payload)
    dark[2] &= ~0x01
    dark[7] = 0
    return bytes(dark)


def _wire(entry):
    if isinstance(entry, dict) and isinstance(entry.get("payload_hex"), str):
        return {"seq": entry.get("seq"), "payload_hex": entry["payload_hex"]}
    return None


def _entry(answer, device):
    """Stop's and Resume's answer for one device: {seq, payload_hex, status, stop_latched}."""
    fixtures = answer.get("fixtures") if isinstance(answer, dict) else None
    entry = fixtures.get(device) if isinstance(fixtures, dict) else None
    return entry if isinstance(entry, dict) else {}


class Sender:
    def __init__(self, fixtures, client_factory, *, record, on_release=None, on_board_stop=None,
                 clock=time.monotonic, threaded=True, light=True):
        """fixtures: {fixture_id: the board's HA device reference}. client_factory() gives each lane its own client
        (ha_link.HaSession). record(row) stores a send row. clock must be the preview's own: a move's deadline counts
        from its row's `at`."""
        if (not isinstance(fixtures, dict) or not fixtures
                or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in fixtures.items())):
            raise ValueError("the sender needs at least one fixture mapped to a device")
        self.devices = dict(fixtures)
        self.client_factory, self.record_sink = client_factory, record
        self.on_release = on_release or (lambda *_: None)
        self.on_board_stop = on_board_stop or (lambda *_: None)
        self.clock, self.threaded, self.light = clock, threaded, light is not False
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.record_lock = threading.Lock()
        self.slots = {fid: {"keep": None, "move": None} for fid in self.devices}
        self.controls = collections.deque()
        self.busy = set()
        self.resuming = dict.fromkeys(self.devices, 0)
        self.resumed_at = dict.fromkeys(self.devices, -math.inf)   # den-live's latest Resume answer
        self.own_stop = dict.fromkeys(self.devices, False)   # den-live's own Stop taken, and no Resume answered since
        self.lit = dict.fromkeys(self.devices)   # (trace seq, wire seq, trace_id) of the latest accepted lit move
        self.keep_failures = dict.fromkeys(self.devices, 0)
        self.counts = dict.fromkeys((*OUTCOMES, "errors"), 0)
        self.clients, self.threads, self.closed = {}, [], False

    # ---- intake ----

    def after(self, append):
        """The trace's storage, then the sender: a row reaches take() only once append has returned."""
        def write(row):
            append(row)
            self.take(row)
        return write

    def take(self, row):
        """One trace row. Only encoded rows for a mapped fixture go further: refusals, lifecycle rows and run headers
        never reach the air. Never blocks on a call."""
        if (not isinstance(row, dict) or row.get("kind") == "run_header" or row.get("fixture_id") not in self.devices
                or row.get("status") != "encoded_only"):
            return
        job, dropped = _Job(row, self.clock()), []
        with self.lock:
            slots = self.slots[job.fixture_id]
            if job.action == "stop":
                self.own_stop[job.fixture_id] = True
                dropped = [slots[key] for key in ("keep", "move") if slots[key] is not None]
                slots["keep"] = slots["move"] = None
                self.controls.append(job)
            elif job.action == "resume":
                self.resuming[job.fixture_id] += 1
                self.controls.append(job)
            else:
                key = "keep" if job.action == "keep_lit" else "move"
                if slots[key] is not None:
                    dropped.append(slots[key])
                slots[key] = job
            self.changed.notify_all()
        for old in dropped:
            self._log(old, outcome="dropped_superseded", reason="replaced_before_its_call")

    def observe(self, fixture_id, stop_latched):
        """The board's `stop_latched` as the readback polled it."""
        if stop_latched is True and fixture_id in self.devices:
            self._copy(fixture_id, self.clock() - RESUME_GUARD_S, "stop_latched_read")

    # ---- lanes ----

    def _pick(self, lane):
        if lane == CONTROL:
            return self.controls.popleft() if self.controls else None
        if self.resuming[lane]:
            return None
        slots = self.slots[lane]
        keep, move = slots["keep"], slots["move"]
        renews_move = keep is None or keep.row.get("move_token") == (move and move.row.get("sequence"))
        first = "move" if move is not None and renews_move else "keep"
        job, slots[first] = slots[first], None
        return job

    def _lanes(self):
        return (CONTROL, *self.devices)

    def _client(self, lane):
        if lane not in self.clients:
            self.clients[lane] = self.client_factory()
        return self.clients[lane]

    def start(self):
        if self.threaded:
            for lane in self._lanes():
                thread = threading.Thread(target=self._loop, args=(lane,), name="den-c12-send-" + lane, daemon=True)
                thread.start()
                self.threads.append(thread)
        return self

    def close(self):
        with self.lock:
            self.closed = True
            self.changed.notify_all()
        for thread in self.threads:
            thread.join(3)
        for client in list(self.clients.values()):
            getattr(client, "close", lambda: None)()

    def _loop(self, lane):
        client = self._client(lane)
        while True:
            with self.lock:
                job = None
                while not self.closed and (job := self._pick(lane)) is None:
                    self.changed.wait()
                if self.closed:
                    return
                self.busy.add(lane)
            try:
                self._run(client, job)
            except Exception:   # a bug in a callback or the store: counted, never the lane's end
                self.counts["errors"] += 1
            finally:
                with self.lock:
                    self.busy.discard(lane)
                    self.changed.notify_all()

    def pump(self):
        """Not threaded: run every queued call in this thread, Stop's lane first, until none is left."""
        ran = 0
        while True:
            with self.lock:
                lane, job = next(((lane, job) for lane in self._lanes() if (job := self._pick(lane)) is not None),
                                 (None, None))
            if job is None:
                return ran
            self._run(self._client(lane), job)
            ran += 1

    def drain(self, timeout_s=5.0):
        """Threaded: wait until nothing is queued or in flight."""
        end = time.monotonic() + timeout_s
        with self.lock:
            while self.controls or self.busy or any(s["keep"] or s["move"] for s in self.slots.values()):
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self.changed.wait(left)
        return True

    # ---- calls ----

    def _run(self, client, job):
        run = {"aim_move": self._move, "keep_lit": self._keep, "stop": self._stop, "resume": self._resume}
        run[job.action](client, job)

    def _call(self, client, service, data, timeout_s):
        called = self.clock()
        try:
            answer = client.call(DOMAIN, service, data, response=True, timeout_s=timeout_s)
        except ha_link.HaError as exc:
            return None, called, self.clock(), str(exc)
        return answer, called, self.clock(), None

    def _move(self, client, job):
        fid, row = job.fixture_id, job.row
        if self.clock() >= job.deadline:
            self._log(job, outcome="dropped_expired", reason="valid_ms_ran_out_before_its_call")
            self._release(fid, row["trace_id"], "expired_before_send")
            return
        e = row["effective"]
        data = {"fixture": self.devices[fid], "pan_cdeg": e["pan_cdeg"], "tilt_cdeg": e["tilt_cdeg"],
                "spot_level": e["spot_level"] if self.light else 0, "spot_mired": e["spot_mired"],
                "lit_during_move": e["lit_during_move"] and self.light,
                "source": e["source"], "velocity_cdeg_s": list(e["velocity_cdeg_s"]), "valid_ms": e["valid_ms"]}
        expected = row["payload_hex"] if self.light else dark_payload(bytes.fromhex(row["payload_hex"])).hex()
        answer, called, answered, problem = self._call(client, "move", data, CALL_TIMEOUT_S)
        result = answer.get("result") if isinstance(answer, dict) else None
        wire = _wire(answer)
        outcome = ("acked" if result == "accepted" else "no_answer" if answer is None or result == "no_ack"
                   else "refused")
        self._log(job, called=called, answered=answered, sent=None if answer is None else wire is not None,
                  outcome=outcome, wire=wire,
                  answer=answer and {"result": result, "predicted_ms": answer.get("predicted_ms")},
                  matches=wire and wire["payload_hex"][4:] == expected[4:], reason=problem)
        if outcome == "acked":
            with self.lock:
                self.keep_failures[fid] = 0
                lit = self.light and e["spot_level"] > 0 and e["source"] == "coordinator"
                self.lit[fid] = (row["sequence"], wire["seq"], row["trace_id"]) if lit else None
            return
        self._release(fid, row["trace_id"], "c12_" + result if isinstance(result, str) else "no_answer")
        if result == "stopped":
            self._copy(fid, called, "move_refused_stopped")

    def _keep(self, client, job):
        fid, row = job.fixture_id, job.row
        if not self.light:
            self._log(job, outcome="dropped_dark", reason="dark_rehearsal")
            return
        with self.lock:
            lit = self.lit[fid]
        if lit is None or lit[0] != row.get("move_token"):
            self._log(job, outcome="dropped_superseded", reason="no_accepted_lit_move")
            return
        answer, called, answered, problem = self._call(client, "keep_lit",
                                                       {"fixture": self.devices[fid], "lit_seq": lit[1]},
                                                       KEEP_LIT_TIMEOUT_S)
        status = answer.get("status") if isinstance(answer, dict) else None
        wire = _wire(answer)
        outcome = "acked" if status == "sent" else "no_answer" if answer is None else "refused"
        self._log(job, called=called, answered=answered, sent=None if answer is None else wire is not None,
                  outcome=outcome, wire=wire, answer=answer and {"status": status},
                  matches=wire and wire["payload_hex"][4:8] == struct.pack("<H", lit[1]).hex(), reason=problem)
        with self.lock:
            self.keep_failures[fid] = 0 if outcome == "acked" else self.keep_failures[fid] + 1
            release = self.keep_failures[fid] >= KEEP_LIT_FAILURES
            if release:
                self.keep_failures[fid] = 0
        if release:
            self._release(fid, lit[2], "keep_lit_failed")

    def _stop(self, client, job):
        fid, device = job.fixture_id, self.devices[job.fixture_id]
        for attempt in range(1, STOP_CALLS + 1):
            answer, called, answered, problem = self._call(client, "stop", {"fixture": device}, CALL_TIMEOUT_S)
            entry = _entry(answer, device)
            latched = entry.get("stop_latched")
            outcome = ("acked" if entry.get("status") == "sent" and latched is True
                       else "no_answer" if answer is None else "refused")
            self._log(job, attempt=attempt, called=called, answered=answered,
                      sent=None if answer is None else _wire(entry) is not None, outcome=outcome, wire=_wire(entry),
                      answer=answer and {"status": entry.get("status"), "stop_latched": latched},
                      matches=_wire(entry) is not None or None, reason=problem)
            if outcome == "acked":
                with self.lock:
                    self.lit[fid], self.keep_failures[fid] = None, 0
                return

    def _resume(self, client, job):
        fid, device = job.fixture_id, self.devices[job.fixture_id]
        answer, called, answered, problem = None, self.clock(), self.clock(), None
        try:
            answer, called, answered, problem = self._call(client, "resume", {"fixture": device}, CALL_TIMEOUT_S)
            entry = _entry(answer, device)
            latched = entry.get("stop_latched")
            outcome = ("acked" if entry.get("status") == "sent" and latched is False
                       else "no_answer" if answer is None else "refused")
            self._log(job, called=called, answered=answered,
                      sent=None if answer is None else _wire(entry) is not None, outcome=outcome, wire=_wire(entry),
                      answer=answer and {"status": entry.get("status"), "stop_latched": latched},
                      matches=_wire(entry) is not None or None, reason=problem)
        finally:
            with self.lock:
                self.resuming[fid] -= 1
                self.resumed_at[fid], self.own_stop[fid] = answered, False
                self.changed.notify_all()

    # ---- outcomes ----

    def _release(self, fid, trace_id, reason):
        try:
            self.on_release(fid, trace_id, reason)
        except Exception:
            self.counts["errors"] += 1

    def _copy(self, fid, since, reason):
        """A Stop den-live didn't send. Not while den-live's own Stop holds, nor while a Resume of den-live's is
        unanswered, nor for what was seen before its answer: the latch then may be den-live's own, not yet cleared."""
        with self.lock:
            if self.own_stop[fid] or self.resuming[fid] or since <= self.resumed_at[fid]:
                return
        try:
            self.on_board_stop(fid, reason)
        except Exception:
            self.counts["errors"] += 1

    def _log(self, job, *, outcome, attempt=1, called=None, answered=None, sent=False, wire=None, answer=None,
             matches=None, reason=None):
        row = job.row
        record = {"schema": SEND_SCHEMA, "kind": "send", "run_id": row.get("run_id"), "trace_id": row.get("trace_id"),
                  "fixture_id": job.fixture_id, "device": self.devices[job.fixture_id], "action": job.action,
                  "attempt": attempt, "called_mono": called, "answered_mono": answered, "sent": sent,
                  "outcome": outcome, "wire": wire, "answer": answer, "matches_trace": matches, "reason": reason}
        try:
            with self.record_lock:
                self.counts[outcome] += 1
                self.record_sink(record)
        except Exception:   # the call already happened: the gap is counted, the lane goes on
            self.counts["errors"] += 1


def copy_stop_into(preview):
    """The Stop copy (plan 05 §3.4) through the preview's own public Stop: a board latched by a Stop den-live didn't
    send becomes Home's Stop, so Home shows Stopped and only Home's Resume lights again. Home already stopped, or Off:
    nothing to copy."""
    def copy(_fixture_id, _reason):
        if preview.on and not preview.stopped:
            preview.stop(None)
    return copy
