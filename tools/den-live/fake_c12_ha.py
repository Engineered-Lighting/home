"""A fake of the den home's Home Assistant with the `c12` integration, for the FC13 sender's offline tests (plan 05,
step 05-2). Never a transport: it listens on 127.0.0.1 only, and its boards are a host simulator or a model.

- FakeHa: the REST API for the `c12` services only. It checks the bearer token, answers `?return_response`, and keeps
  HTTP/1.1 connections alive, as Home Assistant does. Knobs: `down` (drop each call unanswered) and `delay_s` (per
  service, before the integration runs).
- Integration: the `c12` integration as plan 05 §3.2 states it (firmware P5, not built). It's the only sender of
  0xFC13 and owns the wire `seq`: it reads the board's `last_seq` when it starts, and again after `stale_seq`. Until it
  can read it, only STOP goes, which the board takes at any `seq`. It resends a MOVE once with the same `seq` when no
  MOVE_ACK comes, and retries STOP until the board reads latched (3 frames). `air` logs every frame in order.
- SimBoard: the firmware's own v2-a host simulator (c12v2_sim.exe) as the board. A MOVE is answered `lights_only`,
  or `stopped` while latched, and KEEP_LIT has no effect (zcl-fc13.md, P2). The latch survives `#sim reboot`.
- ModelBoard: the board as P3 will be (zcl-fc13.md): MOVE `accepted`, and the 3 s coordinator lease keyed by the lit
  MOVE's `seq`. Joints aren't modeled: the spot is lit or dark.
"""

import http.server
import json
import struct
import subprocess
import threading
import time
import urllib.parse


MOVE, KEEP_LIT, STOP, RESUME = 0x00, 0x01, 0x02, 0x03
MOVE_ACK = 0x80
SIZES = {MOVE: 16, KEEP_LIT: 4, STOP: 2, RESUME: 2}
SET_SPOT, LIT_DURING_MOVE, SOURCE_HAND, HAS_VELOCITY = 0x08, 0x01, 0x02, 0x04
RESULTS = ["accepted", "stale_seq", "duplicate", "stopped", "out_of_range", "needs_commissioning", "updating",
           "bad_payload", "lights_only", "fault", "expired"]
SEQ_WINDOW = 1024
LEASE_S = 3.0


def classify_seq(last, seq):
    """zcl-fc13.md: 'new', 'duplicate' or 'stale'. last None: nothing accepted since boot."""
    if last is None:
        return "new"
    if seq == last:
        return "duplicate"
    return "new" if 1 <= (seq - last) & 0xFFFF <= SEQ_WINDOW else "stale"


def _taken(cmd, payload, last):
    """The seq a frame makes the board's last_seq, or None (v2_app.h handleFc13)."""
    size = SIZES.get(cmd)
    if size is None or len(payload) < 2 or (cmd != STOP and len(payload) != size):
        return None
    seq = struct.unpack_from("<H", payload)[0]
    return seq if classify_seq(last, seq) == "new" else None


class SimBoard:
    """c12v2_sim.exe on pipes: one frame in, one answer out."""

    def __init__(self, executable):
        self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, encoding="ascii", bufsize=1)
        self._boot_line()

    def _boot_line(self):
        boot = json.loads(self.process.stdout.readline())
        if boot.get("event") != "boot" or boot.get("fw") != "c12-v2":
            raise RuntimeError("the simulator didn't boot as c12-v2")
        self.last = None   # the board layer's last_seq: RAM only, so a reboot clears it

    def _line(self, text):
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def frame(self, cmd, payload):
        taken = _taken(cmd, payload, self.last)
        reply = self._line("#sim fc13 %02x %s" % (cmd, payload.hex()))
        if reply.get("sim") != "ok":
            raise RuntimeError("the simulator refused the frame line")
        if taken is not None:
            self.last = taken
        return bytes.fromhex(reply["reply"]) if reply["reply"] else None

    def last_seq(self):
        return 0 if self.last is None else self.last

    def stop_latched(self):
        return self._line("1 status")["stop_latched"] is True

    def spot_lit(self):
        return self._line("1 status")["lights"]["spot_lit"] is True

    def reboot(self):
        self.process.stdin.write("#sim reboot\n")
        self.process.stdin.flush()
        self._boot_line()
        if json.loads(self.process.stdout.readline()) != {"sim": "ok"}:
            raise RuntimeError("the simulator didn't confirm its reboot")

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            self.process.wait(5)
        self.process.stdout.close()


