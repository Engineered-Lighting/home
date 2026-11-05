"""verify_incumbent-pycache (REPRODUCE lens): does a den-core tool run without -B write bytecode into den_lighting's
folder (ha-config/custom_components/den_lighting/__pycache__), and is incumbent.load_policy the writer?

Steps (synthetic data only; temp folders only; no network):
1. Copy the checkout (without .git, node_modules, __pycache__) to a temp folder under
   <scratchpad>/mut/R0core-reproduce/ and give it a `.git` marker file, as a real checkout has.
2. In the copy, run  python3 tools/den-core/run.py --synthetic evening --policies incumbent --out <temp dir>
   WITHOUT -B and with PYTHONDONTWRITEBYTECODE unset; list every __pycache__ folder that appears in the copy.
3. Same with -B (the README's documented form): list __pycache__ folders again (expected: none).
4. Control: in a fresh copy, import incumbent and call load_policy(find_policy()) only, without -B; see whether
   den_lighting/__pycache__ appears (isolates load_policy as the writer), and the same for validate.load_den_lighting.

How to run:
    python3 -B verify_incumbent-pycache.py /path/to/den-core-checkout
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MUT = Path(__file__).resolve().parents[2] / "mut" / "R0core-reproduce"
DEN_REL = Path("ha-config") / "custom_components" / "den_lighting"


def fresh(tmp):
    work = tmp / "copy"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"), symlinks=True)
    (work / ".git").write_text("gitdir: /nonexistent\n")
    return work


def caches(work):
    return sorted(str(p.relative_to(work)) for p in work.rglob("__pycache__"))


env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
MUT.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="vb-pycache-", dir=str(MUT)))
out_root = Path(tempfile.mkdtemp(prefix="den-core-out-"))
try:
    results = {}
    for label, flags in (("without -B", []), ("with -B", ["-B"])):
        work = fresh(tmp)
        out = out_root / label.replace(" ", "_")
        p = subprocess.run([sys.executable, *flags, "tools/den-core/run.py", "--synthetic", "evening", "--policies",
                            "incumbent", "--out", str(out)], cwd=str(work), capture_output=True, text=True, env=env,
                           timeout=600)
        found = caches(work)
        results[label] = found
        print("RESULT run.py %s: exit=%d; __pycache__ folders in the copy: %s" % (label, p.returncode, found))
        if p.returncode:
            print(p.stderr[-800:])
    for label, code in (("incumbent.load_policy only", "import incumbent; incumbent.load_policy(incumbent.find_policy())"),
                        ("validate.load_den_lighting only", "import validate; validate.load_den_lighting()")):
        work = fresh(tmp)
        # Import with bytecode for the importing step switched off, so only what the call itself writes shows.
        script = ("import sys; sys.dont_write_bytecode = True; sys.path.insert(0, 'tools/den-core'); %s; "
                  "sys.dont_write_bytecode = False; " % code.split(";")[0] +
                  ("import importlib; m = sys.modules['%s']; " % code.split(";")[0].split()[-1]) +
                  code.split(";")[1].strip())
        p = subprocess.run([sys.executable, "-c", script], cwd=str(work), capture_output=True, text=True, env=env,
                           timeout=600)
        found = caches(work)
        print("RESULT %s (no -B): exit=%d; __pycache__ folders: %s" % (label, p.returncode, found))
        if p.returncode:
            print(p.stderr[-800:])
        results[label] = found
    den = str(DEN_REL / "__pycache__")
    ok = (den in results["without -B"] and den not in results["with -B"] and den in results["incumbent.load_policy only"]
          and den not in results["validate.load_den_lighting only"])
    print(("PASS (finding reproduced)" if ok else "FAIL (finding not reproduced)") +
          ": den_lighting/__pycache__ without -B=%s, with -B=%s, load_policy alone=%s, load_den_lighting alone=%s" % (
              den in results["without -B"], den in results["with -B"], den in results["incumbent.load_policy only"],
              den in results["validate.load_den_lighting only"]))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(out_root, ignore_errors=True)
