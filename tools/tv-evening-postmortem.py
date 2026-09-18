#!/usr/bin/env python3
"""Story T post-mortem: did watching TV mean a dark living room, on a real evening.

This is the story T twin of tools/night-postmortem-join.py. It reads what the
house recorded for one evening (18:00 to 01:00 by default) and scores the
plan's story T bar, with story S's asleep guard folded in so one tool covers
both stories on the same evening. It is strictly read-only: GET requests to
the Home Assistant API, a read-only SQLite URI on the intelligence ledger, and
the publisher's journal files. It never calls a service and never changes a
light.

Sources, in order of preference. Each one is optional and each one is named in
the receipt, so a reader can tell what the verdict rests on:

1. The Home Assistant recorder over the REST API (``history/period``). It
   carries the lights, ``binary_sensor.living_lights_tv_playing``,
   ``media_player.lg_tv``, the per-zone ``*_person_occupancy_stable``
   sensors, ``binary_sensor.living_lights_any_occupied``,
   ``input_boolean.living_lights_asleep``, the zone ``*_lighting_state``
   sensors with their ``predicted_brightness_pct`` attribute (fetched without
   ``minimal_response``, which omits attributes), the activity sensors and
   ``binary_sensor.living_lights_tv_watching`` once the belief publisher
   publishes it. Recorder retention is short: when the window is not covered
   the tool refuses to score a partial evening (exit 2) instead of reporting a
   fraction of it.
2. The intelligence ledger (read-only ``mode=ro`` URI):
   ``lighting_change_events`` for what the lights did and who wrote them
   (``ha_context_json``), and ``lighting_decisions`` for the classifier's
   state at that instant. Both are queried per zone through their
   ``(zone, ts)`` index, never scanned.
3. The publisher journal, when it exists, for the TV state machine
   (UNATTENDED) and the belief.

Metric definitions are shared with the simulator: the interval helpers and the
zone/light tables come from tools/lighting-sim/analyze_evening.py, and writer
attribution from tools/night-postmortem-join.py, so a metric measured on the
house and the same metric measured in simulation cannot drift apart.

Every metric is reported with its denominator, so a one-episode evening cannot
read as a pass, and a metric whose source is absent is reported ``skipped``
with the reason -- never guessed.

Exit codes: 0 every metric met its bar; 1 at least one did not; 2 the evening
was not scored (no recorder coverage, no watching episode, or a receipt for
that evening already exists -- receipts are never overwritten).

Usage:
  tools/tv-evening-postmortem.py --since 2026-09-17 --until 2026-09-18
  tools/tv-evening-postmortem.py --json --no-ledger
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import importlib.util
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

TOOLS = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_FAILED_BAR = 1
EXIT_UNSCORABLE = 2

SCHEMA = "living-lights-tv-evening/v1"

DEFAULT_HA_URL = "http://homeassistant.local:8123"
DEFAULT_ENV_FILE = "/opt/home-ai-voice/.env"
DEFAULT_DB = "/opt/home-ai-voice/intelligence-data/intelligence.sqlite"
DEFAULT_JOURNAL_DIR = "/opt/home-ai-voice/data/lighting-publisher"
DEFAULT_OUT_ROOT = "/home/marcelo-lima/vjepa-home/experiments/tv-evenings"
DEFAULT_TZ = "America/Los_Angeles"
REDACTED = "<redacted>"

# The plan's story T bars (rev 5, "Definition of done").
BAR_DARK_FRACTION = 0.90
REQUIRED_BAR = "living_room_dark_within_30s"
"""The bar story T is. An evening that could not score it has not tested the
story, whatever the other bars did, so it is unscorable rather than a pass."""
BAR_ROUTE_FRACTION = 0.90
DARK_S = 30
ROUTE_S = 60
ROUTE_OFF_S = 150
COVERAGE_SLACK_S = 300
MAX_QUIET_GAP_S = 7200
"""A stretch this long inside the window with no recorded change from any
entity the tool asked for reads as the recorder having stopped, not as a quiet
house. Two hours is generous: an evening with a television, a sofa sensor, ten
lights and ten occupancy sensors that records nothing for two hours is not an
evening anyone can score."""

TV_PLAYING = "binary_sensor.living_lights_tv_playing"
TV_WATCHING = "binary_sensor.living_lights_tv_watching"
LG_TV = "media_player.lg_tv"
SOFA_STABLE = "binary_sensor.living_room_sofa_person_occupancy_stable"
ANY_OCCUPIED = "binary_sensor.living_lights_any_occupied"
ASLEEP = "input_boolean.living_lights_asleep"
ROUTE_HELPER = "input_number.living_lights_tv_route_pct"

UNKNOWN_STATES = ("unavailable", "unknown", "none", "")
INDETERMINATE_STATES = ("unavailable", "unknown", "none", "")
"""A light in one of these is not known to be off. Darkness declared while a
light is unavailable is an integration blip reported as a story working."""
IDLE_STATES = ("idle", "unavailable", "unknown", "none", "")
HUMAN_WRITERS = ("user", "explicit_command")
UNATTENDED = "UNATTENDED"

# Mirrors ZONE_CAMERA in tools/build-living-lights-actuators.py: the entity ids
# the observability package generates are "<camera>_<zone>_<suffix>".
ZONE_CAMERA = {
    "dining_left": "dining_room", "dining_right": "dining_room",
    "sink": "kitchen", "island_left": "kitchen", "island_right": "kitchen",
    "sofa": "living_room", "front_left": "living_room", "weights": "living_room",
    "office": "living_room", "front_door": "living_room",
}


def _load_module(name, path):
    """Import a repo tool by path (the tool file names carry hyphens)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# One definition of an interval, a zone's lights and a writer class for the
# simulator, the night post-mortem and this tool.
ae = _load_module("analyze_evening", TOOLS / "lighting-sim" / "analyze_evening.py")
njoin = _load_module("night_postmortem_join", TOOLS / "night-postmortem-join.py")

ZONE_LIGHTS = {zone: list(lights) for zone, lights in ae.ZONE_LIGHTS.items() if zone in ZONE_CAMERA}
LIVING_ROOM_LIGHTS = tuple(ae.LIVING_ROOM_LIGHTS)
ERRAND_ZONES = tuple(zone for zone in ZONE_LIGHTS if zone not in ae.LIVING_ROOM_ZONES)
ALL_LIGHTS = sorted({light for lights in ZONE_LIGHTS.values() for light in lights} | set(LIVING_ROOM_LIGHTS))


class PostmortemError(Exception):
    """A condition that stops the evening being scored (exit 2)."""


# ---------------------------------------------------------------- entity ids
def stable_sensor(zone):
    """The zone's debounced occupancy sensor (the observability package)."""
    return f"binary_sensor.{ZONE_CAMERA[zone]}_{zone}_person_occupancy_stable"


def activity_sensor(zone):
    """The zone's activity sensor -- published by the belief publisher only."""
    return f"sensor.{ZONE_CAMERA[zone]}_{zone}_activity"


def lighting_state_sensor(zone):
    """The zone's classifier sensor, which carries predicted_brightness_pct."""
    return f"sensor.{ZONE_CAMERA[zone]}_{zone}_lighting_state"


