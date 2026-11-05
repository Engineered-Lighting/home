"""Rung 9's score on replay: coordinator v0 (decide) against the frozen den_lighting, both through one light model.

    python -B tools/den-core/score.py LOG [--gate tools/den-core/rung9-gate-v0.json] [--marks FILE] [--plumbing]
        [--out FILE]

LOG is a run.py log with need, incumbent and decide. The gate (rung9-gate-v0.json) says what is scored. A scored
run is refused (exit 2, with every reason) while any slot in the gate or in decide's logged config is open, while
decide's light model is a fake, for a synthetic log, or when the log's compensation option is not the gate's.
--plumbing runs anyway on the proposed values and labels every line "plumbing: ... not a score" (exit 0).

What it checks (the gate's "gate" text):
- replay: check.check() replays LOG byte for byte, while timing each call of decide.handle that makes a decision;
  each must take under the gate's decision_ms_max (the whole plan and record; hue-den-4 has no aims);
- record: every decide decision carries its requested and applied scenes (the same channels) and the recipients of
  each channel that is on; its exclusions recompute from its own inputs (state_ids, field_support, body envelope
  version, t_exec); its hard_conflicts recompute from its scene and list no free channel;
- surrogates: the candidate's anticipation and stability rates against their floors (the gate's definitions). The
  incumbent's are reported too: 0 and 1 by construction.
Reported, never gated, labelled "predicted, replay surrogate": predicted service coverage and missed service at
the owner's marks (--marks), for both arms, when the light model is in lx; otherwise n/a.

Both arms go through the light model in decide's logged config: the candidate's scene is decide's latest request
per bulb (den_lighting's in a game and before decide's first request for the bulb, as decide is silent there by
design), the incumbent's is what den_lighting last acted on (incumbent.Shadow), else the bulb's last recorded
state. Held bulbs are pinned in both: decide pins them at read-back and den_lighting holds them.

--marks FILE: one JSON object per line, {mark_id, track_id, start, end, band: fine|casual, points: [point ids of
the light model]}: the owner's marks of where the work was, until the gold set's regions can be put on rung 8's
grid. Offline, standard library; it reads LOG, the gate and the marks, and writes only --out (a new file outside
every git checkout).
"""
import argparse
import json
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check  # noqa: E402
import decide  # noqa: E402
import events  # noqa: E402
import incumbent  # noqa: E402
import light_model  # noqa: E402

SCHEMA = "den-rung9-score-1"
GATE = HERE / "rung9-gate-v0.json"
PLUMBING = "plumbing: proposed values for open slots, not a score"
SURROGATE = "predicted, replay surrogate"


def open_slots(tree, prefix=""):
    """The names of every {value, status: open, proposed} slot in a JSON tree."""
    found = []
    if isinstance(tree, dict):
        if tree.get("status") == "open" and "proposed" in tree:
            return [prefix.rstrip(".")]
        for key, item in sorted(tree.items()):
            found += open_slots(item, prefix + key + ".")
    return found


def slot(entry):
    """A slot's value, or its proposed value while open (only ever reached under --plumbing)."""
    return entry["proposed"] if entry["status"] == "open" else entry["value"]


def refusals(header, gate, config, model):
    reasons = []
    if header["synthetic"]:
        reasons.append("a synthetic log: plumbing only, never a gate")
    if header["actuation"] not in ("replay", "shadow"):
        reasons.append("actuation %s: a scored run is replay or shadow" % header["actuation"])
    gate_open, config_open = open_slots(gate), open_slots(config.get("slots", {}))
    if gate_open:
        reasons.append("open slot(s) in the gate: %s" % ", ".join(gate_open))
    if config_open:
        reasons.append("open slot(s) in decide's logged config: %s" % ", ".join(config_open))
    if model.fake:
        reasons.append("the light model is a fake built from rung 8's plan (%s): a scored run needs rung 8 Stage 1's "
                       "light-model.json (run.py --light-model)" % model.version)
    wanted = gate["parameters"]["compensation"]
    if wanted["status"] != "open" and wanted["value"] != config["compensation"]["option"]:
        reasons.append("the log used compensation %s; the gate scores %s" % (config["compensation"]["option"],
                                                                            wanted["value"]))
    return reasons


