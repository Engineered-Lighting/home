"""verify_entered-on-gap-flicker: independent check of N2 PLAN.md section 4.2 (onset tags) driven by
a need-v1 state machine written from PLAN.md section 5, not by a hand-made state log.

What it checks
  One task offset at t=100 s, a 30 s "Stop. Hands still." gap, the next touch at t=131 s (inside the
  previous task's tau_off = 60 s, so no measure scores the gap). Replies arrive every 4 s (+S=1.25 s).
  Two reply streams for the gap, identical except for ONE reply:
    hold-through : task rows, then hold rows (`unclear`) through the gap            -> plan: stayed_on (miss)
    one-blip     : task rows, one low-evidence row (`transition`), one high row,
                   then the same hold rows                                         -> plan: entered_on, d_on = 0
  In both streams the estimator never reacts to the touch at 131 s (no new `starting`).
  If a second arg (a den-n2 checkout root) is given, its tools/den-n2/score.py onset_of() is run too.

Run
  python3 -B verify_entered-on-gap-flicker.py <root of claude/plan-n2 checkout> [<den-n2 root>]
Synthetic data only; no network; no files written.
"""
import importlib.util
import math
import re
import sys
from pathlib import Path

THETA_ON, THETA_OFF, ENTRY_DWELL, EXIT_NEG = 0.6, 0.4, 5.0, 20.0
ON = ("starting", "engaged")


def need_v1(rows):
    """rows: [(t, evidence or None for a hold row)] -> [(t, state)] on every change (PLAN.md 5)."""
    state, s, above_since, neg_clock, last_t, out = "none", None, None, 0.0, None, [(0.0, "none")]
    for t, ev in rows:
        hold = ev is None
        if state == "ending" and last_t is not None and s is not None and s < THETA_OFF and not prev_hold:
            neg_clock += t - last_t  # clock ran since the previous row (that row was not a hold row)
        if not hold:
            s = ev
        new = state
        if state == "none" and s is not None and s > THETA_ON:
            new, above_since = "starting", t
        elif state == "starting":
            if not hold and s <= THETA_ON:
                new = "none"
            elif s > THETA_ON and t - above_since >= ENTRY_DWELL:
                new = "engaged"
        elif state == "engaged" and s is not None and s < THETA_OFF:
            new, neg_clock = "ending", 0.0
        elif state == "ending":
            if s > THETA_ON:
                new, neg_clock = "engaged", 0.0
            elif neg_clock >= EXIT_NEG:
                new = "none"
        if new != state:
            out.append((t, new))
            state = new
        last_t, prev_hold = t, hold
    return out


def onset_plan(t_on, t_off, recs, prev_off):
    """PLAN.md 4.2 read literally: (d_on, tag)."""
    i = max(j for j, (rt, _) in enumerate(recs) if rt <= t_on)
    if recs[i][1] in ON:
        start = i
        while start > 0 and recs[start - 1][1] in ON:
            start -= 1
        if prev_off is None or recs[start][0] > prev_off:
            return 0.0, "entered_on", recs[start][0]
        return math.inf, "stayed_on", recs[start][0]
    for t, st in recs[i + 1:]:
        if t >= t_off:
            break
        if st in ON:
            return t - t_on, "entered", t
    return math.inf, "missed", None


def main(root, den_n2=None):
    plan = re.sub(r"\s+", " ", (Path(root) / "docs/plans/n2-need-test/PLAN.md").read_text(encoding="utf-8"))
    for phrase in ("On, and the run began after the previous labelled offset",
                   "d_on = 0, tagged `entered_on`",
                   "[end, end + τ_off) with τ_off = 60 s, every measure"):
        print(("PASS" if phrase in plan else "FAIL") + f": PLAN.md text present: {phrase!r}")
    S = 1.25
    prev_off, t_on, t_off = 100.0, 131.0, 160.5
    req = [4.0 * k for k in range(1, 50)]
    def stream(blip):
        rows = []
        for r in req:
            t = r + S
            if r < prev_off:            # task: high-evidence rows (e.g. other/other at the counter)
                ev = 0.9
            elif r < t_off:             # the gap and then the next task (estimator touch-blind)
                k = int((r - prev_off) // 4)
                if blip and k == 0:
                    ev = 0.1            # one 'transition' reply right after "Stop."
                elif blip and k == 1:
                    ev = 0.8            # one noisy other/other reply
                else:
                    ev = None           # hold rows (unclear / hands hidden)
            else:
                ev = 0.1
            rows.append((t, ev))
        return rows
    tool = None
    if den_n2:
        p = Path(den_n2) / "tools/den-n2/score.py"
        spec = importlib.util.spec_from_file_location("den_n2_score_v", p)
        tool = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode = True
        spec.loader.exec_module(tool)
    res = {}
    for name, blip in (("hold-through", False), ("one-blip", True)):
        recs = need_v1(stream(blip))
        gap_recs = [(t, s) for t, s in recs if prev_off - 10 <= t <= t_off]
        d, tag, run_start = onset_plan(t_on, t_off, recs, prev_off)
        res[name] = (d, tag)
        lead = (t_on - run_start) if run_start is not None else None
        print(f"RESULT {name:12s}: records near the gap {gap_recs}; onset tag {tag}, d_on {d}"
              + (f", on-run began {lead:.2f} s before the touch" if lead is not None else ""))
        if tool is not None:
            class R:
                def __init__(self, t, s):
                    self.t, self.state = t, s
            class TL:
                def __init__(self, recs):
                    self.recs = [R(t, s) for t, s in recs]
                def index_at(self, t):
                    return max(j for j, r in enumerate(self.recs) if r.t <= t)
            out = tool.onset_of({"on": t_on, "off": t_off}, TL(recs), prev_off, 0.0, 4.0)
            print(f"  den-n2 score.onset_of: d_on {out['d_on']} tag {out.get('tag')}")
    if res["hold-through"][1] == "stayed_on" and res["one-blip"] == (0.0, "entered_on"):
        print("FAIL: one low-evidence reply inside the unscored gap turns a missed onset (stayed_on, +inf) "
              "into a perfect onset (entered_on, d_on 0) although the estimator never reacts to the touch")
    else:
        print("PASS: the onset tag does not hinge on a single gap reply")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".", sys.argv[2] if len(sys.argv) > 2 else None)
