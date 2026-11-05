"""Verifier re-check of the four mutants reported as surviving in tools/den-n2/score.py.

For each mutant: copy tools/den-n2, tools/den-label and tools/den-take from the checkout into
<mut-root>/<mutant>/ (a throwaway git repo, since two tests expect a git checkout), apply the one-line
mutant to score.py, and run den-n2's whole suite with failfast. Prints one RESULT line per mutant
(caught = suite fails, survived = suite passes) and a table. Synthetic data only; nothing outside the copies.

Run:  python3 -B mutate_survivors_recheck.py <root of the den-n2 checkout> <mut-root>
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MUT = Path(sys.argv[2]).resolve()
MUTANTS = [
    ("baseline", None, None),
    ("A4-sample-gate-ignores-sessions",
     'met = n >= needed and len(sessions) >= gate["sample.sessions"] and len(days) >= gate["sample.days"]',
     'met = n >= needed and len(days) >= gate["sample.days"]'),
    ("A7-tick-stand-in-unchecked", '        if entry["tick_s"] != gate["tick_s"]:', '        if False:'),
    ("A8-prompt-version-unchecked",
     '    if any(v != gate["prompt"]["version"] for v in served["prompt_versions"]):', '    if False:'),
    ("A9-tie-counts-as-on", '    return on > len(seconds) - on  # a tie is off',
     '    return on >= len(seconds) - on  # a tie is off'),
]


def tree_for(name):
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
        tree = tree_for(name)
        target = tree / "tools" / "den-n2" / "score.py"
        if old is not None:
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"RESULT {name} not-run (pattern count {text.count(old)})", flush=True)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
        start = time.time()
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-n2"],
                              cwd=tree, capture_output=True, text=True, timeout=900)
        tail = (proc.stderr.strip().splitlines() or [""])[-1]
        ran = [l for l in proc.stderr.splitlines() if l.startswith("Ran ")]
        result = ("passes" if proc.returncode == 0 else "FAILS") if old is None else \
                 ("caught" if proc.returncode else "survived")
        rows.append((name, result, f"{ran[-1] if ran else ''} {tail}"))
        print(f"RESULT {name} {result} ({time.time() - start:.0f} s) {ran[-1] if ran else ''} {tail}", flush=True)
    print("\n| mutant | result | suite |\n|---|---|---|")
    for row in rows:
        print("| %s | %s | %s |" % row)


if __name__ == "__main__":
    main()
