#!/usr/bin/env python3
"""Verifier probe for finding fine-recall-per-second-coverage-pooled.

Rebuilds the finding's own synthetic session (3 long fine episodes read fine, 3 short fine episodes read
casual, 4 casual episodes with no level) and asks whether the gate as a whole lets it through. score.py also
gates band competence per unit (competence.fine, competence.casual, exact binomial against owner floors).
A unit is right only when its band equals the gold band AND its engagement label equals the gold, so
competence.fine <= per-episode fine recall by construction, and an abstained casual unit is wrong.

Also scales the same pattern to a sample-gate-sized held-out pool (36 fine, 32 casual units) to show what
competence's exact-binomial status is at the floors the finding uses (0.6).

Synthetic data only, no files, no network.
Run:  python3 -B verify_fine-recall-per-second-coverage-pooled.py <root of the claude/den-n2 checkout>
"""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
import score  # noqa: E402

GATE = score.load_gate()


def iv(on, off, band, case):
    return {"on": on, "off": off, "band": band, "case": case, "seg": 0, "onset_kind": "touch",
            "offset_kind": "release", "sure": {"onset": True, "offset": True, "band": True}}


def build(n_long, n_short, n_casual, take="e1"):
    ivs, recs = [], [score.Rec(0.5, "none", 0.9, 0.2, 0.9, 0.0)]
    t = 100.0
    for i in range(n_long + n_short):
        long_ = i < n_long
        off = t + (60 if long_ else 12)
        ivs.append(iv(t, off, "fine", "still_reader" if long_ else "sorting_mail"))
        k = t + 1
        while k < off + 1:
            recs.append(score.Rec(k, "engaged", 0.9, 0.8 if long_ else 0.5, 0.9, k - 1))
            k += 2
        recs.append(score.Rec(off + 1, "none", 0.9, 0.2, 0.9, off))
        t = off + 100
    for i in range(n_casual):
        ivs.append(iv(t, t + 30, "casual", "wiping"))
        recs.append(score.Rec(t + 1, "engaged", 0.9, None, None, None))
        recs.append(score.Rec(t + 31, "none", 0.9, 0.2, 0.9, t + 30))
        t += 130
    s = score.Session(take, "development", 1_793_016_000.0, "2026-10-26", [(0.0, t + 100)], ivs, [])
    s.logs = [recs]
    return score.analyse(s, GATE, 4)


for label, (nl, ns, nc) in (("finding's session", (3, 3, 4)), ("x8 pooled (sample-gate size)", (18, 18, 32))):
    an = build(nl, ns, nc)
    st = score.session_stats(an, GATE)
    fr = score.m_ratio("fine_recall")([st])
    cov = score.m_list("coverage", score.mean)([st])
    out = []
    for band in ("fine", "casual"):
        right, n = st["competence"][band]
        ci = score.clopper_pearson(right, n, 0.95)
        out.append(f"competence.{band}={right}/{n} CP=[{ci[0]:.3f}, {ci[1]:.3f}] "
                   f"vs floor 0.6 -> {score.judge('floor', 0.6, right / n, ci)}")
    print(f"RESULT {label}: fine_recall(per s)={fr:.3f} coverage(pooled)={cov:.3f}; " + "; ".join(out))
    caught = any(score.judge("floor", 0.6, st["competence"][b][0] / st["competence"][b][1],
                             score.clopper_pearson(*st["competence"][b])) in ("failed", "straddled")
                 for b in ("fine", "casual"))
    print(("PASS" if caught else "FAIL") + f" {label}: band competence blocks a feasible verdict "
          "(rule 3 or rule 4) in the finding's scenario")
