"""probe_staged_schedule_shortcut: do development and held-out staged takes share one timetable?

What it checks
  * Reads tools/den-take/scripts/*.json (den-take-script-1) from the branch and takes N1's split
    rule as written in its plan: within each script name, sessions alternate dev/held-out in
    recording order, the first of each script is development.
  * Simulates the planned batch (8 takes, hard-a/hard-b alternating) with take.load_script and
    R's make_take(script=...), reads back each take.json's stage cues, and checks whether every
    held-out take's (case, at_s) timetable equals its development twin's.
  * Scores a "clock only" predictor fitted on the development takes alone (case = the scripted case
    at time-since-Begin, shifted by a fixed 3 s reaction time) on the held-out takes' scripted
    schedule: the share of usable seconds it assigns the scripted case, and its engaged/not-engaged
    agreement, within the same script and across scripts.

How to run
  python3 -B probe_staged_schedule_shortcut.py <den-take-script checkout root>
Synthetic data only, temp dirs only.
"""
import json, shutil, sys, tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import check  # noqa: E402
import take  # noqa: E402
import test_den_take as T  # noqa: E402

QUIET = {"idle_seated", "idle_standing", "lights_down"}
settings = take.Settings(slate_s=check.load_method()["slate_length_s"])
tmp = Path(tempfile.mkdtemp(prefix="probe-sched-")).resolve()
try:
    names = sorted(p.stem for p in (ROOT / "tools" / "den-take" / "scripts").glob("*.json"))
    takes = []
    for i in range(8):
        name = names[i % len(names)]
        staged = take.load_script(name, settings)
        folder = T.make_take(tmp, activity=float(staged["end_s"]) + 1, commissioning=False, script=name)
        record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
        begin = next(c["pc_mono_s"] for c in record["cues"] if c["cue"] == "begin")
        table = [(c["case"], round(c["pc_mono_s"] - begin, 6)) for c in record["cues"] if c["cue"] == "stage"]
        takes.append((name, table, staged["end_s"]))
    splits, count = [], {}
    for name, table, end_s in takes:
        count[name] = count.get(name, 0) + 1
        splits.append("dev" if count[name] % 2 == 1 else "held-out")

    def case_at(table, t):
        current = None
        for case, at in table:
            if at <= t:
                current = case
        return current

    same = all(table == next(tb for (n2, tb, _), s2 in zip(takes, splits) if n2 == name and s2 == "dev")
               for (name, table, _), s in zip(takes, splits) if s == "held-out")
    print(f"RESULT scripts {names}; splits {list(zip([t[0] for t in takes], splits))}")
    print(f"RESULT every held-out timetable equals its dev twin's: {same}")
    for fit_name in names:
        dev_table = next(tb for (n, tb, _), s in zip(takes, splits) if n == fit_name and s == "dev")
        for name, table, end_s in [(n, tb, e) for (n, tb, e), s in zip(takes, splits) if s == "held-out"][:2]:
            secs = range(3, int(end_s))
            hits = sum(case_at(dev_table, t - 3) == case_at(table, t - 3) for t in secs)
            eng = sum((case_at(dev_table, t - 3) not in QUIET) == (case_at(table, t - 3) not in QUIET) for t in secs)
            print(f"RESULT clock-only predictor fit on dev {fit_name} -> held-out {name}: "
                  f"case agreement {100 * hits / len(secs):.1f}%, engaged agreement {100 * eng / len(secs):.1f}%")
    print("FAIL: within a script, held-out takes replay the development take's exact schedule"
          if same else "PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
