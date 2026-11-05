"""mutate_score: targeted mutants on rung 9's gate logic (tools/den-core/score.py and the parts of decide.py the
gate reads), each applied to a fresh minimal temp copy of the checkout, then den-core's rung 9 tests run with
failfast.

How to run
  python3 -B mutate_score.py /path/to/den-core-live-checkout [--only M1,M4]
  For each mutant: copy tools/den-core, tools/la-latency, tools/correction-capture, stack/services/perception-lab
  and ha-config/custom_components/den_lighting into a temp dir (with an empty .git folder at its root, as a
  checkout has), apply one exact text replacement (it must match exactly once), and run
      python3 -B -m unittest -f <the test_rung9 classes below>
  from tools/den-core. test_rung9.TimingTest.test_a_full_size_light_model_stays_under_100_ms is left out: it
  measures wall-clock time and fails on a loaded machine even unmutated (see probe_atk_full_grid_timing.py).
  The unmutated copy is run first as a baseline. Prints one caught/survived line per mutant and a table.
  Synthetic test data only; nothing contacts a host.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NEEDED = ("tools/den-core", "tools/la-latency", "tools/correction-capture", "stack/services/perception-lab",
          "ha-config/custom_components/den_lighting")
TESTS = ["test_rung9.LightModelTest", "test_rung9.RecordTest", "test_rung9.HardConstraintTest",
         "test_rung9.ActuationTest", "test_rung9.ScoreTest", "test_rung9.FixturesTest",
         "test_rung9.TimingTest.test_every_decide_decision_under_100_ms"]

MUTANTS = [
    ("M1", "tools/den-core/score.py", "timing check ignores the 100 ms limit",
     'checks = {\n        "replay": {"ok": result["ok"], "text": result["reason"]},\n        "timing": {"ok": bool(ms) and max(ms) < fixed["decision_ms_max"],',
     'checks = {\n        "replay": {"ok": result["ok"], "text": result["reason"]},\n        "timing": {"ok": True,'),
    ("M2", "tools/den-core/score.py", "record check never compares hard_conflicts",
     'if conflicts != d["hard_conflicts"] or any(c["held_channel"] not in held for c in d["hard_conflicts"]):',
     'if False:'),
    ("M3", "tools/den-core/score.py", "anticipation needs no rise (any need-driven change counts)",
     'hits["anticipation"].append(any(e >= (1 + rise) * start if start > 0 else e > 0 for _, e, _ in up))',
     'hits["anticipation"].append(bool(up))'),
    ("M4", "tools/den-core/score.py", "stability threshold x100 (no change ever unstable)",
     'if abs(e - previous) > change * max(previous, 1e-9):',
     'if abs(e - previous) > 100 * change * max(previous, 1e-9):'),
    ("M5", "tools/den-core/score.py", "recipients check removed",
     'if set(d.get("recipients") or {}) != lit:',
     'if False:'),
    ("M6", "tools/den-core/score.py", "replay check result ignored",
     '"replay": {"ok": result["ok"], "text": result["reason"]},',
     '"replay": {"ok": True, "text": result["reason"]},'),
    ("M7", "tools/den-core/score.py", "candidate follows decide during a game too",
     'mine = shown if gaming else {b: requested.get(b, shown[b]) for b in events.LIGHTS}',
     'mine = {b: requested.get(b, shown[b]) for b in events.LIGHTS}'),
    ("M8", "tools/den-core/score.py", "episodes counted without reaching engaged",
     '            if engaged:\n                found.append((track, t0, e["t"]))',
     '            if True:\n                found.append((track, t0, e["t"]))'),
    ("M9", "tools/den-core/score.py", "anticipation window ignored (any later need change counts)",
     'up = [(t, e, c) for t, e, c in light if t0 <= t <= t0 + window and c == "need"]',
     'up = [(t, e, c) for t, e, c in light if t0 <= t <= t1 and c == "need"]'),
    ("M10", "tools/den-core/decide.py", "applied readback of a held bulb = last read-back, not its pin",
     'applied[b] = {"status": "held", "readback": _scene(held[b]["pinned"]), "steps": [],',
     'applied[b] = {"status": "held", "readback": state["actuator_state"].get(b), "steps": [],'),
    ("M11", "tools/den-core/score.py", "open slots in decide's logged config no longer refused",
     '    if config_open:\n        reasons.append',
     '    if False:\n        reasons.append'),
]


def copy_tree(src, dst):
    for rel in NEEDED:
        if (src / rel).exists():
            shutil.copytree(src / rel, dst / rel, ignore=shutil.ignore_patterns("__pycache__", ".tmp"))
    (dst / ".git").mkdir()


def run_tests(root):
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", *TESTS], cwd=root / "tools" / "den-core",
                          capture_output=True, text=True, timeout=1500)
    text = proc.stdout + proc.stderr
    first = next((line for line in text.splitlines() if line.startswith(("FAIL:", "ERROR:"))), "")
    last = [line for line in text.strip().splitlines() if line.strip()][-1:] or [""]
    return proc.returncode, first or last[0]


def main():
    src = Path(sys.argv[1]).resolve()
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    results = []
    with tempfile.TemporaryDirectory(prefix="mutate-r9-base-") as tmp:
        base = Path(tmp)
        copy_tree(src, base)
        code, text = run_tests(base)
        print("RESULT baseline: exit %d, %s" % (code, text))
        if code != 0:
            print("FAIL baseline is red; mutant results would be meaningless")
            return
    for mid, rel, what, old, new in MUTANTS:
        if only and mid not in only:
            continue
        with tempfile.TemporaryDirectory(prefix="mutate-r9-%s-" % mid) as tmp:
            root = Path(tmp)
            copy_tree(src, root)
            path = root / rel
            text = path.read_text("utf-8")
            if text.count(old) != 1:
                print("RESULT %s not-run: pattern matches %d times" % (mid, text.count(old)))
                results.append((mid, what, "not-run", ""))
                continue
            path.write_text(text.replace(old, new), "utf-8")
            code, first = run_tests(root)
            verdict = "caught" if code != 0 else "survived"
            print("RESULT %s %s: %s [%s]" % (mid, verdict, what, first))
            results.append((mid, what, verdict, first))
    print("\n%-4s %-9s %s" % ("id", "result", "mutant / first failing test"))
    for mid, what, verdict, first in results:
        print("%-4s %-9s %s | %s" % (mid, verdict, what, first))


if __name__ == "__main__":
    main()
