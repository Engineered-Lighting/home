#!/usr/bin/env python3
"""Verifier's independent re-run of the reported den-n2 score.py mutants (skeptical-verifier pass).

What it checks: for each mutant (same edit as the reporter's mutate_score.py, re-typed here), it
 1. applies the edit to a fresh temp copy of tools/den-n2, tools/den-label, tools/den-take (a throwaway
    git repo, since two tests expect a checkout) and runs the branch's whole suite with failfast;
 2. runs a small semantic check on the MUTATED score.py that shows the mutant is not equivalent
    (its behaviour differs from the original on a tiny synthetic input).
Synthetic data and temp dirs only; never writes inside <root>; no network.

Run:  python3 -B verify_mutants.py <root of the claude/den-n2 checkout> [mutant-id ...]
"""
import shutil, subprocess, sys, tempfile, time, textwrap
from pathlib import Path

MUTANTS = {
    "M01-cut-fine-strict": ('return "fine" if level >= gate["cut.fine"]', 'return "fine" if level > gate["cut.fine"]',
        'print("SEM band_of(0.6) =", score.band_of(0.6, score.load_gate()))'),
    "M02-ece-9-bins": ("b = ece[name][min(int(conf * bins), bins - 1)]",
                       "b = ece[name][min(int(conf * (bins - 1)), bins - 1)]", None),
    "M03-stale-is-a-hit": ('ratio["fine_recall"][0] += s.fresh and fine', 'ratio["fine_recall"][0] += fine', None),
    "M04-no-days-rule": (' and len(days) >= gate["sample.days"]', "",
        'g=score.load_gate(); print("SEM 3 sessions/1 day, n=30:", score.sample_gate("x", 30, 30, {"a","b","c"}, {"a":"d1","b":"d1","c":"d1"}, g)["met"])'),
    "M05-units-off-by-one": ("met = n >= needed and", "met = n > needed and",
        'g=score.load_gate(); print("SEM exactly 30 units, 3s/2d:", score.sample_gate("x", 30, 30, {"a","b","c"}, {"a":"d1","b":"d2","c":"d2"}, g)["met"])'),
    "M06-bootstrap-90pct": ("        return None\n    tail = (1 - level) / 2\n    return [percentile(values, tail)",
                            "        return None\n    tail = (1 - level)\n    return [percentile(values, tail)",
        'p=[{"ratio":{"early_off":[k,10]}} for k in (0,1,3,6,2,4)]; print("SEM interval:", score.bootstrap(p, score.m_ratio("early_off"), score.resamples(6, 4000, "ab"*32), 0.95))'),
    "M07-identity-resample": ("return [[rng.randrange(n) for _ in range(n)] for _ in range(rounds)]",
                              "return [list(range(n)) for _ in range(rounds)]",
        'p=[{"ratio":{"early_off":[k,10]}} for k in (0,1,3,6)]; print("SEM interval:", score.bootstrap(p, score.m_ratio("early_off"), score.resamples(4, 2000, "ab"*32), 0.95))'),
    "M08-no-data-passes": ('in ("straddled", "no_data")]', 'in ("straddled",)]',
        'm={"fine_recall":{"threshold":{"kind":"floor","value":0.6},"status":"met"},"ece.engagement":{"threshold":{"kind":"cap","value":0.1},"status":"no_data"}}; print("SEM verdict:", score.verdict(m, {}, [{"name":"units.fine","met":True}], True))'),
    "M09-skip-fixture-verify": ("for folder in (labels_dir, fixture_dir):", "for folder in (labels_dir,):", None),
    "M10-skip-tick-standin": ('if entry["tick_s"] != gate["tick_s"]:', "if False:", None),
    "M11-data-gate-vacuous": ('with_data = {an.session.take for an, s in zip(analyses, stats) if s["ratio"][name][1]}',
                              "with_data = {an.session.take for an, s in zip(analyses, stats)}", None),
}


def suite(copy):
    t = time.time()
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-n2"], cwd=copy,
                       capture_output=True, text=True, timeout=900)
    lines = p.stderr.strip().splitlines() or [""]
    first = next((x for x in lines if x.startswith(("FAIL:", "ERROR:"))), "")
    ran = next((x for x in lines if x.startswith("Ran ")), "")
    return p.returncode, first, ran, lines[-1], time.time() - t


def semantic(copy, code):
    if not code:
        return ""
    snippet = "import sys; sys.path.insert(0, 'tools/den-n2'); import score\n" + code
    p = subprocess.run([sys.executable, "-B", "-c", snippet], cwd=copy, capture_output=True, text=True, timeout=120)
    return (p.stdout.strip() or p.stderr.strip().splitlines()[-1]).replace("\n", " | ")


def main():
    root = Path(sys.argv[1]).resolve()
    want = sys.argv[2:] or list(MUTANTS)
    with tempfile.TemporaryDirectory(prefix="n2-verify-mut-") as tmp:
        base = Path(tmp) / "base"
        for sub in ("tools/den-n2", "tools/den-label", "tools/den-take"):
            shutil.copytree(root / sub, base / sub, ignore=shutil.ignore_patterns("__pycache__"))
        git = ["git", "-c", "user.email=v@example.invalid", "-c", "user.name=v"]
        subprocess.run(git + ["init", "-q", str(base)], check=True)
        subprocess.run(git + ["-C", str(base), "add", "-A"], check=True)
        subprocess.run(git + ["-C", str(base), "commit", "-qm", "base"], check=True)
        code, first, ran, last, secs = suite(base)
        print(f"RESULT baseline exit={code} {ran} {last} ({secs:.0f} s)")
        print("SEM-ORIG " + semantic(base, MUTANTS["M01-cut-fine-strict"][2]))
        for mid in ("M04-no-days-rule", "M05-units-off-by-one", "M06-bootstrap-90pct", "M07-identity-resample",
                    "M08-no-data-passes"):
            print(f"SEM-ORIG {mid}: " + semantic(base, MUTANTS[mid][2]))
        if code:
            print("FAIL baseline")
            return 1
        for mid in want:
            old, new, sem = MUTANTS[mid]
            copy = Path(tmp) / mid
            shutil.copytree(base, copy)
            f = copy / "tools/den-n2/score.py"
            text = f.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"RESULT {mid} not-run (old text x{text.count(old)})")
                continue
            f.write_text(text.replace(old, new), encoding="utf-8")
            code, first, ran, last, secs = suite(copy)
            print(f"RESULT {mid} {'caught' if code else 'survived'} ({secs:.0f} s) {first or (ran + ' ' + last)}")
            s = semantic(copy, sem)
            if s:
                print(f"SEM-MUT {mid}: {s}")
            shutil.rmtree(copy, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
