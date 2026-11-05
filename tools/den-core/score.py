"""Rung 9's gate, on a replayed log: coordinator v0 against the frozen den_lighting, both through one light model.

    python -B tools/den-core/score.py LOG [--marks FILE] [--preview]

    python -B tools/den-core/score.py --slots

A scored run refuses while the gate file (rung9-gate-v0.json) has an open slot, and on a synthetic log or a fake light
model, which are plumbing and never evidence. --preview runs on the proposals in the log's header and prints no verdict.

Gated: the candidate's anticipation and stability rates against their floors, every decision replaying byte for byte in
under 100 ms, each carrying its requested and applied scenes and recipients, exclusions reproduced from what the
Decision logged, and the glare limit holding on free channels with every conflict in hard_conflicts.

Reported, never gated, and labelled 'predicted, replay surrogate': predicted service coverage and missed service at
the owner's marks. Light is electric only (rung 8 does not model daylight), and floors are in lux, so a relative model
leaves every mark unknown rather than guessing a scale.

--marks is JSON Lines, one gold need episode each, following EvalRecord.R_eval:
    {"episode_id": str, "track_id": str, "band": "fine"|"casual", "start": s, "end": s,
     "R_eval": {"surface_id": str, "placement_sd": cm, "source": "gold_mark"}}
A mark names its surface; rung 8 v1's blocks have no room geometry, so a mark's outline is not used yet.

Standard library only. It re-runs the logged policies in memory, opens no network and writes nothing.
Exit 0: scored pass, or a preview with no structural failure. 1: a failure. 2: refused. 3: insufficient evidence.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check  # noqa: E402
import coordinator  # noqa: E402
import decide  # noqa: E402
import events  # noqa: E402
import incumbent  # noqa: E402
import light_adapter  # noqa: E402

LABEL = "predicted, replay surrogate"
DEFINITIONS = "need-raise-v0"
CARRIED = ("requested_scene", "applied_scene", "recipients", "predicted", "targets", "hard_conflicts")


def coordinator_entry(header):
    return next((p for p in header["policies"] if p["module"] == "coordinator"), None)


def open_slot_lines(gate):
    rows = ["%s: proposed %s; %s" % (name, json.dumps(slot["proposed"]), slot["note"])
            for name, slot in sorted(gate["slots"].items()) if slot["status"] == "open"]
    return ["open slots in %s: %d" % (gate["gate"], len(rows))] + ["  " + row for row in rows]


def refusals(header, entry, gate, light):
    """Why this can't be a scored run; empty when it can."""
    reasons = []
    if gate["open"]:
        reasons.append("%d open slot(s) in the gate file: %s" % (len(gate["open"]), ", ".join(gate["open"])))
    if header["synthetic"]:
        reasons.append("synthetic: plumbing only, never a gate")
    if light.fake:
        reasons.append("fake light model: plumbing only, never a gate")
    logged = entry["config"]["gate"]
    if logged["sha256"] != gate["sha256"]:
        reasons.append("the log was run under another gate file: rerun it")
    comp, slots = entry["config"]["compensation"], gate["slots"]
    if (comp["option"], comp["form"]) != (slots["compensation_option"], slots["compensation_form"]):
        reasons.append("the log ran compensation %s, a counterfactual: the gate's is %s-%s"
                       % (coordinator.option_name(comp), slots["compensation_option"], slots["compensation_form"]))
    if slots["surrogate_definitions"] != DEFINITIONS:
        reasons.append("this tool only computes the %s definitions of the surrogates" % DEFINITIONS)
    return reasons


def replay_and_time(log_path, limit_ms):
    """(check result, the slowest handle() in ms, calls). A replay that goes over the limit runs once more and each call
    keeps its faster time, so one scheduling hiccup on a busy PC does not read as a slow decision."""
    def one_round():
        spans = []

        def wrap(policy):
            if policy.id != "decide":
                return
            inner = policy.handle

            def timed(event):
                start = time.perf_counter()
                out = inner(event)
                spans.append(time.perf_counter() - start)
                return out
            policy.handle = timed
        return check.check(log_path, on_policy=wrap), spans

    result, spans = one_round()
    if spans and max(spans) * 1000 >= limit_ms:
        _, again = one_round()
        spans = [min(a, b) for a, b in zip(spans, again)]
    return result, (max(spans) * 1000 if spans else 0.0), len(spans)


