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

DEFAULT_DB = "/opt/home-ai-voice/intelligence-data/intelligence.sqlite"
DEFAULT_MEMORY_SNAPSHOT = "/srv/data/vjepa-home-snapshots/2026-09-17/memory.sqlite3"
DEFAULT_OUT_ROOT = Path("/home/marcelo-lima/vjepa-home/operations")

# Mirrors ZONE_CAMERA in tools/build-living-lights-actuators.py.
ZONE_CAMERA = {
    "sofa": "living_room", "office": "living_room", "front_left": "living_room",
    "front_door": "living_room", "weights": "living_room",
    "sink": "kitchen", "island_left": "kitchen", "island_right": "kitchen",
    "dining_left": "dining_room", "dining_right": "dining_room",
}

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
    try:
        context = json.loads(ha_context_json or "{}")
    except ValueError:
        context = {}
    if command_id:
        return "explicit_command"
    if context.get("user_id"):
        return "user"
    if context.get("parent_id"):
        return "automation"
    if source_hint == "automation_or_script":
        return "automation_by_hint"
    return "unattributed"


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
    lines.append("## Derived indicators")
    lines.append("")
    lines.append(f"- Vacant-night floor (20 %) turn-ons while the asleep flag was off: {summary['vacant_floor_while_not_asleep']}")
    lines.append(f"- Turn-ons whose only change was colour temperature (CT actuator suspect): {summary['color_temp_only_turn_ons']}")
    lines.append(f"- Followed by a user-attributed off within 60 s: {summary['followed_by_user_off_within_60s']}")
    lines.append(f"- Followed by any off within 60 s (includes writer oscillation): {summary['followed_by_any_off_within_60s']}")
    lines.append(f"- Events with observer memory rows within 2 min for corroboration: {summary['memory_corroboration_available']}")
    lines.append("")
    lines.append("Writer classes: `automation` = HA context has a parent_id; `user` = HA context has a user_id; `explicit_command` = a command id; `automation_by_hint` = no context but the ledger's source hint says automation; `unattributed` = bridge, physical switch or unknown.")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--memory-db", default=DEFAULT_MEMORY_SNAPSHOT, help="read-only memory snapshot for presence corroboration; '' to skip")
    parser.add_argument("--since", default="2026-09-02", help="inclusive local date or ISO timestamp")
    parser.add_argument("--until", default=None, help="exclusive; default now")
    parser.add_argument("--night-start", default="00:00")
    parser.add_argument("--night-end", default="08:00")
    parser.add_argument("--out", default=None, help="report directory; default operations/night-postmortem-<date>/")
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
