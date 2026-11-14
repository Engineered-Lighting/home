"""Verify a local FC13 preview trace against the pinned C12 v2-a firmware archive.

Trace v3 (plan 05, step 05-3) adds the FC13 sender's send rows, and five checks on them:
1. every send row names an earlier encoded row of its run and fixture;
2. its wire payload equals the encoded payload, except the seq (and a keep-lit's lit_seq);
3. a keep-lit's lit_seq is the wire seq of the latest accepted lit move of its run and fixture;
4. per board, across runs in time order, each wire seq is new to the board (STOP is taken at any seq; a repeat is only
   a retry of the same row);
5. with --sim, those frames through the simulator give the answers the board gave (replay_wire).
A run whose header maps no desk fixture may send nothing, and a desk run only to its one fixture. A desk run with
light false is the dark rehearsal: each MOVE goes with its spot at level 0 and no lit travel, and no keep-lit goes.
Device rows hold the board's readback (joint angles and states only) when it changes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import sys
import types
import zipfile


FIRMWARE_COMMIT = "cf1c325"
FIRMWARE_ZIP_SHA256 = "88c5fa3e348ffbcc97ab318eb7c55e045da89c20907254ede4eda1f9967dd2a2"
FIRMWARE_SOURCE = "c12-firmware-for-codex-20261112/source/c12-lit-tracking-v2/"
TRACE_SCHEMA = "c12-preview-trace-1"
TRACE_SCHEMA_2 = "c12-preview-trace-2"   # contract 5: run headers, and each MOVE's binding and subject
TRACE_SCHEMA_3 = "c12-fixture-trace-3"   # plan 05: send rows, and the run header's desk mapping
TRACE_SCHEMAS = (TRACE_SCHEMA, TRACE_SCHEMA_2, TRACE_SCHEMA_3)
SEND_FIELDS = {"schema", "kind", "run_id", "trace_id", "fixture_id", "device", "action", "attempt", "called_mono",
               "answered_mono", "sent", "outcome", "wire", "answer", "matches_trace", "reason"}
SEND_OUTCOMES = {"acked", "refused", "no_answer", "dropped_expired", "dropped_superseded", "dropped_dark"}
DEVICE_FIELDS = {"schema", "kind", "run_id", "fixture_id", "device", "at_mono", "values"}
READBACK = {"state", "controller", "stop_latched", "last_seq", "pan_cdeg", "tilt_cdeg", "fault_code"}
SEQ_WINDOW = 1024
COMMANDS = {"aim_move": 0, "keep_lit": 1, "stop": 2, "resume": 3}
STATE_ACTIONS = {"expired", "evidence_stale", "arrived", "disabled"}
FRAME_FIELDS = {"endpoint", "cluster", "command_id", "seq", "payload_hex"}
MAX_LINE_BYTES = 65_536


class ReplayError(ValueError):
    pass


def firmware_codec(path):
    """Load only the known pure codec and vectors from the hash-verified archive."""
    with Path(path).open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != FIRMWARE_ZIP_SHA256:
            raise ReplayError("firmware ZIP SHA-256 differs from the pinned archive")
        handle.seek(0)
        with zipfile.ZipFile(handle) as archive:
            source = archive.read(FIRMWARE_SOURCE + "tools/c12v2/fc13.py")
            vectors = json.loads(archive.read(FIRMWARE_SOURCE + "contracts/c12v2/vectors/fc13.json"))

    module_name = "_c12_preview_pinned_firmware_fc13"
    codec = types.ModuleType(module_name)
    sys.modules[module_name] = codec  # dataclasses resolves annotations in the module
    try:
        exec(compile(source, "<pinned FC13 firmware codec>", "exec"), codec.__dict__)
    finally:
        del sys.modules[module_name]
    return codec, vectors


def verify_vectors(codec, vectors):
    count = 0
    for index, item in enumerate(vectors["moves"], 1):
        move = codec.Move(**item["fields"])
        payload = bytes.fromhex(item["hex"])
        if codec.parse_move(payload) != move or codec.encode_move(move) != payload:
            raise ReplayError(f"firmware MOVE vector {index} disagrees with the codec")
        count += 1
    for index, item in enumerate(vectors["parse"], 1):
        if codec.payload_ok(codec.CMD_MOVE, bytes.fromhex(item["hex"])) != item["ok"]:
            raise ReplayError(f"firmware parse vector {index} disagrees with the codec")
        count += 1
    for index, item in enumerate(vectors["seq"], 1):
        if codec.classify_seq(item["last"], item["seq"]) != item["class"]:
            raise ReplayError(f"firmware sequence vector {index} disagrees with the codec")
        count += 1
    for index, item in enumerate(vectors["checks"], 1):
        move = codec.parse_move(bytes.fromhex(item["hex"]))
        result = codec.check_move(move, *vectors["check_limits"])
        if result != item["result"] or codec.RESULT_NAMES[result] != item["result_name"]:
            raise ReplayError(f"firmware range vector {index} disagrees with the codec")
        count += 1
    reply_vectors = [codec.encode_move_ack(7, 0, 1840), codec.encode_move_done(7, 0, 1912, -12, 30)]
    if [reply.hex() for reply in reply_vectors] != [item["hex"] for item in vectors["replies"]]:
        raise ReplayError("firmware reply vectors disagree with the codec")
    pages = [bytes.fromhex(page) for page in vectors["profile_pages"]]
    if codec.profile_pages(b"".join(page[3:] for page in pages)) != pages:
        raise ReplayError("firmware profile-page vectors disagree with the codec")
    return count + len(reply_vectors) + len(pages)


def is_u16(value):
    return type(value) is int and 0 <= value <= 0xFFFF


def nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


@dataclass
class FixtureState:
    registration_revision: str
    profile_sha256: str
    last_seq: int | None = None
    lit_move: tuple[int, str] | None = None
    stopped: bool = False


@dataclass(frozen=True)
class SimFrame:
    line: int
    command_id: int
    payload_hex: str
    expected_reply: str | None


def validate_frame(row, state, codec):
    frame = row.get("frame")
    if not isinstance(frame, dict) or frame.keys() != FRAME_FIELDS:
        raise ReplayError("encoded row requires the five frame fields")
    cmd = COMMANDS.get(row["action"])
    if (cmd is None or type(frame["command_id"]) is not int or frame["command_id"] != cmd
            or frame["endpoint"] != codec.MOTION_ENDPOINT or frame["cluster"] != codec.CLUSTER_ID
            or type(frame["endpoint"]) is not int or type(frame["cluster"]) is not int):
        raise ReplayError("frame endpoint, cluster or command disagrees with action")
    seq = frame["seq"]
    payload_hex = frame["payload_hex"]
    if (not is_u16(seq) or not isinstance(payload_hex, str)
            or len(payload_hex) != 2 * codec.PAYLOAD_SIZE[cmd] or re.fullmatch(r"[0-9a-f]+", payload_hex) is None):
        raise ReplayError("invalid frame sequence, length or hex")
    payload = bytes.fromhex(payload_hex)
    if not codec.payload_ok(cmd, payload) or codec.payload_seq(payload) != seq:
        raise ReplayError("payload structure or sequence disagrees with the frame")
    if state.last_seq is not None and ((seq - state.last_seq) & 0xFFFF) != 1:
        raise ReplayError("encoded fixture sequence must advance by one modulo 65536")
    if codec.classify_seq(state.last_seq, seq) != "new":
        raise ReplayError("firmware classifies encoded sequence as stale or duplicate")
    effective = row.get("effective")
    if not isinstance(effective, dict):
        raise ReplayError("encoded row is missing effective settings")
    return cmd, seq, payload, effective


def verify_move(row, state, codec, seq, payload, effective):
    if state.stopped:
        raise ReplayError("MOVE emitted while preview Stop is latched")
    move = codec.parse_move(payload)
    if codec.encode_move(move) != payload:
        raise ReplayError("decoded MOVE does not round-trip against firmware codec")
    if codec.check_move(move, -18000, 18000, -10000, 10000) != codec.ACCEPTED:
        raise ReplayError("MOVE exceeds the firmware record bounds or light field limits")
    if not move.flags & codec.FLAG_SET_SPOT:
        raise ReplayError("preview MOVE must set the spot level explicitly")
    if move.flags & codec.FLAG_SOURCE_HAND or effective.get("source") != "coordinator":
        raise ReplayError("preview MOVE must have coordinator provenance")
    if not move.flags & codec.FLAG_HAS_VELOCITY and (move.vel_pan_cdeg_s or move.vel_tilt_cdeg_s):
        raise ReplayError("MOVE carries velocity without the velocity flag")
    if move.effective_valid_ms > 1500 or (move.spot_level == 0 and move.flags & codec.FLAG_LIT_DURING_MOVE):
        raise ReplayError("MOVE exceeds preview validity or requests lit travel at zero level")
    expected = {
        "pan_cdeg": move.pan_cdeg,
        "tilt_cdeg": move.tilt_cdeg,
        "flags": move.flags,
        "spot_level": move.spot_level,
        "spot_mired": move.spot_mired,
        "lit_during_move": bool(move.flags & codec.FLAG_LIT_DURING_MOVE),
        "valid_ms": move.valid_ms,
        "velocity_cdeg_s": [move.vel_pan_cdeg_s, move.vel_tilt_cdeg_s],
    }
    if any(type(effective.get(key)) is not type(value) or effective[key] != value for key, value in expected.items()):
        raise ReplayError("effective MOVE fields disagree with the decoded payload")
    for key, value in (("level", move.spot_level / 254), ("cct_k", 1_000_000 / move.spot_mired)):
        observed = effective.get(key)
        if type(observed) not in (int, float) or not 0 <= observed <= 1_000_000 or not math.isclose(observed, value, rel_tol=1e-12, abs_tol=1e-12):
            raise ReplayError("effective light settings disagree with the decoded payload")
    decision = row.get("decision_id")
    if (not nonempty_string(decision) or not is_u16(row.get("move_seq")) or row["move_seq"] != seq
            or row.get("move_decision_id") != decision):
        raise ReplayError("MOVE is missing its own sequence and decision binding")
    state.lit_move = (seq, decision) if move.spot_level else None
    return f"{codec.CMD_MOVE_ACK:02x}" + codec.encode_move_ack(seq, codec.LIGHTS_ONLY, 0).hex()


def verify_command(row, state, codec, line):
    cmd, seq, payload, effective = validate_frame(row, state, codec)
    expected_reply = None
    if cmd == codec.CMD_MOVE:
        expected_reply = verify_move(row, state, codec, seq, payload, effective)
    elif cmd == codec.CMD_KEEP_LIT:
        token = effective.get("token")
        move_seq, move_decision = row.get("move_seq"), row.get("move_decision_id")
        if (state.stopped or effective.get("source") != "coordinator"
                or not is_u16(token) or not is_u16(move_seq) or token != move_seq
                or row.get("decision_id") != move_decision or state.lit_move != (move_seq, move_decision)
                or codec.encode_keep_lit(seq, token) != payload):
            raise ReplayError("KEEP_LIT does not renew the active lit MOVE")
    else:
        if (codec.encode_seq_only(seq) != payload or effective.get("source") not in ("coordinator", "hand")
                or row.get("move_seq") is not None or row.get("move_decision_id") is not None):
            raise ReplayError("STOP/RESUME carries an invalid payload or MOVE binding")
        state.stopped = cmd == codec.CMD_STOP
        state.lit_move = None
    state.last_seq = seq
    return SimFrame(line, cmd, payload.hex(), expected_reply)


def json_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ReplayError("JSON contains duplicate keys")
    return result


def reject_constant(_value):
    raise ReplayError("JSON contains a non-finite constant")


def valid_desk(desk):
    return (isinstance(desk, dict) and set(desk) == {"fixture_id", "device", "light"}
            and nonempty_string(desk["fixture_id"]) and nonempty_string(desk["device"]) and type(desk["light"]) is bool)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def verify_header(row, headers):
    """A trace-2 or -3 run header: its anchor, the codec and simulator it names, and its binding; trace-3's also its
    desk mapping or null. A day file a run crosses into repeats it; the repeat must be the same."""
    run, binding = row.get("run_id"), row.get("binding")
    if row["schema"] == TRACE_SCHEMA_3 and ("desk" not in row or not (row["desk"] is None or valid_desk(row["desk"]))):
        raise ReplayError("trace-3 run header needs its desk mapping or null")
    if (not nonempty_string(run) or not all(type(row.get(k)) in (int, float) and math.isfinite(row[k]) for k in ("at_mono", "at_ts"))
            or not all(isinstance(row.get(k), str) and re.fullmatch(r"[0-9a-f]{64}", row[k]) for k in ("codec_sha256", "simulator_sha256"))
            or not isinstance(binding, dict) or not all(nonempty_string(binding.get(k)) for k in ("scan_revision", "transform_id", "equipment_digest"))
            or type(binding.get("equipment_revision")) is not int):
        raise ReplayError("run header is missing its anchor, hashes or binding")
    if headers.setdefault(run, row) != row:
        raise ReplayError("a run's header changed between its day files")


def verify_binding_2(row, header, effective):
    """A trace-2 MOVE names its room model and its subject; a lit one, the reply it answers."""
    binding = header["binding"]
    if any(row.get(key) != binding[key] for key in ("transform_id", "equipment_revision", "equipment_digest")):
        raise ReplayError("MOVE binding disagrees with its run header")
    if row.get("registration_revision") != binding["scan_revision"]:
        raise ReplayError("MOVE scan revision disagrees with its run header")
    if not nonempty_string(row.get("track_id")) or type(row.get("camera_epoch")) is not int:
        raise ReplayError("MOVE is missing its subject")
    if effective.get("spot_level") and not nonempty_string(row.get("request_id")):
        raise ReplayError("lit MOVE names no reply")


@dataclass
class WireFrame:
    line: int
    trace_id: str
    command_id: int
    seq: int
    payload: bytes
    called: float      # wall time, from the run header's anchor
    answered: float
    answer: dict
    delivered: bool    # the integration says the frame left (a MOVE: it was sent at all)


class WireCheck:
    """Trace v3's send rows: checks 1-3 row by row, check 4 at the end, and each board's frames for check 5."""

    def __init__(self, codec):
        self.codec = codec
        self.encoded = {}   # (run, trace_id) -> (fixture, command, payload, encoded row)
        self.lit = {}       # (run, fixture) -> (trace seq, wire seq) of the latest accepted lit MOVE
        self.frames = {}    # device -> [WireFrame]

    def add_encoded(self, row, command_id, payload):
        key = (row["run_id"], row.get("trace_id"))
        if not nonempty_string(key[1]) or key in self.encoded:
            raise ReplayError("encoded row needs a trace_id of its own")
        self.encoded[key] = (row["fixture_id"], command_id, payload, row)

    def take(self, row, header, line):
        codec = self.codec
        if set(row) != SEND_FIELDS:
            raise ReplayError("send row has missing or unexpected fields")
        run, fixture, device = row["run_id"], row["fixture_id"], row["device"]
        desk = header.get("desk")
        if desk is None:
            raise ReplayError("a run with no desk mapping sent a command")
        if (fixture, device) != (desk["fixture_id"], desk["device"]):
            raise ReplayError("send row names a fixture or device other than its run's desk mapping")
        encoded = self.encoded.get((run, row["trace_id"]))
        if encoded is None or encoded[0] != fixture:   # check 1
            raise ReplayError("send row names no earlier encoded row of its run and fixture")
        _fixture, cmd, payload, source = encoded
        if COMMANDS.get(row["action"]) != cmd:
            raise ReplayError("send row's action differs from its encoded row's")
        outcome, sent, wire, answer = row["outcome"], row["sent"], row["wire"], row["answer"]
        dark = desk["light"] is False
        if (outcome == "dropped_dark") != (dark and cmd == codec.CMD_KEEP_LIT):
            raise ReplayError("only a dark rehearsal drops a keep-lit as dark, and it sends none")
        if dark and cmd == codec.CMD_MOVE:
            payload = bytearray(payload)
            payload[2] &= ~codec.FLAG_LIT_DURING_MOVE
            payload[7] = 0
            payload = bytes(payload)
        if (outcome not in SEND_OUTCOMES or sent not in (True, False, None) or type(row["attempt"]) is not int
                or row["attempt"] < 1):
            raise ReplayError("send row has an unknown outcome, sent value or attempt")
        if outcome.startswith("dropped_"):
            if sent is not False or wire is not None or answer is not None or row["called_mono"] is not None:
                raise ReplayError("a dropped command can't have been called or sent")
            return
        called, answered = row["called_mono"], row["answered_mono"]
        if not finite(called) or not finite(answered) or answered < called:
            raise ReplayError("send row's call and answer times are missing or out of order")
        if (sent is None) != (answer is None) or (sent is True) != (wire is not None):
            raise ReplayError("send row's sent, wire and answer disagree")
        if outcome == "acked" and sent is not True:
            raise ReplayError("an acknowledged command has no wire frame")
        if wire is None:
            return
        if not isinstance(wire, dict) or set(wire) != {"seq", "payload_hex"} or not is_u16(wire["seq"]):
            raise ReplayError("send row's wire frame needs its seq and payload")
        text = wire["payload_hex"]
        if (not isinstance(text, str) or len(text) != 2 * codec.PAYLOAD_SIZE[cmd]
                or re.fullmatch(r"[0-9a-f]+", text) is None):
            raise ReplayError("send row's wire payload has the wrong length or hex")
        sent_payload = bytes.fromhex(text)
        if codec.payload_seq(sent_payload) != wire["seq"] or not codec.payload_ok(cmd, sent_payload):
            raise ReplayError("send row's wire payload disagrees with its seq or the firmware codec")
        skip = 4 if cmd == codec.CMD_KEEP_LIT else 2
        if sent_payload[skip:] != payload[skip:]:   # check 2
            raise ReplayError("wire payload differs from the encoded payload beyond the seq and lit_seq")
        if not isinstance(answer, dict):
            raise ReplayError("a sent command needs its answer")
        if cmd == codec.CMD_KEEP_LIT:   # check 3
            lit_seq = struct.unpack_from("<H", sent_payload, 2)[0]
            if self.lit.get((run, fixture)) != (source.get("move_seq"), lit_seq):
                raise ReplayError("keep-lit's lit_seq isn't the wire seq of the latest accepted lit MOVE")
        if cmd == codec.CMD_MOVE and outcome == "acked":
            if answer.get("result") != "accepted":
                raise ReplayError("an acknowledged MOVE's answer isn't accepted")
            move = codec.parse_move(sent_payload)
            lit = move.spot_level > 0 and not move.flags & codec.FLAG_SOURCE_HAND
            self.lit[(run, fixture)] = (source["sequence"], wire["seq"]) if lit else None
        if cmd == codec.CMD_STOP and outcome == "acked":
            self.lit[(run, fixture)] = None
        delivered = cmd == codec.CMD_MOVE or answer.get("status") == "sent"
        at_mono, at_ts = header["at_mono"], header["at_ts"]
        self.frames.setdefault(device, []).append(WireFrame(
            line, row["trace_id"], cmd, wire["seq"], sent_payload, at_ts + called - at_mono,
            at_ts + answered - at_mono, answer, delivered))

    def take_device(self, row, header):
        """A device row: the board's readback, joint angles and states only."""
        desk = header.get("desk")
        values = row.get("values")
        if (set(row) != DEVICE_FIELDS or desk is None
                or (row["fixture_id"], row["device"]) != (desk["fixture_id"], desk["device"])
                or not finite(row["at_mono"]) or not isinstance(values, dict) or not set(values) <= READBACK
                or not all(value is None or type(value) in (bool, int, str) for value in values.values())):
            raise ReplayError("device row isn't its run's desk board's readback")

    def order(self):
        """Check 4. Each board's frames in time order; two calls that overlapped went on the air in the integration's
        own count, so the lower seq goes first."""
        codec = self.codec
        for frames in self.frames.values():
            frames.sort(key=lambda f: (f.called, f.line))
            for i in range(len(frames) - 1):
                first, second = frames[i], frames[i + 1]
                if second.called < first.answered and 1 <= (first.seq - second.seq) & 0xFFFF <= SEQ_WINDOW:
                    frames[i], frames[i + 1] = second, first
            last, owner = None, {}
            for frame in frames:
                if not frame.delivered:
                    continue
                kind = codec.classify_seq(last, frame.seq)
                if frame.command_id != codec.CMD_STOP:
                    if kind == "stale":
                        raise ReplayError(f"line {frame.line}: the board would refuse this wire seq as stale")
                    if kind == "duplicate" and owner.get(frame.seq) != frame.trace_id:
                        raise ReplayError(f"line {frame.line}: a wire seq repeats for another command")
                if kind == "new":
                    last, owner[frame.seq] = frame.seq, frame.trace_id
        return sum(len(frames) for frames in self.frames.values())


