"""mutate_den_core_live: targeted mutants on den-core-live's new adapter and comparison logic (run.py live_diff,
status_span, pair, the corrections cut; sources.py den_lighting_status and correction_capture), each applied to a
fresh minimal temp copy of the checkout, then the den-core tests run with failfast.

How to run
  python3 -B mutate_den_core_live.py /path/to/den-core-live-checkout [--only M1,M4] [--full-on-survive]
  For each mutant: copy the folders the den-core tests need into a temp dir (with a .git marker at its root, as a
  checkout has), apply one exact text replacement, run
      python3 -B -m unittest discover -f -s tools/den-core -p test_live_inputs.py
  and, with --full-on-survive, re-run the whole den-core suite (-p "test_*.py") for a survivor.
  Prints one caught/survived line per mutant and a table. Synthetic test data only; nothing contacts a host.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NEEDED = ("tools/den-core", "tools/correction-capture", "tools/la-latency", "stack/services/perception-lab",
          "ha-config/custom_components/den_lighting",
          "stack/services/lighting-brain-bridges")

MUTANTS = [
    ("M1", "tools/den-core/run.py", 'lambda: [(bulb, not state["left_out"] and not _agree(',
     'lambda: [(bulb, not _agree(', "live_diff: left-out (game/disabled) time no longer left out"),
    ("M2", "tools/den-core/run.py",
     'state["left_out"] = not (data["automatic"] is True and data["gaming"] is False)',
     'state["left_out"] = not (data["automatic"] is True)', "live_diff: a live game is not left out"),
    ("M3", "tools/den-core/sources.py", "if revision is not None and status[3] < revision:",
     "if revision is not None and status[3] <= revision:", "status: an unchanged revision reads as a restart"),
    ("M4", "tools/den-core/sources.py", 'values = ["unknown", status[2]]', "values = [status[2]]",
     "status: a restart (revision down) is not read as unknown first"),
    ("M5", "tools/den-core/run.py", "early = start - runner.TAIL_S", "early = start",
     "status_span: an old vacancy carried to the start, not 150 s before it"),
    ("M6", "tools/den-core/run.py", "if abs(rt - t) <= PAIR_S and", "if abs(rt - t) < PAIR_S and",
     "pair: the 5 s window made exclusive"),
    ("M7", "tools/den-core/run.py", ' and rd["context"] != "own" and rd["on"]', ' and rd["on"]',
     "pair: den_lighting's own rows may be paired"),
    ("M8", "tools/den-core/run.py", "        if rt < t:\n", "        if rt <= t:\n",
     "pair: a row at the correction's own instant also takes it ahead"),
    ("M9", "tools/den-core/run.py", "and (bulb == held or events.scene_matches(target, recorded.get(bulb)))):",
     "and events.scene_matches(target, recorded.get(bulb))):",
     "live_diff: the live side no longer takes a correction's target"),
    ("M10", "tools/den-core/run.py", "corrections = [item for item in items if start <= item[0] <= last]",
     "corrections = list(items)", "main: corrections no longer cut to the replayed span"),
    ("M11", "tools/den-core/sources.py", "if seen[key] != item:", "if False:",
     "correction_capture: a repeat with other values is accepted"),
    ("M12", "tools/den-core/run.py", ' and rd["on"] == data["after"]["on"]]', "]",
     "pair: a row turning the bulb the other way may be paired"),
]


def copy_checkout(root, dest):
    for rel in NEEDED:
        src = root / rel
        if src.exists():
            shutil.copytree(src, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".git").write_text("gitdir: none\n")


def tests(dest, pattern):
    done = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-core",
                           "-p", pattern], cwd=dest, capture_output=True, text=True, timeout=900)
    tail = done.stderr.strip().splitlines()
    return done.returncode, (tail[-1] if tail else ""), done.stderr


def main(argv):
    root = Path(argv[0]).resolve()
    only = None
    full = "--full-on-survive" in argv
    for a in argv[1:]:
        if a.startswith("--only"):
            only = set(a.split("=", 1)[1].split(",")) if "=" in a else set(argv[argv.index(a) + 1].split(","))
    work = Path(tempfile.mkdtemp(prefix="mut-r9-"))
    base = work / "base"
    copy_checkout(root, base)
    code, last, _ = tests(base, "test_live_inputs.py")
    print("RESULT baseline test_live_inputs: exit %d, %s" % (code, last))
    if code:
        print("FAIL baseline does not pass; mutants not run")
        return 2
    rows = []
    for mid, rel, old, new, what in MUTANTS:
        if only and mid not in only:
            continue
        dest = work / mid
        shutil.copytree(base, dest)
        path = dest / rel
        text = path.read_text()
        if text.count(old) != 1:
            rows.append((mid, rel, what, "not-run", "pattern found %d times" % text.count(old)))
            print("RESULT %s not-run: pattern found %d times" % (mid, text.count(old)))
            continue
        path.write_text(text.replace(old, new))
        code, last, err = tests(dest, "test_live_inputs.py")
        by = ""
        if code:
            for line in err.splitlines():
                if line.startswith(("FAIL:", "ERROR:")):
                    by = line.split(":", 1)[1].strip()
                    break
            result = "caught"
        else:
            result = "survived"
            if full:
                code, last, err = tests(dest, "test_*.py")
                if code:
                    result = "caught"
                    for line in err.splitlines():
                        if line.startswith(("FAIL:", "ERROR:")):
                            by = "(full suite) " + line.split(":", 1)[1].strip()
                            break
        rows.append((mid, rel, what, result, by or last))
        print("RESULT %s %s: %s [%s]" % (mid, result, what, by or last))
        shutil.rmtree(dest, ignore_errors=True)
    print()
    print("%-4s %-9s %s" % ("id", "result", "mutation / caught by"))
    for mid, rel, what, result, by in rows:
        print("%-4s %-9s %s :: %s" % (mid, result, what, by))
    survived = [r[0] for r in rows if r[3] == "survived"]
    print("RESULT survived: %s" % (", ".join(survived) or "none"))
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
