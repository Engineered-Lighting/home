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

Person evidence is mandatory. Scoring a latch against nothing proves nothing,
so a run that read no person row -- no ``--frigate-jsonl`` at all, or files
that carried none -- is a usage error unless ``--no-evidence`` is passed to
acknowledge it. Such a run scores every latch ``inconclusive``, never
``explained``, and says so in both the rendered and the JSON form.

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

VERDICT_EXPLAINED = "explained"
VERDICT_UNEXPLAINED = "unexplained"
VERDICT_INCONCLUSIVE = "inconclusive"
"""A latch is only *explained* when evidence was scored against it. With no
person evidence at all the honest verdict is neither -- the run measured
nothing -- so it is ``inconclusive`` and the reader is told how many."""

MALFORMED_SHOWN = 10
"""How many ``file:line`` locations of unreadable lines the receipt names."""


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


def read_jsonl(path: pathlib.Path) -> tuple[list[dict], list[str]]:
    """Read a JSONL file; return its objects and one note per unreadable line.

    A malformed line is never fatal -- a half-written last line is ordinary in
    a journal a live process is appending to -- but it is never silent either:
    the caller counts them into the receipt, so a reader can tell the scored
    records from all the records.
    """
    rows: list[dict] = []
    malformed: list[str] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                malformed.append(f"{path.name}:{number}")
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                malformed.append(f"{path.name}:{number}")
    return rows, malformed


def journal_files_present(directory: pathlib.Path) -> int:
    """How many journal files the directory holds, whatever the range.

    A range with no record is two different things. Asking about a day the
    publisher was not running is an ordinary empty answer. A directory holding
    no journal file at all means the publisher never wrote one, and since the
    journal never raises on I/O failure, that is the only way a directory the
    container cannot write ever announces itself.
    """
    if not directory.is_dir():
        return 0
    return len(list(directory.glob(JOURNAL_GLOB)))


def read_journals(directory: pathlib.Path, since: dt.date | None,
                  until: dt.date | None) -> tuple[list[dict], list[str], list[str]]:
    """Every journal record in the date range, oldest first, plus bad lines."""
    if not directory.is_dir():
        raise ReportError(f"journal directory not found: {directory}")
    files = sorted(directory.glob(JOURNAL_GLOB))
    used: list[str] = []
    rows: list[dict] = []
    malformed: list[str] = []
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
        read, bad = read_jsonl(path)
        rows.extend(read)
        malformed.extend(bad)
    rows.sort(key=lambda row: row.get("t") or "")
    return rows, used, malformed


def read_person_evidence(path: pathlib.Path,
                         assume_person: bool) -> tuple[list[dict], list[str]]:
    """Frigate rows that are evidence of a person, with a usable time."""
    out: list[dict] = []
    rows, malformed = read_jsonl(path)
    for row in rows:
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
    return out, malformed


def read_recorder(path: pathlib.Path, entity_id: str) -> tuple[list[dict], list[str]]:
    """Recorder rows for one entity, oldest first."""
    out: list[dict] = []
    rows, malformed = read_jsonl(path)
    for row in rows:
        if row.get("entity_id") != entity_id:
            continue
        when = row_time(row)
        state = row.get("state")
        if when is None or not isinstance(state, str):
            continue
        out.append({"at": when, "state": state})
    out.sort(key=lambda item: item["at"])
    return out, malformed


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


def verdict_for(scored: bool, seen: list[dict]) -> str:
    """Explained, unexplained -- or inconclusive when nothing was scored."""
    if not scored:
        return VERDICT_INCONCLUSIVE
    return VERDICT_UNEXPLAINED if seen else VERDICT_EXPLAINED


def build_report(records: list[dict], people: list[dict], recorder: list[dict],
                 window_min: int, files: list[str], evidence_sources: int,
                 malformed: list[str]) -> dict:
    """The receipt: every latch, its evidence, and the run's counters."""
    window_s = window_min * 60
    # Scored means person evidence actually arrived. A file that parsed to no
    # person row proves exactly as much as no file at all, so the count of
    # files is not the test: the rows are.
    scored = bool(people)
    rows = []
    for latch in latches(records):
        seen = people_in_window(people, latch["at"], window_s)
        verdict = verdict_for(scored, seen)
        rows.append({
            "at": latch["at"].isoformat(timespec="seconds"),
            "from": latch["from"],
            "quiet_s": latch["quiet_s"],
            "mode": latch["mode"],
            "frigate_people_in_window": len(seen),
            "cameras": sorted({item["camera"] for item in seen}),
            "last_person_at": (seen[-1]["at"].isoformat(timespec="seconds") if seen else None),
            "legacy_latch": state_at(recorder, latch["at"]) if recorder else None,
            "verdict": verdict,
            "explained": verdict == VERDICT_EXPLAINED,
        })
    gated = sum(1 for record in records if record.get("event") == "gated")
    decisions = sum(1 for record in records if record.get("event") == "decision")
    tv_changes = sum(1 for record in records
                     if isinstance(record.get("tv"), dict) and record["tv"].get("changed"))
    unexplained = [row for row in rows if row["verdict"] == VERDICT_UNEXPLAINED]
    inconclusive = [row for row in rows if row["verdict"] == VERDICT_INCONCLUSIVE]
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
        "evidence_sources": evidence_sources,
        "inconclusive": not scored,
        "frigate_person_rows": len(people),
        "unexplained_latches": len(unexplained),
        "inconclusive_latches": len(inconclusive),
        "malformed_lines": len(malformed),
        "malformed_where": malformed[:MALFORMED_SHOWN],
    }