def verify_trace(handle, codec, *, collect_sim=False, headers=None, wire=None):
    """wire: a WireCheck that keeps each board's frames for replay_wire (else a fresh one)."""
    events = {}
    states = {}
    groups = {}
    headers = {} if headers is None else headers   # trace-2 and -3: run_id -> its run header
    wire = WireCheck(codec) if wire is None else wire
    counts = {"encoded_only": 0, "refused": 0, "state": 0, "send": 0, "device": 0}
    for line, raw in enumerate(iter(lambda: handle.readline(MAX_LINE_BYTES + 1), b""), 1):
        try:
            if len(raw) > MAX_LINE_BYTES:
                raise ReplayError("trace row exceeds the replay size limit")
            row = json.loads(raw, object_pairs_hook=json_object, parse_constant=reject_constant)
            if not isinstance(row, dict) or row.get("schema") not in TRACE_SCHEMAS:
                raise ReplayError("unrecognized trace schema")
            version_2 = row["schema"] in (TRACE_SCHEMA_2, TRACE_SCHEMA_3)
            if version_2 and row.get("kind") == "run_header":
                verify_header(row, headers)
                continue
            if version_2 and row.get("run_id") not in headers:
                raise ReplayError("trace-2 row before its run header")
            if row["schema"] == TRACE_SCHEMA_3 and row.get("kind") == "send":
                if headers[row["run_id"]]["schema"] != TRACE_SCHEMA_3:
                    raise ReplayError("send row in a run whose header isn't trace-3")
                wire.take(row, headers[row["run_id"]], line)
                counts["send"] += 1
                continue
            if row["schema"] == TRACE_SCHEMA_3 and row.get("kind") == "device":
                wire.take_device(row, headers[row["run_id"]])
                counts["device"] += 1
                continue
            if "kind" in row:
                raise ReplayError("unrecognized trace row kind")
            run, fixture = row.get("run_id"), row.get("fixture_id")
            if not nonempty_string(run) or not nonempty_string(fixture):
                raise ReplayError("row is missing its run or fixture identity")
            revision, profile = row.get("registration_revision"), row.get("profile_sha256")
            if not nonempty_string(revision) or not isinstance(profile, str) or not re.fullmatch(r"[0-9a-f]{64}", profile):
                raise ReplayError("row is missing its registration revision or profile digest")
            index = row.get("event_index")
            if type(index) is not int or index != events.get(run, 0) + 1:
                raise ReplayError("event index must be consecutive from one within each run")
            events[run] = index
            action, status = row.get("action"), row.get("status")
            if not isinstance(action, str) or not isinstance(status, str) or status not in counts:
                raise ReplayError("unrecognized trace action or status")
            if "sent" in row and row["sent"] is not False:
                raise ReplayError("preview row cannot claim a transmission")
            state = states.setdefault((run, fixture), FixtureState(revision, profile))
            if (revision, profile) != (state.registration_revision, state.profile_sha256):
                raise ReplayError("fixture registration or profile changed within a run")
            if status == "encoded_only":
                frame = verify_command(row, state, codec, line)
                if row["schema"] == TRACE_SCHEMA_3:
                    wire.add_encoded(row, frame.command_id, bytes.fromhex(frame.payload_hex))
                if version_2 and action == "aim_move":
                    verify_binding_2(row, headers[run], row["effective"])
                if collect_sim:
                    groups.setdefault((run, fixture), []).append(frame)
            elif row.get("frame") is not None:
                raise ReplayError("non-encoded row has a frame")
            elif status == "state":
                if action not in STATE_ACTIONS:
                    raise ReplayError("unrecognized lifecycle action")
                if action != "arrived":
                    state.lit_move = None
            elif action == "aim_move":
                state.lit_move = None
            counts[status] += 1
        except (ReplayError, ValueError, UnicodeError, RecursionError) as error:
            reason = str(error) if isinstance(error, ReplayError) else "invalid JSON row"
            raise ReplayError(f"line {line}: {reason}") from None
    if not counts["encoded_only"]:
        raise ReplayError("trace contains no encoded FC13 commands")
    counts["wire"] = wire.order()
    return counts, groups