class ModelBoard:
    """P3's receive rules in Python (zcl-fc13.md): seq rules, the Stop latch kept over a reboot, and the lease."""

    def __init__(self, *, clock=time.monotonic, lease_s=LEASE_S):
        self.clock, self.lease_s, self.latched = clock, lease_s, False
        self.boots = 0
        self.reboot()

    def reboot(self):
        self.boots += 1
        self.last, self.cached = None, None
        self.lit, self.token, self.lease_until = False, None, None   # the spot boots dark

    def _ack(self, seq, result):
        reply = bytes([MOVE_ACK]) + struct.pack("<HBH", seq, RESULTS.index(result), 0)
        return reply

    def _lapse(self):
        if self.lit and self.lease_until is not None and self.clock() >= self.lease_until:
            self.lit, self.token, self.lease_until = False, None, None

    def frame(self, cmd, payload):
        self._lapse()
        if cmd == STOP:
            taken = _taken(cmd, payload, self.last)
            self.last = self.last if taken is None else taken
            self.latched, self.lit, self.token, self.lease_until = True, False, None, None
            return None
        size = SIZES.get(cmd)
        if size is None or len(payload) != size:
            if cmd == MOVE and len(payload) >= 2:
                return self._ack(struct.unpack_from("<H", payload)[0], "bad_payload")
            return None
        seq = struct.unpack_from("<H", payload)[0]
        kind = classify_seq(self.last, seq)
        if kind == "stale":
            return self._ack(seq, "stale_seq") if cmd == MOVE else None
        if kind == "duplicate":
            if cmd != MOVE:
                return None
            if self.cached and struct.unpack_from("<H", self.cached, 1)[0] == seq:
                return self.cached
            return self._ack(seq, "duplicate")
        self.last, self.cached = seq, None
        if cmd == RESUME:
            self.latched = False
            return None
        if cmd == KEEP_LIT:
            lit_seq = struct.unpack_from("<H", payload, 2)[0]
            if self.lit and self.token is not None and lit_seq == self.token:
                self.lease_until = self.clock() + self.lease_s
            return None
        _seq, flags, _pan, _tilt, level, mired, _vp, _vt, _valid = struct.unpack("<HBhhBHhhH", payload)
        if flags & 0xF0 or (flags & SET_SPOT and not 154 <= mired <= 556):
            result = "bad_payload"
        elif self.latched:
            result = "stopped"
        else:
            result = "accepted"
            if flags & SET_SPOT:
                self.lit = level > 0
                hand = bool(flags & SOURCE_HAND)
                self.token = seq if self.lit and not hand else None
                self.lease_until = self.clock() + self.lease_s if self.token is not None else None
        self.cached = self._ack(seq, result)
        return self.cached

    def last_seq(self):
        return 0 if self.last is None else self.last

    def stop_latched(self):
        return self.latched

    def spot_lit(self):
        self._lapse()
        return self.lit

    def close(self):
        pass


class BadRequest(ValueError):
    pass


def _int(data, key, low, high):
    value = data.get(key)
    if type(value) is not int or not low <= value <= high:
        raise BadRequest(key)
    return value