# ------------------------------------------------------------------ plumbing
def parse_time(value):
    """ISO 8601 (with or without a zone) or an epoch number; else None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value), dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed


def iso(value):
    return value.isoformat() if isinstance(value, dt.datetime) else value


def local(when, tz):
    """Every timestamp this tool reports is in the evening's own zone.

    The recorder answers in UTC, the ledger in the house's offset and the
    journal in whatever the container's TZ was; a receipt that mixed the three
    would make a reader compare 01:57+00:00 with 18:57-07:00 by hand.
    """
    return when.astimezone(tz) if isinstance(when, dt.datetime) else when


def read_token(env_path):
    """HA_TOKEN out of the env file. The value is never printed or written."""
    path = Path(env_path)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("HA_TOKEN="):
            value = line.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value or None
    return None


def scrub(value, secrets):
    """Replace every secret with <redacted>, anywhere in a JSON-ish value.

    Applied to everything this tool prints or writes, so a token that reached
    an error message or a URL cannot leave the process.
    """
    live = [s for s in secrets if s]
    if not live:
        return value
    if isinstance(value, str):
        for secret in live:
            value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, dict):
        return {scrub(k, live): scrub(v, live) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, live) for v in value]
    if isinstance(value, tuple):
        return tuple(scrub(v, live) for v in value)
    return value


def http_getter(base_url, token, timeout=60):
    """A GET-only JSON getter over the Home Assistant REST API.

    The returned callable takes an API path ("history/period/...") and returns
    the decoded JSON. It can only issue GET requests: there is no body and no
    method argument, so no caller of this tool can reach a service call.
    """
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def get(path):
        request = urllib.request.Request(f"{base}/api/{path.lstrip('/')}", headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    return get


def history_path(start, end, entities, minimal=True):
    """The recorder query for one window and one entity list.

    The attribute query drops ``significant_changes_only``: the classifier's
    ``predicted_brightness_pct`` moves without the sensor's state moving, so
    the default would hand back a row hours stale and the evidence printed
    beside a turn-on failure would describe a different moment.
    """
    query = urllib.parse.urlencode({"end_time": end.isoformat(),
                                    "filter_entity_id": ",".join(entities)})
    path = f"history/period/{urllib.parse.quote(start.isoformat(), safe='')}?{query}"
    return path + "&minimal_response" if minimal else path + "&significant_changes_only=0"


def parse_history(payload):
    """{entity: [{"t", "state", "attrs"}, ...]} out of a history/period reply.

    Home Assistant returns one list per entity. With ``minimal_response`` only
    the first row of each list carries ``entity_id`` and ``attributes``; the
    rest carry ``state`` and ``last_changed`` alone.
    """
    out = {}
    for series in payload or []:
        if not series:
            continue
        entity = None
        for row in series:
            if isinstance(row, dict) and row.get("entity_id"):
                entity = row["entity_id"]
                break
        if not entity:
            continue
        rows = []
        for row in series:
            if not isinstance(row, dict):
                continue
            when = parse_time(row.get("last_changed") or row.get("last_updated"))
            if when is None:
                continue
            rows.append({"t": when, "state": str(row.get("state")),
                         "attrs": dict(row.get("attributes") or {})})
        rows.sort(key=lambda r: r["t"])
        if rows:
            out.setdefault(entity, []).extend(rows)
            out[entity].sort(key=lambda r: r["t"])
    return out


def edges_of(hist, entity):
    """[(t, state)] for one entity, the shape analyze_evening's helpers take."""
    return [(row["t"], row["state"]) for row in hist.get(entity, [])]


def row_at(hist, entity, when):
    """The last recorded row at or before `when`, or None."""
    found = None
    for row in hist.get(entity, []):
        if row["t"] <= when:
            found = row
        else:
            break
    return found


def state_at(hist, entity, when):
    row = row_at(hist, entity, when)
    return row["state"] if row else None


def has_real_states(hist, entity):
    """Whether the recorder holds a usable state for the entity in the window.

    A series of nothing but unavailable/unknown means the entity did not exist
    that evening (the belief sensors before the publisher ships), which is not
    the same as a recorder gap.
    """
    return any(row["state"].lower() not in UNKNOWN_STATES for row in hist.get(entity, []))


def clip(intervals, low, high):
    out = []
    for a, b in intervals:
        lo, hi = max(a, low), min(b, high)
        if lo < hi:
            out.append((lo, hi))
    return out


def all_off_after(hist, lights, t0, end):
    """Seconds from t0 until every light is off; 0.0 when already off, None
    when they never all are before `end`.

    Returns ``(seconds, lights_without_data, indeterminate)``.

    "Off" is the recorder's own light state: Home Assistant turns a light to
    brightness 0 into state off, so state off is the darkness test. A light
    reading unavailable is NOT off; it is unknown. Counting it as off lets an
    integration blip grant darkness, which is a story reported as working on
    the strength of a dropped connection, so the lights that were
    indeterminate at the instant darkness is declared come back with the
    answer and the caller refuses to score that episode.
    """
    missing = sorted(light for light in lights if not hist.get(light))
    known = [light for light in lights if hist.get(light)]
    if not known:
        return None, missing, []

    def snapshot(when):
        state = {}
        for light in known:
            value = (state_at(hist, light, when) or "").lower()
            state[light] = "on" if value == "on" else (
                "indeterminate" if value in INDETERMINATE_STATES else "off")
        return state

    now_state = snapshot(t0)
    if not any(v == "on" for v in now_state.values()):
        return 0.0, missing, sorted(k for k, v in now_state.items() if v == "indeterminate")
    changes = sorted((row["t"], light, row["state"])
                     for light in known for row in hist[light] if t0 < row["t"] < end)
    for when, light, state in changes:
        value = (state or "").lower()
        now_state[light] = "on" if value == "on" else (
            "indeterminate" if value in INDETERMINATE_STATES else "off")
        if not any(v == "on" for v in now_state.values()):
            return (round((when - t0).total_seconds(), 1), missing,
                    sorted(k for k, v in now_state.items() if v == "indeterminate"))
    return None, missing, []


# ------------------------------------------------------------------ episodes
def watching_episodes(hist, start, end):
    """Watching episodes and the source each one came from.

    A deterministic episode is ``tv_playing`` on (or, when that sensor did not
    exist yet, ``media_player.lg_tv`` in an on state) while the sofa's stable
    occupancy is on. A belief episode is ``tv_watching`` on, and is used from
    the first instant the belief entity has a real state: an evening that
    straddles the publisher's arrival is scored deterministically before it and
    on the belief after it, each episode saying which.
    """
    sofa = ae.on_intervals(edges_of(hist, SOFA_STABLE), start, end)
    if has_real_states(hist, TV_PLAYING):
        playing = ae.on_intervals(edges_of(hist, TV_PLAYING), start, end)
        det_source = "tv_playing_and_sofa"
    elif has_real_states(hist, LG_TV):
        playing = ae.on_intervals(edges_of(hist, LG_TV), start, end,
                                  on=lambda s: s not in ae.TV_OFF_STATES)
        det_source = "lg_tv_and_sofa"
    else:
        playing, det_source = [], None
    deterministic = ae.intersect(playing, sofa)
    episodes = []
    if has_real_states(hist, TV_WATCHING):
        belief_from = next(row["t"] for row in hist[TV_WATCHING]
                           if row["state"].lower() not in UNKNOWN_STATES)
        belief = ae.on_intervals(edges_of(hist, TV_WATCHING), start, end)
        episodes += [{"from": a, "to": b, "source": "tv_watching"}
                     for a, b in clip(belief, belief_from, end)]
        deterministic = clip(deterministic, start, belief_from)
    episodes += [{"from": a, "to": b, "source": det_source or "none"} for a, b in deterministic]
    # Episodes closer together than the darkness bar are one episode: a TV that
    # blips off for eight seconds must not become two films, each demanding
    # its own darkening.
    raw_count = len(episodes)
    merged = []
    for source in sorted({e["source"] for e in episodes}):
        same = [(e["from"], e["to"]) for e in episodes if e["source"] == source]
        merged += [{"from": a, "to": b, "source": source}
                   for a, b in ae.coalesce(same, DARK_S)]
    episodes = sorted(merged, key=lambda e: e["from"])
    for episode in episodes:
        overlap = ae.intersect([(episode["from"], episode["to"])], sofa)
        episode["sofa_from"] = overlap[0][0] if overlap else None
        episode["sofa_occupied"] = overlap[0][0] is not None if overlap else False
        episode["sofa_minutes"] = round(sum((b - a).total_seconds() for a, b in overlap) / 60, 1)
        episode["minutes"] = round((episode["to"] - episode["from"]).total_seconds() / 60, 1)
    return episodes, sofa, det_source, {"raw": raw_count, "merged": len(episodes)}


