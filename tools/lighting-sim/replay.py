#!/usr/bin/env python3
"""Turn recorded nights from the intelligence ledger into replayable input
timelines for the Living Lights simulation.

Reads only. Source: `lighting_decisions` (one row per zone classifier
evaluation, about every 5 s, with the classifier's state), the flag
snapshots on `override_events` / `preference_observations` (tv_playing,
asleep, user_at_home, night_safe at each event) and `lighting_change_events`
(what the lights actually did, for the comparison column).

Reconstruction, stated plainly so nobody mistakes it for the recorder:

* zone raw occupancy `binary_sensor.<zone>_person_occupancy` is ON while the
  recorded classifier state is `present` or `pass_through`. The recorded
  state already includes the 90 s stable hold, so the replayed raw sensor
  stays on about 90 s longer than the real one did;
* camera-level `binary_sensor.<camera>_person_occupancy` is the OR of that
  camera's zones;
* the raw `binary_sensor.<camera>_motion` sensors are NOT recorded anywhere
  we can read. They are replayed equal to camera-level person occupancy,
  which understates them (motion also fires on screens, shadows and pets).
  The old package's motion blockers therefore look better in replay than
  they were; the new package does not read them;
* `media_player.lg_tv` follows the forward-filled `tv_playing` flag;
* `input_boolean.user_at_home` follows the forward-filled snapshot (it read
  home on every night in the window);
* the actual asleep flag transitions and the actual light turn-ons are
  written next to the inputs, for the comparison only; nothing feeds them
  back into the simulation.

Output: one JSON per night under --out (default
vjepa-home/experiments/lighting-sim/nights/, private: it is a presence
pattern). Usage:

    python3 tools/lighting-sim/replay.py --since 2026-09-02 --until 2026-09-17
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sqlite3

LEDGER = "/opt/home-ai-voice/intelligence-data/intelligence.sqlite"
OUT_DEFAULT = pathlib.Path.home() / "vjepa-home" / "experiments" / "lighting-sim" / "nights"
NIGHT_START_HOUR = 22   # local
NIGHT_END_HOUR = 9
ZONE_CAMERA = {
    "dining_left": "dining_room", "dining_right": "dining_room", "whole_dining_room": "dining_room",
    "sink": "kitchen", "island_left": "kitchen", "island_right": "kitchen", "whole_kitchen": "kitchen",
    "sofa": "living_room", "front_left": "living_room", "weights": "living_room", "office": "living_room",
    "front_door": "living_room", "whole_living_room": "living_room",
    "workshop_zone": "workshop", "e28": "driveway",
}
OCCUPIED_STATES = {"present", "pass_through"}


def open_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def night_window(night: dt.date) -> tuple[dt.datetime, dt.datetime]:
    tz = dt.datetime.now().astimezone().tzinfo
    start = dt.datetime.combine(night, dt.time(NIGHT_START_HOUR), tzinfo=tz)
    end = dt.datetime.combine(night + dt.timedelta(days=1), dt.time(NIGHT_END_HOUR), tzinfo=tz)
    return start, end


def zone_of(entity_id: str) -> str | None:
    if not entity_id or not entity_id.endswith("_lighting_state"):
        return None
    body = entity_id[len("sensor."):-len("_lighting_state")]
    for zone, camera in ZONE_CAMERA.items():
        if body == f"{camera}_{zone}":
            return zone
    return None


def extract_night(cur: sqlite3.Cursor, night: dt.date) -> dict:
    start, end = night_window(night)
    s, e = start.isoformat(), end.isoformat()
    # 1) classifier states per zone -> raw occupancy edges
    last_state: dict[str, str] = {}
    initial: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []   # (iso, entity, state)
    # The table is indexed by (zone, ts), not ts alone: query per zone so a
    # night is fifteen index range scans instead of a scan of 11 M rows.
    rows: list[tuple[str, str, str]] = []
    for zone_name in ZONE_CAMERA:
        rows.extend(cur.execute(
            "select ts, entity_id, to_state from lighting_decisions "
            "where zone = ? and ts >= ? and ts < ? and entity_id is not null", (zone_name, s, e)).fetchall())
    rows.sort()
    for ts, ent, to_state in rows:
        zone = zone_of(ent)
        if zone is None or to_state is None:
            continue
        occ = "on" if to_state in OCCUPIED_STATES else "off"
        entity = f"binary_sensor.{zone}_person_occupancy"
        if entity not in last_state:
            initial[entity] = occ
            last_state[entity] = occ
        elif last_state[entity] != occ:
            edges.append((ts, entity, occ))
            last_state[entity] = occ
    for zone in ZONE_CAMERA:
        initial.setdefault(f"binary_sensor.{zone}_person_occupancy", "off")
    # 2) camera-level occupancy = OR of the camera's zones (also used as the motion proxy)
    cam_zones = collections.defaultdict(list)
    for zone, cam in ZONE_CAMERA.items():
        cam_zones[cam].append(f"binary_sensor.{zone}_person_occupancy")
    state = dict(initial)
    cam_initial = {}
    cam_edges: list[tuple[str, str, str]] = []
    cam_state = {}
    for cam, zones in cam_zones.items():
        cam_state[cam] = "on" if any(state[z] == "on" for z in zones) else "off"
        cam_initial[f"binary_sensor.{cam}_person_occupancy"] = cam_state[cam]
        cam_initial[f"binary_sensor.{cam}_motion"] = cam_state[cam]
    for ts, entity, occ in edges:
        state[entity] = occ
        cam = ZONE_CAMERA[entity[len("binary_sensor."):-len("_person_occupancy")]]
        now_on = "on" if any(state[z] == "on" for z in cam_zones[cam]) else "off"
        if now_on != cam_state[cam]:
            cam_state[cam] = now_on
            cam_edges.append((ts, f"binary_sensor.{cam}_person_occupancy", now_on))
            cam_edges.append((ts, f"binary_sensor.{cam}_motion", now_on))
    # 3) flag snapshots: tv / asleep / user_at_home
    flags = []
    for table in ("override_events", "preference_observations"):
        for ts, tv, asleep, home in cur.execute(
                f"select ts, tv_playing, asleep, user_at_home from {table} "
                "where ts >= ? and ts < ? order by ts", (s, e)):
            flags.append((ts, bool(tv), bool(asleep), bool(home)))
    flags.sort()
    tv_edges, home_edges, actual_asleep = [], [], []
    tv_prev = home_prev = asleep_prev = None
    for ts, tv, asleep, home in flags:
        if tv_prev is None:
            tv_prev, home_prev, asleep_prev = tv, home, asleep
            initial_tv, initial_home, initial_asleep = tv, home, asleep
            continue
        if tv != tv_prev:
            tv_edges.append((ts, "media_player.lg_tv", "on" if tv else "off")); tv_prev = tv
        if home != home_prev:
            home_edges.append((ts, "input_boolean.user_at_home", "on" if home else "off")); home_prev = home
        if asleep != asleep_prev:
            actual_asleep.append((ts, "on" if asleep else "off")); asleep_prev = asleep
    if tv_prev is None:
        initial_tv, initial_home, initial_asleep = False, True, False
    # 4) what the lights actually did (off->on with brightness, 00:00-08:00), deduped
    day = night + dt.timedelta(days=1)
    tz = start.tzinfo
    m_start = dt.datetime.combine(day, dt.time(0), tzinfo=tz).isoformat()
    m_end = dt.datetime.combine(day, dt.time(8), tzinfo=tz).isoformat()
    seen = set()
    actual_turn_ons = []
    for ts, light, fs, tsx, tob in cur.execute(
            "select ts, light_entity, from_state, to_state, to_brightness_pct from lighting_change_events "
            "where ts >= ? and ts < ? and from_state = 'off' and to_state = 'on' order by ts", (m_start, m_end)):
        key = (light, ts[:19])
        if key in seen:
            continue
        seen.add(key)
        actual_turn_ons.append({"t": ts, "light": light, "brightness_pct": tob})
    events = sorted(edges + cam_edges + tv_edges + home_edges, key=lambda r: r[0])
    return {
        "schema": "living-lights-sim-night/v1",
        "night": night.isoformat(),
        "start": start.isoformat(), "end": end.isoformat(),
        "initial": {**initial, **cam_initial,
                     "media_player.lg_tv": "on" if initial_tv else "off",
                     "input_boolean.user_at_home": "on" if initial_home else "off"},
        "initial_asleep_recorded": initial_asleep,
        "events": [{"t": t, "entity": ent, "state": st} for t, ent, st in events],
        "actual": {"asleep_transitions": [{"t": t, "state": st} for t, st in actual_asleep],
                   "turn_ons_00_08": actual_turn_ons,
                   "turn_on_count_00_08": len(actual_turn_ons)},
        "notes": ["raw zone occupancy reconstructed from classifier states (includes the 90 s stable hold)",
                  "camera motion sensors replayed as camera-level person occupancy (understates real motion)"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=LEDGER)
    ap.add_argument("--since", required=True, help="first night (date of the evening), e.g. 2026-09-02")
    ap.add_argument("--until", required=True, help="last night, exclusive")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()
    out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True); out.chmod(0o700)
    con = open_ro(args.db); cur = con.cursor()
    night = dt.date.fromisoformat(args.since); until = dt.date.fromisoformat(args.until)
    while night < until:
        doc = extract_night(cur, night)
        path = out / f"{night.isoformat()}.json"
        path.write_text(json.dumps(doc, indent=1) + "\n"); path.chmod(0o600)
        print(f"{night}: {len(doc['events'])} input edges, actual asleep {[(a['t'][11:16], a['state']) for a in doc['actual']['asleep_transitions']]}, "
              f"actual turn-ons 00-08: {doc['actual']['turn_on_count_00_08']}")
        night += dt.timedelta(days=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
