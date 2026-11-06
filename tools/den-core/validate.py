"""den-core validate: did den-core behave as designed on one run log, against its den_lighting baseline?

    python3 -B tools/den-core/validate.py LOG [--truth TRUTH_JSON]

LOG is a den-core run log (run.py's log.jsonl), from copied real files or a synthetic evening. This is a
consistency check of den-core's own code against den_lighting. It is not a lighting-quality gate. It prints one
line per check (OK, FAIL: <first reason, with times> or n/a: <why>), then an overall line, and exits 0 when no
check fails, 1 otherwise. A file it can't read at all is one sentence and exit 2. Times are UTC, with seconds from the log's
first event. A synthetic log also prints "synthetic: plumbing only, never a gate".

1 replay: check.check() replays LOG byte for byte.
2 need parity: the log's facts, mapped by need.sample(), go through den_lighting's own
  policy.Controller.gaming_sample (policy.py loaded unchanged by path, as test_need.py's parity test loads it).
  den_lighting's on and off moments must equal need's focused_engagement moves into engaged and back to none.
3 only the look: for every interval of run.diff() (decide's request against what den_lighting, re-run beside the
  log, wanted for the bulb, levels compared both ways), each decide decision in force for that bulb (the latest at
  the interval's start, and any during it) chose the look: candidate (the first lowest-cost candidate, decide's own
  rule) and put that look's logged scene on the bulb. OK also says, as information, how many seconds of one
  interval at most fall while need's latest person state is not reading by decide.reading(). That is nonzero
  when decide's base has nothing to request (den_lighting never saw the bulbs, as in a facts-only replay):
  decide then emits no decision when reading ends, so its last look stays requested.
4 holds: after each instant at which the incumbent's latest mode lists a hold, decide's latest decision for the
  held bulb requests its pinned scene (events.scene_matches) and lists the bulb in skipped. Instants at which
  decide makes no decision by design are left out: those of check 5, and occupancy off for less than vacancy_s.
5 quiet: as decide.py implements it, plan() returns no decision while decide's occupancy is unknown (before the
  first occupancy event, or after one that is neither on nor off), and none while the incumbent's latest mode
  says gaming. Meanwhile decide still takes in person states, modes and bulb states, and it replans at the first
  event after; a game that ends on the scene decide last requested stays silent. The check: no decide decision in
  either state. A decide decision in the same causal chain as an occupancy event may be planned before decide
  sees that event, so there either occupancy value counts.
--truth (reading-shadow-truth-1): information only; tools/reading-shadow's reading-shadow-v0 is the gate. Per
  reading block, the seconds from its cue (its start) to need's move into engaged, and from the next cue (the next
  block's start) to the move to none. The truth file's clock offsets are not applied. A truth file that is missing or the
  wrong shape is refused like any other file; one with no reading blocks, or a log with no need, reads n/a.

Standard library only and offline: it reads LOG and TRUTH_JSON and re-runs the logged policies in memory. It
contacts no host, Home Assistant, MQTT, Jetson or LA, and sends no light commands.
"""
import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check  # noqa: E402
import decide  # noqa: E402
import events  # noqa: E402
import incumbent  # noqa: E402
import need  # noqa: E402
import run  # noqa: E402

NAMES = ("replay", "need parity", "only the look", "holds", "quiet")
TRUTH_SCHEMA = "reading-shadow-truth-1"
TRUTH_LABEL = "information only; tools/reading-shadow's reading-shadow-v0 is the gate"
NOT_A_GATE = "a consistency check of den-core's own code against den_lighting, not a lighting-quality gate"
ROOTS = events.ADAPTER_SOURCES | {"runner", "plant"}  # events the runner queues; the rest derive from them


def ok(text):
    return ("OK", text)


def fail(text):
    return ("FAIL", text)


def na(text):
    return ("n/a", text)


def at(t, first):
    """A time as UTC with tenths, and seconds from the log's first event."""
    clock = datetime.fromtimestamp(round(t, 1), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-5]
    return "%sZ (%+.1f s)" % (clock, t - first)