# -------------------------------------------------------------------- ledger
LEDGER_COLUMNS = ("ts", "light_entity", "zone", "from_state", "to_state", "from_brightness_pct",
                  "to_brightness_pct", "command_id", "source_hint", "trigger_attribute",
                  "ha_context_json", "context_json")


def open_ledger(path):
    """Read-only connection to the intelligence ledger (never writes)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    return conn


def ledger_events(conn, zones, start, end, tz):
    """Deduplicated light changes in the window, one query per zone.

    The ledger records up to four rows per physical change (one per trigger
    attribute); they are collapsed by night-postmortem-join's own ``dedupe`` so
    both post-mortems count the same physical change. That dedupe keys on the
    whole second, so the rare change whose attribute rows straddle a second
    boundary still counts twice: it can inflate a defect count, never turn a
    clean evening into a defect. The ``(zone, ts)`` index carries every query:
    the table is far too large to scan.
    """
    pad = dt.timedelta(days=1)
    low, high = (start - pad).isoformat(), (end + pad).isoformat()
    rows = []
    for zone in zones:
        query = ("SELECT " + ", ".join(LEDGER_COLUMNS) + " FROM lighting_change_events "
                 "WHERE zone = ? AND ts >= ? AND ts < ? ORDER BY ts")
        for row in conn.execute(query, (zone, low, high)):
            rows.append({key: row[key] for key in LEDGER_COLUMNS})
    events = []
    for row in njoin.dedupe(rows):
        when = parse_time(row["ts"])
        if when is None or not (start <= when < end):
            continue
        event = dict(row)
        event["t"] = local(when, tz)
        event["writer"] = njoin.writer_class(row["ha_context_json"], row["source_hint"], row["command_id"])
        events.append(event)
    events.sort(key=lambda e: (e["t"], e["light_entity"]))
    return events


def ledger_decisions(conn, zones, start, end, tz):
    """The classifier's decisions in the window, one indexed query per zone.

    ``lighting_decisions`` is indexed on ``(zone, ts)``; a query without a zone
    would scan a twenty-gigabyte table.
    """
    pad = dt.timedelta(days=1)
    low, high = (start - pad).isoformat(), (end + pad).isoformat()
    out = {}
    for zone in zones:
        rows = []
        for row in conn.execute(
                "SELECT ts, from_state, to_state, predicted_brightness_pct FROM lighting_decisions "
                "WHERE zone = ? AND ts >= ? AND ts < ? ORDER BY ts", (zone, low, high)):
            when = parse_time(row["ts"])
            if when is not None:
                rows.append((local(when, tz), {"ts": row["ts"], "from_state": row["from_state"],
                                    "to_state": row["to_state"],
                                    "predicted_brightness_pct": row["predicted_brightness_pct"]}))
        out[zone] = rows
    return out


def decision_at(decisions, zone, when):
    """The last classifier decision for a zone at or before `when`."""
    found = None
    for stamp, row in decisions.get(zone, []):
        if stamp <= when:
            found = row
        else:
            break
    return found


def is_turn_on(event):
    """An off-to-on ledger change with a brightness above 0 (0 is a turn-off)."""
    return (event["to_state"] == "on" and event["from_state"] != "on"
            and (event["to_brightness_pct"] or 0) > 0)


def is_brighten(event):
    """A change that raised an already-on light's level."""
    return (event["to_state"] == "on" and event["from_state"] == "on"
            and (event["to_brightness_pct"] or 0) > (event["from_brightness_pct"] or 0))


# ------------------------------------------------------------------- journal
def read_journal(directory, start, end, tz):
    """The publisher's journal records inside the window, oldest first.

    One JSONL file per local day (stack/services/lighting-publisher's
    journal.py). A malformed line is counted, never fatal: the publisher
    appends to the file this tool is reading.
    """
    path = Path(directory)
    if not path.is_dir():
        return {"available": False, "reason": "journal directory not found",
                "directory": str(path), "records": 0, "files": [], "malformed": 0}
    days = {start.date(), end.date(), (start - dt.timedelta(days=1)).date()}
    records, files, malformed = [], [], 0
    for day in sorted(days):
        candidate = path / f"decisions-{day.isoformat()}.jsonl"
        if not candidate.is_file():
            continue
        files.append(candidate.name)
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            malformed += 1
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(row, dict):
                malformed += 1
                continue
            when = parse_time(row.get("t"))
            if when is not None and start <= when < end:
                row["_t"] = local(when, tz)
                records.append(row)
    records.sort(key=lambda r: r["_t"])
    return {"available": bool(files), "reason": None if files else "no journal file for this evening",
            "directory": str(path), "records": len(records), "files": files,
            "malformed": malformed, "rows": records}


def machine_intervals(records, state_name, end):
    """Intervals the TV state machine spent in `state_name`, from its journal
    transitions ({"event": "transition", "from": ..., "to": ...})."""
    out, open_at = [], None
    for row in records:
        if row.get("event") != "transition":
            continue
        if row.get("to") == state_name and open_at is None:
            open_at = row["_t"]
        elif row.get("to") != state_name and open_at is not None:
            if row["_t"] > open_at:
                out.append((open_at, row["_t"]))
            open_at = None
    if open_at is not None and open_at < end:
        out.append((open_at, end))
    return out


# ------------------------------------------------------------------- metrics
def metric(name, verdict, bar, **fields):
    row = {"metric": name, "verdict": verdict, "bar": bar}
    row.update(fields)
    return row


def fraction_verdict(numerator, denominator, bar):
    if denominator == 0:
        return "skipped", None
    value = numerator / denominator
    return ("pass" if value + 1e-9 >= bar else "fail"), round(value, 4)


def route_pct_at(hist, when, fallback, fallback_source):
    """The route cap in force at `when`, from the recorder alone.

    Returns ``(cap, source)`` with ``cap`` None when the recorder holds no
    value for the helper in this window. The helper's value TODAY is not
    evidence about a past evening: importing it silently would score last
    week against a cap the owner set yesterday. An explicit
    ``--assume-route-pct`` is the only way to supply one, and the receipt
    names it as an assumption.
    """
    state = state_at(hist, ROUTE_HELPER, when)
    if state is not None and state.lower() not in UNKNOWN_STATES:
        try:
            return int(round(float(state))), "recorder"
        except ValueError:
            pass
    if fallback is None:
        return None, "absent"
    return fallback, fallback_source


