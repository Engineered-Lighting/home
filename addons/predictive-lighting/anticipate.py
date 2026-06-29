"""anticipate.py — kinematic room anticipator.

For each Frigate person event, look at the recent trajectory (Frigate's
`path_data` field, already a list of `[[x_norm, y_norm], unix_ts]` entries)
and decide whether the person is heading toward a different room. If so,
emit an "ON" prediction for that room; when the prediction goes away,
emit "OFF."

The unifying primitive is *direction*, not transition statistics: where is
the person heading right now? The Markov predictor it replaces tried to
re-derive direction from aggregate transition counts.

Cross-camera predictions resolve via `camera_edges` in spatial_model.json
(per-camera N/E/S/W -> neighbor room name). Intra-camera ray-cast against
the camera's polygons is available as a secondary refinement but most homes
have one room per camera so it rarely fires.

Coordinate convention (image-space, normalized [0, 1]):
  x = 0 left, x = 1 right
  y = 0 top,  y = 1 bottom
  angle = atan2(dy, dx) in radians:
     0       -> east  (right)
    +pi/2    -> south (down)
    -pi/2    -> north (up)
    pi/-pi   -> west  (left)
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

# Tunables. Live-walk-tuned to suppress gait-noise flap that the offline
# replay didn't show (replay had clean trajectories; live data has body-
# bobbing left/right while walking forward).
#
# 2026-05-26: lowered 0.08 → 0.025 based on Phase 2 walk-diagnostic data.
# Walk #7 (a successful hit) showed real walking speeds of 0.01–0.16 with
# extended sub-0.08 stretches — the algorithm was missing most of the walk
# and only catching the final commitment-to-exit moment. Watching for
# false-positive blow-up during stationary periods; MIN_HOLD_S + hysteresis
# should suppress single-event noise. Revert via this comment if flap
# returns to >2 changes/walk.
SPEED_THRESHOLD = 0.025     # normalized units / second; below = at rest
VELOCITY_WINDOW_S = 3.0     # use path_data within this many seconds back —
                            # longer window averages out side-to-side bob
                            # (was 2.0)
VELOCITY_MIN_DT = 0.5       # need this much time-span to denoise (was 0.3)
PATH_RECENT_N = 8           # cap on number of path points considered (was 5)
FOOT_Y_CLAMP_HIGH = 0.80    # tighter clamp away from south edge (was 0.85)
FOOT_Y_CLAMP_LOW = 0.20
FOOT_X_CLAMP_HIGH = 0.80
FOOT_X_CLAMP_LOW = 0.20
HYSTERESIS_BUFFER_N = 5     # last-N-events majority vote (was 3)
HYSTERESIS_MAJORITY = 3     # need this many to agree (was 2)
# Minimum hold-on time after a target is published. Suppresses ON→OFF→ON
# flap on the same track. After MIN_HOLD_S the prediction can flip
# (target change or speed-drop). A track ending or moving below threshold
# for the duration still clears cleanly.
MIN_HOLD_S = 2.5
# Chained lookahead: how many rooms to pre-warm in the direction of motion.
# 1 = legacy single-room behaviour (predict only the immediate next room).
# 2 = primary + one further hop in the same compass direction. Lights up the
#     whole path through transit rooms (e.g. LR→DR→KIT pre-warms DR AND KIT
#     when the user leaves the living room headed east). Requires linear-
#     enough topology — the chain stops at the first edge that has no neighbor
#     in the same direction. Sticky-decay in the Living Lights classifier
#     (ANTICIPATED_DECAY_S=12) absorbs the OFF-flap when a chained room
#     briefly stops being predicted.
CHAIN_DEPTH = 2


def _derive_velocity(path_data):
    """Compute (speed_norm, angle_rad, current_point) from the recent tail of
    a Frigate path_data list. Returns None if not enough motion/data.

    path_data is a list of `[[x, y], unix_ts]` entries. The list accumulates
    over the track's lifetime; we only look at the most recent VELOCITY_WINDOW_S
    seconds (up to PATH_RECENT_N points).
    """
    if not path_data or len(path_data) < 2:
        return None
    pts = sorted(path_data, key=lambda p: p[1])
    last_ts = pts[-1][1]
    # Most recent points within the window.
    window = [p for p in pts if last_ts - p[1] <= VELOCITY_WINDOW_S]
    if len(window) < 2:
        # Fall back to last two points regardless of window
        window = pts[-2:]
    # Cap
    window = window[-PATH_RECENT_N:]
    (x0, y0), t0 = window[0]
    (x1, y1), t1 = window[-1]
    dt = t1 - t0
    if dt < VELOCITY_MIN_DT:
        return None
    dx = x1 - x0
    dy = y1 - y0
    speed = math.hypot(dx, dy) / dt
    angle = math.atan2(dy, dx)
    return speed, angle, (x1, y1), last_ts


def _ray_exits_edge(point, angle_rad):
    """Cast a ray from `point` in direction `angle_rad`. Return the FIRST
    edge of the [0,1]^2 unit square the ray exits through: 'north' (y=0),
    'east' (x=1), 'south' (y=1), 'west' (x=0).

    The point is assumed to be strictly inside [0,1]^2. Foot Y is clamped
    in the caller to avoid degenerate distances.
    """
    fx, fy = point
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    # For each of 4 edges, compute the (signed) parameter t at which the ray
    # crosses; keep only positive (future) crossings.
    candidates = []
    if cos_a > 1e-9:
        candidates.append(("east", (1.0 - fx) / cos_a))
    elif cos_a < -1e-9:
        candidates.append(("west", (-fx) / cos_a))
    if sin_a > 1e-9:
        candidates.append(("south", (1.0 - fy) / sin_a))
    elif sin_a < -1e-9:
        candidates.append(("north", (-fy) / sin_a))
    # Smallest positive t = nearest exit edge.
    candidates = [(edge, t) for edge, t in candidates if t > 0]
    if not candidates:
        return None
    edge, _ = min(candidates, key=lambda et: et[1])
    return edge


def _polygon_contains(polygon, x, y):
    """Standard ray-cast point-in-polygon (Wikipedia even-odd rule).

    `polygon` is a list of `[x, y]` vertices in [0, 1] normalized coords.
    """
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _zone_at(spatial_cameras, camera, x, y):
    """Which zone in this camera contains (x, y)? Lowercase normalized;
    returns None if no zone contains the point. Prefers smaller zones over
    `whole_*` background zones."""
    cam = spatial_cameras.get(camera)
    if not cam:
        return None
    candidates = []
    for zone_name, zinfo in cam.get("zones", {}).items():
        poly = zinfo.get("polygon") or []
        if poly and _polygon_contains(poly, x, y):
            candidates.append(zone_name)
    if not candidates:
        return None
    # Prefer non-whole zones.
    specific = [z for z in candidates if not z.lower().startswith("whole_")]
    return (specific[0] if specific else candidates[0]).lower()


class Anticipator:
    """Per-event projector + per-track + per-room state.

    Caller pattern:
        ant = Anticipator(spatial_model)
        for evt in mqtt stream:
            for change in ant.process(evt):
                publish(change)        # {'room': X, 'state': 'on'|'off', ...}

    `process` returns the *changes* — rooms whose ON/OFF state flipped on
    this event. Caller publishes those to MQTT. No publish-spam on
    no-change events.
    """

    def __init__(self, spatial_model: dict):
        self.cameras = spatial_model.get("cameras", {}) or {}
        self.camera_edges = spatial_model.get("camera_edges", {}) or {}
        # Lower-case zone names; spatial_model has mixed casing.
        self.zone_to_room = {
            k.lower(): v for k, v in
            (spatial_model.get("zone_to_room") or {}).items()
        }
        # rooms = the SET OF DESTINATIONS the anticipator can ever publish
        # (from zone_to_room values + edge target rooms). Used for startup-
        # clear by the app.
        self.rooms = sorted({
            r for r in self.zone_to_room.values() if r
        } | {
            target
            for cam_edges in self.camera_edges.values()
            for target in cam_edges.values()
            if target
        })
        # Per-track current OFFICIAL primary target room (after hysteresis).
        # Stays a single room because hysteresis-voting only makes sense over
        # one classification axis. The CHAIN built on top of this is the
        # set of rooms actually published (primary + lookahead).
        self._track_target: dict[str, Optional[str]] = {}
        # Per-track most recent CHAIN of rooms published (primary + lookahead
        # hops). Diff against the next computed chain to know which rooms
        # changed ON/OFF state. Empty frozenset = no prediction.
        self._track_chain: dict[str, frozenset[str]] = {}
        # Per-track compass edge used for the latest lookahead. Cached so
        # the chain only changes when the primary changes, not on every event.
        self._track_edge: dict[str, Optional[str]] = {}
        # Per-track timestamp of the last CHAIN change (for MIN_HOLD_S).
        self._track_target_change_ts: dict[str, float] = {}
        # Reverse index: track ids currently pointing at this room.
        self._room_tracks: dict[str, set[str]] = defaultdict(set)
        # Last-event timestamp per track, for stale-track sweeping.
        self._track_last_seen: dict[str, float] = {}
        # Hysteresis: last-N raw computed targets per track (most recent right).
        self._track_history: dict[str, list[Optional[str]]] = defaultdict(list)

    # -- helpers -----------------------------------------------------------

    def _camera_to_room(self, camera: str) -> Optional[str]:
        """Which ROOM does this CAMERA observe? Inferred from zone_to_room
        on the camera's zones (consistent across all its zones in this
        home). Fallback: the camera name itself if it matches a room name."""
        cam = self.cameras.get(camera) or {}
        for zone in cam.get("zones", {}).keys():
            r = self.zone_to_room.get(zone.lower())
            if r:
                return r
        return None  # camera has no polygons -> not an observable room

    def _build_chain(self, root_room: Optional[str],
                     edge: Optional[str]) -> frozenset[str]:
        """Build the chain of rooms to publish from a primary target.

        Walks `camera_edges` in the same compass direction the user is moving:
        the user is heading `edge` (e.g. 'east') out of the source camera,
        so the next room beyond `root_room` is whatever sits on `root_room`'s
        same-edge neighbor. Repeated up to CHAIN_DEPTH hops. Stops on the
        first edge that has no neighbor (end of the house) or a self-cycle.

        Returns empty frozenset if no primary target. With CHAIN_DEPTH=1 or
        no edge cached, returns just {root_room} — same behaviour as the
        pre-chain single-target predictor."""
        if not root_room:
            return frozenset()
        chain = [root_room]
        if edge and CHAIN_DEPTH > 1:
            current = root_room
            for _ in range(CHAIN_DEPTH - 1):
                nxt = (self.camera_edges.get(current) or {}).get(edge)
                if not nxt or nxt in chain:
                    break
                chain.append(nxt)
                current = nxt
        return frozenset(chain)

    def _flip(self, track_id: str, new_chain: frozenset[str], ts: float,
              force_clear: bool = False):
        """Update per-track chain state. Return list of room state changes
        the caller should publish.

        Each change: {room, state, source_track, ts}. State is 'on' or 'off'.
        Multi-track OR semantics: a room flips ON when its first track adds
        it to a chain; flips OFF when its last track removes it.

        force_clear bypasses the MIN_HOLD_S guard — for track-end and
        sweep_stale, where we know the prediction needs to die now.
        """
        prev_chain = self._track_chain.get(track_id, frozenset())
        self._track_last_seen[track_id] = ts
        if prev_chain == new_chain:
            return []
        # MIN_HOLD_S guard — SYMMETRIC: block ANY chain change for this long
        # after the last one. The first change for a track passes through
        # unblocked (target_change_ts defaults to 0). force_clear=True
        # (track-end / sweep_stale) bypasses the hold.
        if (not force_clear
                and (ts - self._track_target_change_ts.get(track_id, 0))
                    < MIN_HOLD_S):
            return []  # too soon to change; keep prev
        changes = []
        # Rooms LEAVING the chain.
        for room in (prev_chain - new_chain):
            self._room_tracks[room].discard(track_id)
            if not self._room_tracks[room]:
                changes.append({"room": room, "state": "off",
                                "source_track": track_id, "ts": ts})
        # Rooms ENTERING the chain.
        for room in (new_chain - prev_chain):
            was_empty = not self._room_tracks[room]
            self._room_tracks[room].add(track_id)
            if was_empty:
                changes.append({"room": room, "state": "on",
                                "source_track": track_id, "ts": ts})
        if not new_chain:
            self._track_chain.pop(track_id, None)
        else:
            self._track_chain[track_id] = new_chain
        self._track_target_change_ts[track_id] = ts
        return changes

    # -- public API --------------------------------------------------------

    def process(self, event: dict):
        """Process one Frigate event payload. Returns `(changes, debug)`:
          changes: list of per-room state changes (possibly empty)
          debug:   dict of per-event diagnostic fields (or None for non-person
                   events). The caller publishes this to a debug MQTT topic so
                   the Phase-2 eval harness can compute predictability ceiling
                   and offline param sweeps without re-running the algorithm
                   live.
        """
        after = event.get("after") or {}
        if after.get("label") != "person":
            return [], None
        track_id = after.get("id")
        if not track_id:
            return [], None
        camera = after.get("camera")
        if not camera:
            return [], None

        # Debug skeleton — filled in as we walk the decision flow.
        debug = {
            "ts": after.get("frame_time", 0),
            "track_id": track_id,
            "camera": camera,
            "current_zones": after.get("current_zones") or [],
            "path_data_len_s": None,
            "derived_speed": None,
            "derived_angle_deg": None,
            "foot_xy": None,
            "target_room_raw": None,
            "target_room_official": None,
            "suppressed_reason": None,
            "event_type": event.get("type"),
        }
        pd = after.get("path_data") or []
        if len(pd) >= 2:
            debug["path_data_len_s"] = round(pd[-1][1] - pd[0][1], 3)

        # 'end' events: clear any prediction for this track (force, no hold).
        if event.get("type") == "end":
            changes = self._flip(track_id, frozenset(),
                                 after.get("frame_time", 0),
                                 force_clear=True)
            self._track_edge.pop(track_id, None)
            self._track_target.pop(track_id, None)
            debug["suppressed_reason"] = "track_end"
            return changes, debug

        # Derive velocity from the trajectory.
        vel = _derive_velocity(pd)
        if vel is None:
            # No reliable velocity yet — don't disturb prior prediction or
            # the hysteresis buffer. Just refresh last_seen.
            self._track_last_seen[track_id] = after.get("frame_time", 0)
            debug["suppressed_reason"] = "no_velocity"
            return [], debug
        speed, angle, point, ts = vel
        debug["ts"] = ts
        debug["derived_speed"] = round(speed, 4)
        debug["derived_angle_deg"] = round(math.degrees(angle), 1)
        debug["foot_xy"] = [round(point[0], 4), round(point[1], 4)]
        if speed < SPEED_THRESHOLD:
            # Real measurement, just below threshold — push None to the
            # hysteresis buffer so sustained idleness eventually clears.
            # No new edge is computed below threshold; reuse the cached one
            # so a sticky primary keeps its lookahead direction.
            official = self._hysteresis(track_id, None)
            prev_official = self._track_target.get(track_id)
            if official != prev_official:
                if official is None:
                    self._track_target.pop(track_id, None)
                    self._track_edge.pop(track_id, None)
                else:
                    self._track_target[track_id] = official
            new_chain = self._build_chain(official, self._track_edge.get(track_id))
            changes = self._flip(track_id, new_chain, ts)
            debug["target_room_official"] = official
            debug["suppressed_reason"] = "below_threshold"
            return changes, debug

        # Clamp foot away from FOV boundaries — a foot at y=1.0 makes the
        # south-exit distance ~0, which overwhelms any other candidate even
        # with a strongly east-pointing velocity. Same on each edge.
        x, y = point
        y = min(max(y, FOOT_Y_CLAMP_LOW), FOOT_Y_CLAMP_HIGH)
        x = min(max(x, FOOT_X_CLAMP_LOW), FOOT_X_CLAMP_HIGH)

        cam_room = self._camera_to_room(camera)
        target_room: Optional[str] = None

        # PRIMARY: edge exit -> camera_edges lookup.
        edge = _ray_exits_edge((x, y), angle)
        if edge:
            target_room = (self.camera_edges.get(camera) or {}).get(edge)

        # SECONDARY: if no edge target (e.g. camera_edges has null for that
        # edge), try intra-camera polygon hit. Only useful when the camera
        # has multiple rooms in its view (rare in this home).
        if not target_room:
            # Walk along the ray in small steps; first polygon we hit that
            # maps to a DIFFERENT room than the camera's own room wins.
            step = 0.05
            for k in range(1, 21):  # up to t=1.0 normalized units
                px = x + k * step * math.cos(angle)
                py = y + k * step * math.sin(angle)
                if not (0 < px < 1 and 0 < py < 1):
                    break  # ray left the FOV; edge case already handled above
                hit_zone = _zone_at(self.cameras, camera, px, py)
                if hit_zone:
                    mapped = self.zone_to_room.get(hit_zone)
                    if mapped and mapped != cam_room:
                        target_room = mapped
                        break

        # No cross-room target this event -> record None in the history.
        raw = target_room if (target_room and target_room != cam_room) else None
        official = self._hysteresis(track_id, raw)
        prev_official = self._track_target.get(track_id)
        # Cache the edge that produced this primary, so the lookahead chain
        # stays stable while the primary is sticky (hysteresis-locked) even
        # though per-event edges may oscillate.
        if official != prev_official:
            if official is None:
                self._track_target.pop(track_id, None)
                self._track_edge.pop(track_id, None)
            else:
                self._track_target[track_id] = official
                self._track_edge[track_id] = edge  # current event's edge
        new_chain = self._build_chain(official, self._track_edge.get(track_id))
        changes = self._flip(track_id, new_chain, ts)

        debug["target_room_raw"] = raw
        debug["target_room_official"] = official
        if not changes:
            if raw is None and official is None:
                debug["suppressed_reason"] = "no_target_resolved"
            elif official is None and raw is not None:
                debug["suppressed_reason"] = "hysteresis_no_majority"
            elif prev_official == official:
                debug["suppressed_reason"] = "no_change"
            else:
                debug["suppressed_reason"] = "min_hold_active"
        return changes, debug

    def _hysteresis(self, track_id: str, raw: Optional[str]) -> Optional[str]:
        """Smooth the per-event raw target through a last-N majority vote.
        Returns the OFFICIAL target that will be published. A single
        misclassified event doesn't flap the published prediction; sustained
        agreement does."""
        hist = self._track_history[track_id]
        hist.append(raw)
        if len(hist) > HYSTERESIS_BUFFER_N:
            del hist[0]
        # Majority vote: which non-None value (if any) has >= HYSTERESIS_MAJORITY?
        counts: dict[Optional[str], int] = defaultdict(int)
        for v in hist:
            counts[v] += 1
        # Tie-breaker: keep the current target if it still has at least 1 vote
        # (sticky).
        current = self._track_target.get(track_id)
        if current is not None and counts.get(current, 0) >= 1:
            # Has the new candidate UNAMBIGUOUSLY taken over?
            best_other = max(
                (k for k in counts if k != current and k is not None),
                key=lambda k: counts[k], default=None)
            if best_other and counts[best_other] >= HYSTERESIS_MAJORITY \
                    and counts[best_other] > counts[current]:
                return best_other
            # Otherwise stay on current (or go OFF only if explicit majority None).
            if counts.get(None, 0) >= HYSTERESIS_MAJORITY:
                return None
            return current
        # No current target: only emit a new one if the candidate hits majority.
        best = max((k for k in counts if k is not None),
                   key=lambda k: counts[k], default=None)
        if best and counts[best] >= HYSTERESIS_MAJORITY:
            return best
        return None

    def sweep_stale(self, now: float, stale_after_s: float = 5.0) -> list[dict]:
        """Clear predictions for tracks that haven't been seen recently.
        Caller invokes periodically (~every 2s) to catch tracks that ended
        without firing an 'end' event."""
        changes = []
        for tid in list(self._track_last_seen.keys()):
            if now - self._track_last_seen[tid] > stale_after_s:
                changes.extend(self._flip(tid, frozenset(), now, force_clear=True))
                self._track_last_seen.pop(tid, None)
                self._track_target_change_ts.pop(tid, None)
                self._track_history.pop(tid, None)
                self._track_edge.pop(tid, None)
                self._track_target.pop(tid, None)
        return changes