def scene(s):
    return "missing" if s is None else ("%s@%sK" % (s["brightness"], s["kelvin"]) if s["on"] else "off")


def policy_ids(header):
    return {p["id"] for p in header["policies"]}


def logged_config(header, policy_id):
    return next(p["config"] for p in header["policies"] if p["id"] == policy_id)


def load_den_lighting():
    """den_lighting's policy.py, loaded unchanged by path as test_need.py's parity test loads it; None if absent."""
    path = incumbent.find_policy()
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("den_lighting_policy", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look their module up while the class is built
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # leave no __pycache__ in den_lighting
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module


def checked(one_check, *args):
    """One check's line. Past a line's envelope a log can hold anything, and only the check that reads it finds out, so a line of
    the wrong shape fails that check. A file it can't read at all stays one sentence and exit 2."""
    try:
        return one_check(*args)
    except events.BadFile:
        raise
    except (OSError, ValueError, *events.SHAPE_ERRORS) as error:
        return fail("a line of this log is the wrong shape for this check: %s" % events.named(error))


def replay(log_path, first):
    """1: check.check(), with the time of the first mismatching line."""
    try:
        result = check.check(log_path)
    except (OSError, ValueError) as error:
        return fail("replay refused: %s" % error)
    if result.get("unreadable"):
        raise events.BadFile(result["reason"])  # a logged light model gone bad: main refuses, as check.py does
    if result["ok"]:
        return ok(result["reason"])
    mismatch = result["first_mismatch"]
    if mismatch and mismatch["logged"] and mismatch["line"] > 1:  # line 1 is the header
        logged = json.loads(mismatch["logged"])  # events.read has already parsed every line
        return fail("%s: the logged %s from %s at %s" % (result["reason"], logged["kind"], logged["source"],
                                                         at(logged["t"], first)))
    return fail(result["reason"])


def need_moves(log):
    """[(t, 'on'|'off')]: need's focused_engagement moves into engaged and back to none."""
    moves, previous = [], "none"
    for e in log:
        if e["kind"] == "person_state" and e["source"] == "need":
            state = e["data"]["focused_engagement"]["state"]
            if state == "engaged" and previous in ("none", "starting"):
                moves.append((e["t"], "on"))
            elif state == "none" and previous in ("engaged", "ending"):
                moves.append((e["t"], "off"))
            previous = state
    return moves


def need_parity(header, log, den):
    """2: den_lighting's gaming dwell over the log's facts, mapped by need.sample(), against need's moves."""
    if "need" not in policy_ids(header):
        return na("need is not in this log")
    if den is None:
        return na("den_lighting policy.py not found above tools/den-core")
    facts = [e for e in log if e["kind"] == "facts"]
    if not facts:
        return na("no facts in this log")
    controller, expected = den.Controller(), []
    controller.set_gaming_enabled(True, facts[0]["t"])
    for e in facts:
        s, was = need.sample(e["data"]), controller.gaming
        controller.gaming_sample(s["status"], s["observed_at"], e["t"])
        if controller.gaming != was:
            expected.append((e["t"], "on" if controller.gaming else "off"))
    got, first = need_moves(log), log[0]["t"]
    words = {"on": "into engaged", "off": "back to none"}
    for n in range(max(len(expected), len(got))):
        a = expected[n] if n < len(expected) else None
        b = got[n] if n < len(got) else None
        if a == b:
            continue
        if a and b:
            return fail("move %d: den_lighting %s at %s, need %s at %s" % (n + 1, a[1], at(a[0], first),
                                                                           words[b[1]], at(b[0], first)))
        if a:
            return fail("move %d: den_lighting %s at %s, need makes no move" % (n + 1, a[1], at(a[0], first)))
        return fail("move %d: need %s at %s, den_lighting makes no move" % (n + 1, words[b[1]], at(b[0], first)))
    ons = sum(kind == "on" for _, kind in got)
    return ok("%d on and %d off moment(s) over %d facts equal den_lighting's dwell" % (ons, len(got) - ons,
                                                                                       len(facts)))


def chosen(decision):
    """The first lowest-cost candidate, decide's own choice rule; None without candidates."""
    candidates = decision.get("candidates") or []
    return min(candidates, key=lambda c: c["cost"])["scene_id"] if candidates else None


def not_reading_s(marks, start, end):
    """Seconds of [start, end) in which need's latest person state, in marks [(t, reading)], is not reading."""
    total, now, current = 0.0, start, False
    for t, value in marks:
        if t <= start:
            current = value
            continue
        if t >= end:
            break
        total += 0.0 if current else t - now
        now, current = t, value
    return total + (0.0 if current else end - now)


def only_the_look(header, log):
    """3: every run.diff() interval is decide's look: candidate, with that look's scene on the bulb."""
    if not {"incumbent", "decide"} <= policy_ids(header):
        return na("needs both incumbent and decide in this log")
    by_bulb = {}
    for e in log:
        if e["kind"] == "decision" and e["source"] == "decide":
            for bulb in e["data"]["requested_scene"]:
                by_bulb.setdefault(bulb, []).append(e)
    if not by_bulb:
        return na("decide made no decision in this log")
    looks, first = logged_config(header, "decide").get("looks", {}), log[0]["t"]
    try:
        spans = run.diff(header, log)
    except (OSError, ValueError) as error:  # check 1 reports a changed den_lighting as its FAIL
        return na("den_lighting refused this log's incumbent config: %s" % error)
    for span in spans:
        bulb, start, end = span["entity_id"], span["start"], span["end"]
        mine = by_bulb.get(bulb, [])
        in_force = [d for d in mine if d["t"] <= start][-1:] + [d for d in mine if start < d["t"] < end]
        where = "%s differs from den_lighting from %s to %s" % (bulb, at(start, first), at(end, first))
        for d in in_force:
            pick, wanted = chosen(d["data"]), d["data"]["requested_scene"][bulb]
            if (pick or "").startswith("solve:"):  # the coordinator's solved look: its candidate logs the scene it chose
                look = next(c["scene"] for c in d["data"]["candidates"] if c["scene_id"] == pick).get(bulb)
            elif (pick or "").startswith("look:"):
                look = (looks.get(pick[len("look:"):]) or {}).get("scene")
            else:
                return fail("%s; decide's decision at %s chose %s, not the look" % (where, at(d["t"], first), pick))
            if look is None or not events.scene_matches(look, wanted):
                return fail("%s; decide's decision at %s chose %s but requests %s for the bulb" % (
                    where, at(d["t"], first), pick, scene(wanted)))
    if not spans:
        return ok("decide never differs from den_lighting")
    marks = [(e["t"], decide.reading(e["data"])) for e in log if e["kind"] == "person_state" and e["source"] == "need"]
    outside, span = max(((not_reading_s(marks, s["start"], s["end"]), s) for s in spans), key=lambda pair: pair[0])
    text = "%d interval(s), each decide's labelled placeholder reading look" % len(spans)
    if outside < 0.05:
        return ok(text + "; all of them while need is reading")
    return ok(text + "; up to %.1f s of one (%s from %s) while need is not reading" % (
        outside, span["entity_id"], at(span["start"], first)))


def holds_respected(header, log):
    """4: while decide is deciding, every held bulb is pinned at its hold scene and skipped."""
    if not {"incumbent", "decide"} <= policy_ids(header):
        return na("needs both incumbent and decide in this log")
    vacancy_s, first = logged_config(header, "decide")["vacancy_s"], log[0]["t"]
    mode, latest, occupancy, vacant_since = None, {}, "unknown", None
    holds, checked = set(), set()
    for n, e in enumerate(log):
        kind, data, t = e["kind"], e["data"], e["t"]
        if kind == "mode":
            mode = data
            holds |= {(h["entity_id"], h["since"]) for h in data["holds"]}
        elif kind == "decision" and e["source"] == "decide":
            latest.update({bulb: e for bulb in data["requested_scene"]})
        elif kind == "occupancy":  # decide's own occupancy and vacancy rules
            value = data["value"] if data["value"] in ("on", "off") else "unknown"
            vacant_since = None if value != "off" else (t if occupancy != "off" else vacant_since)
            occupancy = value
        if n + 1 < len(log) and log[n + 1]["t"] == t:
            continue  # one look per instant, after all its events
        if (mode is None or not mode["holds"] or mode["gaming"] or occupancy == "unknown"
                or occupancy == "off" and (vacant_since is None or t - vacant_since < vacancy_s)):
            continue
        for h in mode["holds"]:
            bulb, pinned = h["entity_id"], h["pinned"]
            checked.add((bulb, h["since"]))
            held = "%s held at %s since %s" % (bulb, scene(pinned), at(h["since"], first))
            d = latest.get(bulb)
            if d is None:
                return fail("%s; decide has no decision for it at %s" % (held, at(t, first)))
            wanted = d["data"]["requested_scene"][bulb]
            if not events.scene_matches(pinned, wanted):
                return fail("%s; decide's latest decision, at %s, requests %s (seen at %s)" % (
                    held, at(d["t"], first), scene(wanted), at(t, first)))
            if bulb not in {s["channel"] for s in d["data"]["skipped"]}:
                return fail("%s; decide's latest decision, at %s, does not list it in skipped (seen at %s)" % (
                    held, at(d["t"], first), at(t, first)))
    if not holds:
        return na("no holds in this log")
    if not checked:
        return na("all %d hold(s) fell while decide makes no decision by design" % len(holds))
    return ok("%d hold(s) pinned and skipped by decide whenever it was deciding%s" % (
        len(checked), "" if checked == holds else "; %d more only while it was not" % len(holds - checked)))


def quiet(header, log):
    """5: no decide decision while its occupancy is unknown or the incumbent's latest mode says gaming."""
    if "decide" not in policy_ids(header):
        return na("decide is not in this log")
    occupancy, before, chain, gaming = "unknown", "unknown", False, False
    unknown_since, game_since, unknown_s, game_s, first = log[0]["t"], None, 0.0, 0.0, log[0]["t"]
    for n, e in enumerate(log):
        kind, data, t = e["kind"], e["data"], e["t"]
        if e["source"] in ROOTS:
            chain = kind == "occupancy"  # decide handles a root event only after its derived events
            before = occupancy
            if kind == "occupancy":
                occupancy = data["value"] if data["value"] in ("on", "off") else "unknown"
                if occupancy == "unknown" and before != "unknown":
                    unknown_since = t
        elif kind == "mode" and e["source"] == "incumbent":
            if data["gaming"] and not gaming:
                game_since = t
            gaming = data["gaming"]
        elif kind == "decision" and e["source"] == "decide":
            if occupancy == "unknown" and (not chain or before == "unknown"):
                return fail("decide decided at %s with occupancy unknown since %s" % (at(t, first),
                                                                                    at(unknown_since, first)))
            if gaming:
                return fail("decide decided at %s during a game (mode.gaming since %s)" % (at(t, first),
                                                                                        at(game_since, first)))
        if n + 1 < len(log):
            step = log[n + 1]["t"] - t
            unknown_s += step if occupancy == "unknown" else 0.0
            game_s += step if gaming else 0.0
    return ok("no decide decision in %.1f s of game or %.1f s of unknown occupancy" % (game_s, unknown_s))


def read_truth(path):
    """A truth file, refused as any other file is, here before any check runs: a mistyped path is not a reason to read n/a."""
    try:
        truth = events.read_json(path)
    except OSError as error:
        raise events.BadFile("cannot read %s: %s" % (path, error)) from None
    blocks = truth.get("blocks") if isinstance(truth, dict) else None
    if (not isinstance(blocks, list) or truth.get("schema") != TRUTH_SCHEMA
            or not all(isinstance(b, dict) and events.is_number(b.get("start")) and events.is_number(b.get("end")) for b in blocks)):
        raise events.BadFile("%s is not %s: it needs blocks, each with a start and an end in seconds" % (path, TRUTH_SCHEMA))
    return truth


def truth_lines(truth, truth_path, header, log):
    """--truth: per reading block, seconds from its cue to need's engaged and from the next cue to none."""
    title = "truth: " + TRUTH_LABEL
    if "need" not in policy_ids(header):
        return [title, "  n/a: need is not in this log"]
    try:
        moves = need_moves(log)
    except events.SHAPE_ERRORS as error:  # need parity's line has already said so
        return [title, "  n/a: a person state in this log is the wrong shape: %s" % events.named(error)]
    try:
        return _truth_blocks(truth, truth_path, title, moves, log[0]["t"])
    except (ValueError, OSError, *events.SHAPE_ERRORS) as error:  # a time past any clock, which at() meets
        raise events.BadFile("%s is not %s: %s" % (truth_path, TRUTH_SCHEMA, events.named(error))) from None


def _truth_blocks(truth, truth_path, title, moves, first):
    blocks = sorted(truth["blocks"], key=lambda b: b["start"])
    reading = [n for n, b in enumerate(blocks) if b.get("reading")]
    if not reading:
        return [title, "  n/a: no reading blocks in %s" % truth_path]
    lines = [title]
    for k, n in enumerate(reading):
        block = blocks[n]
        start, cue = block["start"], blocks[n + 1]["start"] if n + 1 < len(blocks) else block["end"]
        until = blocks[reading[k + 1]]["start"] if k + 1 < len(reading) else float("inf")
        on = next((t for t, kind in moves if kind == "on" and start <= t < cue), None)
        off = next((t for t, kind in moves if kind == "off" and cue <= t < until), None)
        lines.append("  %s (cue %s): %s; %s" % (
            block.get("block", "?"), at(start, first),
            "engaged %.1f s after the cue" % (on - start) if on is not None else "no move into engaged in the block",
            "none %.1f s after the next cue" % (off - cue) if off is not None
            else "no move to none before the next reading cue"))
    return lines


def report(log_path, truth_path=None):
    """(lines, passed): one line per check, the overall line, then --truth and synthetic lines."""
    try:
        header, log = events.read(log_path)
        if not log:
            raise ValueError("no events")
    except (OSError, ValueError) as error:
        results = [fail("unreadable log: %s" % error)] + [na("unreadable log")] * (len(NAMES) - 1)
        header, log = None, None
    else:
        truth = read_truth(truth_path) if truth_path is not None else None
        results = [checked(replay, log_path, log[0]["t"]), checked(need_parity, header, log, load_den_lighting()),
                   checked(only_the_look, header, log), checked(holds_respected, header, log), checked(quiet, header, log)]
    lines = ["%s: %s" % (name, "OK (%s)" % text if status == "OK" else "%s: %s" % (status, text))
             for name, (status, text) in zip(NAMES, results)]
    counts = {status: sum(r[0] == status for r in results) for status in ("OK", "FAIL", "n/a")}
    passed = counts["FAIL"] == 0
    if passed:
        lines.append("overall: OK: no check failed (%d OK, %d n/a); %s" % (counts["OK"], counts["n/a"], NOT_A_GATE))
    else:
        lines.append("overall: FAIL: %d of %d checks failed; %s" % (counts["FAIL"], len(NAMES), NOT_A_GATE))
    if truth_path is not None:
        lines += truth_lines(truth, truth_path, header, log) if log else ["truth: %s" % TRUTH_LABEL, "  n/a: unreadable log"]
    if header and header.get("synthetic"):
        lines.append("synthetic: plumbing only, never a gate")
    return lines, passed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", metavar="LOG", help="a den-core run log (run.py's log.jsonl)")
    parser.add_argument("--truth", metavar="TRUTH_JSON", help="a reading-shadow-truth-1 file (information only)")
    args = parser.parse_args(argv)
    try:
        events.read_run(args.log)  # refused here, before any check, in the same words as check.py and score.py
        lines, passed = report(args.log, args.truth)
    except events.BadFile as error:
        print(error, file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print("validate.py: %s" % error, file=sys.stderr)
        return 2
    except Exception as error:
        print(events.stopped("validate.py", error), file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
