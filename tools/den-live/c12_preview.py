"""Offline FC13 projection. The coordinator owns the clock, log and Stop latch."""

import math
import uuid

import c12_fc13

SCHEMA = "c12-preview-trace-1"
LEASE_S = 3.0


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def fixture_rows(fixtures):
    """Use the raw FC13 limits for encoding and adapt the core's fixed 5 degree inset once."""
    rows = {}
    for fixture_id, original in fixtures.items():
        profile = original["profile"]
        revision = original["registration_revision"]
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("preview fixture needs a registration revision")
        if not isinstance(profile.get("axes"), list) or len(profile["axes"]) != 2 or any(
               not isinstance(axis, dict) or not finite(axis.get(key)) or axis[key] <= 0
               for axis in profile["axes"] for key in ("v_max", "a_max", "j_max")):
            raise ValueError("preview fixture needs positive modeled motion limits")
        for key in ("aim_origin_m", "horizontal_world_reference"):
            vector = original[key]
            if len(vector) != 3 or not all(finite(x) for x in vector):
                raise ValueError("invalid preview fixture geometry")
        if math.hypot(*original["horizontal_world_reference"][:2]) <= 1e-9:
            raise ValueError("invalid preview fixture forward basis")
        start = original["start_joints_cdeg"]
        if len(start) != 2 or any(type(x) is not int for x in start):
            raise ValueError("preview fixture needs an integer starting pose")
        inset = profile["host_inset_cdeg"]
        ranges = {}
        for axis, angle in zip(("pan", "tilt"), start):
            lo, hi = profile["range_cdeg"][axis]
            if not lo + inset <= angle <= hi - inset:
                raise ValueError("preview starting pose outside inset limits")
            # den-core's joints.in_range will subtract its own 5 degree margin.
            ranges[axis] = [(lo + inset - 500) / 100, (hi - inset + 500) / 100]
        rows[fixture_id] = dict(original, real=False, profile=dict(profile, range=ranges, dim_motion=False))
    return rows


