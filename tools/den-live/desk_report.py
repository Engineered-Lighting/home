#!/usr/bin/env python3
"""The desk stage's check on LA (plan 05, the D-1 runbook): one day's fixture trace, in words and counts only.

    sudo /usr/bin/python3.12 -B /opt/den-live/current/tools/den-live/desk_report.py [--day YYYY-MM-DD]

It reads <state>/days/<UTC day>/coordinator-fixture_calls.jsonl (--state, default /var/lib/den-live) and says, per run,
whether it was virtual or the desk (the fixture, and lit or dark), what was sent and how the board answered, and then
a PASS or FAIL line per check:
- one fixture: every send names its run's desk fixture, and a virtual run sends nothing;
- the dark rule: in a light-false run, every MOVE on the air has spot level 0 and no lit travel, and no keep-lit goes;
- the bytes: every sent frame matches its encoded row (the sender's own matches_trace);
- no stale seq: no MOVE answered stale_seq, and per board every wire seq is new to it, across runs in time order;
- Stop: every Stop den-live sent ended with the board latched, and every Resume with it clear.
It prints no device reference, joint angle, coordinate or token. The full replay (the pinned firmware codec and the
simulator) runs on the PC once the trace is copied off LA with the owner's OK (step D-3).
"""
import argparse
import collections
import datetime
import json
import struct
import sys
from pathlib import Path

STATE = Path("/var/lib/den-live")
FILE = "coordinator-fixture_calls.jsonl"
SEQ_WINDOW = 1024
MOVE, KEEP_LIT, STOP, RESUME = 0, 1, 2, 3
COMMANDS = {"aim_move": MOVE, "keep_lit": KEEP_LIT, "stop": STOP, "resume": RESUME}


def classify_seq(last, seq):
    if last is None:
        return "new"
    if seq == last:
        return "duplicate"
    return "new" if 1 <= (seq - last) & 0xFFFF <= SEQ_WINDOW else "stale"


def rows_of(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            row = {"kind": "unreadable"}
        rows.append(row if isinstance(row, dict) else {"kind": "unreadable"})
    return rows


def report(rows):
    """(lines, every check passed) for one day's rows."""
    lines, checks = [], []
    headers = {}
    runs = collections.OrderedDict()
    frames = collections.defaultdict(list)   # device -> [(wall, line, trace_id, cmd, seq)]
    unreadable = 0
    for index, row in enumerate(rows, 1):
        kind = row.get("kind")
        if kind == "unreadable":
            unreadable += 1
            continue
        run = row.get("run_id")
        if kind == "run_header":
            headers.setdefault(run, row)
            runs.setdefault(run, collections.Counter())
            continue
        if kind == "device":
            runs.setdefault(run, collections.Counter())["device rows"] += 1
            runs[run]["latched read"] = row.get("values", {}).get("stop_latched")
            continue
        if kind != "send":
            if row.get("status") == "encoded_only":
                runs.setdefault(run, collections.Counter())["encoded"] += 1
            continue
        tally = runs.setdefault(run, collections.Counter())
        header = headers.get(run) or {}
        desk = header.get("desk")
        action, outcome = row.get("action"), row.get("outcome")
        tally["%s %s" % (action, outcome)] += 1
        answer = row.get("answer") or {}
        if action == "aim_move" and answer.get("result"):
            tally["move answered %s" % answer["result"]] += 1
        if row.get("sent") is None:
            tally["calls with no answer"] += 1
        if desk is None:
            checks.append((False, "one fixture: a virtual run sent a command"))
        elif row.get("fixture_id") != desk.get("fixture_id") or row.get("device") != desk.get("device"):
            checks.append((False, "one fixture: a send names another fixture or device than its run's desk"))
        wire = row.get("wire")
        if not row.get("sent") or not isinstance(wire, dict):
            if desk and desk.get("light") is False and action == "keep_lit" and outcome != "dropped_dark":
                checks.append((False, "the dark rule: a keep-lit in a dark run wasn't dropped as dark"))
            continue
        payload = bytes.fromhex(wire.get("payload_hex") or "")
        if row.get("matches_trace") is False:
            checks.append((False, "the bytes: a sent frame differs from its encoded row"))
        if desk and desk.get("light") is False:
            if action == "keep_lit":
                checks.append((False, "the dark rule: a dark run sent a keep-lit"))
            if action == "aim_move" and len(payload) == 16 and (payload[7] or payload[2] & 0x01):
                checks.append((False, "the dark rule: a dark run sent a MOVE with its spot lit"))
        if action in ("stop", "resume") and outcome == "acked":
            want = action == "stop"
            if answer.get("stop_latched") is not want:
                checks.append((False, "Stop: a %s den-live sent didn't leave the latch %s" % (
                    action, "set" if want else "clear")))
        delivered = action == "aim_move" or answer.get("status") == "sent"
        if delivered and len(payload) >= 2 and isinstance(row.get("called_mono"), (int, float)):
            wall = header.get("at_ts", 0) + row["called_mono"] - header.get("at_mono", 0)
            frames[row.get("device")].append((wall, index, row.get("trace_id"), COMMANDS.get(action),
                                              struct.unpack_from("<H", payload)[0]))
    stale = 0
    for board in frames.values():
        board.sort()
        last, owner = None, {}
        for _wall, _index, trace_id, cmd, seq in board:
            kind = classify_seq(last, seq)
            if cmd != STOP and (kind == "stale" or (kind == "duplicate" and owner.get(seq) != trace_id)):
                stale += 1
            if kind == "new":
                last, owner[seq] = seq, trace_id
    answered_stale = sum(t["move answered stale_seq"] for t in runs.values())
    if stale or answered_stale:
        checks.append((False, "no stale seq: %d frames the board would refuse as stale, %d moves answered stale_seq"
                       % (stale, answered_stale)))
    for run, tally in runs.items():
        desk = (headers.get(run) or {}).get("desk")
        what = ("virtual" if desk is None else "the desk, %s, %s" % (desk.get("fixture_id"),
                                                                     "lit" if desk.get("light") else "dark"))
        latched = tally.pop("latched read", None)
        counts = ", ".join("%s %d" % (key, value) for key, value in sorted(tally.items()) if value)
        lines.append("Run %s: %s. %s%s" % (str(run)[:8], what, counts or "nothing sent",
                                           "" if latched is None else "; last latch read %s" % latched))
    if unreadable:
        lines.append("%d lines didn't read" % unreadable)
    failed = sorted({text for ok, text in checks if not ok})
    for name in ("one fixture", "the dark rule", "the bytes", "no stale seq", "Stop"):
        bad = [text for text in failed if text.startswith(name)]
        lines.extend("FAIL " + text for text in bad) if bad else lines.append("PASS " + name)
    return lines, not failed and not unreadable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--day", help="the UTC day, YYYY-MM-DD (default today, UTC)")
    parser.add_argument("--state", type=Path, default=STATE, help="den-live's state folder (default %(default)s)")
    args = parser.parse_args(argv)
    day = args.day or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    path = args.state / "days" / day / FILE
    if not path.is_file():
        print("No fixture trace for %s yet (run it with sudo, on LA)." % day)
        return 2
    lines, ok = report(rows_of(path))
    print("The desk stage's trace for %s:" % day)
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
