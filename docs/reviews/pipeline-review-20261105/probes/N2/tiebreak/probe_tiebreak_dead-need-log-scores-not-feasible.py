"""Tiebreak probe for finding dead-need-log-scores-not-feasible (N2, den-n2 tools/den-n2/score.py).

What it checks, on synthetic data in temp folders only (den-n2's own test World, its fake check.py that
stands in for den-core's byte-identical replay check; nothing contacts any host):

  S  "stale replay": every held-out take's replay log was made from an EARLIER, partial copy of its ask
     file (the ask step was interrupted at t_s 600 s, the replay was made, then the ask step was resumed and
     the full ask file written). The log's header `sources` records the partial file's sha256, which differs
     from asks/<take>.jsonl's. den-core's check.py replays from the log's own logged inputs, so it would
     pass such a log. Does score.py refuse or warn, or does it give a verdict?
  D  "need ignored every reply": the log holds inputs, but need never leaves 'none' (one none record).
     Same question.
  C  control: the World as built (logs follow the gold) gives feasible.

It also greps score.py for any comparison of a log header's `sources` with the ask file.

Run:  python3 -B probe_tiebreak_dead-need-log-scores-not-feasible.py <root of the den-n2 checkout>
"""
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
import score  # noqa: E402


def rewrite_log(world, take, records, sources):
    log = world.n2 / "replay" / take / "seg-1" / "log.jsonl"
    lines = [json.loads(x) for x in log.read_bytes().decode("ascii").split("\n") if x]
    header = dict(lines[0], sources=sources)
    started = T.BASE_UNIX + dict((t, d) for t, _, d in world.takes)[take] * T.DAY
    t0 = int(started // 1)
    events = [{"schema": "den-event-1", "t": t0 + r.t, "seq": i, "kind": "person_state", "source": "need",
               "data": T.person_state(t0 + r.t, r, t0)} for i, r in enumerate(records)]
    world.write_jsonl(log, [header] + events)


def show(label, world):
    try:
        report, _ = world.run("held_out")
    except score.Refused as exc:
        print(f"PASS [{label}] refused: {exc}")
        return None
    s = report["service"]
    print(f"RESULT [{label}] verdict={report['verdict']} replied={s['replied']}/{s['requests']} "
          f"rows={s['rows']} missed_onsets={report['excluded']['missed_onsets']}/{len(report['onsets'])} "
          f"diagnosis={report['diagnosis']}")
    return report["verdict"]


def main():
    golds, _ = T.layout()
    gold_ivs = [T.iv(a, b, band, case) for a, b, band, case in golds]
    with tempfile.TemporaryDirectory(prefix="n2-tb-ctrl-") as tmp:
        world = T.World(Path(tmp), T.FOUR_DAYS)
        print(f"RESULT [C control] verdict={show('C control', world)}")
    with tempfile.TemporaryDirectory(prefix="n2-tb-stale-") as tmp:
        world = T.World(Path(tmp), T.FOUR_DAYS)
        for take in world.held_out():
            asks = (world.n2 / "asks" / f"{take}.jsonl").read_bytes()
            partial = b"".join(line + b"\n" for line in asks.split(b"\n")
                               if line and json.loads(line)["r"] < 600)
            full_sha, partial_sha = hashlib.sha256(asks).hexdigest(), hashlib.sha256(partial).hexdigest()
            # the replay of the partial file: need follows the gold up to the last input + den-core's 150 s tail
            records = T.follower(gold_ivs, 0.0, 600.0) + [T.rec(750.0, "none", T.LEVEL["none"])]
            rewrite_log(world, take, records, [{"name": f"{take}.jsonl", "sha256": partial_sha}])
        print(f"RESULT [S] header sources sha {partial_sha[:12]} vs asks file sha {full_sha[:12]} (last take)")
        v = show("S stale replay from a partial ask file", world)
        if v is not None:
            print(f"FAIL [S] score.py scored a replay made from a different ask file: verdict={v}, no refusal")
    with tempfile.TemporaryDirectory(prefix="n2-tb-dead-") as tmp:
        world = T.World(Path(tmp), T.FOUR_DAYS)
        for take in world.held_out():
            rewrite_log(world, take, [T.rec(0.5, "none")], [{"name": f"{take}.jsonl", "sha256": "0" * 64}])
        v = show("D need never leaves none", world)
        if v is not None:
            print(f"FAIL [D] verdict={v}, no refusal")
    text = (ROOT / "tools" / "den-n2" / "score.py").read_text(encoding="utf-8")
    hits = [n for n, line in enumerate(text.split("\n"), 1) if re.search(r"\bheader\b.*sources|\"sources\"", line)]
    print(f"RESULT score.py lines reading a log header's sources: {hits or 'none'}")


if __name__ == "__main__":
    main()