def replay_simulator(executable, sha256, groups):
    path = Path(executable).resolve()
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != sha256:
            raise ReplayError("simulator SHA-256 differs from the expected build digest")
    checked = 0
    for frames in groups.values():
        commands = "".join(f"#sim fc13 {frame.command_id:02x} {frame.payload_hex}\n" for frame in frames)
        try:
            result = subprocess.run([str(path)], input=commands, capture_output=True, encoding="ascii", timeout=30)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
            raise ReplayError(f"line {frames[0].line}: simulator could not complete the fixture run") from None
        if (result.returncode or len(replies) != len(frames) + 1 or not isinstance(replies[0], dict)
                or replies[0].get("event") != "boot" or replies[0].get("fw") != "c12-v2"
                or replies[0].get("contract") != 1):
            raise ReplayError(f"line {frames[0].line}: invalid v2-a simulator boot or reply count")
        for frame, reply in zip(frames, replies[1:]):
            if reply != {"sim": "ok", "reply": frame.expected_reply}:
                raise ReplayError(f"line {frame.line}: simulator reply differs from the expected v2-a result")
            checked += 1
    return checked


def replay_wire(executable, sha256, wire):
    """Check 5. Each board's delivered frames, in check 4's order, through one simulator run per board (one board,
    across every run): a MOVE's MOVE_ACK result, and the latch after a STOP or RESUME, must be the board's own."""
    path = Path(executable).resolve()
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != sha256:
            raise ReplayError("simulator SHA-256 differs from the expected build digest")
    codec, checked = wire.codec, 0
    for frames in wire.frames.values():
        frames = [frame for frame in frames if frame.delivered]
        if not frames:
            continue
        lines = []
        for frame in frames:
            lines.append(f"#sim fc13 {frame.command_id:02x} {frame.payload.hex()}\n")
            if frame.command_id in (codec.CMD_STOP, codec.CMD_RESUME):
                lines.append("1 status\n")
        try:
            result = subprocess.run([str(path)], input="".join(lines), capture_output=True, encoding="ascii",
                                    timeout=30)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
            raise ReplayError(f"line {frames[0].line}: simulator could not complete the board's frames") from None
        if result.returncode or len(replies) != len(lines) + 1 or replies[0].get("event") != "boot":
            raise ReplayError(f"line {frames[0].line}: invalid simulator boot or reply count for the board's frames")
        answers = iter(replies[1:])
        for frame in frames:
            reply = next(answers)
            if reply.get("sim") != "ok":
                raise ReplayError(f"line {frame.line}: the simulator refused the frame")
            hex_reply, said = reply.get("reply"), frame.answer
            if frame.command_id == codec.CMD_MOVE:
                if said.get("result") != "no_ack":
                    ack = bytes.fromhex(hex_reply) if hex_reply else b""
                    seq, result = struct.unpack_from("<HB", ack, 1) if len(ack) == 6 else (None, None)
                    if (ack[:1] != bytes([codec.CMD_MOVE_ACK]) or seq != frame.seq or result is None
                            or result >= len(codec.RESULT_NAMES) or codec.RESULT_NAMES[result] != said.get("result")):
                        raise ReplayError(f"line {frame.line}: "
                                          "the simulator answers this MOVE otherwise than the board")
            elif hex_reply is not None:
                raise ReplayError(f"line {frame.line}: the simulator answered a command that has no reply")
            if frame.command_id in (codec.CMD_STOP, codec.CMD_RESUME):
                status = next(answers)
                if status.get("stop_latched") is not said.get("stop_latched"):
                    raise ReplayError(f"line {frame.line}: the simulator's Stop latch differs from the board's")
            checked += 1
    return checked