class Integration:
    """The `c12` integration as plan 05 §3.2 states it. boards: {HA device reference: board}."""

    def __init__(self, boards):
        self.boards = dict(boards)
        self.lock = threading.Lock()
        self.air = []   # {device, cmd, payload_hex, reply_hex, lost}: every frame, in order
        self.read_failures = 0   # the next n last_seq reads fail
        self.lose_frames = {}    # device -> the next n frames never reach the board
        self.lose_replies = {}   # device -> the board's next n answers never come back
        self.starts = 0
        self.start()

    def start(self):
        """Start or restart: forget every count, read each board's last_seq."""
        with self.lock:
            self.starts += 1
            self.seq = {}
            for device in self.boards:
                self._read(device)

    def _read(self, device):
        if self.read_failures:
            self.read_failures -= 1
            self.seq.pop(device, None)
            return False
        self.seq[device] = self.boards[device].last_seq()
        return True

    def _next(self, device):
        if device not in self.seq and not self._read(device):
            return None
        self.seq[device] = (self.seq[device] + 1) & 0xFFFF
        return self.seq[device]

    def _air(self, device, cmd, payload):
        lost = self.lose_frames.get(device, 0) > 0
        reply = None
        if lost:
            self.lose_frames[device] -= 1
        else:
            reply = self.boards[device].frame(cmd, payload)
            if reply is not None and self.lose_replies.get(device, 0) > 0:
                self.lose_replies[device] -= 1
                reply = None
        self.air.append({"device": device, "cmd": cmd, "payload_hex": payload.hex(),
                         "reply_hex": reply.hex() if reply else None, "lost": lost})
        return lost, reply

    def _device(self, data):
        device = data.get("fixture") if isinstance(data, dict) else None
        if device not in self.boards:
            raise BadRequest("fixture")
        return device

    def move(self, data):
        device = self._device(data)
        pan, tilt = _int(data, "pan_cdeg", -18000, 18000), _int(data, "tilt_cdeg", -10000, 10000)
        level, mired = _int(data, "spot_level", 0, 254), _int(data, "spot_mired", 0, 0xFFFF)
        valid_ms = _int(data, "valid_ms", 0, 0xFFFF)
        velocity = data.get("velocity_cdeg_s")
        if (not isinstance(velocity, list) or len(velocity) != 2
                or not all(type(v) is int and -32768 <= v <= 32767 for v in velocity)):
            raise BadRequest("velocity_cdeg_s")
        if type(data.get("lit_during_move")) is not bool or data.get("source") not in ("coordinator", "hand"):
            raise BadRequest("lit_during_move or source")
        flags = (SET_SPOT | (LIT_DURING_MOVE if data["lit_during_move"] else 0)
                 | (SOURCE_HAND if data["source"] == "hand" else 0) | (HAS_VELOCITY if any(velocity) else 0))
        with self.lock:
            for fresh_read in (False, True):
                seq = self._next(device)
                if seq is None:
                    return {"seq": None, "payload_hex": None, "result": "seq_unknown", "predicted_ms": None}
                payload = struct.pack("<HBhhBHhhH", seq, flags, pan, tilt, level, mired, *velocity, valid_ms)
                ack = None
                for _try in range(2):   # once more with the same seq: the board answers a duplicate as before
                    _lost, reply = self._air(device, MOVE, payload)
                    if reply and reply[0] == MOVE_ACK and struct.unpack_from("<H", reply, 1)[0] == seq:
                        ack = struct.unpack_from("<BH", reply, 3)
                        break
                if ack is None:
                    return {"seq": seq, "payload_hex": payload.hex(), "result": "no_ack", "predicted_ms": None}
                result = RESULTS[ack[0]] if ack[0] < len(RESULTS) else "unknown"
                if result != "stale_seq" or fresh_read:
                    return {"seq": seq, "payload_hex": payload.hex(), "result": result, "predicted_ms": ack[1]}
                if not self._read(device):   # stale: read last_seq again, then once more with a new seq
                    return {"seq": seq, "payload_hex": payload.hex(), "result": result, "predicted_ms": ack[1]}

    def keep_lit(self, data):
        device = self._device(data)
        lit_seq = _int(data, "lit_seq", 0, 0xFFFF)
        with self.lock:
            seq = self._next(device)
            if seq is None:
                return {"seq": None, "payload_hex": None, "status": "seq_unknown"}
            payload = struct.pack("<HH", seq, lit_seq)
            lost, _reply = self._air(device, KEEP_LIT, payload)
            return {"seq": seq, "payload_hex": payload.hex(), "status": "not_delivered" if lost else "sent"}

    def _targets(self, data):
        if isinstance(data, dict) and data.get("fixture") == "all":
            return list(self.boards)
        return [self._device(data)]

    def stop(self, data):
        """Goes even with the count unknown: the board takes STOP at any seq."""
        fixtures = {}
        with self.lock:
            for device in self._targets(data):
                seq = self._next(device)
                payload = struct.pack("<H", 0 if seq is None else seq)
                latched = False
                for _try in range(3):
                    self._air(device, STOP, payload)
                    latched = self.boards[device].stop_latched()
                    if latched:
                        break
                fixtures[device] = {"seq": 0 if seq is None else seq, "payload_hex": payload.hex(), "status": "sent",
                                    "stop_latched": latched}
        return {"fixtures": fixtures}

    def resume(self, data):
        fixtures = {}
        with self.lock:
            for device in self._targets(data):
                seq = self._next(device)
                if seq is None:
                    fixtures[device] = {"seq": None, "payload_hex": None, "status": "seq_unknown",
                                        "stop_latched": self.boards[device].stop_latched()}
                    continue
                payload = struct.pack("<H", seq)
                lost, _reply = self._air(device, RESUME, payload)
                fixtures[device] = {"seq": seq, "payload_hex": payload.hex(),
                                    "status": "not_delivered" if lost else "sent",
                                    "stop_latched": self.boards[device].stop_latched()}
        return {"fixtures": fixtures}