class Timeline:
    """Each arm's scene through the log, with held bulbs pinned at read-back in both arms. The candidate's comes from its
    applied scenes, the incumbent's from den_lighting re-run beside the log (incumbent.Shadow)."""

    def __init__(self, header, log):
        self.spans, self.corrections, self.need_changes, self.episodes = [], [], [], []
        den = incumbent.Shadow(header)
        candidate, people, phase = {}, {}, {}
        state = {"gaming": False, "occupancy": "unknown", "holds": {}, "limited": []}
        for n, event in enumerate(log):
            den.feed(event)
            kind, data, t = event["kind"], event["data"], event["t"]
            if kind == "mode":
                state.update(gaming=data["gaming"], holds={h["entity_id"]: h["pinned"] for h in data["holds"]})
            elif kind == "occupancy":
                state["occupancy"] = data["value"]
            elif kind == "correction":
                self.corrections.append(t)
            elif kind == "person_state":
                self._need(event, people, phase, state)
            elif kind == "decision" and event["source"] == "decide":
                before = dict(candidate)
                for bulb, entry in data["applied_scene"].items():
                    if entry["status"] != "superseded":
                        candidate[bulb] = entry["readback"]
                state["limited"] = [row["point_id"] for row in data.get("hold_limited", [])]
                if data["trigger"] == "person_state change":
                    self.need_changes.append({"t": t, "before": before, "after": dict(candidate)})
            if n + 1 < len(log) and log[n + 1]["t"] == t:
                continue
            scenes = {"candidate": {**candidate, **state["holds"]},
                      "incumbent": {b: den.wants(b, t) for b in events.LIGHTS}}
            self.spans.append({"start": t, "end": t, "scenes": scenes, "gaming": state["gaming"],
                               "occupied": state["occupancy"] == "on", "limited": list(state["limited"])})
        for one, later in zip(self.spans, self.spans[1:]):
            one["end"] = later["start"]
        self.occupied_h = sum(s["end"] - s["start"] for s in self.spans if s["occupied"] and not s["gaming"]) / 3600.0

    def _need(self, event, people, phase, state):
        """A need episode: a person's move into engaged, from none or starting, while occupied and out of a game."""
        data, t = event["data"], event["t"]
        track, now = data["track_id"], data["focused_engagement"]["state"]
        if now == "engaged" and phase.get(track, "none") in ("none", "starting"):
            if state["occupancy"] == "on" and not state["gaming"]:
                self.episodes.append({"t": t, "track": track, "person": data})
        phase[track] = now


def anticipation(timeline, light, slots):
    """need-raise-v0: an episode counts when, within the window after its move into engaged and before any correction,
    a decision the person's need triggered raised the predicted light at their surface by at least the minimum. den_lighting
    makes no decision on a person's state, so it scores 0 by construction. (rate, hits, episodes, unknown)"""
    hits, unknown = 0, 0
    for episode in timeline.episodes:
        surface = coordinator.surface_of(episode["person"], light)
        first_touch = next((c for c in timeline.corrections if c >= episode["t"]), float("inf"))
        for change in timeline.need_changes:
            if not episode["t"] <= change["t"] <= episode["t"] + slots["anticipation_window_s"] or change["t"] > first_touch:
                continue
            was, now = light.predict(change["before"])[surface], light.predict(change["after"])[surface]
            if was is None or now is None:
                unknown += 1
                break
            if now > was and (was <= 0 or now / was - 1 >= slots["anticipation_rise_min"]):
                hits += 1
                break
    total = len(timeline.episodes)
    return (hits / total if total else None), hits, total, unknown


def stability(changes, slots):
    """need-raise-v0: the share of need-triggered scene changes that stood at least the dwell before the next one. An arm
    with none scores 1, which is den_lighting by construction. (rate, stable, changes)"""
    total = len(changes)
    unstable = sum(later["t"] - one["t"] < slots["stability_dwell_s"] for one, later in zip(changes, changes[1:]))
    return (1.0 - unstable / total if total else 1.0), total - unstable, total