class Preview:
    def __init__(self, fixtures, *, profile_hash, move_time, write):
        self.fixtures = fixtures
        self.profile_hash = profile_hash
        self.move_time = move_time
        self.write = write
        self.run_id = uuid.uuid4().hex
        self.event_index = 0
        self.trace_complete = True
        self.spots = {
            fid: dict(next_seq=0, joints_cdeg=list(row["start_joints_cdeg"]), spot_level=0,
                      spot_mired=370, move_seq=None, move_decision_id=None, move_event_index=None, event_index=None,
                      expires_at=None, travel=None, pending_level=0, who=None)
            for fid, row in fixtures.items()
        }

    def _trace(self, fid, action, status, t, **fields):
        self.event_index += 1
        row, spot = self.fixtures[fid], self.spots[fid]
        record = dict(schema=SCHEMA, run_id=self.run_id, event_index=self.event_index, at=t,
                      fixture_id=fid, registration_revision=row["registration_revision"],
                      profile_sha256=self.profile_hash(row["profile"]), action=action, status=status,
                      move_seq=spot["move_seq"], move_decision_id=spot["move_decision_id"],
                      move_event_index=spot["move_event_index"], **fields)
        if not self.write(record, t):
            self.trace_complete = False
        return self.event_index

    def _clear(self, spot):
        spot.update(spot_level=0, expires_at=None, travel=None, pending_level=0,
                    move_seq=None, move_decision_id=None, move_event_index=None, who=None)

    def darken(self, fid, action, t):
        spot = self.spots[fid]
        if spot["move_seq"] is not None:
            self._clear(spot)
            spot["event_index"] = self._trace(fid, action, "state", t)

    def advance(self, t, evidence_until, invalidate, *, stopping=False):
        for fid, spot in self.spots.items():
            travel = spot["travel"]
            if travel:
                duration = travel["arrives_at"] - travel["started_at"]
                sample_at = min(t, spot["expires_at"]) if spot["expires_at"] is not None else t
                fraction = min(1, max(0, (sample_at - travel["started_at"]) / duration))
                # Shared modeled progress for both axes. This is a visual approximation of the board path.
                s = fraction ** 3 * (10 - 15 * fraction + 6 * fraction ** 2)
                spot["joints_cdeg"] = [round(a + (b - a) * s) for a, b in zip(
                    travel["start_joints_cdeg"], travel["target_joints_cdeg"])]
            if stopping:
                continue
            if spot["expires_at"] is not None:
                deadline = evidence_until(spot["who"], t)
                if t >= spot["expires_at"] or deadline is None:
                    self.darken(fid, "expired" if t >= spot["expires_at"] else "evidence_stale", t)
                    invalidate(fid)
                    continue
            if travel and t >= travel["arrives_at"]:
                spot.update(travel=None, spot_level=spot["pending_level"])
                spot["event_index"] = self._trace(fid, "arrived", "state", t)

    def apply(self, intent, t, *, who=None, evidence_until=None, stopped=False):
        fid, action = intent["fixture_id"], intent["action"]
        spot, row = self.spots[fid], self.fixtures[fid]
        seq = spot["next_seq"]
        wire = dict(intent)
        try:
            if stopped and action in ("aim_move", "keep_lit"):
                raise c12_fc13.Refusal("preview_stopped")
            if action == "keep_lit":
                if not spot["spot_level"] or evidence_until is None:
                    raise c12_fc13.Refusal("no_active_move")
                wire["decision_id"] = spot["move_decision_id"]
            if action == "aim_move" and spot["travel"]:
                # The policy may have checked from its previous target, ahead of the modeled head.
                wire["lit_during_move"] = False
            budget = math.floor((evidence_until - t) * 1000) if evidence_until is not None else None
            encoded = c12_fc13.encode(wire, row["profile"], seq=seq, current_joints=spot["joints_cdeg"],
                                      token=spot["move_seq"], remaining_ms=budget)
            if action == "aim_move" and encoded["effective"]["spot_level"] and evidence_until is None:
                raise c12_fc13.Refusal("evidence_stale")
        except c12_fc13.Refusal as exc:
            if action == "aim_move":
                self._clear(spot)
            index = self._trace(fid, action, "refused", t, decision_id=wire.get("decision_id"),
                                requested=intent, reason=str(exc))
            if action == "aim_move":
                spot["event_index"] = index
            return False
        effective = encoded["effective"]
        frame = {key: encoded[key] for key in ("endpoint", "cluster", "command_id")}
        frame.update(seq=seq, payload_hex=encoded["payload"].hex())
        spot["next_seq"] = (seq + 1) % 65536
        if action == "aim_move":
            target = [effective["pan_cdeg"], effective["tilt_cdeg"]]
            start = spot["joints_cdeg"]
            duration = self.move_time([(b - a) / 100 for a, b in zip(start, target)], row["profile"]["axes"])
            level = effective["spot_level"]
            travel = dict(start_joints_cdeg=start, target_joints_cdeg=target, started_at=t,
                          arrives_at=t + duration) if duration else None
            spot.update(move_seq=seq, move_decision_id=wire["decision_id"], move_event_index=self.event_index + 1,
                        travel=travel, who=who,
                        spot_level=level if not travel or effective["lit_during_move"] else 0,
                        spot_mired=effective["spot_mired"], pending_level=level, expires_at=t + LEASE_S if level else None)
            if not travel:
                spot["joints_cdeg"] = target
        elif action == "keep_lit":
            spot["expires_at"] = t + LEASE_S
        elif action in ("stop", "resume"):
            self._clear(spot)
        index = self._trace(fid, action, "encoded_only", t, decision_id=wire.get("decision_id"),
                            requested=intent, effective=effective, frame=frame, sent=False)
        if action != "keep_lit":
            spot["event_index"] = index
        return True

    def snapshot(self, t, evidence_until):
        public = ("joints_cdeg", "spot_level", "spot_mired", "move_seq", "move_decision_id", "move_event_index",
                  "event_index", "travel")
        rows = {}
        for fid, spot in self.spots.items():
            row = self.fixtures[fid]
            expiry = spot["expires_at"]
            if expiry is not None:
                expiry = min(expiry, evidence_until(spot["who"], t) or t)
            rows[fid] = {key: spot[key] for key in public} | dict(
                registration_revision=row["registration_revision"], profile_sha256=self.profile_hash(row["profile"]),
                aim_origin_m=row["aim_origin_m"], horizontal_world_reference=row["horizontal_world_reference"],
                expires_at=expiry)
        return dict(run_id=self.run_id, event_index=self.event_index, as_of=t,
                    trace_complete=self.trace_complete, fixtures=rows)
