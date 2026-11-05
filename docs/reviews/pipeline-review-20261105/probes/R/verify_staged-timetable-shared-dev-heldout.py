"""verify_staged-timetable-shared-dev-heldout: is the shared per-script timetable a leakage path for N2?

What it checks (no camera, no network, no take built)
  * the premise: take.load_script returns the script's blocks verbatim, so every take of one script
    speaks the same (case, at_s) list (take.py's play() speaks block at begin + at_s);
  * the duration structure: for each script, the lengths of task blocks and of quiet blocks; if both
    scripts share them, a split by script (all hard-a dev, all hard-b held-out) would leave every
    duration-dependent fit exactly as exposed as the per-script alternation does;
  * the consumer: lists GATE-V1's slots (tools/den-n2/GATE-V1.md in the den-n2 checkout, second CLI
    arg) and reports whether any slot is an elapsed-time input.

How to run
  python3 -B verify_staged-timetable-shared-dev-heldout.py <den-take-script root> <den-n2 root>
"""
import re, sys
from collections import Counter
from pathlib import Path

root, n2 = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(root / "tools" / "den-take"))
import check, take  # noqa: E402

settings = take.Settings(slate_s=check.load_method()["slate_length_s"])
QUIET = {"idle_seated", "idle_standing", "lights_down"}
for name in ("hard-a", "hard-b"):
    s1, s2 = take.load_script(name, settings), take.load_script(name, settings)
    same = [(b["case"], b["at_s"]) for b in s1["blocks"]] == [(b["case"], b["at_s"]) for b in s2["blocks"]]
    blocks = s1["blocks"] + [{"at_s": s1["end_s"], "case": "end"}]
    lengths = [(b["case"], n["at_s"] - b["at_s"]) for b, n in zip(blocks, blocks[1:])]
    task = Counter(l for c, l in lengths if c not in QUIET)
    quiet = Counter(l for c, l in lengths if c in QUIET)
    print(f"RESULT {name}: every take speaks the same timetable: {same}; task block lengths {dict(sorted(task.items()))}; "
          f"quiet block lengths {dict(sorted(quiet.items()))}")
gate = (n2 / "tools" / "den-n2" / "GATE-V1.md").read_text(encoding="utf-8")
slots = re.findall(r"^\| `([^|]+)` \(?[^|]*\| ([^|]+) \|$", gate, re.M) or re.findall(r"^\| (`[^|]+`[^|]*)\| ([^|]+)\|$", gate, re.M)
for slot, by in slots:
    print(f"RESULT GATE-V1 slot: {slot.strip()} -- filled by: {by.strip()}")
timeish = [s for s, _ in slots if re.search(r"elapsed|since|clock|t_s|begin|time", s, re.I)]
print(f"RESULT slots that are an elapsed-time input: {timeish or 'none'}")
fixed = re.findall(r"^(a7\.\w+|schedule_s|tick_s|max_age_s) = (.+)$", gate, re.M)
print(f"RESULT fixed (not tuned) timing values: {fixed}")
