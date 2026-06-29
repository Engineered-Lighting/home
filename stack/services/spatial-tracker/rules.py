"""Activity rules v0.

A pure-python :class:`ActivityEngine` (injected clock + state getter — fully
testable without Home Assistant) plus the impure :class:`HAStateListener`
that feeds it from the HA WebSocket API (``subscribe_events state_changed``
over ``{HA_URL}/api/websocket``, tracking ``media_player.*`` and ``light.*``).

Rule table (enter-debounce s / exit-hysteresis s; '-' = exit immediately):

  watching_tv      10 / 20   room has a 'tv' device whose media_player is
                             playing; if track pos known, within 2.5 m of the
                             tv, else room-level match suffices
  listening_music  15 / 30   a 'speaker' device in the room is playing AND no
                             tv in the room is playing
  cooking          60 / 120  room id contains 'kitchen' AND dwell > 60 s AND
                             recent movement variance high
  working          120 / 60  'desk' zone/device proximity < 1.5 m AND
                             speed < 0.2 AND dwell > 120 s
  moving             2 / 5   speed > 0.5 m/s
  idle              30 / -   speed < 0.2 m/s

Notes on interpretation:
  * The dwell conditions written in the table are evaluated *in addition to*
    the enter-debounce (the spec lists both), so e.g. cooking fires after
    dwell>60 s has been true for a further 60 s of debounce.
  * Priority when several rules are on: the table order above (first wins).
  * If the HA link is stale (> 30 s without any HA websocket traffic) the
    media-dependent rules (watching_tv, listening_music) are suppressed and
    only location-derived labels remain.
  * Movement variance / speed need a precise position; room-only tracks can
    match only the room-level conditions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from typing import Callable, Optional

log = logging.getLogger("spatial.rules")

HA_STALE_S = 30.0
TV_PROXIMITY_M = 2.5
DESK_PROXIMITY_M = 1.5
SPEED_MOVING = 0.5
SPEED_STILL = 0.2
COOKING_DWELL_S = 60.0
WORKING_DWELL_S = 120.0
MOVEMENT_STD_HIGH_M = 0.25     # std-dev of position over the window = "high"
MOVEMENT_WINDOW_S = 60.0
TRACK_STATE_TTL_S = 60.0       # drop per-track rule state after this silence

PLAYING_STATES = {"playing"}

# (name, enter_s, exit_s or None, conf, needs_ha)
RULES: list[tuple[str, float, Optional[float], float, bool]] = [
    ("watching_tv", 10.0, 20.0, 0.8, True),
    ("listening_music", 15.0, 30.0, 0.7, True),
    ("cooking", 60.0, 120.0, 0.6, False),
    ("working", 120.0, 60.0, 0.65, False),
    ("moving", 2.0, 5.0, 0.7, False),
    ("idle", 30.0, None, 0.5, False),
]


class _RuleState:
    __slots__ = ("pending_since", "on", "false_since")

    def __init__(self):
        self.pending_since: Optional[float] = None
        self.on = False
        self.false_since: Optional[float] = None

    def step(self, cond: bool, now: float, enter_s: float,
             exit_s: Optional[float]) -> bool:
        if cond:
            self.false_since = None
            if not self.on:
                if self.pending_since is None:
                    self.pending_since = now
                if now - self.pending_since >= enter_s:
                    self.on = True
        else:
            self.pending_since = None
            if self.on:
                if exit_s is None:
                    self.on = False
                else:
                    if self.false_since is None:
                        self.false_since = now
                    if now - self.false_since >= exit_s:
                        self.on = False
                        self.false_since = None
        return self.on


class _TrackState:
    __slots__ = ("rules", "room", "room_since", "pos_hist", "last_seen",
                 "last_pos", "last_pos_ts")

    def __init__(self, now: float):
        self.rules: dict[str, _RuleState] = {name: _RuleState() for name, *_ in RULES}
        self.room: Optional[str] = None
        self.room_since: float = now
        self.pos_hist: deque = deque()  # (ts, x, y)
        self.last_seen = now
        self.last_pos: Optional[tuple[float, float]] = None
        self.last_pos_ts: Optional[float] = None


class ActivityEngine:
    """Pure rules engine.

    ``model_provider()`` -> ApartmentModel-like object.
    ``state_getter(entity_id)`` -> HA state string (e.g. "playing") or None.
    Call :meth:`note_ha_alive` whenever the HA link shows life; without it the
    media rules stay suppressed.
    """

    def __init__(self,
                 model_provider: Callable[[], object],
                 state_getter: Callable[[str], Optional[str]],
                 clock: Callable[[], float] = time.time):
        self.model_provider = model_provider
        self.state_getter = state_getter
        self.clock = clock
        self._tracks: dict[str, _TrackState] = {}
        self._ha_alive_ts: Optional[float] = None

    # ---- HA link freshness ---------------------------------------------------
    def note_ha_alive(self, ts: Optional[float] = None) -> None:
        self._ha_alive_ts = self.clock() if ts is None else ts

    def ha_stale(self, now: float) -> bool:
        return self._ha_alive_ts is None or (now - self._ha_alive_ts) > HA_STALE_S

    # ---- main entry ------------------------------------------------------------
    def annotate(self, tracks: list[dict], now: Optional[float] = None) -> list[dict]:
        """Merge activity fields into track dicts (mutates and returns them)."""
        now = self.clock() if now is None else now
        stale = self.ha_stale(now)
        model = self.model_provider()

        for track in tracks:
            ts = self._tracks.get(track["id"])
            if ts is None:
                ts = self._tracks[track["id"]] = _TrackState(now)
            ts.last_seen = now
            self._update_motion(ts, track, now)
            feats = self._features(ts, track, now)

            active: Optional[tuple[str, float]] = None
            for name, enter_s, exit_s, conf, needs_ha in RULES:
                cond = (not (needs_ha and stale)) and self._condition(
                    name, track, feats, model)
                on = ts.rules[name].step(cond, now, enter_s, exit_s)
                if on and active is None:
                    active = (name, conf)

            if active is not None:
                track["activity"] = active[0]
                track["activity_conf"] = active[1]
                track["activity_source"] = "rules"
            else:
                track["activity"] = None
                track["activity_conf"] = None
                track["activity_source"] = None

        self._gc(now)
        return tracks

    # ---- features ------------------------------------------------------------
    def _update_motion(self, ts: _TrackState, track: dict, now: float) -> None:
        room = track.get("room")
        if room != ts.room:
            ts.room = room
            ts.room_since = now
        pos = track.get("pos")
        if pos is not None:
            ts.pos_hist.append((now, float(pos[0]), float(pos[1])))
            ts.last_pos = (float(pos[0]), float(pos[1]))
            ts.last_pos_ts = now
        while ts.pos_hist and now - ts.pos_hist[0][0] > MOVEMENT_WINDOW_S:
            ts.pos_hist.popleft()

    def _features(self, ts: _TrackState, track: dict, now: float) -> dict:
        vel = track.get("vel")
        speed: Optional[float] = None
        if vel is not None:
            speed = math.hypot(float(vel[0]), float(vel[1]))
        elif len(ts.pos_hist) >= 2:
            (t0, x0, y0), (t1, x1, y1) = ts.pos_hist[-2], ts.pos_hist[-1]
            if t1 > t0:
                speed = math.hypot(x1 - x0, y1 - y0) / (t1 - t0)

        movement_std: Optional[float] = None
        if len(ts.pos_hist) >= 5:
            xs = [p[1] for p in ts.pos_hist]
            ys = [p[2] for p in ts.pos_hist]
            mx = sum(xs) / len(xs)
            my = sum(ys) / len(ys)
            var = (sum((x - mx) ** 2 for x in xs) + sum((y - my) ** 2 for y in ys)) / len(xs)
            movement_std = math.sqrt(var)

        return {
            "speed": speed,
            "dwell": now - ts.room_since,
            "movement_std": movement_std,
            "pos": track.get("pos"),
            "room": track.get("room"),
        }

    # ---- conditions --------------------------------------------------------------
    def _condition(self, name: str, track: dict, feats: dict, model) -> bool:
        room = feats["room"]
        pos = feats["pos"]
        speed = feats["speed"]
        dwell = feats["dwell"]

        if name == "watching_tv":
            if not room:
                return False
            for tv in model.devices_in_room(room, "tv"):
                if not self._is_playing(tv):
                    continue
                if pos is None:
                    return True  # room-level match
                if self._dist_to_device(pos, tv) <= TV_PROXIMITY_M:
                    return True
            return False

        if name == "listening_music":
            if not room:
                return False
            tv_playing = any(self._is_playing(tv)
                             for tv in model.devices_in_room(room, "tv"))
            if tv_playing:
                return False
            return any(self._is_playing(sp)
                       for sp in model.devices_in_room(room, "speaker"))

        if name == "cooking":
            if not room or "kitchen" not in str(room):
                return False
            if dwell <= COOKING_DWELL_S:
                return False
            std = feats["movement_std"]
            return std is not None and std > MOVEMENT_STD_HIGH_M

        if name == "working":
            if pos is None or speed is None:
                return False
            if speed >= SPEED_STILL or dwell <= WORKING_DWELL_S:
                return False
            return self._near_desk(pos, model)

        if name == "moving":
            return speed is not None and speed > SPEED_MOVING

        if name == "idle":
            return speed is not None and speed < SPEED_STILL

        return False

    def _is_playing(self, device: dict) -> bool:
        entity = device.get("ha_entity_id")
        if not entity:
            return False
        state = self.state_getter(entity)
        return state in PLAYING_STATES

    @staticmethod
    def _dist_to_device(pos, device: dict) -> float:
        dpos = device.get("pos")
        if not dpos:
            return float("inf")
        return math.hypot(float(pos[0]) - float(dpos[0]),
                          float(pos[1]) - float(dpos[1]))

    def _near_desk(self, pos, model) -> bool:
        px, py = float(pos[0]), float(pos[1])
        for dev in getattr(model, "devices", []):
            label = f"{dev.get('id', '')} {dev.get('name', '')}".lower()
            if "desk" in label and dev.get("pos"):
                if math.hypot(px - float(dev["pos"][0]),
                              py - float(dev["pos"][1])) < DESK_PROXIMITY_M:
                    return True
        for zone in getattr(model, "zones", []):
            label = f"{zone.get('id', '')} {zone.get('name', '')}".lower()
            if "desk" not in label:
                continue
            poly = getattr(model, "zone_polys", {}).get(zone.get("id"))
            if poly is not None:
                from shapely.geometry import Point  # local import; cheap
                if poly.distance(Point(px, py)) < DESK_PROXIMITY_M:
                    return True
        return False

    def _gc(self, now: float) -> None:
        for tid in [t for t, ts in self._tracks.items()
                    if now - ts.last_seen > TRACK_STATE_TTL_S]:
            self._tracks.pop(tid, None)


# ---- HA WebSocket listener (impure; wired in main.py) ---------------------------

class HAStateListener:
    """Maintains a cache of media_player.* / light.* states from the HA
    WebSocket API and keeps the engine's staleness clock fresh."""

    def __init__(self, ha_url: str, token: str,
                 engine: Optional[ActivityEngine] = None,
                 prefixes: tuple[str, ...] = ("media_player.", "light.")):
        self.ha_url = (ha_url or "").rstrip("/")
        self.token = token or ""
        self.engine = engine
        self.prefixes = prefixes
        self.states: dict[str, str] = {}
        self.connected = False

    def state_getter(self, entity_id: str) -> Optional[str]:
        return self.states.get(entity_id)

    def _ws_url(self) -> str:
        url = self.ha_url
        if url.startswith("https://"):
            url = "wss://" + url[len("https://"):]
        elif url.startswith("http://"):
            url = "ws://" + url[len("http://"):]
        return url + "/api/websocket"

    def _note_alive(self) -> None:
        if self.engine is not None:
            self.engine.note_ha_alive(time.time())

    def _accept(self, entity_id: str) -> bool:
        return any(entity_id.startswith(p) for p in self.prefixes)

    async def run(self) -> None:
        import websockets  # imported here so tests never need it

        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self._ws_url(), max_size=16 * 2 ** 20) as ws:
                    msg = json.loads(await ws.recv())
                    if msg.get("type") == "auth_required":
                        await ws.send(json.dumps(
                            {"type": "auth", "access_token": self.token}))
                        msg = json.loads(await ws.recv())
                    if msg.get("type") != "auth_ok":
                        raise RuntimeError(f"HA auth failed: {msg}")
                    self.connected = True
                    backoff = 1.0
                    log.info("HA websocket connected")
                    await ws.send(json.dumps({"id": 1, "type": "get_states"}))
                    await ws.send(json.dumps(
                        {"id": 2, "type": "subscribe_events",
                         "event_type": "state_changed"}))
                    ping_task = asyncio.ensure_future(self._ping_loop(ws))
                    try:
                        async for raw in ws:
                            self._note_alive()
                            try:
                                m = json.loads(raw)
                            except (TypeError, ValueError):
                                continue
                            self._handle(m)
                    finally:
                        ping_task.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("HA websocket error: %s; retrying in %.0f s", e, backoff)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _ping_loop(self, ws) -> None:
        pid = 1000
        while True:
            await asyncio.sleep(15)
            pid += 1
            try:
                await ws.send(json.dumps({"id": pid, "type": "ping"}))
            except Exception:
                return

    def _handle(self, m: dict) -> None:
        if m.get("id") == 1 and m.get("type") == "result" and isinstance(
                m.get("result"), list):
            for st in m["result"]:
                eid = st.get("entity_id", "")
                if self._accept(eid):
                    self.states[eid] = st.get("state")
            log.info("seeded %d HA entity states", len(self.states))
        elif m.get("type") == "event":
            data = (m.get("event") or {}).get("data") or {}
            eid = data.get("entity_id", "")
            if not self._accept(eid):
                return
            new = data.get("new_state")
            self.states[eid] = new.get("state") if isinstance(new, dict) else "unavailable"
