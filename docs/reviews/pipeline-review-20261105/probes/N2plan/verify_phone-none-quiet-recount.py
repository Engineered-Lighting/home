"""verify_phone-none-quiet-recount: independent count of scored none-band units (quiet spans and the
phone span under the `none` phone band) and of fine/casual units, from N1's round tables.

What it checks
  The block tables of N1's plan (claude/plan-n1 @ b1f1a9d2, docs/plans/n1-reference/PLAN.md, the hard-a and
  hard-b round tables and the "If the phone band is `none`" bullet) are re-typed here independently of
  n2_scripts.py. N2's unit rule (PLAN.md 4.1): a quiet or no-need span is a unit when it has a scored
  second, and a second is scored only past tau_off = 60 s after the latest interval offset. Touch 1 s after
  each task cue, release 0.5 s before the next cue (any value under ~5 s gives the same counts).
  Prints per-take and per-held-out-set (2 hard-a + 2 hard-b takes) counts for both phone bands, and the
  N2 plan's own figures (5/6/22 and 7/8/30) for comparison. Also prints the none-band count against the
  owner's 30-per-band number.

Run
  python3 -B verify_phone-none-quiet-recount.py <root of claude/plan-n2 checkout> [<root of claude/plan-n1>]
Synthetic timelines only; no network; no files written.
"""
import math
import re
import sys
from pathlib import Path

Q, T, P = "quiet", "task", "phone-nospan"


def take(script, phone):
    rows = []
    for rnd in range(1, 6):
        if script == "hard-a":
            r = [("idle_seated", Q, None, 90 if rnd in (1, 3, 5) else 30), ("still_reader", T, "fine", 30),
                 ("idle_standing", Q, None, 90 if rnd in (2, 4) else 30), ("sorting_mail", T, "fine", 30),
                 ("idle_standing", Q, None, 30), ("wiping", T, "casual", 25), ("idle_standing", Q, None, 30),
                 ("phone_at_counter", T, "casual", 25) if phone == "casual" else ("tidying", T, "casual", 25)]
            if phone == "none" and rnd in (2, 4):
                r[3:3] = [("phone_at_counter", P, None, 25), ("idle_standing", Q, None, 30)]
        else:
            r = [("idle_standing", Q, None, 90 if rnd in (2, 4) else 30), ("wiping", T, "casual", 30),
                 ("idle_standing", Q, None, 30),
                 ("phone_at_counter", T, "casual", 20) if phone == "casual" else ("tidying", T, "casual", 20),
                 ("idle_standing", Q, None, 30), ("sorting_mail", T, "fine", 25),
                 ("idle_seated", Q, None, 90 if rnd in (1, 3, 5) else 30), ("still_reader", T, "fine", 35)]
            if phone == "none" and rnd in (2, 4):
                r[1:1] = [("phone_at_counter", P, None, 25), ("idle_standing", Q, None, 30)]
        if rnd == 1:
            c, k, b, d = r[0]
            r[0] = (c, k, b, d - 3)
        rows += r
        if rnd == 3:
            rows.append(("lights_down", "other", None, 20))
    rows.append(("idle_standing" if script == "hard-a" else "idle_seated", Q, None, 30))
    t, out = 3.0, []
    for c, k, b, d in rows:
        out.append((c, k, b, t, t + d))
        t += d
    return out, t


def count(script, phone):
    blocks, end = take(script, phone)
    offs = [b - 0.5 for c, k, band, a, b in blocks if k == T]
    n = {"fine": 0, "casual": 0, "none_quiet": 0, "none_phone": 0}
    for c, k, band, a, b in blocks:
        if k == T:
            n[band] += 1
        elif k in (Q, P):
            prev = [o for o in offs if o <= a]
            first = max(a, prev[-1] + 60.0) if prev else a
            if math.ceil(first) + 1 <= b:  # at least one whole scored second
                n["none_quiet" if k == Q else "none_phone"] += 1
    return n, end


def main(root, n1_root=None):
    plan = re.sub(r"\s+", " ", (Path(root) / "docs/plans/n2-need-test/PLAN.md").read_text(encoding="utf-8"))
    claim = "7 and 8 with the phone as `none`: 22 per held-out set of two takes each, or 30 with the phone as `none`"
    print(("PASS" if claim in plan else "FAIL") + f": N2 PLAN.md states {claim!r}")
    if n1_root:
        n1 = re.sub(r"\s+", " ", (Path(n1_root) / "docs/plans/n1-reference/PLAN.md").read_text(encoding="utf-8"))
        for ph in ("the phone block moves to right after the long `idle_standing` block in rounds 2 and 4",
                   "\"Stop. Put the phone away. Hands still.\" (30 s). Its seconds then lie past τ_off",
                   "a quiet-span case like `idle_seated`", "`end_s` of 1,610 s"):
            print(("PASS" if ph in n1 else "FAIL") + f": N1 PLAN.md states {ph!r}")
    totals = {}
    for phone in ("casual", "none"):
        per = {}
        for script in ("hard-a", "hard-b"):
            n, end = count(script, phone)
            per[script] = n
            print(f"RESULT phone {phone:6s} {script}: {n}; end_s {end:.0f}")
        none_a = per["hard-a"]["none_quiet"] + per["hard-a"]["none_phone"]
        none_b = per["hard-b"]["none_quiet"] + per["hard-b"]["none_phone"]
        quiet_only = 2 * (per["hard-a"]["none_quiet"] + per["hard-b"]["none_quiet"])
        totals[phone] = (none_a, none_b, 2 * (none_a + none_b), quiet_only,
                         2 * (per["hard-a"]["fine"] + per["hard-b"]["fine"]),
                         2 * (per["hard-a"]["casual"] + per["hard-b"]["casual"]))
        t = totals[phone]
        print(f"RESULT phone {phone}: none-band units per take {t[0]}, {t[1]}; held-out set none {t[2]} "
              f"(idle-only {t[3]}), fine {t[4]}, casual {t[5]}; owner's 30-per-band on none: "
              f"{'met' if t[2] >= 30 else 'unmet'} (margin {t[2] - 30})")
    ok_casual = totals["casual"][:3] == (5, 6, 22)
    ok_none = totals["none"][:3] == (7, 8, 30)
    print(("PASS" if ok_casual else "FAIL") + f": phone casual counts match the plan (5, 6, 22): got {totals['casual'][:3]}")
    print(("PASS" if ok_none else "FAIL") + f": phone none counts match the plan (7, 8, 30): got {totals['none'][:3]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".", sys.argv[2] if len(sys.argv) > 2 else None)
