#!/usr/bin/env python3
"""Score the belief publisher's shadow nights against evidence it did not use.

The publisher journals every decision it makes. This tool reads those journals
and asks one question of every ``likely_asleep`` latch:

    was there a Frigate person in the fifteen minutes before it?

The answer comes from raw Frigate rows and, optionally, from a Home Assistant
recorder export -- never from the journal's own ``credible_cameras`` evidence,
because scoring a rule with the rule's own output proves nothing. A latch with
a person in the window is *unexplained*: the publisher decided the house was
asleep while somebody was still on camera, and the plan's acceptance bar is
zero of those over seven nights. A latch with no person in the window is
explained, and so is the whole quiet night around it.

Reads only local files, makes no network call and writes nothing outside the
report it prints.

Usage:

    python3 tools/shadow-report.py \\
        --journal-dir /opt/home-ai-voice/data/lighting-publisher \\
        --frigate-jsonl /share/predictive-lighting/transitions.jsonl \\
        --since 2026-09-18 --until 2026-09-25

    python3 tools/shadow-report.py --journal-dir ... --json     # machine form

Row shapes accepted (both files are read leniently, one JSON object per line):

- Frigate evidence: any row carrying a time (``t``, ``ts``, ``time``,
  ``timestamp``, ``start_time``, ``last_changed``) and, where present, a
  ``camera`` and a ``label``/``object``. Rows whose label is set and is not
  ``person`` are skipped; rows with no label are treated as person evidence
  only when the file is a Frigate person export (``--assume-person``).
- Recorder evidence: rows with ``entity_id``, ``state`` and a time. Used to
  report what the legacy ``input_boolean.living_lights_asleep`` was doing at
  each shadow latch, so the two can be compared without trusting either.

Exit codes: 0 clean, 2 usage, 3 at least one unexplained latch.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNEXPLAINED = 3

DEFAULT_WINDOW_MIN = 15
JOURNAL_GLOB = "decisions-*.jsonl"
TIME_KEYS = ("t", "ts", "time", "timestamp", "start_time", "last_changed", "when")
LABEL_KEYS = ("label", "object", "object_type")
CAMERA_KEYS = ("camera", "camera_name", "source")
LATCH_STATE = "likely_asleep"
LEGACY_LATCH_ENTITY = "input_boolean.living_lights_asleep"


class ReportError(RuntimeError):
    """A usage or input problem whose message is safe to print."""


# -- reading ------------------------------------------------------------------

def parse_time(value: object) -> dt.datetime | None:
    """Accept ISO 8601 (with a zone) or an epoch number; else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value), dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            return dt.datetime.fromtimestamp(float(text), dt.timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def row_time(row: dict) -> dt.datetime | None:
    for key in TIME_KEYS:
        if key in row:
            when = parse_time(row[key])
            if when is not None:
                return when
    return None


def read_jsonl(path: pathlib.Path) -> list[dict]:
    """Read a JSONL file, skipping blank and unparsable lines."""
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def read_journals(directory: pathlib.Path, since: dt.date | None,
                  until: dt.date | None) -> tuple[list[dict], list[str]]:
    """Every journal record in the date range, oldest first."""
    if not directory.is_dir():
        raise ReportError(f"journal directory not found: {directory}")
    files = sorted(directory.glob(JOURNAL_GLOB))
    used: list[str] = []
    rows: list[dict] = []
    for path in files:
        stamp = path.stem.split("-", 1)[-1]
        try:
            day = dt.date.fromisoformat(stamp)
        except ValueError:
            continue
        if since is not None and day < since:
            continue
        if until is not None and day > until:
            continue
        used.append(path.name)
        rows.extend(read_jsonl(path))
    rows.sort(key=lambda row: row.get("t") or "")
    return rows, used


def read_person_evidence(path: pathlib.Path, assume_person: bool) -> list[dict]:
    """Frigate rows that are evidence of a person, with a usable time."""
    out: list[dict] = []
    for row in read_jsonl(path):
        when = row_time(row)
        if when is None:
            continue
        label = None
        for key in LABEL_KEYS:
            value = row.get(key)
            if isinstance(value, str) and value:
                label = value.lower()
                break
        if label is not None and label != "person":
            continue
        if label is None and not assume_person:
            continue
        camera = None
        for key in CAMERA_KEYS:
            value = row.get(key)
            if isinstance(value, str) and value:
                camera = value
                break
        out.append({"at": when, "camera": camera or "unknown"})
    out.sort(key=lambda item: item["at"])
    return out


def read_recorder(path: pathlib.Path, entity_id: str) -> list[dict]:
    """Recorder rows for one entity, oldest first."""
    out: list[dict] = []
    for row in read_jsonl(path):
        if row.get("entity_id") != entity_id:
            continue
        when = row_time(row)
        state = row.get("state")
        if when is None or not isinstance(state, str):
            continue
        out.append({"at": when, "state": state})
    out.sort(key=lambda item: item["at"])
    return out


# -- scoring ------------------------------------------------------------------

def latches(records: list[dict]) -> list[dict]:
    """Every transition into ``likely_asleep`` the journal recorded."""
    found: list[dict] = []
    previous: str | None = None
    for record in records:
        asleep = record.get("asleep")
        if not isinstance(asleep, dict):
            continue
        state = asleep.get("state")
        if not isinstance(state, str):
            continue
        if state == LATCH_STATE and previous != LATCH_STATE:
            when = parse_time(record.get("t"))
            if when is not None:
                evidence = asleep.get("evidence") or {}
                found.append({
                    "at": when, "from": previous,
                    "quiet_s": evidence.get("quiet_s"),
                    "window": evidence.get("window"),
                    "tv_rule": evidence.get("tv_rule"),
                    "mode": record.get("mode"),
                })
        previous = state
    return found


def exits(records: list[dict]) -> list[dict]:
    """Every transition out of ``likely_asleep``, with the reason journaled."""
    found: list[dict] = []
    previous: str | None = None
    for record in records:
        asleep = record.get("asleep")
        if not isinstance(asleep, dict):
            continue
        state = asleep.get("state")
        if not isinstance(state, str):
            continue
        if previous == LATCH_STATE and state != LATCH_STATE:
            when = parse_time(record.get("t"))
            if when is not None:
                evidence = asleep.get("evidence") or {}
                found.append({"at": when, "to": state, "reason": evidence.get("reason")})
        previous = state
    return found


def people_in_window(people: list[dict], end: dt.datetime,
                     window_s: float) -> list[dict]:
    """Person evidence in ``[end - window, end]``."""
    start = end - dt.timedelta(seconds=window_s)
    return [item for item in people if start <= item["at"] <= end]


def state_at(rows: list[dict], when: dt.datetime) -> str | None:
    """The last recorder state at or before ``when``."""
    found = None
    for row in rows:
        if row["at"] <= when:
            found = row["state"]
        else:
            break
    return found


def build_report(records: list[dict], people: list[dict], recorder: list[dict],
                 window_min: int, files: list[str]) -> dict:
    """The receipt: every latch, its evidence, and the run's counters."""
    window_s = window_min * 60
    rows = []
    for latch in latches(records):
        seen = people_in_window(people, latch["at"], window_s)
        rows.append({
            "at": latch["at"].isoformat(timespec="seconds"),
            "from": latch["from"],
            "quiet_s": latch["quiet_s"],
            "mode": latch["mode"],
            "frigate_people_in_window": len(seen),
            "cameras": sorted({item["camera"] for item in seen}),
            "last_person_at": (seen[-1]["at"].isoformat(timespec="seconds") if seen else None),
            "legacy_latch": state_at(recorder, latch["at"]) if recorder else None,
            "explained": not seen,
        })
    gated = sum(1 for record in records if record.get("event") == "gated")
    decisions = sum(1 for record in records if record.get("event") == "decision")
    tv_changes = sum(1 for record in records
                     if isinstance(record.get("tv"), dict) and record["tv"].get("changed"))
    unexplained = [row for row in rows if not row["explained"]]
    return {
        "schema": "lighting-shadow-report/v1",
        "window_min": window_min,
        "journal_files": files,
        "records": len(records),
        "decisions": decisions,
        "gated_ticks": gated,
        "tv_transitions": tv_changes,
        "latches": rows,
        "exits": [{"at": item["at"].isoformat(timespec="seconds"), "to": item["to"],
                   "reason": item["reason"]} for item in exits(records)],
        "frigate_person_rows": len(people),
        "unexplained_latches": len(unexplained),
    }


def render(report: dict) -> str:
    """The human receipt."""
    lines = ["Living Lights shadow report", "=" * 27, ""]
    lines.append(f"journal files : {len(report['journal_files'])}")
    lines.append(f"records       : {report['records']} "
                 f"({report['decisions']} decisions, {report['gated_ticks']} gated)")
    lines.append(f"frigate rows  : {report['frigate_person_rows']} person observations")
    lines.append(f"tv transitions: {report['tv_transitions']}")
    lines.append(f"latches       : {len(report['latches'])}")
    lines.append(f"exits         : {len(report['exits'])}")
    lines.append(f"window        : {report['window_min']} min before each latch")
    lines.append("")
    if not report["latches"]:
        lines.append("No likely_asleep latch in this range.")
    for row in report["latches"]:
        verdict = "explained" if row["explained"] else "UNEXPLAINED"
        lines.append(f"- {row['at']}  {verdict}")
        lines.append(f"    quiet_s={row['quiet_s']}  from={row['from']}  mode={row['mode']}")
        if row["frigate_people_in_window"]:
            lines.append(f"    frigate: {row['frigate_people_in_window']} person rows "
                         f"on {', '.join(row['cameras'])}, last at {row['last_person_at']}")
        else:
            lines.append("    frigate: no person in the window")
        if row["legacy_latch"] is not None:
            lines.append(f"    legacy input_boolean.living_lights_asleep was "
                         f"{row['legacy_latch']}")
    lines.append("")
    if report["exits"]:
        lines.append("Exits:")
        for row in report["exits"]:
            lines.append(f"- {row['at']} -> {row['to']} ({row['reason']})")
        lines.append("")
    lines.append(f"unexplained latches: {report['unexplained_latches']} "
                 f"(the acceptance bar is zero over seven nights)")
    return "\n".join(lines)


# -- entry point ---------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--journal-dir", required=True,
                        help="the publisher's journal directory (decisions-*.jsonl)")
    parser.add_argument("--frigate-jsonl", action="append", default=[],
                        help="raw Frigate person evidence; may be repeated")
    parser.add_argument("--recorder-jsonl",
                        help="Home Assistant recorder export (entity_id, state, last_changed)")
    parser.add_argument("--recorder-entity", default=LEGACY_LATCH_ENTITY,
                        help=f"recorder entity to compare against (default {LEGACY_LATCH_ENTITY})")
    parser.add_argument("--assume-person", action="store_true",
                        help="treat unlabelled Frigate rows as person evidence")
    parser.add_argument("--since", help="first journal day, YYYY-MM-DD")
    parser.add_argument("--until", help="last journal day, YYYY-MM-DD")
    parser.add_argument("--window-min", type=int, default=DEFAULT_WINDOW_MIN,
                        help=f"minutes of Frigate evidence before a latch (default {DEFAULT_WINDOW_MIN})")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser.parse_args(argv)


def as_date(value: str | None, label: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ReportError(f"--{label} must be YYYY-MM-DD") from None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        since = as_date(args.since, "since")
        until = as_date(args.until, "until")
        if args.window_min <= 0:
            raise ReportError("--window-min must be positive")
        records, files = read_journals(pathlib.Path(args.journal_dir), since, until)
        people: list[dict] = []
        for name in args.frigate_jsonl:
            path = pathlib.Path(name)
            if not path.is_file():
                raise ReportError(f"frigate evidence not found: {path}")
            people.extend(read_person_evidence(path, args.assume_person))
        people.sort(key=lambda item: item["at"])
        recorder: list[dict] = []
        if args.recorder_jsonl:
            path = pathlib.Path(args.recorder_jsonl)
            if not path.is_file():
                raise ReportError(f"recorder export not found: {path}")
            recorder = read_recorder(path, args.recorder_entity)
    except ReportError as exc:
        print(f"shadow-report: {exc}", file=sys.stderr)
        return EXIT_USAGE
    report = build_report(records, people, recorder, args.window_min, files)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return EXIT_UNEXPLAINED if report["unexplained_latches"] else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
