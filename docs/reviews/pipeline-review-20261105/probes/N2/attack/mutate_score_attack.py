"""Mutation runner (attack lens) for tools/den-n2/score.py: verdict, gate and refusal logic.

Each mutant is applied to a fresh temporary copy of tools/den-n2, tools/den-label and tools/den-take (made a
throwaway git repo, because two tests assume a git checkout); then den-n2's suite runs with failfast. A mutant
is "caught" when the suite fails and "survived" when it still passes. Synthetic data only; temp dirs only.

Run:  python3 -B mutate_score_attack.py <root of the den-n2 checkout>
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()

MUTANTS = [
    ("A1-floor-judged-on-point-estimate",
     'return "met" if lo >= threshold else "failed" if hi < threshold else "straddled"',
     'return "met" if value >= threshold else "failed" if hi < threshold else "straddled"'),
    ("A2-failed-before-unmet",
     "    if unmet:\n        return \"insufficient\", [\"rule 2: a sample gate is unmet\"] + unmet + failed + straddled\n"
     "    if failed:\n        return \"not_feasible\", [\"rule 3: a gated metric or a case failed\"] + failed + straddled\n",
     "    if failed:\n        return \"not_feasible\", [\"rule 3: a gated metric or a case failed\"] + failed + straddled\n"
     "    if unmet:\n        return \"insufficient\", [\"rule 2: a sample gate is unmet\"] + unmet + failed + straddled\n"),
    ("A3-case-fails-on-lower-bound",
     'else "failed" if interval[1] < floor else "not_failed")',
     'else "failed" if interval[0] < floor else "not_failed")'),
    ("A4-sample-gate-ignores-sessions",
     'met = n >= needed and len(sessions) >= gate["sample.sessions"] and len(days) >= gate["sample.days"]',
     'met = n >= needed and len(days) >= gate["sample.days"]'),
    ("A5-stayed-on-scores-zero",
     '            d, tag = INF, "stayed_on"',
     '            d, tag = 0.0, "stayed_on"'),
    ("A6-false-fine-counts-fresh-only",
     '        fine = band_of(s.level, gate) == "fine"\n        if s.iv is not None:',
     '        fine = band_of(s.level, gate) == "fine" and s.fresh\n        if s.iv is not None:'),
    ("A7-tick-stand-in-unchecked",
     '        if entry["tick_s"] != gate["tick_s"]:',
     '        if False:'),
    ("A8-prompt-version-unchecked",
     '    if any(v != gate["prompt"]["version"] for v in served["prompt_versions"]):',
     '    if False:'),
    ("A9-tie-counts-as-on",
     '    return on > len(seconds) - on  # a tie is off',
     '    return on >= len(seconds) - on  # a tie is off'),
]


def run_suite(tree):
    start = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-n2"],
                          cwd=tree, capture_output=True, text=True, timeout=900)
    tail = (proc.stderr.strip().splitlines() or [""])[-1]
    return proc.returncode, tail, time.time() - start


def make_tree(tmp):
    tree = Path(tmp) / "tree"
    for part in ("tools/den-n2", "tools/den-label", "tools/den-take"):
        shutil.copytree(ROOT / part, tree / part, ignore=shutil.ignore_patterns("__pycache__"))
    git = ["git", "-C", str(tree), "-c", "user.name=probe", "-c", "user.email=probe@example.invalid"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "copy"], check=True)
    return tree


def main():
    rows = []
    with tempfile.TemporaryDirectory(prefix="n2-mut-base-") as tmp:
        code, tail, secs = run_suite(make_tree(tmp))
        print(f"RESULT baseline exit={code} {tail} ({secs:.0f} s)")
        if code:
            print("FAIL baseline does not pass; mutants would be meaningless")
            return
    for name, old, new in MUTANTS:
        with tempfile.TemporaryDirectory(prefix="n2-mut-") as tmp:
            tree = make_tree(tmp)
            target = tree / "tools" / "den-n2" / "score.py"
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                rows.append((name, "not-run", f"pattern found {text.count(old)} times"))
                print(f"RESULT {name} not-run (pattern count {text.count(old)})")
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            code, tail, secs = run_suite(tree)
            result = "caught" if code else "survived"
            rows.append((name, result, tail))
            print(f"RESULT {name} {result} ({secs:.0f} s) {tail}", flush=True)
    print("\n| mutant | result | suite tail |\n|---|---|---|")
    for name, result, tail in rows:
        print(f"| {name} | {result} | {tail} |")


if __name__ == "__main__":
    main()
