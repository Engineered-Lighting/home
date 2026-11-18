"""Offline C12 FC13 payload receipts for virtual Den control.

Wire source: firmware cf1c325 tools/c12v2/fc13.py and its published test vectors.
This module only writes receipts. It has no device address, credential or transport.
"""
import hashlib
import json
import os
import struct


class ComputedTrace:
    def __init__(self, folder, epoch):
        self.folder, self.epoch = folder, epoch
        self.sequence = {}
        self.latest = {}
        self.aims = {}

    def record(self, intent, date):
        fixture = intent["fixture_id"]
        action = intent["action"]
        seq = (self.sequence.get(fixture, 0) + 1) & 0xffff
        decision = intent.get("decision_id")
        if action == "aim_move":
            cmd = 0x00
            flags = 0x08 | int(bool(intent["lit_during_move"]))
            velocities = intent.get("velocity_cdeg_s", [0, 0])
            if any(velocities):
                flags |= 0x04
            fields = (seq, flags, intent["pan_cdeg"], intent["tilt_cdeg"],
                      round(intent["level"] * 254), round(1_000_000 / intent["cct_k"]),
                      *velocities, intent["valid_for_ms"])
            layout = "<HBhhBHhhH"
        elif action == "keep_lit":
            cmd = 0x01
            word = int.from_bytes(hashlib.sha256(str(decision).encode()).digest()[:2], "little")
            fields, layout = (seq, word), "<HH"
        elif action in ("stop", "resume"):
            cmd = 0x02 if action == "stop" else 0x03
            fields, layout = (seq,), "<H"
        else:
            return None
        raw = struct.pack(layout, *fields)
        if not wire_matches(cmd, raw, fields):
            raise ValueError("computed C12 payload failed its byte check")
        trace_id = f"{self.epoch}:{fixture}:{self.sequence.get(fixture, 0) + 1}:{hashlib.sha256(raw).hexdigest()[:16]}"
        receipt = {"schema": "den-c12-computed-trace-1", "trace_id": trace_id, "fixture_id": fixture,
                   "decision_id": decision, "cluster": 0xFC13, "endpoint": 18, "command": cmd, "payload_hex": raw.hex(),
                   "wire_source": "cf1c325", "byte_checked": True, "sent": False}
        dest = self.folder / date / "coordinator-c12-computed.jsonl"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(receipt, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.sequence[fixture] = self.sequence.get(fixture, 0) + 1
        self.latest[fixture] = trace_id
        if action == "aim_move":
            self.aims[fixture] = trace_id
        return trace_id


def wire_matches(command, raw, fields):
    """Check against the documented offsets independently of the pack format."""
    lengths = {0: 16, 1: 4, 2: 2, 3: 2}
    if len(raw) != lengths[command]:
        return False
    word = lambda offset, signed=False: int.from_bytes(raw[offset:offset + 2], "little", signed=signed)
    if command == 0:
        decoded = (word(0), raw[2], word(3, True), word(5, True), raw[7], word(8),
                   word(10, True), word(12, True), word(14))
        return decoded == fields and raw[2] & 0xf0 == 0 and raw[7] <= 254 and 154 <= word(8) <= 556
    return ((word(0), word(2)) if command == 1 else (word(0),)) == fields