def replay_timed(log_path):
    """check.check() with decide's handle timed: (check result, seconds per call that made a decision, slowest)."""
    spans, slowest = [], [0.0]

    def make(config):
        policy = decide.make_policy(config)
        handle = policy.handle

        def timed(event):
            start = time.perf_counter()
            out = handle(event)
            elapsed = time.perf_counter() - start
            slowest[0] = max(slowest[0], elapsed)
            if out:
                spans.append(elapsed)
            return out
        policy.handle = timed
        return policy
    result = check.check(log_path, resolve={"decide": types.SimpleNamespace(__file__=decide.__file__,
                                                                            make_policy=make)})
    return result, spans, slowest[0]


def record_problems(log, config, model):
    """The first problem with decide's decisions as rung 9's record, or None, and how many were checked."""
    people, n = {}, 0
    for e in log:
        if e["kind"] == "person_state":
            people[e["data"]["state_id"]] = e["data"]
        if e["kind"] != "decision" or e["source"] != "decide":
            continue
        n += 1
        d, where = e["data"], "decide decision %s" % e["data"]["decision_id"]
        requested, applied = d.get("requested_scene") or {}, d.get("applied_scene")
        if not requested or applied is None or set(applied) != set(requested):
            return "%s: its applied scene does not cover its requested channels" % where, n
        if any(a.get("status") not in ("held", "not_sent") or "readback" not in a for a in applied.values()):
            return "%s: an applied channel has no status or readback" % where, n
        lit = {b for b, s in requested.items() if s["on"]}
        if set(d.get("recipients") or {}) != lit:
            return "%s: its recipients are not the channels that are on" % where, n
        inputs = d["inputs"]
        if decide.replay_exclusions(inputs) != inputs["exclusions"]:
            return "%s: its exclusions do not recompute from its own inputs" % where, n
        held = {h["entity_id"] for h in inputs["hold_set"]["held"]}
        crowd = [people[s] for s in inputs["person_state"] if s in people]
        conflicts, _ = decide.glare_conflicts(model, requested, held, crowd, inputs["hard_constraints"]["glare"]["limit"])
        if conflicts != d["hard_conflicts"] or any(c["held_channel"] not in held for c in d["hard_conflicts"]):
            return "%s: its hard_conflicts do not recompute from its scene, or list a free channel" % where, n
    return None, n


def rule(decision):
    return decision["why"].split(":", 1)[0]


def arms(header, log):
    """{'candidate'|'incumbent': [(t, scenes, cause)]}: each arm's scene of the four bulbs after each instant where
    it changed, and whether a need-driven decide decision changed it ('need') or anything else ('mode')."""
    shadow, observed, requested = incumbent.Shadow(header), {}, {}
    gaming, last_rule, out = False, None, {"candidate": [], "incumbent": []}
    cause = "mode"
    for n, e in enumerate(log):
        shadow.feed(e)
        kind, data = e["kind"], e["data"]
        if kind == "light_observed":
            observed[data["entity_id"]] = {key: data[key] for key in ("on", "brightness", "kelvin")}
        elif kind == "mode" and e["source"] == "incumbent":
            gaming = data["gaming"]
        elif kind == "decision" and e["source"] == "decide":
            now = rule(data)
            if (now == "reading_look") != (last_rule == "reading_look"):
                cause = "need"
            last_rule = now
            requested.update(data["requested_scene"])
        if n + 1 < len(log) and log[n + 1]["t"] == e["t"]:
            continue
        shown = {b: shadow.acted.get(b, observed.get(b)) for b in events.LIGHTS}
        mine = shown if gaming else {b: requested.get(b, shown[b]) for b in events.LIGHTS}
        for name, scenes, why in (("candidate", mine, cause), ("incumbent", shown, "mode")):
            if not out[name] or out[name][-1][1] != scenes:
                out[name].append((e["t"], dict(scenes), why))
        cause = "mode"
    return out


def episodes(log):
    """[(track_id, onset t, end t)]: from a track's first PersonState that leaves none to its next none, counted
    when it reached engaged (a start that falls back to none is not a need)."""
    found, open_ = [], {}
    for e in log:
        if e["kind"] != "person_state" or e["source"] != "need":
            continue
        track, state = e["data"]["track_id"], e["data"]["focused_engagement"]["state"]
        if state != "none" and track not in open_:
            open_[track] = [e["t"], False]
        if track in open_ and state == "engaged":
            open_[track][1] = True
        if state == "none" and track in open_:
            t0, engaged = open_.pop(track)
            if engaged:
                found.append((track, t0, e["t"]))
    end = log[-1]["t"] if log else 0.0
    found += [(track, t0, end) for track, (t0, engaged) in open_.items() if engaged]
    return sorted(found, key=lambda x: (x[1], x[0]))