# ---------------------------------------------------------------- the report
def score_evening(*, window, hist, ledger, journal, route_fallback, route_fallback_source,
                  ignore_missing_lights=False):
    """Every metric from data already read: the recorder history, the ledger
    events and the journal records. No I/O, so the whole score is testable."""
    start, end = window
    episodes, sofa, det_source, episode_counts = watching_episodes(hist, start, end)
    episode_intervals = [(e["from"], e["to"]) for e in episodes]
    ledger_ok = ledger.get("available", False)
    events = ledger.get("events", [])
    metrics = []

    # 1. Living room dark within 30 s of an episode opening.
    dark_rows, dark_ok, dark_total = [], 0, 0
    for episode in episodes:
        row = {"episode_from": iso(episode["from"]), "source": episode["source"],
               "sofa_from": iso(episode["sofa_from"]), "time_to_dark_s": None, "scored": False}
        if episode["sofa_from"] is not None:
            seconds, missing, indeterminate = all_off_after(
                hist, LIVING_ROOM_LIGHTS, episode["sofa_from"], end)
            row["time_to_dark_s"] = seconds
            row["lights_without_recorder_data"] = missing
            row["unavailable_at_dark"] = indeterminate
            if missing and not ignore_missing_lights:
                # Darkness over a subset is not darkness. A light the recorder
                # has nothing for may have been lit the whole film. Scoring it
                # anyway is available, but only as a stated assumption.
                row["note"] = ("no recorder data for " + ", ".join(missing)
                               + ": darkness cannot be established over part of a room "
                               "(--ignore-missing-lights scores it anyway)")
            elif indeterminate:
                row["note"] = ("unavailable at the instant the room read dark: "
                               + ", ".join(indeterminate))
            elif missing:
                row["scored"] = True
                row["assumed"] = "scored over a subset: no recorder data for " + ", ".join(missing)
                dark_total += 1
                if seconds is not None and seconds <= DARK_S:
                    dark_ok += 1
            else:
                row["scored"] = True
                dark_total += 1
                if seconds is not None and seconds <= DARK_S:
                    dark_ok += 1
        else:
            row["note"] = "no sofa occupancy inside the episode"
        dark_rows.append(row)
    verdict, value = fraction_verdict(dark_ok, dark_total, BAR_DARK_FRACTION)
    metrics.append(metric(
        "living_room_dark_within_30s", verdict, f">= {BAR_DARK_FRACTION:.0%} of episodes",
        numerator=dark_ok, denominator=dark_total, fraction=value,
        source="recorder light states",
        note=None if dark_total else "no episode had the sofa occupied with living-room light data",
        detail=dark_rows))

    # 2. Living-room turn-ons above 0 % while an episode is open and the sofa
    #    is occupied, excluding writes a person made.
    watched_and_occupied = ae.intersect(episode_intervals, sofa)
    if ledger_ok:
        lr_all, lr_defects, brightens = [], [], []
        for event in events:
            if event["light_entity"] not in LIVING_ROOM_LIGHTS:
                continue
            if not ae.in_intervals(event["t"], watched_and_occupied):
                continue
            if is_turn_on(event):
                row = {"t": iso(event["t"]), "light": event["light_entity"], "zone": event["zone"],
                       "pct": event["to_brightness_pct"], "writer": event["writer"],
                       "source_hint": event["source_hint"],
                       "classifier_decision": decision_at(ledger.get("decisions", {}),
                                                          event["zone"], event["t"]),
                       "classifier_sensor": classifier_prediction(hist, event["zone"], event["t"])}
                lr_all.append(row)
                if event["writer"] not in HUMAN_WRITERS:
                    lr_defects.append(row)
            elif is_brighten(event):
                brightens.append({"t": iso(event["t"]), "light": event["light_entity"],
                                  "from_pct": event["from_brightness_pct"],
                                  "to_pct": event["to_brightness_pct"], "writer": event["writer"]})
        # A room that never went dark produces no off-to-on row, so zero
        # defects there means nothing was observed, not that nothing was
        # wrong. Only a room that actually darkened can pass this bar on an
        # empty numerator.
        if lr_defects:
            verdict_2, note_2 = "fail", ("denominator counts every living-room turn-on in an "
                                         "episode, human writes included")
        elif dark_ok:
            verdict_2, note_2 = "pass", ("denominator counts every living-room turn-on in an "
                                         "episode, human writes included")
        else:
            verdict_2, note_2 = "skipped", (
                "the living room never darkened inside an episode, so a light that was already "
                "lit produced no turn-on row to attribute: zero here is nothing seen, not a pass")
        metrics.append(metric(
            "living_room_turn_ons_while_watching", verdict_2, "== 0",
            numerator=len(lr_defects), denominator=len(lr_all),
            source="ledger lighting_change_events (writer attribution)",
            note=note_2, episodes_darkened=dark_ok,
            human_writes=len(lr_all) - len(lr_defects), brighten_while_watching=brightens,
            detail=lr_defects))
    else:
        metrics.append(metric(
            "living_room_turn_ons_while_watching", "skipped", "== 0",
            numerator=None, denominator=None, source="ledger unavailable",
            note="the recorder records a light turning on but not who wrote it; "
                 "without the ledger a person's own command cannot be excluded",
            detail=[]))

    # 3 and 4. Errands: a non-living-room zone whose occupancy opens during an
    #          episode, lit at or below the route cap within 60 s and off
    #          within 150 s of the zone clearing.
    errands = []
    # An errand may open inside an episode or inside the oracle's AWAY_HOLD
    # after one ended; the definition is imported from the simulator's analyser
    # so the same evening cannot score differently in the two tools.
    eligible = ae.errand_eligible(episode_intervals)
    errand_counts = {"raw": 0, "merged": 0}
    for zone in sorted(ERRAND_ZONES):
        lights = ZONE_LIGHTS[zone]
        raw_occupancy = ae.on_intervals(edges_of(hist, stable_sensor(zone)), start, end)
        errand_counts["raw"] += len(raw_occupancy)
        # Visits closer together than the turn-off bar are one errand.
        occupancy = ae.coalesce(raw_occupancy, ROUTE_OFF_S)
        errand_counts["merged"] += len(occupancy)
        for opened, cleared in occupancy:
            if not ae.in_intervals(opened, eligible):
                continue
            cap, cap_source = route_pct_at(hist, opened, route_fallback, route_fallback_source)
            row = {"zone": zone, "from": iso(opened), "to": iso(cleared), "route_pct": cap,
                   "route_pct_source": cap_source, "lights": lights,
                   "opened_in": "episode" if ae.in_intervals(opened, episode_intervals) else "hold"}
            # The zone's level immediately before the errand: the floor a route
            # response has to rise above.
            before = {}
            for light in lights:
                prior = [e for e in events if e["light_entity"] == light and e["t"] < opened]
                if prior and prior[-1]["to_state"] == "on":
                    before[light] = prior[-1]["to_brightness_pct"] or 0
                elif prior:
                    before[light] = 0
                else:
                    before[light] = None if state_at(hist, light, opened) is None else (
                        0 if state_at(hist, light, opened) != "on" else None)
            row["pre_errand_levels"] = before
            already_on = [light for light in lights if state_at(hist, light, opened) == "on"]
            row["already_lit"] = already_on
            if cap is None:
                row["route_scored"] = False
                row["route_ok"] = None
                row["lit_after_s"] = None
                row["lit_pct"] = None
                row["overshoot"] = []
                row["note"] = ("no recorded route cap for this evening; pass --assume-route-pct "
                               "to score it against a stated assumption")
            elif ledger_ok:
                zone_events = [e for e in events if e["light_entity"] in lights]
                # A route response is a RAISE. A light that merely sat at the
                # movie floor answered nothing, and scoring it as a pass is the
                # false pass this metric exists to catch.
                def raised(event):
                    floor = before.get(event["light_entity"])
                    level = event["to_brightness_pct"] or 0
                    return event["to_state"] == "on" and level > 0 and (floor is None or level > floor)
                lit = next((e for e in zone_events
                            if opened <= e["t"] <= opened + dt.timedelta(seconds=ROUTE_S)
                            and raised(e)), None)
                overshoot_end = min(cleared + dt.timedelta(seconds=ROUTE_OFF_S), end)
                overshoot = [{"t": iso(e["t"]), "light": e["light_entity"], "pct": e["to_brightness_pct"]}
                             for e in zone_events if opened <= e["t"] < overshoot_end
                             and e["to_state"] == "on" and (e["to_brightness_pct"] or 0) > cap]
                row["overshoot"] = overshoot
                above_cap = sorted(light for light in already_on
                                   if (before.get(light) or 0) > cap)
                if lit is not None:
                    row["lit_after_s"] = round((lit["t"] - opened).total_seconds(), 1)
                    row["lit_pct"] = lit["to_brightness_pct"]
                    row["lit_writer"] = lit["writer"]
                    row["route_scored"] = True
                    row["route_ok"] = bool(row["lit_pct"] <= cap and not overshoot)
                elif above_cap:
                    # Already brighter than the cap during a film is the defect
                    # itself, whether or not anything moved.
                    row["lit_after_s"] = 0.0
                    row["lit_pct"] = max(before[light] for light in above_cap)
                    row["route_scored"] = True
                    row["route_ok"] = False
                    row["note"] = ("zone was already lit above the route cap when the errand "
                                   "opened: " + ", ".join(above_cap))
                elif already_on:
                    row["lit_after_s"] = None
                    row["lit_pct"] = max((before[light] or 0) for light in already_on)
                    row["route_scored"] = False
                    row["route_ok"] = None
                    row["note"] = ("zone was already lit at or below the route cap when the "
                                   "errand opened: there is no route response to measure")
                else:
                    row["lit_after_s"] = None
                    row["lit_pct"] = None
                    row["route_scored"] = True
                    row["route_ok"] = False
                    row["note"] = "the zone never lit inside the route window"
            else:
                row["route_scored"] = False
                row["route_ok"] = None
                row["lit_after_s"] = None
                row["lit_pct"] = None
                row["overshoot"] = []
                row["note"] = "no ledger: the level a zone was lit at is not in the recorder"
            off_s, missing, off_indeterminate = all_off_after(hist, lights, cleared, end)
            row["lights_without_recorder_data"] = missing
            row["unavailable_at_off"] = off_indeterminate
            if cleared >= end:
                row["off_scored"] = False
                row["off_after_s"] = None
                row["off_note"] = "the zone had not cleared when the window ended"
            elif missing:
                row["off_scored"] = False
                row["off_after_s"] = None
                row["off_note"] = "no recorder data for " + ", ".join(missing)
            elif off_indeterminate:
                row["off_scored"] = False
                row["off_after_s"] = None
                row["off_note"] = ("unavailable at the instant the zone read dark: "
                                   + ", ".join(off_indeterminate))
            else:
                row["off_scored"] = True
                row["off_after_s"] = off_s
                row["off_ok"] = bool(off_s is not None and off_s <= ROUTE_OFF_S)
            errands.append(row)
    route_total = sum(1 for e in errands if e.get("route_scored"))
    route_ok = sum(1 for e in errands if e.get("route_scored") and e.get("route_ok"))
    verdict, value = fraction_verdict(route_ok, route_total, BAR_ROUTE_FRACTION)
    metrics.append(metric(
        "errand_lit_at_or_below_route_pct_within_60s", verdict, f">= {BAR_ROUTE_FRACTION:.0%} of errands",
        numerator=route_ok, denominator=route_total, fraction=value,
        source="ledger brightness + recorder occupancy" if ledger_ok else "ledger unavailable",
        note=None if route_total else "no errand opened during an episode, or none could be levelled",
        errands_raw=errand_counts["raw"], errands_merged=errand_counts["merged"],
        errands_eligible=len(errands),
        unscored=[{"zone": e["zone"], "from": e["from"], "reason": e.get("note")}
                  for e in errands if not e.get("route_scored")],
        detail=[e for e in errands if e.get("route_scored") and not e.get("route_ok")]))
    off_total = sum(1 for e in errands if e.get("off_scored"))
    off_ok = sum(1 for e in errands if e.get("off_scored") and e.get("off_ok"))
    verdict, value = fraction_verdict(off_ok, off_total, BAR_ROUTE_FRACTION)
    metrics.append(metric(
        "errand_off_within_150s_of_clearing", verdict, f">= {BAR_ROUTE_FRACTION:.0%} of errands",
        numerator=off_ok, denominator=off_total, fraction=value,
        source="recorder light states",
        note=None if off_total else "no errand cleared inside the window",
        unscored=[{"zone": e["zone"], "from": e["from"], "reason": e.get("off_note")}
                  for e in errands if not e.get("off_scored")],
        detail=[e for e in errands if e.get("off_scored") and not e.get("off_ok")]))

    # 5. UNATTENDED while the sofa is occupied (the publisher's state machine).
    if journal.get("available") and journal.get("rows"):
        unattended = machine_intervals(journal["rows"], UNATTENDED, end)
        bad = ae.intersect(unattended, sofa)
        metrics.append(metric(
            "unattended_while_sofa_occupied", "fail" if bad else "pass", "== 0",
            numerator=len(bad), denominator=len(unattended),
            source=f"publisher journal ({journal['records']} records)",
            detail=[{"from": iso(a), "to": iso(b)} for a, b in bad]))
    else:
        metrics.append(metric(
            "unattended_while_sofa_occupied", "skipped", "== 0",
            numerator=None, denominator=None, source="publisher journal unavailable",
            note=journal.get("reason") or "no journal records for this evening", detail=[]))

    # 6. Activity other than idle in a vacant zone (belief publisher sensors).
    activity_rows, activity_zones = [], []
    for zone in sorted(ZONE_LIGHTS):
        entity = activity_sensor(zone)
        if not has_real_states(hist, entity):
            continue
        activity_zones.append(zone)
        vacant = ae.on_intervals(edges_of(hist, stable_sensor(zone)), start, end,
                                 on=lambda s: s != "on")
        for row in hist[entity]:
            if row["state"].lower() in IDLE_STATES:
                continue
            if ae.in_intervals(row["t"], vacant):
                activity_rows.append({"t": iso(row["t"]), "zone": zone, "activity": row["state"]})
    if activity_zones:
        metrics.append(metric(
            "activity_other_than_idle_in_a_vacant_zone", "fail" if activity_rows else "pass", "== 0",
            numerator=len(activity_rows), denominator=len(activity_zones),
            source="recorder activity sensors", note="denominator is zones with an activity sensor",
            detail=activity_rows))
    else:
        metrics.append(metric(
            "activity_other_than_idle_in_a_vacant_zone", "skipped", "== 0",
            numerator=None, denominator=None, source="no activity sensor existed this evening",
            note="the belief publisher publishes these; nothing to score yet", detail=[]))

    # 7. Story S on the same evening: a light on while the asleep latch was on
    #    in a zone with no person.
    asleep = ae.on_intervals(edges_of(hist, ASLEEP), start, end)
    asleep_minutes = round(sum((b - a).total_seconds() for a, b in asleep) / 60, 1)
    if ledger_ok and has_real_states(hist, ASLEEP) and asleep_minutes > 0:
        asleep_rows, human_rows = [], []
        for event in events:
            if not is_turn_on(event) or not ae.in_intervals(event["t"], asleep):
                continue
            zone = event["zone"]
            if zone not in ZONE_CAMERA:
                continue
            if state_at(hist, stable_sensor(zone), event["t"]) == "on":
                continue
            row = {"t": iso(event["t"]), "zone": zone, "light": event["light_entity"],
                   "pct": event["to_brightness_pct"], "writer": event["writer"]}
            (human_rows if event["writer"] in HUMAN_WRITERS else asleep_rows).append(row)
        metrics.append(metric(
            "lights_on_while_asleep_in_a_vacant_zone", "fail" if asleep_rows else "pass", "== 0",
            numerator=len(asleep_rows), denominator=asleep_minutes,
            source="ledger turn-ons + recorder occupancy",
            note="denominator is minutes the asleep latch was on; human writes are listed apart",
            human_writes=human_rows, detail=asleep_rows))
    else:
        metrics.append(metric(
            "lights_on_while_asleep_in_a_vacant_zone", "skipped", "== 0",
            numerator=None, denominator=None,
            source="ledger unavailable" if not ledger_ok
            else ("no asleep latch history" if not has_real_states(hist, ASLEEP)
                  else "the asleep latch was never on in this window"),
            note="a zero-length latch is nothing to score, so it is not reported as a pass",
            detail=[]))

    # 8. What the evening cost, for information: no bar, but a dark evening
    #    with a hundred light changes is not the same as a dark quiet one.
    if ledger_ok:
        cost = {"physical_changes": len(events),
                "turn_ons": sum(1 for e in events if is_turn_on(e)),
                "by_zone": dict(collections.Counter(e["zone"] for e in events)),
                "by_writer": dict(collections.Counter(e["writer"] for e in events)),
                "source": "ledger"}
    else:
        changes = sum(max(len(hist.get(light, [])) - 1, 0) for light in ALL_LIGHTS)
        cost = {"physical_changes": changes, "turn_ons": None, "by_zone": {}, "by_writer": {},
                "source": "recorder state rows"}
    metrics.append(metric("evening_cost_in_light_changes", "info", "reported, not scored",
                          numerator=cost["physical_changes"], denominator=None, **cost))

    serialized = [{"from": iso(e["from"]), "to": iso(e["to"]), "source": e["source"],
                   "sofa_from": iso(e["sofa_from"]), "sofa_occupied": e["sofa_occupied"],
                   "sofa_minutes": e["sofa_minutes"], "minutes": e["minutes"]} for e in episodes]
    watched = ae.intersect(episode_intervals, sofa)
    return {"episodes": serialized, "metrics": metrics, "errands": errands,
            "watching_minutes": round(sum((b - a).total_seconds() for a, b in episode_intervals) / 60, 1),
            "watching_minutes_with_sofa": round(sum((b - a).total_seconds() for a, b in watched) / 60, 1),
            "sofa_minutes": round(sum((b - a).total_seconds() for a, b in sofa) / 60, 1),
            "populations": {
                "living_room_dark_within_30s": "episodes whose sofa was occupied",
                "living_room_turn_ons_while_watching": "episode time intersected with sofa occupancy",
                "errand_lit_at_or_below_route_pct_within_60s": "errands opening in an episode or its hold",
                "errand_off_within_150s_of_clearing": "the same errands, on clearing",
                "unattended_while_sofa_occupied": "publisher journal transitions against sofa occupancy",
            },
            "episode_counts": episode_counts, "errand_counts": errand_counts,
            "deterministic_source": det_source}


