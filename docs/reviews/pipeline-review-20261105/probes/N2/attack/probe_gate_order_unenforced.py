"""Probe: does score.py --split held_out refuse a gate that was edited after the development step?

Checks, on synthetic data only (the test module's World builder, in a temp folder):
  1. baseline: a need log whose only flaw is a false-fine share of ~0.10 (above the owner's fixed 0.033)
     gives not_feasible under the committed-style gate;
  2. the gate's FIXED value cap.false_fine is edited to 0.2 in a working copy that is DIRTY in git
     (uncommitted): does the held-out run refuse, or give a verdict?
  3. the same edit committed with a commit time AFTER every held-out ask line (the plan's step 3 says the
     commit must come before the first held-out ask line): refused, or a verdict?
  4. what the printed summary / RESULTS.md line says about the dirty or late gate.

Run:  python3 -B probe_gate_order_unenforced.py <root of the den-n2 checkout>
Needs git on PATH (used only inside the temp folder). Prints PASS/FAIL/RESULT lines.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
import score  # noqa: E402


def records_with_early_fine(gold_ivs, a, b, delay=1.0):
    """The test suite's perfect follower, except a casual task reads fine for its first 12 s."""
    def at(t):
        for x in gold_ivs:
            if x["on"] + delay <= t < x["off"] + 1:
                state = "starting" if t < x["on"] + delay + 5 else "engaged"
                if x["band"] == "casual" and t < x["on"] + 12:
                    return state, T.LEVEL["fine"]
                return state, T.LEVEL[x["band"]]
        for x in gold_ivs:
            if x["off"] + 1 <= t < x["off"] + 21:
                return "ending", T.LEVEL["none"]
        return "none", T.LEVEL["none"]
    times = {round(a + 1 + 2 * i, 6) for i in range(int((b - a) / 2))}
    for x in gold_ivs:
        times |= {x["on"] + delay, x["on"] + delay + 5, x["off"] + 1, x["off"] + 21, x["on"] + 12}
    return [T.rec(t, *at(t)) for t in sorted(t for t in times if a <= t < b)]


def git(repo, *args, env=None):
    full = dict(os.environ, **(env or {}))
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=probe", "-c", "user.email=probe@example.invalid",
                           *args], capture_output=True, text=True, env=full, check=True).stdout


def main():
    ok = True
    with tempfile.TemporaryDirectory(prefix="n2-probe-gate-") as tmp:
        tmp = Path(tmp)
        world = T.World(tmp / "w", T.FOUR_DAYS)
        golds, _ = T.layout()
        gold_ivs = [T.iv(a, b, band, case) for a, b, band, case in golds]
        for take, _, _ in world.takes:
            world.write_need(take, gold_ivs, records=records_with_early_fine(gold_ivs, 0.0, T.LAYOUT_END))
        repo = tmp / "gate-repo"
        repo.mkdir()
        git(repo, "init", "-q")
        gate = repo / "GATE-V1.md"
        gate.write_text(T.gate_text(T.filled()), encoding="utf-8")
        git(repo, "add", "GATE-V1.md")
        git(repo, "commit", "-q", "-m", "gate filled", env={"GIT_COMMITTER_DATE": "2026-11-20T00:00:00Z",
                                                            "GIT_AUTHOR_DATE": "2026-11-20T00:00:00Z"})
        report, path = world.run("held_out", gate_path=gate)
        ff = report["metrics"]["false_fine"]
        print(f"RESULT 1 committed gate (cap.false_fine 0.033): verdict={report['verdict']} false_fine={ff['value']} "
              f"interval={ff['interval']} status={ff['status']} gate={report['gate']}")
        if report["verdict"] != "not_feasible":
            print("FAIL setup: expected not_feasible from the false-fine flaw alone")
            ok = False

        # 2. a dirty working copy with a FIXED value relaxed
        gate.write_text(gate.read_text(encoding="utf-8").replace("cap.false_fine = 0.033", "cap.false_fine = 0.2"),
                        encoding="utf-8")
        try:
            report2, path2 = world.run("held_out", gate_path=gate)
            print(f"RESULT 2 dirty gate, fixed cap.false_fine edited to 0.2: verdict={report2['verdict']} "
                  f"gate.dirty={report2['gate']['dirty']} reasons={report2['reasons']}")
            if report2["verdict"] == "feasible":
                print("FAIL a held-out run on an uncommitted gate with a relaxed FIXED value scores feasible "
                      "(no refusal)")
                ok = False
            text = "\n".join(score.summary(report2, path2))
            line = score.results_line(report2, path2)
            print("RESULT 4 RESULTS.md line: " + line)
            if "dirty" not in text and "dirty" not in line:
                print("FAIL neither the printed summary nor the RESULTS.md line mentions the dirty gate")
                ok = False
        except score.Refused as exc:
            print(f"PASS dirty gate refused: {exc}")

        # 3. the edit committed after every held-out ask line
        git(repo, "commit", "-q", "-am", "relax", env={"GIT_COMMITTER_DATE": "2026-12-01T00:00:00Z",
                                                       "GIT_AUTHOR_DATE": "2026-12-01T00:00:00Z"})
        asked = max(json.loads(x)["asked_unix"] for take in world.held_out()
                    for x in (world.n2 / "asks" / f"{take}.jsonl").read_text().splitlines())
        try:
            report3, _ = world.run("held_out", gate_path=gate)
            commit_unix = report3["gate"]["commit_unix"]
            print(f"RESULT 3 gate committed at {commit_unix}, after the last held-out ask at {asked}: "
                  f"verdict={report3['verdict']} dirty={report3['gate']['dirty']}")
            if commit_unix > asked and report3["verdict"] == "feasible":
                print("FAIL a gate committed after the held-out ask lines (plan section 7 step 3) is accepted "
                      "and scores feasible")
                ok = False
        except score.Refused as exc:
            print(f"PASS late gate refused: {exc}")
    print("RESULT overall:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