def serves(light, scene, surface, floor, fixed):
    """The owner's statistic at one instant: at least area_share of the visible patch at the floor, with at least
    visible_share_min of the patch visible. None without a level, or with too little of the patch visible."""
    known = {b: s for b, s in scene.items() if s is not None}
    if any(s["on"] and (s.get("brightness") is None or s.get("kelvin") is None) for s in known.values()):
        return None
    patch = light.patch(known, surface)
    if patch is None:
        return None
    visible = [value for value in patch if value is not None]
    if not patch or len(visible) / len(patch) < fixed["visible_share_min"]:
        return None
    return sum(value >= floor for value in visible) / len(visible) >= fixed["area_share"]


def service(timeline, light, marks, fixed, arm):
    """Per mark: served, missed, or unknown with its reason. Served: the statistic stands, uncorrected, for the quiet window,
    or for the whole of a shorter mark. Game time leaves the comparison. (counts by outcome, minutes hold-limited at the marks)"""
    counts = {"served": 0, "missed": 0, "unknown": {}}
    limited = 0.0

    def unknown(reason):
        counts["unknown"][reason] = counts["unknown"].get(reason, 0) + 1

    for mark in marks:
        region, floor = mark["R_eval"], fixed["service_floor_lx"][mark["band"]]
        if region["placement_sd"] > fixed["tau_r_cm"]:
            unknown("geometry")
            continue
        if region["surface_id"] not in light.surfaces:
            unknown("no_surface")
            continue
        if light.unit != "lx":
            unknown("relative_model")
            continue
        spans = [s for s in timeline.spans if s["end"] > mark["start"] and s["start"] < mark["end"] and not s["gaming"]]
        limited += sum(min(s["end"], mark["end"]) - max(s["start"], mark["start"]) for s in spans
                       if region["surface_id"] in s["limited"])
        if not spans:
            unknown("game_or_outside_the_log")
            continue
        needed = min(fixed["quiet_window_s"], mark["end"] - mark["start"])
        served, run, blind = False, None, False
        for span in spans:
            state = serves(light, span["scenes"][arm], region["surface_id"], floor, fixed)
            blind = blind or state is None
            start, end = max(span["start"], mark["start"]), min(span["end"], mark["end"])
            run = (run if run is not None else start) if state else None
            for touch in timeline.corrections:
                if run is not None and start <= touch < end:
                    served = served or touch - run >= needed
                    run = touch  # a correction restarts the quiet window
            if served or (run is not None and end - run >= needed):
                served = True
                break
        if served:
            counts["served"] += 1
        elif blind:
            unknown("no_level")
        else:
            counts["missed"] += 1
    return counts, limited / 60.0


def mean_light(timeline, light, arm):
    """{surface: mean predicted light} over occupied time out of a game, for one arm, or None without a level."""
    total, seconds = dict.fromkeys(light.surfaces, 0.0), 0.0
    for span in timeline.spans:
        if not span["occupied"] or span["gaming"] or span["end"] <= span["start"]:
            continue
        seen = light.predict({b: s for b, s in span["scenes"][arm].items() if s is not None})
        seconds += span["end"] - span["start"]
        for surface in total:
            total[surface] = None if total[surface] is None or seen[surface] is None else (
                total[surface] + seen[surface] * (span["end"] - span["start"]))
    return {s: None if v is None or not seconds else round(v / seconds, 1) for s, v in total.items()}


