"""Run the den-core policies over copied files or a synthetic evening; write one log and summarise it.

python -B tools/den-core/run.py --out ABSDIR [--synthetic evening [--seed N] [--touch] [--game] [--clean]]
    [--facts PATH ...] [--poll FILE ...] [--mqtt FILE --mqtt-t0 EPOCH] [--journal FILE ... | --assume-occupied]
    [--live-commands FILE ... [--lights FILE ...]] [--policies incumbent] [--no-plant]
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import events
import incumbent
import runner
import sources
import synthetic

REPLANS = frozenset({"occupancy", "light_observed", "person_state", "mode", "tick"})  # decide.handle plans on these


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short(entity_id):
    return entity_id.replace("light.", "").replace("den_", "")


def scene_line(requested, transitions):
    order = {e: n for n, e in enumerate(events.LIGHTS)}
    parts = []
    for e in sorted(requested, key=lambda e: (order.get(e, len(order)), e)):
        s = requested[e]
        look = "%s@%sK" % (s["brightness"], s["kelvin"]) if s["on"] else "off"
        parts.append("%s=%s/%gs" % (short(e), look, transitions.get(e, 0)))
    return " ".join(parts)


def _intervals(log, step, compared):
    """Spans per bulb while compared() says it differs. step(event) folds in each event; bulbs are
    compared once per instant, after all of its events, and a span still open ends at the last event."""
    open_, spans = {}, []
    for n, event in enumerate(log):
        step(event)
        if n + 1 < len(log) and log[n + 1]["t"] == event["t"]:
            continue
        for bulb, differs in compared():
            if differs and bulb not in open_:
                open_[bulb] = event["t"]
            elif not differs and bulb in open_:
                spans.append({"entity_id": bulb, "start": open_.pop(bulb), "end": event["t"]})
    end = log[-1]["t"] if log else 0.0
    spans += [{"entity_id": bulb, "start": start, "end": end} for bulb, start in open_.items()]
    return sorted(spans, key=lambda s: (s["start"], s["entity_id"]))


def _both(a, b):
    """events.scene_matches each way, so a level one scene sets and the other leaves null is a difference."""
    return events.scene_matches(a, b) and events.scene_matches(b, a)


def diff(header, log):
    """Intervals where decide's latest requested scene for a bulb differs from what den_lighting wanted for
    it, leaving out time while the latest mode.gaming was true.

    What den_lighting wanted comes from re-running it beside the log (incumbent.Shadow.wants): its desired
    scene, or while it has none (vacancy wait, unknown occupancy) the target it last acted on. Recorded bulb
    states move neither side, so a live bulb recorded a moment before the replay acts opens no interval.
    Scenes differ unless each matches the other, so a level decide asks for counts against den_lighting's
    last-level ON with no level. A bulb is compared from decide's first request for it, once per instant,
    and only at instants holding an event decide plans on (REPLANS): den_lighting also acts on a facts line,
    which decide follows only at its next tick."""
    den, wanted, state = incumbent.Shadow(header), {}, {"gaming": False, "replan": False, "t": 0.0, "differs": []}

    def step(event):
        den.feed(event)
        kind, data = event["kind"], event["data"]
        if kind == "mode":
            state["gaming"] = data["gaming"]
        elif kind == "decision" and event["source"] == "decide":
            wanted.update(data["requested_scene"])
            state["replan"] = True
        state["replan"] = state["replan"] or kind in REPLANS
        state["t"] = event["t"]

    def compared():
        if state["replan"]:
            state["replan"] = False
            state["differs"] = [(bulb, not state["gaming"] and not _both(wanted[bulb], den.wants(bulb, state["t"])))
                                for bulb in sorted(wanted)]
        return state["differs"]

    return _intervals(log, step, compared)


def _agree(a, b):
    """scene_matches, where a null level on either side matches any level: HA's recorder keeps no light
    levels, so a replay of recorded bulbs sends ONs with no level."""
    if a is None or b is None:
        return a is None and b is None
    return events.scene_matches({key: None if key != "on" and b[key] is None else a[key] for key in a}, b)


def live_diff(header, log):
    """Intervals where the replayed den_lighting's latest target for a bulb does not match the latest
    command the live den_lighting sent for it (live_command lines).

    The replayed target is the one it last acted on (incumbent.Shadow.acted): a command, or a bulb it found
    already there, as when --lights shows it the live bulb's recorded result before its own tick. A null
    level on either side matches any level. A bulb is compared from the first command for it on either
    side, with a side that has none yet differing. Gaming is not left out: both sides are den_lighting.
    Events at one instant are compared once."""
    den, live, sent = incumbent.Shadow(header), {}, set()

    def step(event):
        den.feed(event)
        if event["kind"] == "live_command":
            live[event["data"]["entity_id"]] = event["data"]["target"]
        elif event["kind"] == "decision" and event["source"] == "incumbent":
            sent.update(event["data"]["requested_scene"])

    return _intervals(log, step, lambda: [(bulb, not _agree(live.get(bulb), den.acted.get(bulb)))
                                          for bulb in sorted(set(live) | sent)])


def from_start(rows, start, last):
    """Recorded bulb states for a replay from start to last: each bulb's latest state before start, moved
    to start (den_lighting reads every bulb when it starts), then the states from start to last."""
    before = {}
    for t, kind, data in rows:
        if t < start:
            before[data["entity_id"]] = (start, kind, data)
    return [before[e] for e in events.LIGHTS if e in before] + [row for row in rows if start <= row[0] <= last]


def _span_lines(spans, first):
    return ["  %-11s %+8.1f s .. %+8.1f s" % (short(s["entity_id"]), s["start"] - first, s["end"] - first)
            for s in spans]


def summary(header, log):
    inputs = [e for e in log if e["source"] in events.ADAPTER_SOURCES]
    counts = Counter(e["kind"] for e in inputs)
    first = inputs[0]["t"] if inputs else log[0]["t"]
    lines = ["inputs: " + ", ".join("%s %d" % (kind, counts[kind]) for kind in events.INPUT_KINDS if counts[kind])]
    for policy in header["policies"]:
        decisions = [e for e in log if e["kind"] == "decision" and e["source"] == policy["id"]]
        lines.append("%s (%s): %d decision(s)" % (policy["id"], policy["version"], len(decisions)))
        for e in decisions:
            d = e["data"]
            lines.append("  %s %+8.1f s  %-11s %s" % (utc(e["t"]), e["t"] - first, d["trigger"],
                                                     scene_line(d["requested_scene"], d["transitions"])))
    if {"incumbent", "decide"} <= {p["id"] for p in header["policies"]}:
        spans = diff(header, log)
        lines.append("diff incumbent vs decide (gaming left out): %d interval(s)" % len(spans))
        lines += _span_lines(spans, first)
    live = Counter(e["data"]["entity_id"] for e in log if e["kind"] == "live_command")
    if live and "incumbent" in {p["id"] for p in header["policies"]}:
        ours = Counter(b for e in log if e["kind"] == "decision" and e["source"] == "incumbent"
                       for b in e["data"]["requested_scene"])
        spans = live_diff(header, log)
        lines.append("diff live den_lighting vs incumbent replay (a null level matches any): %d interval(s); "
                     "commands live/replayed: %s"
                     % (len(spans), ", ".join("%s %d/%d" % (short(b), live[b], ours[b]) for b in events.LIGHTS)))
        lines += _span_lines(spans, first)
    if header["synthetic"]:
        lines.append("synthetic: plumbing only, never a gate")
    return "\n".join(lines)


def _new_file(path, text):
    with open(path, "x", encoding="ascii", newline="\n") as handle:
        handle.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="absolute folder outside every git checkout")
    parser.add_argument("--synthetic", choices=["evening"])
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument("--touch", action="store_true")
    parser.add_argument("--game", action="store_true")
    parser.add_argument("--clean", action="store_true", help="no unclear, invalid or failed facts")
    parser.add_argument("--facts", nargs="+", default=[], help="la-fact-log-1 day files or folders of them")
    parser.add_argument("--poll", nargs="+", default=[])
    parser.add_argument("--mqtt")
    parser.add_argument("--mqtt-t0", type=float, default=0.0)
    parser.add_argument("--journal", nargs="+", default=[],
                        help="the Jetson occupancy reader's journal lines (occupancy edge: ...)")
    parser.add_argument("--assume-occupied", action="store_true")
    parser.add_argument("--live-commands", nargs="+", default=[],
                        help="den_lighting_command events exported from the HA recorder: a reference, not an input")
    parser.add_argument("--lights", nargs="+", default=[],
                        help="the four bulbs' states exported from the HA recorder (needs --live-commands)")
    parser.add_argument("--policies", nargs="+", default=["incumbent"])
    parser.add_argument("--no-plant", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if not out.is_absolute() or events.inside_checkout(out):
        parser.error("--out must be an absolute folder outside every git checkout")
    if (out / "log.jsonl").exists():
        parser.error("%s already holds a log" % out)
    if args.journal and args.assume_occupied:
        parser.error("--journal gives real occupancy; it replaces --assume-occupied, so give one or the other")
    if args.live_commands and "incumbent" not in args.policies:
        parser.error("--live-commands is compared with the incumbent: add incumbent to --policies")
    if args.lights and not args.live_commands:
        parser.error("--lights needs --live-commands: den_lighting's own bulb changes are known by its commands")

    inputs, described, truth, fact_lines = [], [], None, []
    if args.synthetic:
        params = {"seed": args.seed, "touch": args.touch, "game": args.game}
        if args.clean:
            params.update(unclear_rate=0.0, invalid_rate=0.0, fail_rate=0.0)
        items, truth, fact_lines = synthetic.evening(**params)
        inputs += runner.tagged(items, "synthetic")
        described.append({"name": "synthetic:evening", "params": params})
    if args.facts:
        paths = [p for arg in map(Path, args.facts)
                 for p in (sorted(arg.glob("la-fact-log-1-*.jsonl")) if arg.is_dir() else [arg])]
        items, torn = sources.fact_log(paths)
        inputs += runner.tagged(items, "fact_log")
        described += [{"name": p.name, "sha256": events.sha256_file(p)} for p in paths]
        if torn:
            print("fact_log: skipped %d torn last line(s)" % torn)
    for path in map(Path, args.poll):
        inputs += runner.tagged(sources.poll_log(path), "poll_log")
        described.append({"name": path.name, "sha256": events.sha256_file(path)})
    if args.mqtt:
        path = Path(args.mqtt)
        inputs += runner.tagged(sources.mqtt_timeline(path, args.mqtt_t0), "mqtt_timeline")
        described.append({"name": path.name, "sha256": events.sha256_file(path), "params": {"t0": args.mqtt_t0}})
    if args.journal:
        paths = list(map(Path, args.journal))
        items, unlogged = sources.occupancy_journal(paths)
        inputs += runner.tagged(items, "occupancy_journal")
        described += [{"name": p.name, "sha256": events.sha256_file(p)} for p in paths]
        if unlogged:
            print("occupancy_journal: %d off(s) the reader never logged; the replay stays occupied through each:"
                  % len(unlogged))
            print("\n".join("  %s .. %s" % (utc(on), utc(off)) for on, off in unlogged))
    if not inputs:
        parser.error("no inputs: give --synthetic, --facts, --poll, --mqtt or --journal")
    if args.live_commands:
        # Cut to the span the other inputs replay: the bulbs up to the last input, the commands (a reference
        # only) through runner.run's tail of ticks as well.
        start, last = min(t for t, *_ in inputs), max(t for t, *_ in inputs)
        paths = list(map(Path, args.live_commands))
        items = sources.recorder_commands(paths)
        kept = [item for item in items if start <= item[0] <= last + runner.TAIL_S]
        if args.lights:
            light_paths = list(map(Path, args.lights))
            rows = sources.recorder_lights(light_paths, own={data["context_id"] for _, _, data in items})
            states = from_start(rows, start, last)
            inputs = runner.tagged(states, "recorder_lights") + inputs  # bulbs first at one instant, as den_lighting starts
            described += [{"name": p.name, "sha256": events.sha256_file(p)} for p in light_paths]
            labels = Counter(data["context"] for _, _, data in states)
            carried = len(states) - sum(1 for t, *_ in rows if start <= t <= last)
            print("recorder_lights: kept %d of %d bulb state(s), %d carried to the start; context %s"
                  % (len(states), len(rows), carried,
                     ", ".join("%s %d" % (label, labels[label]) for label in ("own", "person", "unknown"))))
            print("plant: off, the recorded bulb states stand in for it")
        inputs += runner.tagged(kept, "recorder_commands")
        described += [{"name": p.name, "sha256": events.sha256_file(p)} for p in paths]
        print("recorder_commands: kept %d of %d live command(s) inside the replayed span" % (len(kept), len(items)))

    header, log = runner.run(inputs, runner.load_policies(args.policies), plant=not (args.no_plant or args.lights),
                             synthetic=bool(args.synthetic), assume_occupied=args.assume_occupied,
                             sources=described)
    out.mkdir(parents=True, exist_ok=True)
    events.write(out / "log.jsonl", header, log)
    if truth is not None:
        _new_file(out / "truth.json", json.dumps(truth, indent=2, sort_keys=True) + "\n")
        days = {}
        for line in fact_lines:  # one file per UTC day of available_at, as la_fact_log names them
            days.setdefault(json.loads(line)["available_at"][:10], []).append(line)
        for day, lines in sorted(days.items()):
            _new_file(out / ("la-fact-log-1-%s.jsonl" % day), "".join(line + "\n" for line in lines))
    print(summary(header, log))
    print("log: %s" % (out / "log.jsonl"))


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        sys.exit("run.py: %s" % error)
