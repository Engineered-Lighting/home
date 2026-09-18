#!/usr/bin/env python3
"""Print the story S simulation comparison: old packages vs new packages vs
what the recorder saw, one row per scenario or recorded night.

    python3 tools/lighting-sim/compare.py [--reports DIR] [--labels old,new,m2] [--md OUT.md]

Only night reports (those with the latch metrics: false_latches, latch_on,
turn_ons_00_08, ...) take part; a label directory that also holds evening,
story T or S9-S13 reports from test_sim_tv.py is fine, those are skipped.
"""
from __future__ import annotations

import argparse
import json
import pathlib

DEFAULT_REPORTS = pathlib.Path.home() / "vjepa-home" / "experiments" / "lighting-sim" / "reports"


NIGHT_KEYS = ("false_latches", "latch_on", "turn_ons_00_08", "turn_ons_00_08_while_asleep", "lights_on_at_0300")


def is_night_report(doc: object) -> bool:
    """A story S night report (test_sim.py) carries the latch metrics; the
    evening, story T and S9-S13 reports in the same label directory
    (test_sim_tv.py, analyze_evening.py) do not and are skipped."""
    return isinstance(doc, dict) and all(key in doc for key in NIGHT_KEYS)


def load(reports: pathlib.Path, label: str) -> dict[str, dict]:
    out = {}
    for path in sorted((reports / label).glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except ValueError:
            continue
        if is_night_report(doc):
            out[path.stem] = doc
    return out


def hhmm(iso: str | None) -> str:
    return iso[11:16] if iso else "-"


def summarize(r: dict | None) -> dict[str, str]:
    if not r:
        return {"latch": "n/a", "false": "n/a", "on00_08": "n/a", "asleep_on": "n/a", "at03": "n/a"}
    return {
        "latch": ",".join(hhmm(t) for t in r["latch_on"]) or "never",
        "false": str(len(r["false_latches"])),
        "on00_08": str(r["turn_ons_00_08"]),
        "asleep_on": str(r["turn_ons_00_08_while_asleep"]),
        "at03": str(len(r["lights_on_at_0300"])),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", default=str(DEFAULT_REPORTS))
    ap.add_argument("--labels", default="old,new")
    ap.add_argument("--md", help="also write a markdown table here")
    args = ap.parse_args()
    reports = pathlib.Path(args.reports)
    labels = args.labels.split(",")
    data = {label: load(reports, label) for label in labels}
    names = sorted(set().union(*(d.keys() for d in data.values())))
    header = ["scenario"]
    for label in labels:
        header += [f"{label} latch", f"{label} false", f"{label} on 00-08", f"{label} on asleep", f"{label} lit@03"]
    header += ["actual latch", "actual on 00-08"]
    rows = []
    for name in names:
        row = [name]
        for label in labels:
            s = summarize(data[label].get(name))
            row += [s["latch"], s["false"], s["on00_08"], s["asleep_on"], s["at03"]]
        any_r = next((data[l][name] for l in labels if name in data[l]), {})
        actual = any_r.get("actual_asleep_transitions")
        row += [",".join(f"{hhmm(a['t'])}{'+' if a['state'] == 'on' else '-'}" for a in actual) or "never"
                if actual is not None else "-",
                str(any_r.get("actual_turn_ons_00_08", "-"))]
        rows.append(row)
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)] if rows else [len(h) for h in header]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*header))
    for row in rows:
        print(fmt.format(*row))
    if args.md:
        lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
        lines += ["| " + " | ".join(str(x) for x in row) + " |" for row in rows]
        pathlib.Path(args.md).write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
