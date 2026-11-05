"""Verifier probe for finding dead-need-log-scores-not-feasible (N2, den-n2 score.py).

Checks, on synthetic data in temp folders only:
  A. with den-core's REAL runner/check.py (read-only import from a den-core checkout, no bytecode written):
     a run with zero adapter inputs raises "no inputs", and check() on a header-only log raises instead of
     returning ok -- so the finding's "header only" variant cannot reach score.py through the real check.py;
  B. a held-out world (den-n2's own test World, fake check) whose need logs hold one 'none' person_state per
     segment while every ask line is a valid, gold-matching reply (need ignored every reply, a log the real
     check.py would replay identically): does score.py refuse, warn, or give a verdict?
  C. the same dead logs on the development split: what development prints (would the dead pipeline show
     there first?).

Run:  python3 -B verify_dead-need-log-scores-not-feasible.py <den-n2 checkout root> <den-core checkout root>
"""
import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
CORE = Path(sys.argv[2]).resolve() / "tools" / "den-core"


def part_a():
    sys.path.insert(0, str(CORE))
    import events  # noqa: E402
    import runner  # noqa: E402
    import check  # noqa: E402
    need = runner.import_module("need").make_policy(None)
    try:
        runner.run([], [need], tick_s=2.0)
        print("RESULT A1 runner.run with no inputs: returned a log")
    except ValueError as exc:
        print(f"RESULT A1 runner.run with no inputs raises ValueError: {exc}")
    with tempfile.TemporaryDirectory(prefix="n2-verify-dead-a-") as tmp:
        header = {"schema": events.LOG_SCHEMA, "sources": [], "policies": [runner.describe(need)], "tick_s": 2.0,
                  "tail_s": 150.0, "plant": False, "shown": "incumbent", "synthetic": True,
                  "assume_occupied": False, "actuation": "synthetic"}
        log = Path(tmp) / "log.jsonl"
        log.write_bytes((events.dumps(header) + "\n").encode("ascii"))
        try:
            result = check.check(log)
            print(f"RESULT A2 real check.py on a header-only log returns {result}")
        except Exception as exc:  # noqa: BLE001
            print(f"RESULT A2 real check.py on a header-only log raises {type(exc).__name__}: {exc} "
                  "(score.py does not catch it: a traceback, no verdict)")
    sys.path.remove(str(CORE))
    for name in ("events", "runner", "check", "need"):
        sys.modules.pop(name, None)


def part_bc():
    sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
    import test_den_n2 as T  # noqa: E402
    import score  # noqa: E402
    with tempfile.TemporaryDirectory(prefix="n2-verify-dead-b-") as tmp:
        world = T.World(Path(tmp), T.FOUR_DAYS)
        golds, _ = T.layout()
        gold_ivs = [T.iv(a, b, band, case) for a, b, band, case in golds]
        for take, _, _ in world.takes:
            world.write_need(take, gold_ivs, records=[T.rec(0.5, "none")])
        try:
            report, _ = world.run("held_out")
            s = report["service"]
            print(f"RESULT B held_out: verdict={report['verdict']} replied={s['replied']}/{s['requests']} "
                  f"rows={s['rows']} missed_onsets={report['excluded']['missed_onsets']}/"
                  f"{len(report['onsets'])} diagnosis={report['diagnosis']}")
            print("FAIL B no refusal or warning: every reply valid and gold-matching, need never left none")
        except score.Refused as exc:
            print(f"PASS B refused: {exc}")
        dev, path = world.run("development")
        print(f"RESULT C development: onset={dev['metrics']['onset']['value']} D={dev['service']['D']} "
              f"missed_onsets={dev['excluded']['missed_onsets']}/{len(dev['onsets'])} "
              f"fine_recall={dev['metrics']['fine_recall']['value']} onset_rule.D={dev['onset_rule']['D']}")


if __name__ == "__main__":
    part_a()
    part_bc()
