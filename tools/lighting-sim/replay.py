#!/usr/bin/env python3
"""Turn recorded nights and evenings from the intelligence ledger (and the
observer memory snapshot) into replayable input timelines for the Living
Lights simulation.

Reads only. Source: `lighting_decisions` (one row per zone classifier
evaluation, about every 5 s, with the classifier's state), the flag
snapshots on `override_events` / `preference_observations` (tv_playing,
asleep, user_at_home, night_safe at each event) and `lighting_change_events`
(what the lights actually did, for the comparison column). Evenings may
additionally read the observer snapshot `memory_ha` (media_player.lg_tv and
the input booleans as Home Assistant reported them, with change rows).

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
* nights: `media_player.lg_tv` follows the forward-filled `tv_playing` flag;
  `input_boolean.user_at_home` follows the forward-filled snapshot (it read
  home on every night in the window);
* evenings: `media_player.lg_tv`, `input_boolean.living_lights_asleep` and
  `input_boolean.user_at_home` come from the observer snapshot's change rows
  when the observer was alive across the whole window and had the entity
  before it started (`tv_source: memory_ha`); otherwise from the ledger flag
  snapshots exactly as nights do (`ledger_flags`, which only sample the
  flags when an override or preference event happens, so their edges lag
  the real entity by minutes); otherwise unmeasurable (`none`);
* the actual asleep flag transitions and the actual light turn-ons are
  written next to the inputs, for the comparison only; nothing feeds them
  back into the simulation.

Output: one JSON per night under --out (default
vjepa-home/experiments/lighting-sim/nights/) or, with --evenings, one JSON
per evening (18:00 to 01:00 next day) under vjepa-home/experiments/
lighting-sim/evenings/. Both are private: they are presence patterns. Usage:

    python3 tools/lighting-sim/replay.py --since 2026-09-02 --until 2026-09-17
    python3 tools/lighting-sim/replay.py --evenings --since 2026-09-02 --until 2026-09-17 \
        [--memory-snapshot /srv/data/vjepa-home-snapshots/2026-09-17/memory.sqlite3]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sqlite3

LEDGER = "/opt/home-ai-voice/intelligence-data/intelligence.sqlite"
MEMORY_SNAPSHOT_DEFAULT = "/srv/data/vjepa-home-snapshots/2026-09-17/memory.sqlite3"
EXPERIMENTS = pathlib.Path.home() / "vjepa-home" / "experiments" / "lighting-sim"
OUT_DEFAULT = EXPERIMENTS / "nights"
EVENINGS_OUT_DEFAULT = EXPERIMENTS / "evenings"
NIGHT_START_HOUR = 22   # local
NIGHT_END_HOUR = 9
EVENING_START_HOUR = 18  # local
EVENING_END_HOUR = 1     # next day
# Observer silence longer than this inside a window means the snapshot does
# not cover it (the observer runs in sessions; a missed edge would otherwise
# read as "no change").
MEMORY_GAP_S = 30 * 60
def _house():
    """The one house file (tools/house.py), shared with the generators.

    This tool used to keep its own copy of the zone map. So did the simulator,
    the replay tool and the other post-mortem: five copies, hand-synchronised,
    with nothing checking they agreed. A zone that drifted between them did not
    fail; it produced a report about a house that does not exist.
    """
    import importlib.util
    import pathlib as _p
    here = _p.Path(__file__).resolve().parent
    root = here if (here / "house.py").exists() else here.parent
    spec = importlib.util.spec_from_file_location("_ll_house", str(root / "house.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_HOUSE = _house()


ZONE_CAMERA = dict(_HOUSE.ZONE_CAMERA)
OCCUPIED_STATES = {"present", "pass_through"}
TV = "media_player.lg_tv"
ASLEEP = "input_boolean.living_lights_asleep"
AT_HOME = "input_boolean.user_at_home"


def open_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def local_tz() -> dt.tzinfo:
    return dt.datetime.now().astimezone().tzinfo


def night_window(night: dt.date) -> tuple[dt.datetime, dt.datetime]:
    tz = local_tz()
    start = dt.datetime.combine(night, dt.time(NIGHT_START_HOUR), tzinfo=tz)
    end = dt.datetime.combine(night + dt.timedelta(days=1), dt.time(NIGHT_END_HOUR), tzinfo=tz)
    return start, end


def evening_window(evening: dt.date) -> tuple[dt.datetime, dt.datetime]:
    tz = local_tz()
    start = dt.datetime.combine(evening, dt.time(EVENING_START_HOUR), tzinfo=tz)
    end = dt.datetime.combine(evening + dt.timedelta(days=1), dt.time(EVENING_END_HOUR), tzinfo=tz)
    return start, end


def zone_of(entity_id: str) -> str | None:
    if not entity_id or not entity_id.endswith("_lighting_state"):
        return None
    body = entity_id[len("sensor."):-len("_lighting_state")]
    for zone, camera in ZONE_CAMERA.items():
        if body == f"{camera}_{zone}":
            return zone
    return None


# --- ledger pieces shared by nights and evenings -------------------------

def occupancy_edges(cur: sqlite3.Cursor, s: str, e: str):
    """Zone and camera occupancy for [s, e) from the classifier states.

    Returns (initial, edges, cam_initial, cam_edges); edges are
    (iso, entity, state) tuples in ledger order.
    """
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
    return initial, edges, cam_initial, cam_edges


def flag_snapshots(cur: sqlite3.Cursor, s: str, e: str) -> list[tuple[str, bool, bool, bool]]:
    """(ts, tv_playing, asleep, user_at_home) at every override / preference event in [s, e)."""
    flags = []
    for table in ("override_events", "preference_observations"):
        for ts, tv, asleep, home in cur.execute(
                f"select ts, tv_playing, asleep, user_at_home from {table} "
                "where ts >= ? and ts < ? order by ts", (s, e)):
            flags.append((ts, bool(tv), bool(asleep), bool(home)))
    flags.sort()
    return flags


def flag_edges(flags):
    """Forward-fill the flag snapshots: the first snapshot is the initial
    state, later ones become edges. Returns (initial_tv, initial_home,
    initial_asleep, tv_edges, home_edges, actual_asleep); when there are no
    snapshots the initials default to tv off, home, awake."""
    tv_edges, home_edges, actual_asleep = [], [], []
    tv_prev = home_prev = asleep_prev = None
    initial_tv, initial_home, initial_asleep = False, True, False
    for ts, tv, asleep, home in flags:
        if tv_prev is None:
            tv_prev, home_prev, asleep_prev = tv, home, asleep
            initial_tv, initial_home, initial_asleep = tv, home, asleep
            continue
        if tv != tv_prev:
            tv_edges.append((ts, TV, "on" if tv else "off")); tv_prev = tv
        if home != home_prev:
            home_edges.append((ts, AT_HOME, "on" if home else "off")); home_prev = home
        if asleep != asleep_prev:
            actual_asleep.append((ts, "on" if asleep else "off")); asleep_prev = asleep
    return initial_tv, initial_home, initial_asleep, tv_edges, home_edges, actual_asleep


def actual_turn_ons(cur: sqlite3.Cursor, m_start: str, m_end: str) -> list[dict]:
    """off->on light changes with brightness in [m_start, m_end), deduped per (light, second)."""
    seen = set()
    turn_ons = []
    for ts, light, fs, tsx, tob in cur.execute(
            "select ts, light_entity, from_state, to_state, to_brightness_pct from lighting_change_events "
            "where ts >= ? and ts < ? and from_state = 'off' and to_state = 'on' order by ts", (m_start, m_end)):
        key = (light, ts[:19])
        if key in seen:
            continue
        seen.add(key)
        turn_ons.append({"t": ts, "light": light, "brightness_pct": tob})
    return turn_ons


def extract_night(cur: sqlite3.Cursor, night: dt.date) -> dict:
    start, end = night_window(night)
    s, e = start.isoformat(), end.isoformat()
    initial, edges, cam_initial, cam_edges = occupancy_edges(cur, s, e)
    # 3) flag snapshots: tv / asleep / user_at_home
    initial_tv, initial_home, initial_asleep, tv_edges, home_edges, actual_asleep = flag_edges(flag_snapshots(cur, s, e))
    # 4) what the lights actually did (off->on with brightness, 00:00-08:00), deduped
    day = night + dt.timedelta(days=1)
    tz = start.tzinfo
    m_start = dt.datetime.combine(day, dt.time(0), tzinfo=tz).isoformat()
    m_end = dt.datetime.combine(day, dt.time(8), tzinfo=tz).isoformat()
    turn_ons = actual_turn_ons(cur, m_start, m_end)
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
                   "turn_ons_00_08": turn_ons,
                   "turn_on_count_00_08": len(turn_ons)},
        "notes": ["raw zone occupancy reconstructed from classifier states (includes the 90 s stable hold)",
                  "camera motion sensors replayed as camera-level person occupancy (understates real motion)"],
    }


# --- observer memory snapshot (evenings) ---------------------------------

def memory_covers(mcur: sqlite3.Cursor, start: dt.datetime, end: dt.datetime,
                  gap_s: float = MEMORY_GAP_S) -> tuple[bool, str]:
    """Was the observer alive across [start, end)? True when memory_ha has a
    row (any entity) no later than gap_s after start, no earlier than gap_s
    before end, and no silence longer than gap_s in between."""
    t0, t1 = start.timestamp(), end.timestamp()
    rows = [o for (o,) in mcur.execute(
        "select observed from memory_ha where observed >= ? and observed < ? order by observed", (t0 - gap_s, t1))]
    if not rows:
        return False, "observer has no rows in the window"
    if rows[0] > t0 + gap_s:
        return False, f"observer rows start at {_hhmm(rows[0], start.tzinfo)}"
    if rows[-1] < t1 - gap_s:
        return False, f"observer rows end at {_hhmm(rows[-1], start.tzinfo)}"
    prev = rows[0]
    for o in rows[1:]:
        if o - prev > gap_s:
            return False, f"observer silent {_hhmm(prev, start.tzinfo)}-{_hhmm(o, start.tzinfo)}"
        prev = o
    return True, "observer alive across the window"


def memory_change_rows(mcur: sqlite3.Cursor, entity: str, start: dt.datetime, end: dt.datetime):
    """The entity's state at `start` and its CHANGE rows inside [start, end).

    Returns (initial_state, initial_observed, edges) where edges are
    (observed_epoch, state) and a row counts as a change only when its
    state differs from the previous row for the same entity (the observer
    also writes periodic snapshot rows with an unchanged state). The initial
    state is the last row at or before start; when the entity has none, the
    first in-window row seeds it (initial_observed then lies inside the
    window and the caller decides whether that is good enough). All None
    when the entity has no rows at all up to end.
    """
    t0, t1 = start.timestamp(), end.timestamp()
    row = mcur.execute(
        "select observed, payload from memory_ha where entity = ? and observed <= ? "
        "order by observed desc limit 1", (entity, t0)).fetchone()
    initial_state = initial_observed = None
    if row is not None:
        initial_observed, initial_state = row[0], _state(row[1])
    prev = initial_state
    edges: list[tuple[float, str]] = []
    for observed, payload in mcur.execute(
            "select observed, payload from memory_ha where entity = ? and observed > ? and observed < ? "
            "order by observed", (entity, t0, t1)):
        state = _state(payload)
        if prev is None:
            initial_state, initial_observed, prev = state, observed, state
            continue
        if state != prev:
            edges.append((observed, state))
            prev = state
    return initial_state, initial_observed, edges


def _state(payload: str) -> str | None:
    try:
        return json.loads(payload).get("state")
    except (ValueError, AttributeError):
        return None


def _hhmm(epoch: float, tz) -> str:
    return dt.datetime.fromtimestamp(epoch, tz).strftime("%H:%M")


def tv_measurable(tv_source: str, tv_edges: list, initial_known: bool) -> bool:
    """An evening's TV state is measurable with two or more lg_tv edges, or
    with a known initial state from the observer snapshot (the flag-derived
    initial is only the first override event's reading, so it does not count)."""
    return len(tv_edges) >= 2 or (tv_source == "memory_ha" and initial_known)


def extract_evening(cur: sqlite3.Cursor, evening: dt.date, mcur: sqlite3.Cursor | None = None) -> dict:
    start, end = evening_window(evening)
    tz = start.tzinfo
    s, e = start.isoformat(), end.isoformat()
    initial, edges, cam_initial, cam_edges = occupancy_edges(cur, s, e)
    flags = flag_snapshots(cur, s, e)
    f_tv, f_home, f_asleep, f_tv_edges, f_home_edges, f_asleep_edges = flag_edges(flags)
    notes: list[str] = []
    covered, reason = (False, "no memory snapshot") if mcur is None else memory_covers(mcur, start, end)
    notes.append(f"memory_ha: {reason}")

    def from_memory(entity: str):
        """(initial_state, edges[(iso, state)]) or None when memory_ha cannot vouch for the entity."""
        if not covered:
            return None
        initial_state, initial_observed, m_edges = memory_change_rows(mcur, entity, start, end)
        if initial_state is None:
            notes.append(f"memory_ha has no rows for {entity} up to window end")
            return None
        if initial_observed > start.timestamp():
            notes.append(f"memory_ha first saw {entity} at {_hhmm(initial_observed, tz)}, after the window start")
            return None
        return initial_state, [(dt.datetime.fromtimestamp(o, tz).isoformat(), st) for o, st in m_edges]

    sources = {}
    # TV
    mem = from_memory(TV)
    if mem is not None:
        tv_initial, tv_edges = mem
        tv_source = "memory_ha"
    elif flags:
        tv_initial, tv_edges = ("on" if f_tv else "off"), [(t, st) for t, _, st in f_tv_edges]
        tv_source = "ledger_flags"
    else:
        tv_initial, tv_edges, tv_source = "unknown", [], "none"
    sources[TV] = tv_source
    measurable = tv_measurable(tv_source, tv_edges, tv_initial != "unknown")
    if not measurable:
        notes.append("tv unmeasurable")
    # asleep (actual, not an input)
    mem = from_memory(ASLEEP)
    if mem is not None:
        asleep_initial, asleep_edges = mem; sources[ASLEEP] = "memory_ha"
    elif flags:
        asleep_initial, asleep_edges = ("on" if f_asleep else "off"), f_asleep_edges; sources[ASLEEP] = "ledger_flags"
    else:
        asleep_initial, asleep_edges = "off", []; sources[ASLEEP] = "none"
        notes.append(f"{ASLEEP} unknown, initial defaulted to off")
    # user_at_home
    mem = from_memory(AT_HOME)
    if mem is not None:
        home_initial, home_edges = mem; sources[AT_HOME] = "memory_ha"
    elif flags:
        home_initial, home_edges = ("on" if f_home else "off"), [(t, st) for t, _, st in f_home_edges]; sources[AT_HOME] = "ledger_flags"
    else:
        home_initial, home_edges = "on", []; sources[AT_HOME] = "none"
        notes.append(f"{AT_HOME} unknown, initial defaulted to on")
    if "ledger_flags" in sources.values():
        notes.append("ledger flags are sampled only at override/preference events; their edges lag the entity")
    # actual turn-ons 18:00-01:00, deduped like nights
    turn_ons = actual_turn_ons(cur, s, e)
    events = sorted(edges + cam_edges
                    + [(t, TV, st) for t, st in tv_edges]
                    + [(t, AT_HOME, st) for t, st in home_edges], key=lambda r: r[0])
    return {
        "schema": "living-lights-sim-evening/v1",
        "evening": evening.isoformat(),
        "start": s, "end": e,
        "initial": {**initial, **cam_initial, TV: tv_initial, AT_HOME: home_initial, ASLEEP: asleep_initial},
        "events": [{"t": t, "entity": ent, "state": st} for t, ent, st in events],
        "actual": {"tv_edges": [{"t": t, "state": st} for t, st in tv_edges],
                   "asleep_transitions": [{"t": t, "state": st} for t, st in asleep_edges],
                   "turn_ons_18_01": turn_ons,
                   "turn_on_count_18_01": len(turn_ons)},
        "validity": {"tv_measurable": measurable,
                     "tv_source": tv_source,
                     "occupancy_source": "ledger_classifier",
                     "asleep_source": sources[ASLEEP],
                     "user_at_home_source": sources[AT_HOME],
                     "memory_ha_covers_window": covered,
                     "notes": notes},
        "notes": ["raw zone occupancy reconstructed from classifier states (includes the 90 s stable hold)",
                  "camera motion sensors replayed as camera-level person occupancy (understates real motion)"],
    }


def evening_summary(doc: dict) -> str:
    v = doc["validity"]
    label = "" if v["tv_measurable"] else " [tv unmeasurable]"
    return (f"{doc['evening']}: {len(doc['events'])} input edges, tv {v['tv_source']} "
            f"({len(doc['actual']['tv_edges'])} lg_tv edges, initial {doc['initial'][TV]}), "
            f"measurable {v['tv_measurable']}{label}, asleep {v['asleep_source']} "
            f"{[(a['t'][11:16], a['state']) for a in doc['actual']['asleep_transitions']]}, "
            f"actual turn-ons 18-01: {doc['actual']['turn_on_count_18_01']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=LEDGER)
    ap.add_argument("--since", required=True, help="first night (date of the evening), e.g. 2026-09-02")
    ap.add_argument("--until", required=True, help="last night, exclusive")
    ap.add_argument("--out", default=None, help=f"default {OUT_DEFAULT} (nights) or {EVENINGS_OUT_DEFAULT} (--evenings)")
    ap.add_argument("--evenings", action="store_true", help="write 18:00-01:00 evening files instead of nights")
    ap.add_argument("--memory-snapshot", default=MEMORY_SNAPSHOT_DEFAULT,
                    help="observer memory.sqlite3 for --evenings (read-only); '' to skip")
    args = ap.parse_args()
    out = pathlib.Path(args.out or (EVENINGS_OUT_DEFAULT if args.evenings else OUT_DEFAULT))
    out.mkdir(parents=True, exist_ok=True); out.chmod(0o700)
    con = open_ro(args.db); cur = con.cursor()
    mcur = None
    if args.evenings and args.memory_snapshot:
        try:
            mcur = open_ro(args.memory_snapshot).cursor()
            mcur.execute("select 1 from memory_ha limit 1")
        except sqlite3.Error as exc:
            print(f"memory snapshot unusable ({exc}); evenings fall back to ledger flags")
            mcur = None
    night = dt.date.fromisoformat(args.since); until = dt.date.fromisoformat(args.until)
    while night < until:
        if args.evenings:
            doc = extract_evening(cur, night, mcur)
            line = evening_summary(doc)
        else:
            doc = extract_night(cur, night)
            line = (f"{night}: {len(doc['events'])} input edges, actual asleep {[(a['t'][11:16], a['state']) for a in doc['actual']['asleep_transitions']]}, "
                    f"actual turn-ons 00-08: {doc['actual']['turn_on_count_00_08']}")
        path = out / f"{night.isoformat()}.json"
        path.write_text(json.dumps(doc, indent=1) + "\n"); path.chmod(0o600)
        print(line, flush=True)
        night += dt.timedelta(days=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