class FakeHa:
    """Home Assistant's REST API for the `c12` services, over plain HTTP on 127.0.0.1."""

    SERVICES = ("move", "keep_lit", "stop", "resume")

    def __init__(self, integration, token):
        self.integration, self.token = integration, token
        self.down, self.delay_s, self.redirect = False, {}, False
        self.calls, self.connections = [], 0
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                fake.connections += 1

            def log_message(self, *_args):
                pass

            def do_POST(self):
                fake._serve(self)

            do_GET = do_POST

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="fake-c12-ha", daemon=True)

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _answer(self, handler, status, body, headers=()):
        text = json.dumps(body).encode("utf-8")
        handler.send_response(status)
        for key, value in headers:
            handler.send_header(key, value)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(text)))
        handler.end_headers()
        handler.wfile.write(text)

    def _serve(self, handler):
        body = handler.rfile.read(int(handler.headers.get("Content-Length") or 0))
        if self.down:   # the link between the homes: the call never gets an answer
            handler.close_connection = True
            return
        if handler.headers.get("Authorization") != "Bearer " + self.token:
            return self._answer(handler, 401, {"message": "unauthorized"})
        if self.redirect:
            return self._answer(handler, 302, {}, (("Location", "http://127.0.0.1:9/elsewhere"),))
        parts = urllib.parse.urlsplit(handler.path)
        prefix, _, service = parts.path.rpartition("/")
        if handler.command != "POST" or prefix != "/api/services/c12" or service not in self.SERVICES:
            return self._answer(handler, 404, {"message": "no such service"})
        if parts.query != "return_response":
            return self._answer(handler, 400, {"message": "c12 services answer only with return_response"})
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            return self._answer(handler, 400, {"message": "bad JSON"})
        self.calls.append((service, data))
        time.sleep(self.delay_s.get(service, 0))
        try:
            result = getattr(self.integration, service)(data)
        except BadRequest as exc:
            return self._answer(handler, 400, {"message": "invalid " + str(exc)})
        return self._answer(handler, 200, {"changed_states": [], "service_response": result})
