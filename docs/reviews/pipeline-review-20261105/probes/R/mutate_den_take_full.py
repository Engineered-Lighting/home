"""mutate_den_take_full: re-run surviving mutants from mutate_den_take.py against the WHOLE suite.

How to run
  python3 -B mutate_den_take_full.py <branch checkout root> M4 [M6 ...]
Each mutant: copy tools/den-take to a temp dir, apply it, run `python3 -B -m unittest discover -f`;
exit 0 = SURVIVED the full suite. Synthetic data only; fake camera on 127.0.0.1; temp dirs only.
"""
import re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

sys.dont_write_bytecode = True
sys.argv, ids = sys.argv[:2], sys.argv[2:]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutate_den_take as MD  # noqa: E402

for mid, fname, old, new, what, _ in MD.MUTANTS:
    if mid not in ids:
        continue
    work = Path(tempfile.mkdtemp(prefix=f"mutfull-{mid}-")).resolve()
    try:
        dst = work / "den-take"
        shutil.copytree(MD.SRC, dst)
        path = dst / fname
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, mid
        path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
        t0 = time.time()
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", ".", "-p", "test*.py"],
                              cwd=dst, capture_output=True, text=True, timeout=1200)
        ran = re.findall(r"Ran (\d+) tests?", proc.stderr)
        failed = re.findall(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", proc.stderr, re.M)
        result = "survived" if proc.returncode == 0 else "caught"
        print(f"RESULT {mid} full suite: {result} [ran {ran[-1] if ran else '?'} in {time.time() - t0:.0f}s] "
              f"{', '.join(b + '.' + a for a, b in failed)} :: {what}", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
