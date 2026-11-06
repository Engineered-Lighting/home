"""Run the den-core policies over copied files or a synthetic evening; write one log and summarise it.

python -B tools/den-core/run.py --out ABSDIR [--synthetic evening [--seed N] [--touch] [--game] [--clean]]
    [--facts PATH ...] [--poll FILE ...] [--mqtt FILE --mqtt-t0 EPOCH]
    [--journal FILE ... | --assume-occupied | --status FILE ...] [--corrections FILE ...]
    [--live-commands FILE ... [--lights FILE ...]] [--policies incumbent] [--no-plant] [--actuation shadow]
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import coordinator
import events
import incumbent
import light_adapter
import runner
import sources
import synthetic

REPLANS = frozenset({"occupancy", "light_observed", "person_state", "mode", "tick"})  # decide.handle plans on these
PAIR_S = 5.0  # correction-capture's WINDOW_S: a hold's own bulb row lies at most this long before the hold


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
    which decide follows only at its next tick. While decide is silent by design (its occupancy unknown, or
    off for less than its vacancy_s) each bulb keeps the result it had."""
    den, wanted = incumbent.Shadow(header), {}
    vacancy_s = next(p["config"]["vacancy_s"] for p in header["policies"] if p["id"] == "decide")
    state = {"gaming": False, "replan": False, "t": 0.0, "differs": [], "occupancy": "unknown", "vacant_since": None}

    def step(event):
        den.feed(event)
        kind, data, t = event["kind"], event["data"], event["t"]
        if kind == "mode":
            state["gaming"] = data["gaming"]
        elif kind == "decision" and event["source"] == "decide":
            wanted.update(data["requested_scene"])
            state["replan"] = True
        elif kind == "occupancy":  # as decide.handle keeps it
            value = data["value"] if data["value"] in ("on", "off") else "unknown"
            if value != "off":
                state["vacant_since"] = None
            elif state["occupancy"] != "off":
                state["vacant_since"] = t
            state["occupancy"] = value
        state["replan"] = state["replan"] or kind in REPLANS
        state["t"] = t

    def silent():
        since = state["vacant_since"]
        return state["occupancy"] == "unknown" or (state["occupancy"] == "off" and state["t"] - since < vacancy_s)

    def compared():
        if state["replan"] and not silent():
            state["differs"] = [(bulb, not state["gaming"] and not _both(wanted[bulb], den.wants(bulb, state["t"])))
                                for bulb in sorted(wanted)]
        state["replan"] = False
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
    already there, as when --lights shows it the live bulb's recorded result before its own tick. When the
    replay finds a bulb already at its target on a recorded (not plant) state, the live den_lighting saw the
    same bulb, so the live side takes that target too: neither sends a command. A null level on either side
    matches any level. A bulb is compared from the first command or found target for it on either side,
    with a side that has none yet differing. Gaming is not left out: both sides are den_lighting. Events at
    one instant are compared once.

    A correction (--corrections) is a hold the live den_lighting opened at the scene it read from the bulb, so
    it sent nothing for it: when the replay takes the correction's bulb to a new target without a command,
    the live side takes that target too. With --status, the time while the latest live_status does not show
    den_lighting automatic and out of a game is left out: the replay sees no game (the export carries no
    gaming samples) and is never disabled."""
    den, live, sent, recorded = incumbent.Shadow(header), {}, set(), {}
    state = {"left_out": False}

    def step(event):
        kind, data = event["kind"], event["data"]
        if kind == "light_observed" and event["source"] != "plant":
            recorded[data["entity_id"]] = {key: data[key] for key in ("on", "brightness", "kelvin")}
        before = dict(den.acted)
        commanded = {bulb for out in den.feed(event) if out["kind"] == "decision" for bulb in out["data"]["requested_scene"]}
        held = data["entity_id"] if kind == "correction" else None
        for bulb, target in den.acted.items():
            if (target != before.get(bulb) and bulb not in commanded
                    and (bulb == held or events.scene_matches(target, recorded.get(bulb)))):
                live[bulb] = target
        if kind == "live_command":
            live[data["entity_id"]] = data["target"]
        elif kind == "live_status":
            state["left_out"] = not (data["automatic"] is True and data["gaming"] is False)
        elif kind == "decision" and event["source"] == "incumbent":
            sent.update(data["requested_scene"])

    return _intervals(log, step, lambda: [(bulb, not state["left_out"] and not _agree(live.get(bulb), den.acted.get(bulb)))
                                          for bulb in sorted(set(live) | sent)])


def from_start(rows, start, last):
    """Recorded bulb states for a replay from start to last: each bulb's latest state before start, moved
    to start (den_lighting reads every bulb when it starts), then the states from start to last."""
    before = {}
    for t, kind, data in rows:
        if t < start:
            before[data["entity_id"]] = (start, kind, data)
    return [before[e] for e in events.LIGHTS if e in before] + [row for row in rows if start <= row[0] <= last]


def status_span(items, start, last):
    """den_lighting_status items for a replay from start to last: the latest occupancy and live_status before
    start, each moved to start - runner.TAIL_S if older (to den_lighting an off that old is a full vacancy, so
    an arrival after start is still one), then the items from start to last."""
    carried = {}
    for item in items:
        if item[0] < start:
            carried[item[1]] = item
    early = start - runner.TAIL_S
    return ([(max(t, early), kind, data) for t, kind, data in sorted(carried.values(), key=lambda item: item[0])]
            + [item for item in items if start <= item[0] <= last])


def pair(states, corrections):
    """(tagged inputs, corrections left in place, how many were paired) for --lights with --corrections.

    Each correction is paired with the recorded row of its bulb that it explains: the nearest row not yet paired,
    at most PAIR_S either side of it, not den_lighting's own, that turns the bulb the way the hold says. The row
    takes the hold's brightness and kelvin, which den_lighting's status read from the bulb and the recorder drops.
    A row before the correction (a switch: the bulb changes, then den_lighting holds it) takes the correction just
    ahead of it; a row after it (the app: den_lighting holds at the call, then the bulb reports) leaves it in place.
    The live den_lighting held the bulb as it saw the change and sent nothing; this way the replay holds it first
    and finds it already there. A correction with no such row stays where it was."""
    rows = [list(item) for item in states]
    ahead, paired, rest = {}, set(), []
    for t, kind, data in corrections:
        near = [(abs(rt - t), rt, n) for n, (rt, _, rd) in enumerate(rows)
                if abs(rt - t) <= PAIR_S and n not in paired and rd["entity_id"] == data["entity_id"]
                and rd["context"] != "own" and rd["on"] == data["after"]["on"]]
        if not near:
            rest.append((t, kind, data))
            continue
        _, rt, n = min(near)
        paired.add(n)
        rows[n][2] = {**rows[n][2], "brightness": data["after"]["brightness"], "kelvin": data["after"]["kelvin"]}
        if rt < t:
            ahead[n] = (rt, kind, data, "correction_capture")
        else:
            rest.append((t, kind, data))
    out = []
    for n, (t, kind, data) in enumerate(rows):
        if n in ahead:
            out.append(ahead[n])
        out.append((t, kind, data, "recorder_lights"))
    return out, rest, len(paired)


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
        left_out = "; live game and disabled time left out" if any(e["kind"] == "live_status" for e in log) else ""
        lines.append("diff live den_lighting vs incumbent replay (a null level matches any%s): %d interval(s); "
                     "commands live/replayed: %s"
                     % (left_out, len(spans), ", ".join("%s %d/%d" % (short(b), live[b], ours[b]) for b in events.LIGHTS)))
        lines += _span_lines(spans, first)
    lines += _coordinator_lines(header)
    if header["synthetic"]:
        lines.append("synthetic: plumbing only, never a gate")
    return "\n".join(lines)


def coordinator_config(args):
    """The coordinator's config from run.py's flags: only what they set; the gate file gives the rest."""
    config = {}
    if args.light_model and args.light_model != "fake":
        config["light_model"] = {"source": "file", "path": str(Path(args.light_model).resolve())}
    if args.surfaces:
        spec = config.setdefault("light_model", {"source": "fake", "params": {}})
        spec["surfaces"] = json.loads(Path(args.surfaces).read_bytes().decode("utf-8-sig"))
    comp = {key: value for key, value in (("option", args.compensation), ("form", args.compensation_form)) if value}
    if comp:
        config["compensation"] = comp
    return config


def _coordinator_lines(header):
    """What a rung 9 run stood on, for the summary; nothing for a run without the coordinator."""
    entry = next((p for p in header["policies"] if p["module"] == "coordinator"), None)
    if entry is None:
        return []
    config, model = entry["config"], entry["config"]["light_model"]
    lines = ["coordinator: light model %s (%s); compensation %s (pending: named option, not a choice); actuation %s"
             % (model["version"], "fake: plumbing only, never a gate" if model["backend"] == light_adapter.MARKED_FAKE
                else "fake: %s: plumbing only, never a gate" % model["backend"] if model["fake"] else model["backend"],
                coordinator.option_name(config["compensation"]), header["actuation"])]
    if config["gate"]["open"]:
        lines.append("coordinator: %d open slot(s) at their proposals; score.py --slots lists them" % len(config["gate"]["open"]))
    light = coordinator.make_policy(config).light
    bad = light_adapter.bad_surfaces(light.surfaces, light.points)
    if bad:
        lines.append("coordinator: surface map: %s: no block, or a block the light model does not have; those surfaces' light is "
                     "wrong or unknown in this log" % ", ".join(bad))
    return lines


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
    parser.add_argument("--status", nargs="+", default=[],
                        help="den_lighting's status sensor export (tools/correction-capture step 4): its own occupancy")
    parser.add_argument("--corrections", nargs="+", default=[],
                        help="tools/correction-capture's corrections.jsonl: the holds the live den_lighting opened")
    parser.add_argument("--live-commands", nargs="+", default=[],
                        help="den_lighting_command events exported from the HA recorder: a reference, not an input")
    parser.add_argument("--lights", nargs="+", default=[],
                        help="the four bulbs' states exported from the HA recorder (needs --live-commands)")
    parser.add_argument("--policies", nargs="+", default=["incumbent"])
    parser.add_argument("--no-plant", action="store_true")
    parser.add_argument("--actuation", choices=["replay", "shadow"],
                        help="the label on each decision; nothing is sent either way (default: replay, or synthetic)")
    parser.add_argument("--light-model", help="with the coordinator: 'fake' (the default), or rung 8's light-model.json")
    parser.add_argument("--surfaces", help="with the coordinator: JSON {surface_id: [point_id, ...]} over the light model's blocks, "
                                           "to preview; the default is the gate file's surface_map slot")
    parser.add_argument("--compensation", choices=["P0", "P1", "P2", "P3"],
                        help="with the coordinator: a named option; the default is the gate file's slot")
    parser.add_argument("--compensation-form", choices=["strict", "mode"])
    args = parser.parse_args(argv)
    out = Path(args.out)
    if not out.is_absolute() or events.inside_checkout(out):
        parser.error("--out must be an absolute folder outside every git checkout")
    if (out / "log.jsonl").exists():
        parser.error("%s already holds a log" % out)
    if args.actuation and args.synthetic:
        parser.error("a synthetic run is only ever actuation synthetic: leave out --actuation")
    if args.actuation == "shadow" and not (args.no_plant or args.lights):
        parser.error("--actuation shadow decides beside the real bulbs' reports: give --lights or --no-plant")
    if {"decide", "coordinator"} <= set(args.policies):
        parser.error("decide and coordinator are one policy in two versions: give one of them")
    if ((args.light_model or args.surfaces or args.compensation or args.compensation_form)
            and "coordinator" not in args.policies):
        parser.error("--light-model, --surfaces and --compensation need coordinator in --policies")
    configs = {"coordinator": coordinator_config(args)} if "coordinator" in args.policies else None
    if args.journal and args.assume_occupied:
        parser.error("--journal gives real occupancy; it replaces --assume-occupied, so give one or the other")
    if args.status and (args.synthetic or args.poll or args.mqtt or args.journal or args.assume_occupied):
        parser.error("--status gives den_lighting's own occupancy: give no other occupancy source "
                     "(--synthetic, --poll, --mqtt, --journal or --assume-occupied)")
    if args.corrections and "incumbent" not in args.policies:
        parser.error("--corrections holds bulbs in the replayed den_lighting: add incumbent to --policies")
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
        items, gaps = sources.occupancy_journal(paths)
        inputs += runner.tagged(items, "occupancy_journal")
        described += [{"name": p.name, "sha256": events.sha256_file(p)} for p in paths]
        if items and items[0][2]["value"] == "on":
            print("occupancy_journal: the first edge is an arrival, so the replay starts with occupancy unknown "
                  "and den_lighting makes no arrival ON for it")
        for sure, text in ((True, "off(s) the reader never logged"), (False, "dropout(s) after an on that may hide an off")):
            spans = [(on, line) for on, line, certain in gaps if certain is sure]
            if spans:
                print("occupancy_journal: %d %s; the replay stays occupied through each:" % (len(spans), text))
                print("\n".join("  %s .. %s" % (utc(on), utc(line)) for on, line in spans))
    if args.status:
        paths = list(map(Path, args.status))
        items, restarts = sources.den_lighting_status(paths)
        if inputs:  # cut to the span the other inputs replay, carrying the state at its start
            items = status_span(items, min(t for t, *_ in inputs), max(t for t, *_ in inputs))
        inputs += runner.tagged(items, "den_lighting_status")
        described += [{"name": p.name, "sha256": events.sha256_file(p)} for p in paths]
        edges = Counter(data["value"] for _, kind, data in items if kind == "occupancy")
        print("den_lighting_status: %d occupancy change(s) (%s); %d HA restart(s) in the export, each read as unknown"
              % (sum(edges.values()), ", ".join("%s %d" % (v, edges[v]) for v in ("on", "off", "unknown")), restarts))
    if not inputs:
        parser.error("no inputs: give --synthetic, --facts, --poll, --mqtt, --journal or --status")
    # The span the other inputs replay: corrections and bulb states up to the last input, the commands (a
    # reference only) through runner.run's tail of ticks as well.
    start, last = min(t for t, *_ in inputs), max(t for t, *_ in inputs)
    corrections = []
    if args.corrections:
        paths = list(map(Path, args.corrections))
        items = sources.correction_capture(paths)
        corrections = [item for item in items if start <= item[0] <= last]
        described += [{"name": p.name, "sha256": events.sha256_file(p)} for p in paths]
        print("correction_capture: kept %d of %d correction(s) inside the replayed span" % (len(corrections), len(items)))
    if args.live_commands:
        paths = list(map(Path, args.live_commands))
        items = sources.recorder_commands(paths)
        kept = [item for item in items if start <= item[0] <= last + runner.TAIL_S]
        if args.lights:
            light_paths = list(map(Path, args.lights))
            rows = sources.recorder_lights(light_paths, own={data["context_id"] for _, _, data in items})
            states = from_start(rows, start, last)
            tagged, corrections, paired = pair(states, corrections)
            inputs = tagged + inputs  # bulbs first at one instant, as den_lighting starts
            if args.corrections:
                print("correction_capture: %d correction(s) paired with the bulb row each explains, %d of them moved "
                      "ahead of it" % (paired, len(tagged) - len(states)))
            described += [{"name": p.name, "sha256": events.sha256_file(p)} for p in light_paths]
            labels = Counter(data["context"] for _, _, data in states)
            carried = len(states) - sum(1 for t, *_ in rows if start <= t <= last)
            print("recorder_lights: kept %d of %d bulb state(s), %d carried to the start; context %s"
                  % (len(states), len(rows), carried,
                     ", ".join("%s %d" % (label, labels[label]) for label in ("own", "person", "unknown"))))
            print("plant: off, the recorded bulb states stand in for it")
    inputs += runner.tagged(corrections, "correction_capture")
    if args.live_commands:
        inputs += runner.tagged(kept, "recorder_commands")
        described += [{"name": p.name, "sha256": events.sha256_file(p)} for p in paths]
        print("recorder_commands: kept %d of %d live command(s) inside the replayed span" % (len(kept), len(items)))

    header, log = runner.run(inputs, runner.load_policies(args.policies, configs), plant=not (args.no_plant or args.lights),
                             synthetic=bool(args.synthetic), assume_occupied=args.assume_occupied,
                             sources=described, actuation=args.actuation)
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