def task_light(model, scenes, points):
    values = model.predict(scenes, points)
    return None if not points or any(v is None for v in values.values()) else sum(values.values()) / len(points)


def surrogates(timeline, found, points_of, model, window, rise, change):
    """(anticipation, stability) rates for one arm, each {rate, episodes, scored, unknown}."""
    hits = {"anticipation": [], "stability": []}
    unknown, per_track = 0, {}
    for track, t0, t1 in found:
        if track not in per_track:
            points = points_of(track)
            per_track[track] = [(t, task_light(model, scenes, points), cause) for t, scenes, cause in timeline]
        light = per_track[track]
        before = [x for x in light if x[0] < t0][-1:] or light[:1]
        if not before or before[0][1] is None:
            unknown += 1
            continue
        start = before[0][1]
        up = [(t, e, c) for t, e, c in light if t0 <= t <= t0 + window and c == "need"]
        if any(e is None for _, e, _ in up):
            unknown += 1
            continue
        hits["anticipation"].append(any(e >= (1 + rise) * start if start > 0 else e > 0 for _, e, _ in up))
        stable, previous = True, None
        for t, e, c in light:
            if t > t1:
                break
            if t >= t0 + window and t < t1 and c == "need" and previous is not None and e is not None:
                if abs(e - previous) > change * max(previous, 1e-9):
                    stable = False
            previous = e if e is not None else previous
        hits["stability"].append(stable)
    out = {}
    for name, values in hits.items():
        out[name] = {"rate": round(sum(values) / len(values), 4) if values else None, "episodes": len(found),
                     "scored": len(values), "unknown": unknown}
    return out


def service(timeline, marks, model, fixed):
    """Predicted service coverage and missed service at the marks for one arm, or None without an lx model."""
    if model.basis_unit != "lx" or not marks:
        return None
    served_s = scored_s = 0.0
    missed = 0
    for mark in marks:
        floor = fixed["service_floors_lx"][mark["band"]]
        start, end = mark["start"] + fixed["quiet_window_s"], mark["end"]
        if end <= start:
            continue
        mine = 0.0
        for n, (t, scenes, _) in enumerate(timeline):
            lo = max(t, start)
            hi = min(timeline[n + 1][0] if n + 1 < len(timeline) else end, end)
            if hi <= lo:
                continue
            values = model.predict(scenes, mark["points"])
            lit = sum(1 for v in values.values() if v is not None and v >= floor)
            if lit >= fixed["delivered_area_share"] * len(mark["points"]):
                mine += hi - lo
        served_s += mine
        scored_s += end - start
        missed += mine == 0
    return {"coverage": round(served_s / scored_s, 4) if scored_s else None, "missed_service": missed,
            "marks": len(marks), "label": SURROGATE}


