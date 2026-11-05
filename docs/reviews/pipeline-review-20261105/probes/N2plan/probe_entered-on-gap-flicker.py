"""probe_entered-on-gap-flicker: N2 plan section 4.2 scores d_on = 0 for any on-run that began after
the previous labelled offset, however early, while the gap it began in is never scored.

What it checks
  An independent implementation of PLAN.md section 4.2 (timed onsets, entered_on / stayed_on /
  entered_from_ending / missed, median of max(d_on, 0)) and of false_on and early_off from 4.4,
  run on N1's hard-a and hard-b scripts (5 rounds each) with three synthetic person_state logs:
    honest6   - turns `starting` 6 s after every touch (a real, too-slow onset): median 6 s, FAILS <= 3 s;
    flicker   - same 6 s response after the long quiet blocks, but after every task it emits one
                `ending` record at offset+4 s and re-enters `engaged` at offset+8 s (one noisy reply,
                then hold rows keep it engaged) - it never reacts to the next touch at all;
    flicker0  - like flicker but misses the long-gap onsets entirely.
  The plan's own count (15/20 hard-a and 14/20 hard-b onsets follow a gap wholly inside the previous
  task's tau_off) means the touch-blind flicker log scores median onset 0 s, false_on 0 and
  early_off 0: the onset gate passes by construction of the tag rule.

Run
  python3 probe_entered-on-gap-flicker.py <root of claude/plan-n2 checkout>
  (if <root>/tools/den-n2/score.py exists, its onset_of() is run on the same logs as a cross-check)
Synthetic data only; no network; temp nothing.
"""
import importlib.util
import re
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n2_scripts import intervals_and_spans, scored_from  # noqa: E402

INF = math.inf
ON = ("starting", "engaged")
TAU_OFF = 60.0


def plan_text_checks(root):
    text = (Path(root) / "docs/plans/n2-need-test/PLAN.md").read_text(encoding="utf-8").splitlines()
    want = ["tagged `entered_on`", "the run began after the previous labelled offset",
            "15 of every 20 onsets in `hard-a` and 14 in `hard-b`", "where false_on scores nothing"]
    for w in want:
        hits = [i + 1 for i, line in enumerate(text) if w in line or w.replace("`", "") in line]
        if not hits:  # the phrase may wrap; search the joined text
            joined = re.sub(r"\s+", " ", " ".join(text))
            print(("PASS" if w in joined else "FAIL") + f": PLAN.md states {w!r} (wrapped over lines)")
        else:
            print(f"PASS: PLAN.md line {hits[0]} states {w!r}")


def make_log(ivs, mode):
    """[(t, state)] person_state records for one take (one usable segment from t=0)."""
    recs = [(0.0, "none")]
    prev_off = None
    for iv in ivs:
        short_gap = prev_off is not None and iv["on"] - prev_off < TAU_OFF
        if prev_off is not None:
            recs.append((prev_off + 4.0, "ending"))
            if mode in ("flicker", "flicker0") and short_gap:
                recs.append((prev_off + 8.0, "engaged"))  # re-entry in the unscored gap
            else:
                recs.append((prev_off + 24.0, "none"))
        if not (mode in ("flicker", "flicker0") and short_gap):
            if mode == "flicker0" and prev_off is not None:
                pass  # never turns on for this interval: a missed onset
            else:
                recs.append((iv["on"] + 6.0, "starting"))
                recs.append((iv["on"] + 11.0, "engaged"))
        prev_off = iv["off"]
    recs.append((prev_off + 4.0, "ending"))
    recs.append((prev_off + 24.0, "none"))
    recs.sort()
    return recs


def state_at(recs, t):
    i = max(j for j, (rt, _) in enumerate(recs) if rt <= t)
    return i, recs[i][1]


def onset_plan(iv, recs, prev_off):
    """PLAN.md 4.2, read literally."""
    i, st = state_at(recs, iv["on"])
    if st in ON:
        start = i
        while start > 0 and recs[start - 1][1] in ON:
            start -= 1
        if prev_off is None or recs[start][0] > prev_off:
            return 0.0, "entered_on", recs[start][0] - iv["on"]
        return INF, "stayed_on", None
    for t, s in recs[i + 1:]:
        if t >= iv["off"]:
            break
        if s in ON:
            return t - iv["on"], ("entered_from_ending" if st == "ending" else "entered"), None
    return INF, "missed", None


