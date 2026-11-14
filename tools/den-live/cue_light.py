#!/usr/bin/env python3
"""Cue to light: the latency budget's instrument (den program, workstream 03; latency-budget.md, "The instrument").

On one clock, this PC's wall clock, it lines up three things:
- a cue mark: from the cue script's own record (ask_check.py writes cues.json), or from a recorded take's take.json
  (den-take's cues, each with pc_unix);
- the spot snapshots den-live sent, each with its arrival time here: kept by SpotSubscriber, the cue script's subscriber;
- Home's paint stamps: requestAnimationFrame after applyCoordinatorSpots, saved from Home's page (HomeDenPaintStamps).

For each cue it gives the first change in light after it: when the first snapshot with the new state arrived, and when
Home first painted it. Numbers only: a snapshot is kept as its epoch, seq, on, stopped, whether any fixture is lit and
the highest level, never a target, an angle, a position or a frame.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\cue_light.py report <folder> [--take <take.json>] [--paint <paint.json>]

<folder> is a cue run's numbers folder (ask_check.py prints it). --paint is Home's stamps, saved from the browser's
console with copy(JSON.stringify(HomeDenPaintStamps)), then in PowerShell:
    Get-Clipboard | Set-Content -Encoding ascii <folder>\\paint.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import walk_test as wt  # noqa: E402  the stream's subscriber, its address and its refusals

SPOT_FIELDS = ("epoch", "seq", "on", "stopped")


def spot_row(arrival, snapshot):
    """One den-virtual-spots-1 snapshot as numbers only, with its arrival on this PC's clock."""
    fixtures = snapshot.get("fixtures") if isinstance(snapshot.get("fixtures"), dict) else {}
    levels = [f.get("level") for f in fixtures.values() if isinstance(f, dict) and isinstance(f.get("level"), (int, float))]
    return {"arrival_unix": round(arrival, 4), **{k: snapshot.get(k) for k in SPOT_FIELDS},
            "lit": any(isinstance(f, dict) and f.get("on") is True for f in fixtures.values()),
            "level_max": round(max(levels), 4) if levels else None}


class SpotSubscriber(wt.Subscriber):
    """walk_test's subscriber, which keeps people events, also keeping each spot_snapshot as a numbers-only row."""

    def __init__(self, url, opener=wt.urllib.request.urlopen):
        super().__init__(url, opener)
        self.spots = []

    def read(self, response):
        name, data = None, []
        for raw in response:
            if self.halt.is_set():
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                if name in ("people", "spot_snapshot") and data:
                    try:
                        event = json.loads("\n".join(data))
                    except ValueError:
                        event = None
                    if isinstance(event, dict):
                        with self.lock:
                            if name == "people":
                                self.events.append((time.time(), event))
                            else:
                                self.spots.append(spot_row(time.time(), event))
                name, data = None, []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                name = value
            elif field == "data":
                data.append(value)

    def spot_rows(self):
        with self.lock:
            return list(self.spots)


def connect(home, first_event_s, opener=None):
    sub = SpotSubscriber(wt.stream_url(home), opener or wt.urllib.request.urlopen)
    sub.start()
    end = time.monotonic() + first_event_s
    while sub.first() is None and time.monotonic() < end and sub.is_alive():
        time.sleep(0.1)
    if sub.first() is None:
        sub.halt.set()
        raise wt.Stop(wt.no_stream_reason(sub.refused))
    return sub


def save(folder, cues, spots):
    """The run's numbers: cues.json [{cue, pc_unix}] and spots.jsonl. Nothing else, and never a frame or a position."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cues.json").write_text(json.dumps([{"cue": c, "pc_unix": round(t, 4)} for c, t in cues]), encoding="utf-8")
    (folder / "spots.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in spots), encoding="utf-8")
    return folder


def load_cues(folder=None, take=None):
    """[(cue, pc_unix)] from a take's take.json (its cues list) or the run's cues.json."""
    path = Path(take) if take else Path(folder) / "cues.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("cues", []) if isinstance(doc, dict) else doc
    return [(str(r["cue"]), float(r["pc_unix"])) for r in rows if isinstance(r, dict) and "cue" in r and "pc_unix" in r]


def load_paint(path):
    """Home's stamps: [{epoch, seq, lit, paint_unix_ms}], as HomeDenPaintStamps holds them."""
    rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return [{"epoch": r.get("epoch"), "seq": r.get("seq"), "lit": bool(r.get("lit")), "paint_unix": r["paint_unix_ms"] / 1000}
            for r in rows if isinstance(r, dict) and isinstance(r.get("paint_unix_ms"), (int, float))]


def cue_result(cue_t, spots, paints, end_t=None):
    """After one cue: the light state at the cue, and the first change of it, by snapshot arrival and by paint."""
    before = [s for s in spots if s["arrival_unix"] <= cue_t]
    after = [s for s in spots if cue_t < s["arrival_unix"] and (end_t is None or s["arrival_unix"] <= end_t)]
    was = before[-1]["lit"] if before else False
    change = next((s for s in after if s["lit"] != was), None)
    result = {"was_lit": was, "to": None if change is None else change["lit"],
              "snapshot_s": None if change is None else round(change["arrival_unix"] - cue_t, 2), "paint_s": None}
    if change is not None and paints:
        painted = next((p for p in paints if p["paint_unix"] > cue_t and p["lit"] == change["lit"]
                        and (end_t is None or p["paint_unix"] <= end_t + 5)), None)
        if painted is not None:
            result["paint_s"] = round(painted["paint_unix"] - cue_t, 2)
    return result


def report_lines(cues, spots, paints=None):
    out = ["Cue to light, %d cues, %d snapshots%s" % (len(cues), len(spots), ", %d paints" % len(paints) if paints else "")]
    for index, (name, t) in enumerate(cues):
        end = cues[index + 1][1] if index + 1 < len(cues) else None
        r = cue_result(t, spots, paints or [], end)
        if r["to"] is None:
            out.append("%-14s light stayed %s" % (name + ":", "lit" if r["was_lit"] else "dark"))
            continue
        line = "%-14s %s: snapshot +%.2f s" % (name + ":", "lit" if r["to"] else "dark", r["snapshot_s"])
        if r["paint_s"] is not None:
            line += ", paint +%.2f s (Home %.2f s after the snapshot)" % (r["paint_s"], r["paint_s"] - r["snapshot_s"])
        elif paints:
            line += ", no matching paint"
        out.append(line)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report", help="line up the cues, the snapshots and Home's paints")
    rep.add_argument("folder")
    rep.add_argument("--take", help="a recorded take's take.json, for its cue marks")
    rep.add_argument("--paint", help="Home's paint stamps, saved from its console")
    args = parser.parse_args(argv)
    try:
        cues = load_cues(args.folder, args.take)
        spots = [json.loads(line) for line in (Path(args.folder) / "spots.jsonl").read_text(encoding="utf-8").splitlines() if line]
        paints = load_paint(args.paint) if args.paint else None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("cue to light: stopped: the run's files don't read (%s)" % type(exc).__name__)
        return 2
    for line in report_lines(cues, spots, paints):
        print(line)
    print("Words and numbers only: tell an agent these lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