def read_marks(path):
    marks = []
    for number, raw in enumerate(Path(path).read_text("utf-8-sig").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            mark = json.loads(raw)
            if mark["band"] not in ("fine", "casual") or not mark["points"] or not mark["end"] > mark["start"]:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise ValueError("%s line %d is not a mark" % (path, number)) from None
        marks.append(mark)
    return marks


def score(log_path, gate_path=GATE, marks_path=None, plumbing=False):
    """(record, refused reasons). With reasons and no plumbing, record is None."""
    header, log = events.read(log_path)
    if not {"need", "incumbent", "decide"} <= {p["id"] for p in header["policies"]}:
        return None, ["the log needs need, incumbent and decide"]
    gate = json.loads(Path(gate_path).read_text("utf-8-sig"))  # a gate saved by Notepad may carry a BOM
    config = next(p["config"] for p in header["policies"] if p["id"] == "decide")
    model = light_model.LightModel(config["light_model"])
    reasons = refusals(header, gate, config, model)
    if reasons and not plumbing:
        return None, reasons
    result, spans, slowest = replay_timed(log_path)
    problem, checked = record_problems(log, config, model)
    params = {name: slot(entry) for name, entry in gate["parameters"].items()}
    floors = {name: slot(entry["floor"]) for name, entry in gate["surrogates"].items()}
    fixed = gate["fixed"]
    people = {e["data"]["track_id"]: e["data"] for e in log if e["kind"] == "person_state"}
    timeline, found = arms(header, log), episodes(log)

    def points_of(track):
        return decide.task_points(people[track], config, model)

    rates = {name: surrogates(timeline[name], found, points_of, model, params["anticipation_window_s"],
                              params["anticipation_rise"], params["stability_change"]) for name in timeline}
    marks = read_marks(marks_path) if marks_path else []
    reported = {name: service(timeline[name], marks, model, fixed) for name in timeline}
    ms = [round(s * 1000, 3) for s in spans]
    checks = {
        "replay": {"ok": result["ok"], "text": result["reason"]},
        "timing": {"ok": bool(ms) and max(ms) < fixed["decision_ms_max"],
                   "text": "%d decide decision(s), slowest %.1f ms (any call %.1f ms), limit %d ms"
                           % (len(ms), max(ms) if ms else 0.0, slowest * 1000, fixed["decision_ms_max"])},
        "record": {"ok": problem is None and checked > 0,
                   "text": problem or "%d decide decision(s) carry requested and applied scenes, recipients, "
                                      "exclusions and hard_conflicts that recompute" % checked},
    }
    for name in ("anticipation", "stability"):
        rate = rates["candidate"][name]["rate"]
        checks[name] = {"ok": rate is not None and rate >= floors[name],
                        "text": "candidate %s against floor %s (%d of %d episode(s) scored); incumbent %s"
                                % (rate, floors[name], rates["candidate"][name]["scored"], len(found),
                                   rates["incumbent"][name]["rate"])}
    record = {
        "schema": SCHEMA,
        "gate": {"id": gate["id"], "sha256": events.sha256_json(gate)},
        "log": {"name": Path(log_path).name, "sha256": events.sha256_file(log_path)},
        "plumbing": PLUMBING if reasons else None,
        "refused_without_plumbing": reasons,
        "light_model": model.describe(),
        "compensation": config["compensation"]["option"],
        "actuation": header["actuation"],
        "checks": checks,
        "surrogates": {"label": "replay surrogate", "floors": floors, "params": params, "arms": rates},
        "reported": {"label": SURROGATE, "arms": reported,
                     "status": None if all(reported.values()) else
                     "n/a: %s" % ("no marks given (--marks)" if not marks else
                                  "the light model is %s; the owner's floors are in lx" % model.basis_unit)},
        "verdict": None if reasons else ("pass" if all(c["ok"] for c in checks.values()) else "fail"),
    }
    return record, reasons


def lines(record):
    tag = "[plumbing] " if record["plumbing"] else ""
    out = ["[plumbing] %s; refused without --plumbing because:" % PLUMBING] if record["plumbing"] else []
    out += ["[plumbing]   - %s" % reason for reason in record["refused_without_plumbing"]]
    out += ["%sgate %s, log %s, light model %s%s, compensation %s, actuation %s" % (
        tag, record["gate"]["id"], record["log"]["name"], record["light_model"]["version"],
        " (fake)" if record["light_model"]["fake"] else "", record["compensation"], record["actuation"])]
    for name, check_ in record["checks"].items():
        out.append("%s%s: %s (%s)" % (tag, name, "OK" if check_["ok"] else "FAIL", check_["text"]))
    reported = record["reported"]
    if reported["status"]:
        out.append("%sreported (%s): %s" % (tag, SURROGATE, reported["status"]))
    else:
        for arm, values in reported["arms"].items():
            out.append("%sreported (%s): %s coverage %s, missed service %d of %d mark(s)" % (
                tag, SURROGATE, arm, values["coverage"], values["missed_service"], values["marks"]))
    out.append("verdict: %s" % (record["verdict"] or "none (%s)" % PLUMBING))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log")
    parser.add_argument("--gate", default=str(GATE))
    parser.add_argument("--marks")
    parser.add_argument("--plumbing", action="store_true", help="score on the proposed values, labelled not a score")
    parser.add_argument("--out", help="a new JSON file outside every git checkout")
    args = parser.parse_args(argv)
    if args.out and (not Path(args.out).is_absolute() or events.inside_checkout(Path(args.out).parent)):
        parser.error("--out must be an absolute path outside every git checkout")
    record, reasons = score(args.log, args.gate, args.marks, args.plumbing)
    if record is None:
        print("refused: a scored run needs every slot filled and a real light model:")
        print("\n".join("  - " + reason for reason in reasons))
        print("  (--plumbing runs on the proposed values, labelled not a score)")
        return 2
    print("\n".join(lines(record)))
    if args.out:
        with open(args.out, "x", encoding="ascii", newline="\n") as handle:
            handle.write(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n")
    return 0 if record["verdict"] in ("pass", None) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as error:
        sys.exit("score.py: %s" % error)
