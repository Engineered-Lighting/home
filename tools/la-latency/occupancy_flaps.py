"""Occupancy flap statistics from the Jetson occupancy reader's edge log.

Input: the controller's journal lines (any format containing
'occupancy edge: entity=... from=<a> to=<b> observed_at=<epoch>'), for example
  journalctl _SYSTEMD_USER_UNIT=perception-lab-managed.service -o cat | grep 'occupancy edge'
Output: per window, ON stretches and OFF gaps (counts, p10/p50/p90), short OFF gaps
(<= 10 s and <= 30 s, the flaps), short ON blips, unknown transitions, and rates per
occupied hour, so windows with different amounts of occupancy compare fairly.

A stretch belongs to the window its start falls in; a stretch that overlaps an
excluded (dirty) span is dropped. Stdlib only; reads no host.
"""
import argparse
import datetime
import json
import re
import sys

EDGE = re.compile(r"occupancy edge: entity=(\S+) from=(\w+) to=(\w+) observed_at=([0-9.]+)")


def parse(lines):
    edges = []
    for line in lines:
        match = EDGE.search(line)
        if match:
            edges.append((float(match.group(4)), match.group(2), match.group(3), match.group(1)))
    edges.sort()
    return edges


def stretches(edges):
    """(state, start, end) for every stretch between consecutive edges."""
    return [(state, start, end) for (start, _, state, _), (end, _, _, _) in zip(edges, edges[1:])]


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def stats(spans):
    on = [end - start for state, start, end in spans if state == "on"]
    off = [end - start for state, start, end in spans if state == "off"]
    unknown = [end - start for state, start, end in spans if state not in ("on", "off")]
    on_hours = sum(on) / 3600

    def dist(values):
        return {"n": len(values), "p10_s": pct(values, 0.1), "p50_s": pct(values, 0.5), "p90_s": pct(values, 0.9)}

    short_off_10 = sum(1 for d in off if d <= 10)
    short_off_30 = sum(1 for d in off if d <= 30)
    short_on_10 = sum(1 for d in on if d <= 10)
    rate = (lambda n: round(n / on_hours, 2) if on_hours > 0 else None)
    return {
        "on_stretches": dist(on), "off_gaps": dist(off), "unknown_stretches": len(unknown),
        "occupied_hours": round(on_hours, 2),
        "off_gaps_le_10s": short_off_10, "off_gaps_le_30s": short_off_30, "on_blips_le_10s": short_on_10,
        "off_gaps_le_10s_share": round(short_off_10 / len(off), 3) if off else None,
        "off_gaps_le_10s_per_occupied_hour": rate(short_off_10),
        "off_gaps_le_30s_per_occupied_hour": rate(short_off_30),
        "on_blips_le_10s_per_occupied_hour": rate(short_on_10),
    }


def window(spans, start, end, dirty):
    keep = []
    for state, s, e in spans:
        if not (start <= s < end):
            continue
        if any(s < d_end and e > d_start for d_start, d_end in dirty):
            continue
        keep.append((state, s, e))
    return keep


def epoch(text):
    return datetime.datetime.fromisoformat(text).timestamp()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("journal", nargs="?", help="journal export (default stdin)")
    parser.add_argument("--window", action="append", default=[], metavar="NAME=START..END",
                        help="ISO-8601 local times with offset, e.g. before=2026-10-08T23:36-07:00..2026-10-09T17:00-07:00")
    parser.add_argument("--dirty", action="append", default=[], metavar="START..END", help="span to exclude")
    args = parser.parse_args(argv)
    lines = open(args.journal, encoding="utf-8") if args.journal else sys.stdin
    edges = parse(lines)
    spans = stretches(edges)
    dirty = [tuple(epoch(x) for x in item.split("..")) for item in args.dirty]
    report = {"edges": len(edges),
              "first_edge": datetime.datetime.fromtimestamp(edges[0][0]).isoformat() if edges else None,
              "last_edge": datetime.datetime.fromtimestamp(edges[-1][0]).isoformat() if edges else None,
              "windows": {}}
    for item in args.window or ["all=1970-01-01T00:00+00:00..2100-01-01T00:00+00:00"]:
        name, span = item.split("=", 1)
        start, end = (epoch(x) for x in span.split(".."))
        report["windows"][name] = stats(window(spans, start, end, dirty))
    json.dump(report, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
