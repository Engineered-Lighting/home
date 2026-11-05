"""Probe: does score.py notice when the need logs consumed none of the ask replies (a plumbing fault)?

score.py reads the ask lines (asks/<take>.jsonl) and the den-core need logs separately and never checks that
the replies it counts in `service` ever reached need: a log whose facts events were all dropped upstream
(e.g. an adapter that rejects every la-fact-log line) still passes den-core's check.py, because the replay
drops them the same way. This probe builds a held-out world whose ask lines are all valid, gold-matching
replies, but whose need logs hold (a) only the header, (b) one `none` record with no level. It prints the
verdict and whether anything refused or warned.

Run:  python3 -B probe_dead_log_not_feasible.py <root of the den-n2 checkout>
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
import score  # noqa: E402


def main():
    ok = True
    for label, recs in (("header only", None), ("one none record, no level", [T.rec(0.5, "none")])):
        with tempfile.TemporaryDirectory(prefix="n2-probe-dead-") as tmp:
            world = T.World(Path(tmp), T.FOUR_DAYS)
            golds, _ = T.layout()
            gold_ivs = [T.iv(a, b, band, case) for a, b, band, case in golds]
            for take in world.held_out():
                if recs is None:
                    world.write_need(take, gold_ivs, records=[T.rec(0.5, "none")])
                    log = world.n2 / "replay" / take / "seg-1" / "log.jsonl"
                    log.write_bytes(log.read_bytes().split(b"\n")[0] + b"\n")  # header only
                else:
                    world.write_need(take, gold_ivs, records=recs)
            try:
                report, path = world.run("held_out")
            except score.Refused as exc:
                print(f"PASS [{label}] refused: {exc}")
                continue
            s = report["service"]
            print(f"RESULT [{label}] verdict={report['verdict']} replied={s['replied']}/{s['requests']} "
                  f"rows={s['rows']} diagnosis={report['diagnosis']}")
            print(f"RESULT [{label}] reasons[:4]={report['reasons'][:4]}")
            if report["verdict"] == "not_feasible":
                print(f"FAIL [{label}] every reply was valid and gold-matching, none reached need, and the run "
                      "scores not_feasible with no refusal or warning")
                ok = False
    print("RESULT overall:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
