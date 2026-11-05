"""Verifier re-run of the four surviving mutants claimed in finding gate-logic-mutants-survive (N2, den-n2 score.py).

For each mutant: copy tools/den-n2, tools/den-label, tools/den-take of the checkout into a fresh folder under
<scratchpad>/mut/N2-reproduce/ (a throwaway git repo, since two tests expect a git checkout), apply the one-line
mutant to score.py, run den-n2's whole suite with failfast, and print caught/survived. Also runs the unmutated
copy once as the baseline. Synthetic data only; nothing outside the copy and temp folders is touched.

Run:  python3 -B verify_gate-logic-mutants-survive.py <root of the den-n2 checkout> [<mut dir>]
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MUT = Path(sys.argv[2] if len(sys.argv) > 2 else
           "$SCRATCH/mut/N2-reproduce")

MUTANTS = [
    ("baseline", None, None),
    ("V4-sample-gate-drops-session-count",
     'met = n >= needed and len(sessions) >= gate["sample.sessions"] and len(days) >= gate["sample.days"]',
     'met = n >= needed and len(days) >= gate["sample.days"]'),
    ("V7-tick-stand-in-unchecked",
     '        if entry["tick_s"] != gate["tick_s"]:',
     '        if False:'),
    ("V8-prompt-version-unchecked",
     '    if any(v != gate["prompt"]["version"] for v in served["prompt_versions"]):',
     '    if False:'),
    ("V9-tie-counts-as-on",
     '    return on > len(seconds) - on  # a tie is off',
     '    return on >= len(seconds) - on  # a tie is off'),
]


def make_tree(name):
    tree = MUT / name
    if tree.exists():
        shutil.rmtree(tree)
    for part in ("tools/den-n2", "tools/den-label", "tools/den-take"):
        shutil.copytree(ROOT / part, tree / part, ignore=shutil.ignore_patterns("__pycache__"))
    git = ["git", "-C", str(tree), "-c", "user.name=probe", "-c", "user.email=probe@example.invalid"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "copy"], check=True)
    return tree


def main():
    rows = []
    for name, old, new in MUTANTS:
        tree = make_tree(name)
        target = tree / "tools" / "den-n2" / "score.py"
        if old is not None:
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"RESULT {name} not-run (pattern count {text.count(old)})")
                rows.append((name, "not-run", ""))
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
        start = time.time()
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-n2"],
                              cwd=tree, capture_output=True, text=True, timeout=900)
        lines = proc.stderr.strip().splitlines()
        ran = next((l for l in lines if l.startswith("Ran ")), "")
        tail = lines[-1] if lines else ""
        result = ("baseline-ok" if not proc.returncode else "baseline-FAILED") if old is None else \
                 ("caught" if proc.returncode else "survived")
        rows.append((name, result, f"{ran}; {tail}"))
        print(f"RESULT {name} {result} ({time.time() - start:.0f} s) {ran}; {tail}", flush=True)
        shutil.rmtree(tree, ignore_errors=True)
    print("\n| mutant | result | suite |\n|---|---|---|")
    for row in rows:
        print("| %s | %s | %s |" % row)


if __name__ == "__main__":
    main()