def classifier_prediction(hist, zone, when):
    """The zone classifier's predicted_brightness_pct at that instant, when the
    recorder holds the attribute (fetched without minimal_response)."""
    if zone not in ZONE_CAMERA:
        return None
    row = row_at(hist, lighting_state_sensor(zone), when)
    if not row:
        return None
    return {"state": row["state"], "predicted_brightness_pct": row["attrs"].get("predicted_brightness_pct")}


# ------------------------------------------------------------------ coverage
def coverage_of(hist, start, end):
    """Whether the recorder actually covers the window.

    Home Assistant synthesises the state at the window start when it has an
    earlier row, so a series whose first row is well after the start means the
    recorder was purged into the evening. The core entities are the sofa's
    stable occupancy and whichever TV source exists; without both, an evening
    cannot be scored at all.
    """
    tv_entity = TV_PLAYING if has_real_states(hist, TV_PLAYING) else (
        LG_TV if has_real_states(hist, LG_TV) else None)
    core = [SOFA_STABLE] + ([tv_entity] if tv_entity else [])
    first = {entity: hist[entity][0]["t"] for entity in core if hist.get(entity)}
    slack = start + dt.timedelta(seconds=COVERAGE_SLACK_S)
    missing = [entity for entity in core if entity not in first] + ([] if tv_entity else ["tv source"])
    late = {entity: iso(when) for entity, when in first.items() if when > slack}
    entities = {entity: {"rows": len(rows), "first": iso(rows[0]["t"]), "last": iso(rows[-1]["t"])}
                for entity, rows in sorted(hist.items()) if rows}

    # A recorder that stops partway through the evening leaves a window whose
    # first rows are all present and whose later hours are silence. Checking
    # only the first row of each series calls that fully covered and lets the
    # lights' last known state -- off, at 18:00 -- grant darkness for a film
    # nobody recorded. So measure every stretch with no recorded change from
    # ANY entity; build_report refuses the ones that sit inside an open
    # episode.
    stamps = sorted({row["t"] for rows in hist.values() for row in rows
                     if start <= row["t"] <= end})
    gaps, previous = [], start
    for when in stamps + [end]:
        if (when - previous).total_seconds() > MAX_QUIET_GAP_S:
            gaps.append({"from": iso(previous), "to": iso(when),
                         "seconds": round((when - previous).total_seconds(), 1)})
        previous = max(previous, when)

    # Darkness is a statement about a whole room. A light with no row at all
    # is a light that may have burned through the film unrecorded.
    lights_without_rows = sorted(light for light in ALL_LIGHTS if not hist.get(light))

    # The gaps and the lights without rows are evidence, not a verdict. A house
    # where nothing happens records nothing, and a trailing silence after the
    # film ended is an ordinary quiet night, not a broken recorder. What proves
    # a recorder stopped is silence WHILE a watching episode was open, and that
    # test needs the episodes, so build_report applies it after scoring.
    return {"covered": not missing and not late,
            "core_entities": core, "tv_source": tv_entity,
            "missing": missing, "starts_late": late, "quiet_gaps": gaps,
            "max_quiet_gap_s": MAX_QUIET_GAP_S,
            "lights_without_rows": lights_without_rows,
            "window": [iso(start), iso(end)], "entities": entities}