def structural(log, light, slots):
    """{name: (ok, text)}: the record fields, exclusions, steps and hard constraints, read back from the log."""
    decisions = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
    missing, moved, broken, unchecked, over, unlogged = 0, 0, 0, 0, 0, 0
    for d in decisions:
        missing += any(key not in d for key in CARRIED) or set(d["applied_scene"]) != set(d["requested_scene"])
        result = {b: e["readback"] for b, e in d["applied_scene"].items() if e["readback"] is not None}
        if coordinator.eye_rows(d["inputs"], result, light, slots) != d["predicted"]["eyes"]:
            moved += 1
        held = {h["entity_id"] for h in d["inputs"]["hold_set"]["held"]}
        for entry in d["applied_scene"].values():
            for step in entry["steps"]:
                if step.get("sent_at") is None:
                    broken += 1
                elif not (step["checks"]["revision_current"] or step["checks"]["fresh_check"]) or not step["checks"]["not_held"]:
                    unchecked += 1
        conflicts = {(c["held_channel"], c["track"]) for c in d["hard_conflicts"]}
        for row in d["predicted"]["eyes"]:
            if row["excluded"] and row["channel"] not in held:
                over += 1
            elif row["excluded"] and (row["channel"], row["track_id"]) not in conflicts:
                unlogged += 1
    conflicts = sum(len(d["hard_conflicts"]) for d in decisions)
    return {
        "records": (not missing, "%d of %d decisions lack a requested or applied scene or recipients" % (missing, len(decisions))
                    if missing else "all %d decisions carry requested and applied scenes and recipients" % len(decisions)),
        "exclusions": (not moved, "%d decision(s) do not reproduce predicted.eyes" % moved if moved else
                       "reproduced from state_ids, field_support, the envelope version and t_exec"),
        "steps": (not (broken or unchecked), "%d step(s) sent without passing the check" % (broken + unchecked)
                  if broken or unchecked else "every step logged as sent passed its check"),
        "hard constraints": (not (over or unlogged),
                             "%d free channel row(s) over the glare limit, %d conflict(s) not logged" % (over, unlogged)
                             if over or unlogged else "free channels hold the glare limit; %d conflict(s) logged" % conflicts),
    }