def render(report: dict) -> str:
    """The human receipt."""
    lines = ["Living Lights shadow report", "=" * 27, ""]
    lines.append(f"journal files : {len(report['journal_files'])}")
    lines.append(f"records       : {report['records']} "
                 f"({report['decisions']} decisions, {report['gated_ticks']} gated)")
    lines.append(f"evidence files: {report['evidence_sources']}")
    lines.append(f"frigate rows  : {report['frigate_person_rows']} person observations")
    lines.append(f"tv transitions: {report['tv_transitions']}")
    lines.append(f"latches       : {len(report['latches'])}")
    lines.append(f"exits         : {len(report['exits'])}")
    lines.append(f"window        : {report['window_min']} min before each latch")
    if report["malformed_lines"]:
        where = ", ".join(report["malformed_where"])
        more = "" if report["malformed_lines"] <= len(report["malformed_where"]) else ", ..."
        lines.append(f"malformed     : {report['malformed_lines']} unreadable lines "
                     f"({where}{more})")
    if report["inconclusive"]:
        lines.append("evidence      : NOTHING SCORED -- no person row was read "
                     "(--no-evidence)")
    lines.append("")
    if not report["latches"]:
        lines.append("No likely_asleep latch in this range.")
    for row in report["latches"]:
        verdict = (VERDICT_EXPLAINED if row["verdict"] == VERDICT_EXPLAINED
                   else row["verdict"].upper())
        lines.append(f"- {row['at']}  {verdict}")
        lines.append(f"    quiet_s={row['quiet_s']}  from={row['from']}  mode={row['mode']}")
        if row["verdict"] == VERDICT_INCONCLUSIVE:
            lines.append("    frigate: no person evidence was read, so nothing was checked")
        elif row["frigate_people_in_window"]:
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
    lines.append(f"inconclusive latches: {report['inconclusive_latches']}")
    if report["records"] == 0:
        lines.append("No journal record in this range, so nothing here was measured.")
    if report["inconclusive"]:
        lines.append("This run proves nothing: no person row was read, so no latch was "
                     "scored.")
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
    parser.add_argument("--no-journal", action="store_true",
                        help="acknowledge that the journal is empty for this range; "
                             "the report then states plainly that it measured nothing")
    parser.add_argument("--no-evidence", action="store_true",
                        help="acknowledge a run with no person evidence: every latch is "
                             "scored inconclusive and the run proves nothing")
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
    malformed: list[str] = []
    try:
        since = as_date(args.since, "since")
        until = as_date(args.until, "until")
        if args.window_min <= 0:
            raise ReportError("--window-min must be positive")
        if not args.frigate_jsonl and not args.no_evidence:
            raise ReportError("no person evidence supplied; scoring a latch against "
                              "nothing proves nothing (pass --frigate-jsonl, or "
                              "--no-evidence to acknowledge an unscored run)")
        records, files, bad = read_journals(pathlib.Path(args.journal_dir), since, until)
        malformed.extend(bad)
        # A journal that wrote nothing reads exactly like a week with nothing
        # to report, and the old exit code said so: zero unexplained latches,
        # exit 0, "the acceptance bar is zero over seven nights" -- from a
        # directory the publisher had never been able to write. The journal
        # never raises on I/O failure by design, so this is the only place the
        # failure can surface. It is refused the same way empty person
        # evidence already is.
        if not journal_files_present(pathlib.Path(args.journal_dir)) and not args.no_journal:
            raise ReportError(
                f"{args.journal_dir} holds no journal file at all; the publisher "
                "writes one record a tick and never raises when it cannot, so this "
                "usually means the directory is not writable by the container's uid "
                "-- a week that wrote nothing is not a week that went well (pass "
                "--no-journal to acknowledge an empty run)")
        people: list[dict] = []
        for name in args.frigate_jsonl:
            path = pathlib.Path(name)
            if not path.is_file():
                raise ReportError(f"frigate evidence not found: {path}")
            found, bad = read_person_evidence(path, args.assume_person)
            people.extend(found)
            malformed.extend(bad)
        people.sort(key=lambda item: item["at"])
        if not people and not args.no_evidence:
            # A wrong file, a filtered-out label or an empty export reads
            # exactly like a forgotten argument, and must fail the same way.
            raise ReportError(
                f"person evidence is empty: {len(args.frigate_jsonl)} file(s) read and "
                "no person row in any of them; scoring a latch against nothing proves "
                "nothing (pass --assume-person for an unlabelled export, or "
                "--no-evidence to acknowledge an unscored run)")
        recorder: list[dict] = []
        if args.recorder_jsonl:
            path = pathlib.Path(args.recorder_jsonl)
            if not path.is_file():
                raise ReportError(f"recorder export not found: {path}")
            recorder, bad = read_recorder(path, args.recorder_entity)
            malformed.extend(bad)
    except ReportError as exc:
        print(f"shadow-report: {exc}", file=sys.stderr)
        return EXIT_USAGE
    report = build_report(records, people, recorder, args.window_min, files,
                          len(args.frigate_jsonl), malformed)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return EXIT_UNEXPLAINED if report["unexplained_latches"] else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
