"""verify_pacing-off-ladder: recompute N2 PLAN.md section 1(a)'s pacing rule (lines 57-74) for a few
service times, and check the canon text it departs from.

What it checks
  D_dev = median of max(d_on - w, 0) = S + 4 s x m. With m = 0:
    - is P = 4 s "gated as written" (P/2 + D_dev <= 3 s)?
    - if not, the largest P with P/2 + S + P x m <= 3 s (0.01 s grid), and the re-check at that P;
    - which S sends onset to report-only (no P fits).
  Canon text: ladder.json N2 'tested' says one request per 4 s; schemas.json EvalRecord.replay says the
  same; owner-choices.md item 3 says the 3 s median target is kept; PLAN.md's GATE-V1 table gives
  `pacing_s`, `onset_mode` to the development step.

Run
  python3 -B verify_pacing-off-ladder.py <root of claude/plan-n2> [<root of live-records checkout>]
Arithmetic only; no network; no files written.
"""
import json
import re
import sys
from pathlib import Path


def main(root, rec):
    plan = re.sub(r"\s+", " ", (Path(root) / "docs/plans/n2-need-test/PLAN.md").read_text(encoding="utf-8"))
    print(("PASS" if "| `pacing_s`, `onset_mode` | development step, from D_dev (section 1(a)) |" in plan else "FAIL")
          + ": GATE-V1 table gives pacing_s and onset_mode to the development step")
    if rec:
        base = Path(rec) / "docs/architecture/live-v1"
        lad = json.loads((base / "ladder.json").read_text(encoding="utf-8"))
        n2 = next(r for r in lad["rungs"] if r["n"] == "N2")
        print(("PASS" if "(one request per 4 s)" in n2["tested"] else "FAIL") + ": ladder N2 declares one request per 4 s")
        own = (base / "owner-choices.md").read_text(encoding="utf-8")
        print(("PASS" if "The 3 s median target is kept" in own else "FAIL") + ": owner kept the 3 s median target")
    for S in (1.25, 1.5, 2.0, 2.5, 3.0):
        gated4 = 2.0 + S <= 3.0 + 1e-12
        fits = [p / 100 for p in range(1, 401) if (p / 100) / 2 + S <= 3.0 + 1e-12]
        P = max(fits) if fits else None
        mode = "gated at P = 4" if gated4 else (f"pacing at P = {P} s (re-check {P / 2 + S:.2f} <= 3)" if P else "report-only")
        print(f"RESULT S = {S:.2f} s, m = 0: P = 4 gated as written? {gated4}; plan's rule -> {mode}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".", sys.argv[2] if len(sys.argv) > 2 else None)
