#!/usr/bin/env python3
"""Night light-activation post-mortem: a read-only join over the intelligence ledger.

For every light that turned on between night_start and night_end (local time),
this tool answers "which writer lit it, at what brightness signature, while the
classifier believed what". It reads the intelligence SQLite ledger and, when
available, a read-only snapshot of the observer's memory store for presence
corroboration. It writes nothing except its own report directory.

Writer classes come from the HA context the actuators already record
(``ha_context_json``: ``parent_id`` means an automation or script, ``user_id``
means a person, neither means a bridge or an unknown source), plus the
ledger's ``source_hint``. Brightness signatures follow the generator's
constants in tools/build-living-lights-yaml.py: 20 = vacant-night floor,
8 = night_safe or movie dim, 80 = present ramp target, 50 = vacant-day floor
or anticipated pre-warm, 55 = pass-through, 30 = asleep cap.

The ledger records up to four rows per physical change (one per trigger
attribute); rows are deduplicated by (light, second, from_state, to_state).

Usage:
  tools/night-postmortem-join.py --since 2026-09-02 --out /path/to/report-dir
  tools/night-postmortem-join.py --self-test
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------- latch writers
LATCH_ENTITY = "input_boolean.living_lights_asleep"
LATCH_WRITER_ENTITY = "input_text.living_lights_asleep_writer"
FROM_ESTIMATOR_ENTITY = "input_boolean.living_lights_asleep_from_estimator"
WRITER_WINDOW_S = 30
"""How long after a latch transition the writer helper may still name it.