def median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def metrics(ivs, spans, recs):
    onsets, lead = [], []
    prev_off = None
    for iv in ivs:
        d, tag, lead_s = onset_plan(iv, recs, prev_off)
        onsets.append((max(d, 0.0), tag))
        if lead_s is not None:
            lead.append(lead_s)
        prev_off = iv["off"]
    # false_on: scored quiet seconds (past tau_off) with the system on
    on_q = n_q = 0
    for sp in spans:
        a = scored_from(sp["start"], ivs)
        k = math.ceil(a)
        while k + 1 <= sp["end"]:
            n_q += 1
            on_q += state_at(recs, k + 0.5)[1] in ON
            k += 1
    # early_off: engaged seconds after tau_on (3 s) counted from the first on output (onset if already on)
    off_e = n_e = 0
    for iv, (d, tag) in zip(ivs, onsets):
        first_on = iv["on"] + (0.0 if d == 0.0 else d)
        k = math.ceil(iv["on"] + 3.0)
        while k + 1 <= iv["off"]:
            if d == INF and tag == "missed" or (k + 0.5) >= first_on:
                n_e += 1
                off_e += state_at(recs, k + 0.5)[1] not in ON
            k += 1
    return onsets, lead, (on_q / n_q if n_q else None), (off_e / n_e if n_e else None)


def main(root):
    plan_text_checks(root)
    tool = None
    score = Path(root) / "tools/den-n2/score.py"
    if score.exists():
        spec = importlib.util.spec_from_file_location("den_n2_score", score)
        tool = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode = True
        spec.loader.exec_module(tool)
    worst = {}
    for script in ("hard-a", "hard-b"):
        ivs, spans = intervals_and_spans(script)
        for mode in ("honest6", "flicker", "flicker0"):
            recs = make_log(ivs, mode)
            onsets, lead, false_on, early_off = metrics(ivs, spans, recs)
            tags = {}
            for _, tag in onsets:
                tags[tag] = tags.get(tag, 0) + 1
            med = median([d for d, _ in onsets])
            verdict = "passes <= 3 s" if med <= 3.0 else "fails <= 3 s"
            print(f"RESULT {script} {mode:8s}: median onset {med} s ({verdict}); tags {tags}; "
                  f"false_on {false_on:.3f}; early_off {early_off:.3f}"
                  + (f"; entered_on runs began {min(-x for x in lead):.1f} to {max(-x for x in lead):.1f} s before the touch" if lead else ""))
            worst[(script, mode)] = med
            if tool is not None:
                class R:  # the tool's Timeline needs .t, .state and index_at()
                    def __init__(self, t, s):
                        self.t, self.state = t, s

                class TL:
                    def __init__(self, recs):
                        self.recs = [R(t, s) for t, s in recs]

                    def index_at(self, t):
                        return max(j for j, r in enumerate(self.recs) if r.t <= t)
                tl, prev, tool_d = TL(recs), None, []
                for iv in ivs:
                    out = tool.onset_of({"on": iv["on"], "off": iv["off"]}, tl, prev, 0.0, 4.0)
                    tool_d.append(max(out["d_on"], 0.0))
                    prev = iv["off"]
                print(f"  den-n2 score.onset_of on the same log: median {median(tool_d)} s")
    flick = worst[("hard-a", "flicker")], worst[("hard-b", "flicker")]
    honest = worst[("hard-a", "honest6")], worst[("hard-b", "honest6")]
    if max(flick) <= 3.0 and min(honest) > 3.0:
        print("FAIL: a log that never reacts to 15/20 (hard-a) and 14/20 (hard-b) touches passes the onset "
              "gate with median 0 s, false_on 0 and early_off 0, while a real 6 s responder fails it")
    else:
        print("PASS: the onset rule does not reward gap-time on-runs")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