# -------------------------------------------------------------------- render
MD_DETAIL_LIMIT = 10


def render_markdown(report):
    """A page a human can read in half a minute.

    Failure lists are capped at MD_DETAIL_LIMIT rows: a flapping sensor can
    produce fifty failed errands, and a page nobody reads is not a receipt.
    The JSON beside it holds every row.
    """
    lines = [f"# TV evening post-mortem ({report['evening']})", "",
             f"Window {report['window'][0]} to {report['window'][1]} ({report['timezone']}).",
             f"Verdict: **{report['verdict'].upper()}** (exit {report['exit_code']}).", ""]
    lines.append("## Sources")
    lines.append("")
    for name, block in report["sources"].items():
        detail = block.get("note") or block.get("reason") or ""
        lines.append(f"- **{name}**: {'yes' if block.get('available') else 'no'}"
                     + (f" -- {detail}" if detail else ""))
    lines.append("")
    coverage = report["coverage"]
    lines.append(f"Recorder coverage: {'yes' if coverage['covered'] else 'NO'}; "
                 f"TV source {coverage['tv_source'] or 'none'}.")
    if coverage.get("quiet_gaps"):
        lines.append("")
        lines.append(f"Recorder gaps over {coverage.get('max_quiet_gap_s')} s with nothing "
                     f"recorded from any entity: "
                     + "; ".join(f"{g['from']} to {g['to']}" for g in coverage["quiet_gaps"][:5]))
    if coverage.get("lights_without_rows"):
        lines.append("")
        lines.append("Lights with no recorder row at all: "
                     + ", ".join(coverage["lights_without_rows"]))
    banner = (report.get("packages") or {}).get("note")
    if banner:
        lines.append("")
        lines.append(f"> {banner}")
    counts = report.get("counts")
    if counts:
        lines.append("")
        lines.append(f"Bars: {counts['measured']} measured, {counts['skipped']} skipped, "
                     f"{counts['failed']} failed, of {counts['bars']}.")
    if report.get("unscorable_reason"):
        lines.append("")
        lines.append(f"Not scored: {report['unscorable_reason']}")
    lines.append("")
    lines.append("## Episodes")
    lines.append("")
    if not report["episodes"]:
        lines.append("No watching episode in this window.")
    else:
        lines.append("| from | to | minutes | source | sofa occupied from | sofa minutes |")
        lines.append("|---|---|---|---|---|---|")
        for episode in report["episodes"]:
            lines.append(f"| {episode['from']} | {episode['to']} | {episode['minutes']} | "
                         f"{episode['source']} | {episode['sofa_from'] or '-'} | "
                         f"{episode.get('sofa_minutes', '-')} |")
        lines.append("")
        lines.append(f"Watching minutes {report.get('watching_minutes')}, of which "
                     f"{report.get('watching_minutes_with_sofa')} with the sofa occupied. "
                     "Each metric names the population it used below.")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| metric | verdict | value | bar | source |")
    lines.append("|---|---|---|---|---|")
    for row in report["metrics"]:
        lines.append(f"| {row['metric']} | {row['verdict'].upper()} | {value_text(row)} | "
                     f"{row['bar']} | {row.get('source', '-')} |")
    lines.append("")
    notes = [(row["metric"], row["note"]) for row in report["metrics"] if row.get("note")]
    if notes:
        lines.append("## Notes")
        lines.append("")
        for name, note in notes:
            lines.append(f"- **{name}**: {note}")
        lines.append("")
    failures = [row for row in report["metrics"] if row["verdict"] == "fail"]
    if failures:
        lines.append("## Failures")
        lines.append("")
        for row in failures:
            detail = row.get("detail") or []
            lines.append(f"### {row['metric']}")
            lines.append("")
            for item in detail[:MD_DETAIL_LIMIT]:
                lines.append(f"- `{json.dumps(item, sort_keys=True, default=str)}`")
            if len(detail) > MD_DETAIL_LIMIT:
                lines.append(f"- ... and {len(detail) - MD_DETAIL_LIMIT} more, in "
                             f"evening-{report['evening']}.json")
            lines.append("")
    populations = report.get("populations") or {}
    if populations:
        lines.append("## Populations")
        lines.append("")
        for name, text in populations.items():
            lines.append(f"- **{name}**: {text}")
        lines.append("")
    lines.append("Every fraction carries its denominator: a metric with a small "
                 "denominator is a small sample, not a pass. A skipped bar measured "
                 "nothing and never counts towards one.")
    return "\n".join(lines) + "\n"