Every automation that flips the latch writes the helper in the same action
block, so the helper moves within a tick. A window wider than that would let
the PREVIOUS transition's writer be read as this one's, which is the one
mistake that would make the attribution lie rather than merely say nothing."""

PUBLISHER_WRITERS = ("mirror",)
"""Writer prefixes that mean the belief publisher, through the mirror
automation that is its only path to the latch."""
ALLOWED_WHILE_LIVE = ("mirror", "manual", "hard_backstop")
"""While the estimator is live, these three are the only writers the plan
permits: the mirror automation, a person, and the ungated hard backstop.
Anything else is a legacy automation that should have been gated off."""

DEFAULT_HA_URL = "http://homeassistant.local:8123"
DEFAULT_ENV_FILE = "/opt/home-ai-voice/.env"

DEFAULT_DB = "/opt/home-ai-voice/intelligence-data/intelligence.sqlite"
DEFAULT_MEMORY_SNAPSHOT = "/srv/data/vjepa-home-snapshots/2026-09-17/memory.sqlite3"
DEFAULT_OUT_ROOT = Path("/home/marcelo-lima/vjepa-home/operations")

# Mirrors ZONE_CAMERA in tools/build-living-lights-actuators.py.
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

# Generator brightness constants and what they mean (build-living-lights-yaml.py).
SIGNATURES = [
    (20, "vacant_night_floor"), (8, "night_safe_or_movie_dim"), (80, "present_ramp_target"),
    (50, "vacant_day_floor_or_anticipated"), (55, "pass_through"), (30, "asleep_cap"),
    (90, "morning_energize_override"), (15, "return_home_night"), (3, "gaming_dim"),
]


def signature(brightness):
    if brightness is None:
        return "unknown"
    try:
        value = float(brightness)
    except (TypeError, ValueError):
        return "unknown"
    for pct, name in SIGNATURES:
        if abs(value - pct) <= 2.5:
            return f"{pct}:{name}"
    return f"{int(round(value))}:other"


def writer_class(ha_context_json, source_hint, command_id):
    """Who wrote this light change.

    A person's own command is excluded from the defect count, so the test for
    one has to be the evidence that a person was there: Home Assistant's
    context ``user_id``. A ``command_id`` says only that the change came
    through the override channel, which an automation also uses -- the
    good-morning energize writes one -- so testing it first let an automation
    launder itself into ``explicit_command`` and out of the count. The user
    check therefore comes first, and an override command with no user behind
    it is named as such and stays counted.
    """
    try:
        context = json.loads(ha_context_json or "{}")
    except ValueError:
        context = {}
    if context.get("user_id"):
        return "explicit_command" if command_id else "user"
    if command_id:
        return "explicit_command_automation"
    if context.get("parent_id"):
        return "automation"
    if source_hint == "automation_or_script":
        return "automation_by_hint"
    return "unattributed"


def writer_family(value):
    """The writer's family: the part before the colon, lower-cased.

    The helper holds ``legacy_off:presence``, ``mirror:likely_asleep`` and the
    like, so the family is what a rule is written against and the tail is
    evidence.
    """
    text = (value or "").strip().lower()
    if not text or text in ("unknown", "unavailable", "none"):
        return None
    return text.split(":", 1)[0]


def latch_transitions(latch_rows, writer_rows, live_rows, window_s=WRITER_WINDOW_S):
    """Every on/off transition of the latch, with the writer that claimed it.

    ``*_rows`` are recorder history rows, ``{"t", "state"}``, oldest first.
    The writer is the helper's first value at or within ``window_s`` after the
    transition; automations write it in the same action block as the flip. A
    transition with no such value is ``unattributed`` rather than guessed at.
    """
    out = []
    previous = None
    gaps = 0
    for row in latch_rows:
        state = (row.get("state") or "").lower()
        if state not in ("on", "off"):
            if state in ("unknown", "unavailable"):
                # A Home Assistant restart takes the helper unavailable and
                # restores it. A value that differs across that gap is the
                # restore, not a write, and attributing it would be guesswork;
                # the count is reported so the omission is never silent.
                previous = None
                gaps += 1
            continue
        if previous is not None and state != previous:
            when = row["t"]
            claimed = [w for w in writer_rows
                       if 0 <= (w["t"] - when).total_seconds() <= window_s]
            value = claimed[0]["state"] if claimed else None
            live = None
            for candidate in live_rows:
                if candidate["t"] <= when:
                    live = (candidate.get("state") or "").lower() == "on"
                else:
                    break
            out.append({
                "at": when.isoformat(),
                "to": state,
                "writer": (value or "unattributed"),
                "writer_family": writer_family(value) or "unattributed",
                "estimator_live": live,
            })
        previous = state
    if gaps and out:
        out[0].setdefault("_gaps", gaps)
    return out


def latch_summary(transitions, availability_gaps=0):
    """Counts, and the violations the definition of done names."""
    families = collections.Counter(t["writer_family"] for t in transitions)
    clears = [t for t in transitions if t["to"] == "off"]
    live = [t for t in transitions if t["estimator_live"]]
    forbidden = [t for t in live if t["writer_family"] not in ALLOWED_WHILE_LIVE]
    return {
        "transitions": len(transitions),
        "latches": sum(1 for t in transitions if t["to"] == "on"),
        "clears": len(clears),
        "by_writer_family": dict(sorted(families.items(), key=lambda kv: (-kv[1], kv[0]))),
        "clears_by_writer": dict(sorted(
            collections.Counter(t["writer"] for t in clears).items(),
            key=lambda kv: (-kv[1], kv[0]))),
        "written_by_the_publisher": sum(
            1 for t in transitions if t["writer_family"] in PUBLISHER_WRITERS),
        "while_estimator_live": len(live),
        "forbidden_while_live": len(forbidden),
        "forbidden_detail": forbidden[:20],
        "unattributed": sum(1 for t in transitions if t["writer_family"] == "unattributed"),
        "availability_gaps": availability_gaps,
    }


def parse_ts(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def in_night(local, night_start, night_end):
    minutes = local.hour * 60 + local.minute
    return night_start <= minutes < night_end


def night_key(local, night_end):
    """Nights are keyed by the calendar date they end on (a 03:00 event on the
    18th belongs to the night of 17->18, keyed 2026-09-18)."""
    return local.date().isoformat() if local.hour * 60 + local.minute < night_end else (local + dt.timedelta(days=1)).date().isoformat()


def open_ro(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    return conn


def load_events(conn, since, until):
    rows = conn.execute(
        "SELECT ts, light_entity, zone, from_state, to_state, from_brightness_pct, to_brightness_pct, "
        "from_color_temp_kelvin, to_color_temp_kelvin, command_id, source_hint, trigger_attribute, "
        "ha_context_json, context_json FROM lighting_change_events WHERE ts >= ? AND ts < ? ORDER BY ts",
        (since, until)).fetchall()
    return rows


def dedupe(rows):
    """Collapse the per-attribute rows into one physical change per second."""
    seen = {}
    for row in rows:
        stamp = parse_ts(row["ts"])
        if stamp is None:
            continue
        key = (row["light_entity"], stamp.replace(microsecond=0), row["from_state"], row["to_state"])
        current = seen.get(key)
        # Prefer the row that carries a brightness (the attribute rows differ in what they carry).
        if current is None or (current["to_brightness_pct"] is None and row["to_brightness_pct"] is not None):
            seen[key] = row
    return [seen[key] for key in sorted(seen, key=lambda k: (k[1], k[0]))]


def latest_decision(conn, zone, ts):
    row = conn.execute(
        "SELECT ts, from_state, to_state, predicted_brightness_pct FROM lighting_decisions "
        "WHERE zone = ? AND ts <= ? ORDER BY ts DESC LIMIT 1", (zone, ts)).fetchone()
    return dict(row) if row else None


def load_overrides(conn, since, until):
    rows = conn.execute(
        "SELECT ts, zone, state, tv_playing, asleep, night_safe, user_at_home, profile, "
        "predicted_brightness_pct, actual_brightness_pct FROM override_events WHERE ts >= ? AND ts < ?",
        (since, until)).fetchall()
    by_zone = collections.defaultdict(list)
    for row in rows:
        stamp = parse_ts(row["ts"])
        if stamp is not None:
            by_zone[row["zone"]].append((stamp, dict(row)))
    for zone in by_zone:
        by_zone[zone].sort(key=lambda item: item[0])
    return by_zone


def nearest(items, stamp, window_s):
    best, best_gap = None, None
    for candidate_stamp, payload in items:
        gap = abs((candidate_stamp - stamp).total_seconds())
        if gap <= window_s and (best_gap is None or gap < best_gap):
            best, best_gap = payload, gap
    return best, best_gap


def next_off(all_rows, light, stamp, within_s):
    """The first off transition of the same light within the window, with its writer."""
    for row in all_rows:
        if row["light_entity"] != light or row["to_state"] != "off":
            continue
        other = parse_ts(row["ts"])
        if other is None or other <= stamp:
            continue
        gap = (other - stamp).total_seconds()
        if gap > within_s:
            break
        return {"after_s": round(gap, 1), "writer": writer_class(row["ha_context_json"], row["source_hint"], row["command_id"])}
    return None


def memory_presence(memory_conn, camera, stamp, window_s=120):
    """Best-effort presence corroboration from a read-only memory snapshot."""
    if memory_conn is None or camera is None:
        return None
    # memory_observation.received is a POSIX epoch (REAL), not an ISO string.
    epoch = stamp.timestamp()
    rows = memory_conn.execute(
        "SELECT received, payload FROM memory_observation WHERE camera = ? AND received >= ? AND received <= ? "
        "ORDER BY received LIMIT 40", (camera, epoch - window_s, epoch + window_s)).fetchall()
    if not rows:
        return {"observations_in_window": 0}
    presence = collections.Counter()
    occupancy = collections.Counter()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except ValueError:
            continue
        state = ((payload.get("person_presence") or {}).get("state") if isinstance(payload.get("person_presence"), dict) else None)
        presence[state or "absent_field"] += 1
        context = payload.get("context") or {}
        report = context.get("occupancy_report") if isinstance(context, dict) else None
        occupancy[report if isinstance(report, str) else (report or {}).get("state") if isinstance(report, dict) else "absent_field"] += 1
    return {"observations_in_window": len(rows), "person_presence": dict(presence), "occupancy_report": dict(occupancy)}


def analyze(conn, memory_conn, since, until, night_start, night_end, off_within_s=60):
    raw = load_events(conn, since, until)
    physical = dedupe(raw)
    overrides = load_overrides(conn, since, until)
    events = []
    for row in physical:
        if row["to_state"] != "on" or row["from_state"] == "on":
            continue
        stamp = parse_ts(row["ts"])
        if stamp is None or not in_night(stamp, night_start, night_end):
            continue
        try:
            context = json.loads(row["context_json"] or "{}")
        except ValueError:
            context = {}
        zone = row["zone"]
        override, override_gap = nearest(overrides.get(zone, []), stamp, 120)
        decision = latest_decision(conn, zone, row["ts"])
        camera = ZONE_CAMERA.get(zone)
        events.append({
            "ts": row["ts"], "night": night_key(stamp, night_end), "hour": stamp.hour,
            "light": row["light_entity"], "zone": zone, "camera": camera,
            "from_state": row["from_state"], "to_brightness_pct": row["to_brightness_pct"],
            "signature": signature(row["to_brightness_pct"]),
            "color_temp_changed": (row["from_color_temp_kelvin"] != row["to_color_temp_kelvin"]),
            "writer": writer_class(row["ha_context_json"], row["source_hint"], row["command_id"]),
            "source_hint": row["source_hint"], "command_id": row["command_id"],
            "ledger_context": {k: context.get(k) for k in ("any_occupied", "asleep", "night_safe", "gaming_active", "profile", "state", "user_at_home", "tv_playing") if k in context},
            "classifier_decision": decision,
            "override_context": ({k: override.get(k) for k in ("state", "tv_playing", "asleep", "night_safe", "user_at_home", "profile", "predicted_brightness_pct")} if override else None),
            "override_gap_s": (round(override_gap, 1) if override_gap is not None else None),
            "next_off": next_off(physical, row["light_entity"], stamp, off_within_s),
            "memory": memory_presence(memory_conn, camera, stamp),
        })
    return {"raw_rows": len(raw), "physical_changes": len(physical), "events": events}


def summarize(result, night_end):
    events = result["events"]
    def count(key):
        counter = collections.Counter(key(e) for e in events)
        return dict(sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0]))))
    nights = sorted({e["night"] for e in events})
    summary = {
        "nights_with_events": len(nights), "nights": nights,
        "total_night_on_events": len(events),
        "per_night": count(lambda e: e["night"]),
        "by_writer": count(lambda e: e["writer"]),
        "by_signature": count(lambda e: e["signature"]),
        "by_zone": count(lambda e: e["zone"]),
        "by_hour": count(lambda e: e["hour"]),
        "by_asleep_flag": count(lambda e: str((e["ledger_context"] or {}).get("asleep"))),
        "by_classifier_state": count(lambda e: (e["classifier_decision"] or {}).get("to_state")),
        "by_writer_and_signature": count(lambda e: f"{e['writer']} | {e['signature']}"),
        "color_temp_only_turn_ons": sum(1 for e in events if e["color_temp_changed"] and e["signature"].endswith("other")),
        "followed_by_user_off_within_60s": sum(1 for e in events if e["next_off"] and e["next_off"]["writer"] == "user"),
        "followed_by_any_off_within_60s": sum(1 for e in events if e["next_off"]),
        "vacant_floor_while_not_asleep": sum(1 for e in events if e["signature"].startswith("20:") and (e["ledger_context"] or {}).get("asleep") is False),
        "memory_corroboration_available": sum(1 for e in events if e["memory"] and e["memory"].get("observations_in_window")),
    }
    return summary


def render_markdown(summary, since, until, night_start, night_end):
    lines = [f"# Night light-activation post-mortem ({since} to {until}, {night_start//60:02d}:{night_start%60:02d}-{night_end//60:02d}:{night_end%60:02d} local)", ""]
    lines.append(f"Physical off-to-on events at night: **{summary['total_night_on_events']}** across {summary['nights_with_events']} nights.")
    lines.append("")
    for title, key in (("By writer", "by_writer"), ("By brightness signature", "by_signature"), ("By writer and signature", "by_writer_and_signature"), ("By zone", "by_zone"), ("By hour", "by_hour"), ("By asleep flag at the time", "by_asleep_flag"), ("By classifier state at the time", "by_classifier_state"), ("Per night", "per_night")):
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| key | count |")
        lines.append("|---|---|")
        for k, v in summary[key].items():
            lines.append(f"| {k} | {v} |")
        lines.append("")
    latch = summary.get("latch")
    if latch:
        lines.append("## Who wrote the asleep latch")
        lines.append("")
        if not latch.get("available"):
            lines.append(f"Not attributed: {latch.get('reason')}")
            lines.append("")
        else:
            lines.append(f"{latch['transitions']} transitions "
                         f"({latch['latches']} latches, {latch['clears']} clears), "
                         f"attributed from the writer helper within "
                         f"{latch['writer_window_s']} s of each flip.")
            lines.append("")
            lines.append("| writer family | count |")
            lines.append("|---|---|")
            for k, v in latch["by_writer_family"].items():
                lines.append(f"| {k} | {v} |")
            lines.append("")
            if latch["clears_by_writer"]:
                lines.append("| clear written by | count |")
                lines.append("|---|---|")
                for k, v in latch["clears_by_writer"].items():
                    lines.append(f"| {k} | {v} |")
                lines.append("")
            lines.append(f"- Written by the belief publisher: {latch['written_by_the_publisher']}")
            lines.append(f"- While the estimator was live: {latch['while_estimator_live']}")
            lines.append(f"- Forbidden while live (not the mirror, a person or the hard "
                         f"backstop): **{latch['forbidden_while_live']}** "
                         "(the acceptance bar is zero)")
            lines.append(f"- Unattributed (no writer value within the window): "
                         f"{latch['unattributed']}")
            if latch.get("availability_gaps"):
                lines.append(f"- Restarts the latch went unavailable across: "
                             f"{latch['availability_gaps']}. A value that differs "
                             "across one of those is the restore, not a write, and is "
                             "not counted as a transition.")
            lines.append("")
    lines.append("## Derived indicators")
    lines.append("")
    lines.append(f"- Vacant-night floor (20 %) turn-ons while the asleep flag was off: {summary['vacant_floor_while_not_asleep']}")
    lines.append(f"- Turn-ons whose only change was colour temperature (CT actuator suspect): {summary['color_temp_only_turn_ons']}")
    lines.append(f"- Followed by a user-attributed off within 60 s: {summary['followed_by_user_off_within_60s']}")
    lines.append(f"- Followed by any off within 60 s (includes writer oscillation): {summary['followed_by_any_off_within_60s']}")
    lines.append(f"- Events with observer memory rows within 2 min for corroboration: {summary['memory_corroboration_available']}")
    lines.append("")
    lines.append("Writer classes: `automation` = HA context has a parent_id; `user` = HA context has a user_id; `explicit_command` = a command id written with a user behind it; `explicit_command_automation` = a command id with no user, which is an automation using the override channel and stays in the defect count; `automation_by_hint` = no context but the ledger's source hint says automation; `unattributed` = bridge, physical switch or unknown.")
    return "\n".join(lines) + "\n"


def self_test():
    """Build a tiny ledger and check dedupe, night filtering, writer classes and signatures."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.sqlite"
        conn = sqlite3.connect(path)
        conn.executescript("""
        CREATE TABLE lighting_change_events (id TEXT PRIMARY KEY, raw_event_id TEXT, event_id TEXT, ts TEXT, entity_id TEXT,
          light_entity TEXT, zone TEXT, from_state TEXT, to_state TEXT, from_brightness_pct REAL, to_brightness_pct REAL,
          from_color_temp_kelvin REAL, to_color_temp_kelvin REAL, command_id TEXT, source_hint TEXT, source_confidence TEXT,
          trigger_attribute TEXT, ha_context_json TEXT NOT NULL DEFAULT '{}', context_json TEXT NOT NULL DEFAULT '{}', payload_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE override_events (id TEXT PRIMARY KEY, raw_event_id TEXT, ts TEXT, zone TEXT, light_entity TEXT, light_state TEXT,
          actual_brightness_pct REAL, predicted_brightness_pct REAL, delta_pct REAL, profile TEXT, state TEXT, tv_playing INTEGER,
          asleep INTEGER, night_safe INTEGER, user_at_home INTEGER, shadow_mode INTEGER DEFAULT 0, predictions_by_zone_json TEXT, context_json TEXT, gaming_active INTEGER);
        CREATE TABLE lighting_decisions (id TEXT PRIMARY KEY, raw_event_id TEXT, ts TEXT, entity_id TEXT, zone TEXT, from_state TEXT,
          to_state TEXT, predicted_brightness_pct REAL, predicted_color_temp_kelvin REAL, shadow_mode INTEGER DEFAULT 0, payload_json TEXT NOT NULL);
        """)
        base = "2026-09-10T02:15:00.%06d-07:00"
        for i, attr in enumerate(("None", "brightness", "color_temp_kelvin", "color_mode")):
            conn.execute("INSERT INTO lighting_change_events (id, raw_event_id, event_id, ts, entity_id, light_entity, zone, from_state, to_state, to_brightness_pct, source_hint, trigger_attribute, ha_context_json, context_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"e{i}", "r", f"ev{i}", base % (1000 * i), "light.sink", "light.sink", "sink", "off", "on", 20.0 if attr == "brightness" else None, "automation_or_script", attr, '{"parent_id":"auto1","user_id":null}', '{"asleep":false,"any_occupied":false}'))
        conn.execute("INSERT INTO lighting_change_events (id, raw_event_id, event_id, ts, entity_id, light_entity, zone, from_state, to_state, to_brightness_pct, source_hint, ha_context_json, context_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("e_off", "r", "ev_off", "2026-09-10T02:15:30.000000-07:00", "light.sink", "light.sink", "sink", "on", "off", 0.0, "home_app_or_ha_user", '{"parent_id":null,"user_id":"u1"}', '{}'))
        conn.execute("INSERT INTO lighting_change_events (id, raw_event_id, event_id, ts, entity_id, light_entity, zone, from_state, to_state, to_brightness_pct, source_hint, ha_context_json, context_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("e_day", "r", "ev_day", "2026-09-10T12:00:00.000000-07:00", "light.sink", "light.sink", "sink", "off", "on", 80.0, "automation_or_script", '{"parent_id":"auto1"}', '{}'))
        conn.execute("INSERT INTO lighting_decisions (id, raw_event_id, ts, entity_id, zone, from_state, to_state, predicted_brightness_pct, payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("d1", "r", "2026-09-10T02:10:00.000000-07:00", "sensor.kitchen_sink_lighting_state", "sink", "present", "vacant", 20.0, "{}"))
        conn.commit(); conn.close()
        ro = open_ro(path)
        result = analyze(ro, None, "2026-09-01", "2026-10-01", 0, 8 * 60)
        assert result["raw_rows"] == 6, result["raw_rows"]
        assert result["physical_changes"] == 3, result["physical_changes"]
        assert len(result["events"]) == 1, result["events"]
        event = result["events"][0]
        assert event["writer"] == "automation" and event["signature"] == "20:vacant_night_floor", event
        assert event["classifier_decision"]["to_state"] == "vacant", event
        assert event["next_off"] == {"after_s": 30.0, "writer": "user"}, event
        assert event["night"] == "2026-09-10", event
        summary = summarize(result, 8 * 60)
        assert summary["vacant_floor_while_not_asleep"] == 1 and summary["followed_by_user_off_within_60s"] == 1, summary
        assert signature(82) == "80:present_ramp_target" and signature(None) == "unknown" and signature(42) == "42:other"
        assert writer_class('{"user_id":"u"}', None, None) == "user" and writer_class("{}", "automation_or_script", None) == "automation_by_hint"
    print("self-test ok")


def read_latch_history(ha_url, env_file, start, end):
    """Latch, writer and toggle history from the recorder.

    The HTTP client, the token read and the scrubber come from
    ``tools/tv-evening-postmortem.py`` rather than being written twice: it is
    GET-only, refuses redirects, registers no proxy, and never prints the
    token. Returns ``(rows_by_entity, error)``; a failure is reported, never
    raised, because the ledger half of this report stands on its own.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tv_postmortem", str(Path(__file__).resolve().parent / "tv-evening-postmortem.py"))
    if spec is None or spec.loader is None:
        return {}, "cannot import tv-evening-postmortem.py"
    pm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pm)
    token = pm.read_token(env_file)
    if not token:
        return {}, f"no HA_TOKEN in {env_file}"
    entities = [LATCH_ENTITY, LATCH_WRITER_ENTITY, FROM_ESTIMATOR_ENTITY]
    try:
        getter = pm.http_getter(ha_url, token)
        payload = getter(pm.history_path(start, end, entities, minimal=True))
    except Exception as exc:  # noqa: BLE001 - the ledger half must still report
        return {}, pm.scrub(f"recorder query failed: {type(exc).__name__}", (token,))
    hist = pm.parse_history(payload)
    return {entity: hist.get(entity, []) for entity in entities}, None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--memory-db", default=DEFAULT_MEMORY_SNAPSHOT, help="read-only memory snapshot for presence corroboration; '' to skip")
    parser.add_argument("--since", default="2026-09-02", help="inclusive local date or ISO timestamp")
    parser.add_argument("--until", default=None, help="exclusive; default now")
    parser.add_argument("--night-start", default="00:00")
    parser.add_argument("--night-end", default="08:00")
    parser.add_argument("--out", default=None, help="report directory; default operations/night-postmortem-<date>/")
    parser.add_argument("--ha-url", default=DEFAULT_HA_URL,
                        help="Home Assistant base URL, for the latch-writer attribution")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="file holding HA_TOKEN")
    parser.add_argument("--no-latch", action="store_true",
                        help="skip the latch-writer attribution (no recorder query)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    night_start = int(args.night_start[:2]) * 60 + int(args.night_start[3:5])
    night_end = int(args.night_end[:2]) * 60 + int(args.night_end[3:5])
    until = args.until or dt.datetime.now().astimezone().isoformat()
    out = Path(args.out) if args.out else DEFAULT_OUT_ROOT / f"night-postmortem-{dt.date.today().isoformat()}"
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    conn = open_ro(args.db)
    memory_conn = open_ro(args.memory_db) if args.memory_db and Path(args.memory_db).exists() else None
    result = analyze(conn, memory_conn, args.since, until, night_start, night_end)
    summary = summarize(result, night_end)
    if args.no_latch:
        summary["latch"] = {"available": False, "reason": "skipped with --no-latch"}
    else:
        start_at = parse_ts(args.since) or dt.datetime.fromisoformat(args.since + "T00:00:00").astimezone()
        end_at = parse_ts(until) or dt.datetime.now().astimezone()
        rows, error = read_latch_history(args.ha_url, args.env_file, start_at, end_at)
        if error:
            summary["latch"] = {"available": False, "reason": error}
        else:
            transitions = latch_transitions(rows.get(LATCH_ENTITY, []),
                                            rows.get(LATCH_WRITER_ENTITY, []),
                                            rows.get(FROM_ESTIMATOR_ENTITY, []))
            gaps = sum(1 for row in rows.get(LATCH_ENTITY, [])
                       if (row.get("state") or "").lower() in ("unknown", "unavailable"))
            summary["latch"] = {"available": True, "reason": None,
                                "writer_window_s": WRITER_WINDOW_S,
                                **latch_summary(transitions, gaps)}
            (out / "latch-transitions.jsonl").write_text(
                "".join(json.dumps(t) + "\n" for t in transitions))
    summary["inputs"] = {"db": args.db, "memory_db": args.memory_db if memory_conn else None, "since": args.since, "until": until,
                         "night_window": [args.night_start, args.night_end], "raw_rows_in_range": result["raw_rows"], "physical_changes_in_range": result["physical_changes"]}
    (out / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in result["events"]))
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "summary.md").write_text(render_markdown(summary, args.since, until[:19], night_start, night_end))
    for name in ("events.jsonl", "summary.json", "summary.md"):
        os.chmod(out / name, 0o600)
    print(json.dumps({"out": str(out), "events": summary["total_night_on_events"], "nights": summary["nights_with_events"],
                      "by_writer": summary["by_writer"], "by_signature": dict(list(summary["by_signature"].items())[:6])}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