def read_marks(path):
    marks = []
    for number, line in enumerate(Path(path).read_bytes().decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            mark = json.loads(line)
            if mark["band"] not in ("fine", "casual") or mark["end"] <= mark["start"] or "surface_id" not in mark["R_eval"]:
                raise ValueError("needs band fine or casual, end after start, R_eval.surface_id")
            float(mark["R_eval"]["placement_sd"])
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError("%s line %d: %s" % (path, number, error)) from None
        marks.append(mark)
    return marks


def report(log_path, marks_path=None, preview=False, gate_path=coordinator.GATE_FILE):
    """(lines, exit code)."""
    try:
        header, log = events.read(log_path)
    except (OSError, ValueError) as error:
        return ["refused: unreadable log: %s" % error], 2
    entry = coordinator_entry(header)
    if entry is None:
        return ["refused: no coordinator in this log: run it with --policies need incumbent coordinator"], 2
    gate = coordinator.load_gate(gate_path)
    gate_now = coordinator.gate_config(gate)
    try:
        policy = coordinator.make_policy(entry["config"])
    except ValueError as error:
        return ["refused: the coordinator refused its logged config: %s" % error], 2
    light, config = policy.light, entry["config"]
    reasons = refusals(header, entry, gate_now, light)
    if reasons and not preview:
        return ["refused: not a scored run:"] + ["  " + r for r in reasons] + ["  --preview runs on the proposals; it is never a gate"], 2
    slots = config["gate"]["slots"]
    fixed = {name: item["value"] for name, item in gate["fixed"].items()}
    lines = ["rung 9 on %s" % Path(log_path).name]
    if preview:
        lines.append("preview: logged proposals, not the owner's numbers, so no verdict and never a gate")
    lines.append("both arms through light model %s (%s, unit %s; backend %s)" % (
        light.version, "fake: plumbing only, never a gate" if light.fake else "real", light.unit, light_adapter.BACKEND))
    lines.append("comparator: frozen %s re-run beside the log; candidate %s, compensation %s" % (
        next(p["version"] for p in header["policies"] if p["id"] == "incumbent"), coordinator.POLICY,
        coordinator.option_name(config["compensation"])))
    lines.append("stand-ins, logged in the header: seed Look %s, level factor 1, mired_shift 0, contrast_shift 0" % decide.LOOK)

    failures = []

    def gate_line(name, ok, text):
        lines.append("%s: %s (%s)" % (name, "OK" if ok else "FAIL", text))
        if not ok:
            failures.append(name)

    result, slowest, calls = replay_and_time(log_path, fixed["max_decision_ms"])
    gate_line("replay", result["ok"], result["reason"])
    gate_line("decision time", slowest < fixed["max_decision_ms"], "slowest of %d calls %.1f ms, limit %d ms" % (
        calls, slowest, fixed["max_decision_ms"]))
    for name, (ok, text) in structural(log, light, slots).items():
        gate_line(name, ok, text)

    timeline = Timeline(header, log)
    rate, hits, episodes, unknown = anticipation(timeline, light, slots)
    den_decisions = [e for e in log if e["kind"] == "decision" and e["source"] == "incumbent"]
    den_need = [e for e in den_decisions if e["data"]["trigger"] == "person_state change"]
    insufficient = []
    if not episodes:
        insufficient.append("no need episodes")
    if unknown:
        insufficient.append("%d episode(s) with no level to predict from" % unknown)
    floor = slots["anticipation_floor"]
    text = "candidate %d of %d" % (hits, episodes) + ("" if rate is None else " = %.3f" % rate)
    text += ", floor %s; incumbent %d of %d by construction" % (floor, len(den_need), episodes)
    if rate is None or unknown:
        lines.append("anticipation (replay surrogate): insufficient (%s)" % text)
    else:
        gate_line("anticipation (replay surrogate)", rate >= floor, text)
    steady, stable, changes = stability(timeline.need_changes, slots)
    gate_line("stability (replay surrogate)", steady >= slots["stability_floor"], "candidate %d of %d need-driven changes stood = %.3f, floor %s; "
              "incumbent 1.000 by construction" % (stable, changes, steady, slots["stability_floor"]))
    if den_need:
        gate_line("incumbent by construction", False, "den_lighting made %d person-triggered decision(s)" % len(den_need))

    for arm in ("candidate", "incumbent"):
        lines.append("mean predicted light (%s; reported, not gated; electric only): %s %s %s" % (
            LABEL, arm, json.dumps(mean_light(timeline, light, arm), sort_keys=True), light.unit))
    if marks_path:
        marks = read_marks(marks_path)
        for arm in ("candidate", "incumbent"):
            counts, limited = service(timeline, light, marks, fixed, arm)
            lines.append("predicted service coverage (%s; reported, not gated): %s %d of %d marks served, %d missed, unknown %s" % (
                LABEL, arm, counts["served"], len(marks), counts["missed"], json.dumps(counts["unknown"], sort_keys=True)))
            per_h = counts["missed"] / timeline.occupied_h if timeline.occupied_h else None
            lines.append("missed service (%s; reported, not gated): %s %s per occupied hour over %.2f h" % (
                LABEL, arm, "n/a" if per_h is None else "%.2f" % per_h, timeline.occupied_h))
            if arm == "candidate":
                lines.append("hold_limited at the marks (%s; reported, not gated): %s" % (
                    LABEL, "null: only under P1 to P3" if config["compensation"]["option"] == "P0" else "%.1f min" % limited))
    else:
        lines.append("predicted service (%s; reported, not gated): no --marks given" % LABEL)
    lines.append("not admitted: the hold fixtures (hold_fixtures.py) wait for the owner's compensation decision")
    if header["synthetic"]:
        lines.append("synthetic: plumbing only, never a gate")
    if preview:
        lines.append("verdict: none (preview): %s" % ("; ".join(reasons) if reasons else "no reason to refuse a scored run"))
        return lines, 1 if failures else 0
    if failures:
        lines.append("verdict: FAIL: %s" % ", ".join(failures))
        return lines, 1
    if insufficient:
        lines.append("verdict: insufficient: %s" % "; ".join(insufficient))
        return lines, 3
    lines.append("verdict: PASS on rung 9's replay gate; real-light use is rung 11's")
    return lines, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", nargs="?", metavar="LOG", help="a run.py log.jsonl with the coordinator in it")
    parser.add_argument("--marks", help="the owner's gold need episodes, JSON Lines (see above)")
    parser.add_argument("--preview", action="store_true", help="run on the logged proposals; no verdict, never a gate")
    parser.add_argument("--slots", action="store_true", help="list the gate file's open slots and exit")
    args = parser.parse_args(argv)
    if args.slots:
        print("\n".join(open_slot_lines(coordinator.load_gate())))
        return 0
    if not args.log:
        parser.error("give a LOG, or --slots")
    try:
        lines, code = report(args.log, args.marks, args.preview)
    except ValueError as error:
        sys.exit("score.py: %s" % error)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