def value_text(row):
    if row["verdict"] == "skipped":
        return "-"
    if row.get("fraction") is not None:
        return f"{row['numerator']}/{row['denominator']} ({row['fraction']:.0%})"
    if row.get("denominator") is not None:
        return f"{row['numerator']}/{row['denominator']}"
    return str(row.get("numerator"))


def print_verdicts(report, stream=sys.stdout):
    print(f"evening {report['evening']}  window {report['window'][0]} -> {report['window'][1]}",
          file=stream)
    print(f"episodes: {len(report['episodes'])} "
          f"({report['watching_minutes']} watching minutes, "
          f"{report.get('watching_minutes_with_sofa', '?')} with the sofa, sources: "
          f"{','.join(sorted({e['source'] for e in report['episodes']})) or 'none'})", file=stream)
    banner = (report.get("packages") or {}).get("note")
    if banner:
        print(f"note: {banner}", file=stream)
    for row in report["metrics"]:
        print(f"  {row['verdict'].upper():<8} {row['metric']:<46} {value_text(row):<18} "
              f"bar {row['bar']}", file=stream)
    counts = report.get("counts")
    if counts:
        print(f"bars: {counts['measured']} measured, {counts['skipped']} skipped, "
              f"{counts['failed']} failed, of {counts['bars']}", file=stream)
    print(f"verdict: {report['verdict']} (exit {report['exit_code']})", file=stream)


# ---------------------------------------------------------------------- main
def entity_list(zones):
    """Every entity the recorder is asked for, in one stable order."""
    minimal = [TV_PLAYING, TV_WATCHING, LG_TV, SOFA_STABLE, ANY_OCCUPIED, ASLEEP, ROUTE_HELPER]
    minimal += [stable_sensor(zone) for zone in sorted(zones)]
    minimal += [activity_sensor(zone) for zone in sorted(zones)]
    minimal += ALL_LIGHTS
    with_attributes = [lighting_state_sensor(zone) for zone in sorted(zones)]
    return minimal, with_attributes


def collect_history(getter, start, end, zones, tz):
    """Two recorder queries: the lot with minimal_response, then the classifier
    sensors without it, because minimal_response omits attributes and
    predicted_brightness_pct is an attribute."""
    minimal, with_attributes = entity_list(zones)
    hist = parse_history(getter(history_path(start, end, minimal, minimal=True)))
    hist.update(parse_history(getter(history_path(start, end, with_attributes, minimal=False))))
    for rows in hist.values():
        for row in rows:
            row["t"] = local(row["t"], tz)
    return hist


def parse_when(value, tz, hour):
    """A local date (taken at `hour`) or a local datetime."""
    text = value.strip()
    try:
        return dt.datetime.combine(dt.date.fromisoformat(text), dt.time(hour), tzinfo=tz)
    except ValueError:
        pass
    parsed = dt.datetime.fromisoformat(text)
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed


def default_window(now, tz):
    """The most recent evening: 18:00 to 01:00 the next day."""
    local = now.astimezone(tz)
    day = local.date() if local.hour >= 18 else local.date() - dt.timedelta(days=1)
    start = dt.datetime.combine(day, dt.time(18), tzinfo=tz)
    return start, dt.datetime.combine(day + dt.timedelta(days=1), dt.time(1), tzinfo=tz)


M2_ENTITIES = (TV_PLAYING,)


def packages_seen(hist):
    """Which of the deployed entities actually had a state this evening.

    An evening before the M2 deploy has no ``tv_playing`` sensor at all, so
    its numbers are a baseline and not a test of the deployed fix. The reader
    should not have to infer that from the TV source line.
    """
    absent = [entity for entity in M2_ENTITIES if not has_real_states(hist, entity)]
    return {"m2_entities_absent": absent,
            "note": (f"{', '.join(absent)} had no state this evening: the house was running the "
                     "pre-M2 packages, so these numbers are a baseline, not a test of the "
                     "deployed fix.") if absent else None}


def build_report(*, window, tz_name, hist, ledger, journal, route_fallback, route_fallback_source,
                 inputs, now, ignore_missing_lights=False):
    """The receipt: coverage, episodes, metrics, verdict and exit code."""
    start, end = window
    coverage = coverage_of(hist, start, end)
    report = {
        "schema": SCHEMA, "generated_at": iso(now), "evening": start.date().isoformat(),
        "window": [iso(start), iso(end)], "timezone": tz_name, "inputs": inputs,
        "coverage": coverage,
        "sources": {
            "recorder": {"available": bool(hist), "note": f"{len(hist)} entities with rows"},
            "ledger": {"available": ledger.get("available", False),
                       "reason": ledger.get("reason"),
                       "note": (f"{len(ledger.get('events', []))} physical changes"
                                if ledger.get("available") else None)},
            "publisher_journal": {"available": journal.get("available", False),
                                  "reason": journal.get("reason"),
                                  "note": (f"{journal.get('records')} records"
                                           if journal.get("available") else None)},
        },
    }
    report["packages"] = packages_seen(hist)
    report["counts"] = {"bars": 0, "measured": 0, "skipped": 0, "failed": 0}
    if not coverage["covered"]:
        why = []
        if coverage["missing"]:
            why.append("missing " + ", ".join(coverage["missing"]))
        if coverage["starts_late"]:
            why.append("starts late: " + ", ".join(sorted(coverage["starts_late"])))
        report.update({"episodes": [], "metrics": [], "errands": [], "watching_minutes": 0.0,
                       "verdict": "unscorable", "exit_code": EXIT_UNSCORABLE,
                       "unscorable_reason": "the recorder does not cover this window: "
                                            + "; ".join(why)})
        return report
    scored = score_evening(window=window, hist=hist, ledger=ledger, journal=journal,
                           route_fallback=route_fallback, route_fallback_source=route_fallback_source,
                           ignore_missing_lights=ignore_missing_lights)
    report.update(scored)
    # Silence while an episode was open is a stopped recorder: a film was
    # playing, somebody was on the sofa, and not one entity moved for hours.
    # Silence after the last episode closed is an ordinary quiet night.
    episode_spans = [(parse_time(e["from"]), parse_time(e["to"])) for e in scored["episodes"]]
    blind = [gap for gap in coverage.get("quiet_gaps", [])
             if ae.intersect([(parse_time(gap["from"]), parse_time(gap["to"]))], episode_spans)]
    coverage["quiet_gaps_inside_an_episode"] = blind
    if blind:
        report.update({"metrics": [], "errands": [],
                       "verdict": "unscorable", "exit_code": EXIT_UNSCORABLE,
                       "unscorable_reason": (
                           f"nothing was recorded from any entity for "
                           f"{max(g['seconds'] for g in blind):.0f} s while a watching episode "
                           "was open: the recorder stopped, so this evening cannot be scored")})
        return report
    if not scored["episodes"]:
        report.update({"verdict": "unscorable", "exit_code": EXIT_UNSCORABLE,
                       "unscorable_reason": "no watching episode in this window"})
        return report
    bars = [row for row in scored["metrics"] if row["verdict"] != "info"]
    failed = [row["metric"] for row in bars if row["verdict"] == "fail"]
    skipped = [row["metric"] for row in bars if row["verdict"] == "skipped"]
    measured = [row["metric"] for row in bars if row["verdict"] in ("pass", "fail")]
    report.update({"failed_metrics": failed, "skipped_metrics": skipped,
                   "measured_metrics": measured,
                   "counts": {"bars": len(bars), "measured": len(measured),
                              "skipped": len(skipped), "failed": len(failed)}})
    if failed:
        report.update({"verdict": "fail", "exit_code": EXIT_FAILED_BAR})
        return report
    # A pass has to have measured something, and specifically the bar the
    # whole story rests on. An evening where every bar skipped is an evening
    # nobody scored, and announcing it as a pass is the worst thing this tool
    # could do.
    if not measured:
        report.update({"verdict": "unscorable", "exit_code": EXIT_UNSCORABLE,
                       "unscorable_reason": "no metric could be scored: "
                                            + ", ".join(skipped)})
        return report
    if REQUIRED_BAR not in measured:
        report.update({"verdict": "unscorable", "exit_code": EXIT_UNSCORABLE,
                       "unscorable_reason": f"{REQUIRED_BAR} could not be scored, so the "
                                            "evening says nothing about story T"})
        return report
    report.update({"verdict": "pass", "exit_code": EXIT_OK})
    return report