class Chained:
    """The trace files one after another, as one readline handle: a run's day files in order."""

    def __init__(self, paths):
        self.paths, self.handle = list(paths), None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        if self.handle is not None:
            self.handle.close()

    def readline(self, size=-1):
        while True:
            if self.handle is None:
                if not self.paths:
                    return b""
                self.handle = Path(self.paths.pop(0)).open("rb")
            line = self.handle.readline(size)
            if line:
                return line if line.endswith(b"\n") or not self.paths else line + b"\n"
            self.handle.close()
            self.handle = None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, nargs="+",
                        help="the local trace files (c12-preview-trace-1 or -2, or c12-fixture-trace-3) in time "
                             "order: a run's day files in day order (a run that crosses UTC midnight is in two), "
                             "and for trace-3 every run the board took part in")
    parser.add_argument("--firmware-zip", required=True, type=Path, help="private pinned firmware ZIP")
    parser.add_argument("--sim", type=Path, help="optional locally built v2-a c12v2_sim executable")
    parser.add_argument("--sim-sha256", help="expected SHA-256 of the supplied simulator executable")
    args = parser.parse_args(argv)
    if bool(args.sim) != bool(args.sim_sha256) or (args.sim_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", args.sim_sha256)):
        parser.error("supply --sim and its 64-hex-digit --sim-sha256 together")
    try:
        codec, vectors = firmware_codec(args.firmware_zip)
        verified = verify_vectors(codec, vectors)
        headers, wire = {}, WireCheck(codec)
        with Chained(args.trace) as handle:
            counts, groups = verify_trace(handle, codec, collect_sim=bool(args.sim), headers=headers, wire=wire)
        if args.sim and any(h["simulator_sha256"] != args.sim_sha256.lower() for h in headers.values()):
            raise ReplayError("the trace's run header names another simulator build")
        replayed = replay_simulator(args.sim, args.sim_sha256.lower(), groups) if args.sim else 0
        on_air = replay_wire(args.sim, args.sim_sha256.lower(), wire) if args.sim else 0
    except ReplayError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        print("FAIL could not read the local trace, pinned firmware ZIP or simulator", file=sys.stderr)
        return 1
    print(f"PASS firmware {FIRMWARE_COMMIT}, {verified} vectors, {counts['encoded_only']} encoded, "
          f"{counts['refused']} refused, {counts['state']} lifecycle, {replayed} replayed in the v2-a host simulator; "
          f"{counts['send']} send rows, {counts['wire']} frames on the air, {on_air} of them replayed per board")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
