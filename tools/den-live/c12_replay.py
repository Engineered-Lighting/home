"""Verify a local FC13 preview trace against the pinned C12 v2-a firmware archive."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import types
import zipfile


FIRMWARE_COMMIT = "cf1c325"
FIRMWARE_ZIP_SHA256 = "88c5fa3e348ffbcc97ab318eb7c55e045da89c20907254ede4eda1f9967dd2a2"
FIRMWARE_SOURCE = "c12-firmware-for-codex-20261112/source/c12-lit-tracking-v2/"
TRACE_SCHEMA = "c12-preview-trace-1"
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


def verify_trace(handle, codec, *, collect_sim=False):
    events = {}
    states = {}
    groups = {}
    counts = {"encoded_only": 0, "refused": 0, "state": 0}
    for line, raw in enumerate(iter(lambda: handle.readline(MAX_LINE_BYTES + 1), b""), 1):
        try:
            if len(raw) > MAX_LINE_BYTES:
                raise ReplayError("trace row exceeds the replay size limit")
            row = json.loads(raw, object_pairs_hook=json_object, parse_constant=reject_constant)
            if not isinstance(row, dict) or row.get("schema") != TRACE_SCHEMA:
                raise ReplayError("unrecognized trace schema")
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="complete local c12-preview-trace-1 JSONL file")
    parser.add_argument("--firmware-zip", required=True, type=Path, help="private pinned firmware ZIP")
    parser.add_argument("--sim", type=Path, help="optional locally built v2-a c12v2_sim executable")
    parser.add_argument("--sim-sha256", help="expected SHA-256 of the supplied simulator executable")
    args = parser.parse_args(argv)
    if bool(args.sim) != bool(args.sim_sha256) or (args.sim_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", args.sim_sha256)):
        parser.error("supply --sim and its 64-hex-digit --sim-sha256 together")
    try:
        codec, vectors = firmware_codec(args.firmware_zip)
        verified = verify_vectors(codec, vectors)
        with args.trace.open("rb") as handle:
            counts, groups = verify_trace(handle, codec, collect_sim=bool(args.sim))
        replayed = replay_simulator(args.sim, args.sim_sha256.lower(), groups) if args.sim else 0
    except ReplayError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        print("FAIL could not read the local trace, pinned firmware ZIP or simulator", file=sys.stderr)
        return 1
    print(f"PASS firmware {FIRMWARE_COMMIT}, {verified} vectors, {counts['encoded_only']} encoded, "
          f"{counts['refused']} refused, {counts['state']} lifecycle, {replayed} replayed in the v2-a host simulator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