def receipt_stem(evening, report, rescore=False):
    """The receipt's base name.

    An unscorable run is filed apart, and a deliberate re-score is stamped, so
    neither can take the one name a scored evening will want. Finding an
    evening already blocked by a run that measured nothing was the whole
    problem: the tool would have had no way to score the evening again.
    """
    if report.get("verdict") == "unscorable":
        stamp = str(report.get("generated_at", "")).replace(":", "").replace("-", "")[:15]
        return f"evening-{evening}.unscorable-{stamp or 'run'}"
    if rescore:
        stamp = str(report.get("generated_at", "")).replace(":", "").replace("-", "")[:15]
        return f"evening-{evening}.rescore-{stamp or 'run'}"
    return f"evening-{evening}"


def write_receipt(out_dir, evening, report, rescore=False):
    """<stem>.json and <stem>.md, 0600 in a 0700 directory.

    A scored receipt is never overwritten: an evening scored twice with
    different inputs would leave no record of which run the numbers came from.
    Use --rescore to write a stamped second copy alongside the first.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    stem = receipt_stem(evening, report, rescore)
    paths = {"json": out / f"{stem}.json", "md": out / f"{stem}.md"}
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing:
        raise PostmortemError("a receipt for this evening already exists: " + ", ".join(existing)
                              + " (pass --rescore to write a stamped second copy)")
    paths["json"].write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    paths["md"].write_text(render_markdown(report), encoding="utf-8")
    for path in paths.values():
        os.chmod(path, 0o600)
    return {key: str(path) for key, path in paths.items()}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default=None,
                        help="local date (taken at 18:00) or local datetime; default the most recent evening")
    parser.add_argument("--until", default=None,
                        help="local date (taken at 01:00) or local datetime; default --since plus 7 h")
    parser.add_argument("--out", default=DEFAULT_OUT_ROOT, help="receipt directory (0700)")
    parser.add_argument("--ha-url", default=DEFAULT_HA_URL)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="file holding HA_TOKEN")
    parser.add_argument("--db", default=DEFAULT_DB, help="intelligence ledger (read-only)")
    parser.add_argument("--journal-dir", default=DEFAULT_JOURNAL_DIR)
    parser.add_argument("--timezone", default=DEFAULT_TZ)
    parser.add_argument("--no-ledger", action="store_true", help="score from the recorder alone")
    parser.add_argument("--assume-route-pct", type=int, default=None,
                        help="score errands against this route cap when the recorder holds no "
                             "value for the helper in the window; recorded in the receipt as an "
                             "assumption, never inferred from today's state")
    parser.add_argument("--ignore-missing-lights", action="store_true",
                        help="score darkness over the lights the recorder does have, naming the "
                             "ones it does not; recorded in the receipt as an assumption")
    parser.add_argument("--rescore", action="store_true",
                        help="write a stamped second receipt beside an existing one")
    parser.add_argument("--json", action="store_true", help="print the receipt as JSON on stdout")
    return parser.parse_args(argv)


def run(args, getter=None, now=None, secrets=()):
    """Everything but argument parsing, so tests can inject the HTTP getter."""
    tz = ZoneInfo(args.timezone)
    now = now or dt.datetime.now(tz)
    if args.since:
        start = parse_when(args.since, tz, 18)
        end = parse_when(args.until, tz, 1) if args.until else start + dt.timedelta(hours=7)
    elif args.until:
        end = parse_when(args.until, tz, 1)
        start = end - dt.timedelta(hours=7)
    else:
        start, end = default_window(now, tz)
    if end <= start:
        raise PostmortemError(f"empty window: {iso(start)} to {iso(end)}")

    hist = collect_history(getter, start, end, sorted(ZONE_LIGHTS), tz)
    if args.assume_route_pct is None:
        route_fallback, route_fallback_source = None, "absent"
    else:
        route_fallback, route_fallback_source = args.assume_route_pct, "assumed"

    ledger = {"available": False, "reason": "disabled with --no-ledger", "events": []}
    if not args.no_ledger:
        try:
            conn = open_ledger(args.db)
        except sqlite3.Error as error:
            ledger = {"available": False, "reason": f"ledger not readable: {error}", "events": []}
        else:
            try:
                events = ledger_events(conn, sorted(ZONE_LIGHTS), start, end, tz)
                decisions = ledger_decisions(conn, sorted(ZONE_LIGHTS), start, end, tz)
                ledger = {"available": True, "reason": None, "events": events,
                          "decisions": decisions, "path": args.db}
            except sqlite3.Error as error:
                ledger = {"available": False, "reason": f"ledger query failed: {error}", "events": []}
            finally:
                conn.close()

    journal = read_journal(args.journal_dir, start, end, tz)
    inputs = {"ha_url": args.ha_url, "db": None if args.no_ledger else args.db,
              "journal_dir": args.journal_dir, "timezone": args.timezone,
              "since": args.since, "until": args.until,
              "assumed_route_pct": args.assume_route_pct,
              "ignore_missing_lights": bool(args.ignore_missing_lights)}
    report = build_report(window=(start, end), tz_name=args.timezone, hist=hist, ledger=ledger,
                          journal=journal, route_fallback=route_fallback,
                          route_fallback_source=route_fallback_source, inputs=inputs, now=now,
                          ignore_missing_lights=bool(args.ignore_missing_lights))
    report = json.loads(json.dumps(scrub(report, secrets), default=str))
    return report


def main(argv=None):
    args = parse_args(argv)
    token = read_token(args.env_file)
    if not token:
        print(f"no HA_TOKEN in {args.env_file}", file=sys.stderr)
        return EXIT_UNSCORABLE
    try:
        report = run(args, getter=http_getter(args.ha_url, token), secrets=(token,))
        written = write_receipt(args.out, report["evening"], report, rescore=args.rescore)
    except (PostmortemError, urllib.error.URLError, OSError, ValueError) as error:
        print(scrub(f"cannot score this evening: {error}", (token,)), file=sys.stderr)
        return EXIT_UNSCORABLE
    report["receipt"] = written
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_verdicts(report)
        print(f"receipt: {written['json']}")
        print(f"         {written['md']}")
    if report["verdict"] == "unscorable":
        print(f"not scored: {report.get('unscorable_reason')}", file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
