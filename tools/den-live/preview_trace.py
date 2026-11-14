"""Encode offline fixture commands for C12 endpoint 18, cluster 0xFC13.

Wire layout pinned to C12 cf1c325, contracts/c12v2/zcl-fc13.md. This module
has no transport. The caller owns the control latch, active MOVE and clocks,
and serializes calls to one trace writer per preview epoch. The wire encoder
is shared with the C12 lane.
"""

import copy
import hashlib
import json
import time
from pathlib import Path

import c12_fc13 as wire


MAX_PREVIEW_VALID_MS = 1500
# Contract 5's trace v2. Every row names the room model it was decided on (transform, equipment); every MOVE names
# its subject (track, camera epoch, association revision) and the reply it answers (request_id, which a lit MOVE must
# have). A run header row opens each day file a run writes to: the run's mono-and-wall anchor and the codec's and the
# host simulator's hashes.
SCHEMA = "c12-preview-trace-2"
FIRMWARE_COMMIT = "cf1c325"
# The v2-a host simulator the replay is checked with: c12v2_sim.exe, built on 2026-11-12 from the firmware's
# tests/v2/c12v2_sim.cpp (C12 lane). Contract 5, item 5: its SHA-256 was on disk nowhere until here.
SIMULATOR_SHA256 = "cf5ffec355c7b280bdcdce29480ff91b9080fc7b03c1fb35ced9d95823f4fc64"
BINDING_FIELDS = ("transform_id", "equipment_revision", "equipment_digest")
SUBJECT_FIELDS = ("track_id", "camera_epoch", "association_revision", "request_id")


def _codec_sha256():
    """The encoder's own source hash, line endings as git keeps them."""
    return hashlib.sha256(Path(wire.__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


CODEC_SHA256 = _codec_sha256()


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _pair(value):
    return isinstance(value, (list, tuple)) and len(value) == 2


def _frame(fixture_id, action, seq, decision_id, move_token, move):
    intent = {"fixture_id": fixture_id, "action": action, "decision_id": decision_id}
    if action != "aim_move":
        return wire.encode(intent, {}, seq=seq, token=move_token)
    if not isinstance(move, dict):
        raise wire.Refusal("move_required")
    start, target, ranges = (move.get(key) for key in ("start_cdeg", "target_cdeg", "range_cdeg"))
    if not _pair(target) or not _pair(ranges):
        raise wire.Refusal("invalid_joints_or_range")
    valid_ms = move.get("valid_ms", MAX_PREVIEW_VALID_MS)
    if not _integer(valid_ms, 0, MAX_PREVIEW_VALID_MS):
        raise wire.Refusal("invalid_valid_for_ms")
    travel = move.get("travel", "dark")
    intent.update(pan_cdeg=target[0], tilt_cdeg=target[1], level=move.get("level"), cct_k=move.get("cct_k"),
                  valid_for_ms=valid_ms, source=move.get("source", "coordinator"),
                  motion_style="dark" if travel == "none" else travel, lit_during_move=travel == "lit",
                  velocity_cdeg_s=move.get("velocity_cdeg_s", (0, 0)))
    profile = {
        "frame": wire.FRAME, "range_cdeg": dict(zip(("pan", "tilt"), ranges)), "host_inset_cdeg": 0,
        "motion_mode": move.get("motion_mode", 1), "lit_motion": move.get("lit_motion", True),
        "terminal_velocity": move.get("terminal_velocity", False),
        "max_velocity_cdeg_s": move.get("max_velocity_cdeg_s"),
    }
    frame = wire.encode(intent, profile, seq=seq, current_joints=start, remaining_ms=move.get("remaining_ms"))
    effective = frame["effective"]
    effective.update(target_cdeg=list(target), display_cct_k=intent["cct_k"],
                     travel="none" if effective["motion_style"] == "stationary" else effective["motion_style"],
                     fade_s=None, dim_level=None, effective_valid_ms=effective["valid_ms"] or wire.DEFAULT_VALID_MS)
    return frame


class PreviewTrace:
    """Write one trace per intent. The append callback defines local storage."""

    def __init__(self, epoch, append, *, initial_sequence=0, binding=None, profiles=None, clock=time.monotonic,
                 wall=time.time):
        if not isinstance(epoch, str) or not epoch or not _integer(initial_sequence, 0, 65535):
            raise ValueError("preview trace needs an epoch and a u16 initial sequence")
        self.epoch, self.append = epoch, append
        self.initial_sequence = initial_sequence
        binding = binding or {}
        self.registration_revision = binding.get("scan_revision")
        self.binding = {key: binding.get(key) for key in ("scan_revision", *BINDING_FIELDS)}
        self.profile_hashes = {
            fid: hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode("utf-8")).hexdigest()
            for fid, profile in (profiles or {}).items()
        }
        # The run header: what the storage writes first in each day file this run writes to (Preview._append).
        self.header = {
            "schema": SCHEMA, "kind": "run_header", "run_id": self.epoch, "at_mono": clock(), "at_ts": wall(),
            "codec": "c12_fc13", "codec_sha256": CODEC_SHA256, "firmware": FIRMWARE_COMMIT,
            "simulator_sha256": SIMULATOR_SHA256, "binding": dict(self.binding),
            "profile_sha256": dict(self.profile_hashes),
        }
        self.sequences = {}
        self.ordinal = 0

    def emit(self, fixture_id, action, *, decision_id=None, move_token=None, move=None, at=None, subject=None):
        """Append a frame or refusal and return it. Storage errors reach the caller. subject: the preview's
        {track_id, camera_epoch, association_revision, request_id}, recorded on a MOVE."""
        seq = self.sequences.get(fixture_id, self.initial_sequence)
        self.ordinal += 1
        row = {
            "schema": SCHEMA, "run_id": self.epoch, "preview_run_id": self.epoch,
            "trace_id": f"{self.epoch}/{fixture_id}/trace-{self.ordinal}",
            "event_index": self.ordinal, "ordinal": self.ordinal, "fixture_id": fixture_id, "action": action,
            "registration_revision": self.registration_revision, "profile_sha256": self.profile_hashes.get(fixture_id),
            **{key: self.binding[key] for key in BINDING_FIELDS},
            "decision_id": decision_id, "move_token": move_token, "sent": False,
            "requested": copy.deepcopy(move), "at": at,
        }
        if action == "aim_move":
            row.update({key: (subject or {}).get(key) for key in SUBJECT_FIELDS})
        try:
            frame = _frame(fixture_id, action, seq, decision_id, move_token, move)
        except wire.Refusal as error:
            row.update(outcome="refused", status="refused", reason=str(error), sequence=None)
        else:
            encoded = {key: frame[key] for key in ("endpoint", "cluster", "command_id")}
            encoded.update(seq=seq, payload_hex=frame["payload"].hex())
            move_seq = {"aim_move": seq, "keep_lit": move_token}.get(action)
            row.update(outcome="encoded_only", status="encoded_only", sequence=seq, effective=frame["effective"],
                       frame=encoded, endpoint=encoded["endpoint"], cluster=encoded["cluster"],
                       command=encoded["command_id"], payload_hex=encoded["payload_hex"], move_seq=move_seq,
                       move_decision_id=decision_id if move_seq is not None else None)
            # A failed append can have written a prefix or the full record. Never reuse its packet sequence.
            self.sequences[fixture_id] = (seq + 1) % 65536
        self.append(row)
        return row
