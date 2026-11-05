"""Shared helper for the N2plan probes: N1's staged scripts as timelines (synthetic only).

Encodes the hard-a and hard-b round tables of N1's plan (claude/plan-n1 at b1f1a9d2,
docs/plans/n1-reference/PLAN.md lines 257-321): 5 rounds, the first cue 3 s after
"Begin" (first block 3 s shorter), a 20 s lights_down block after round 3 and a final
30 s block after round 5. Times are seconds after "Begin". Not a probe by itself.
"""

FINE, CASUAL, NONE = "fine", "casual", "none"


def blocks(script, phone_band="casual"):
    """[(case, band_or_None_for_quiet, start_s, end_s, kind)] with kind in task/quiet/other."""
    out, t = [], 3.0
    for rnd in range(1, 6):
        if script == "hard-a":
            long_seated = rnd in (1, 3, 5)
            row = [("idle_seated", None, 90 if long_seated else 30, "quiet"),
                   ("still_reader", FINE, 30, "task"),
                   ("idle_standing", None, 30 if long_seated else 90, "quiet"),
                   ("sorting_mail", FINE, 30, "task"),
                   ("idle_standing", None, 30, "quiet"),
                   ("wiping", CASUAL, 25, "task"),
                   ("idle_standing", None, 30, "quiet")]
            if phone_band == "casual":
                row.append(("phone_at_counter", CASUAL, 25, "task"))
            else:
                row.append(("tidying", CASUAL, 25, "task"))
                if rnd in (2, 4):  # phone moves right after the long idle_standing block
                    row.insert(3, ("phone_at_counter", None, 25, "nospan"))
                    row.insert(4, ("idle_standing", None, 30, "quiet"))
        else:
            long_standing = rnd in (2, 4)
            row = [("idle_standing", None, 90 if long_standing else 30, "quiet"),
                   ("wiping", CASUAL, 30, "task"),
                   ("idle_standing", None, 30, "quiet")]
            if phone_band == "casual":
                row.append(("phone_at_counter", CASUAL, 20, "task"))
            else:
                row.append(("tidying", CASUAL, 20, "task"))
            row += [("idle_standing", None, 30, "quiet"),
                    ("sorting_mail", FINE, 25, "task"),
                    ("idle_seated", None, 30 if long_standing else 90, "quiet"),
                    ("still_reader", FINE, 35, "task")]
            if phone_band != "casual" and rnd in (2, 4):
                row.insert(1, ("phone_at_counter", None, 25, "nospan"))
                row.insert(2, ("idle_standing", None, 30, "quiet"))
        for i, (case, band, dur, kind) in enumerate(row):
            if rnd == 1 and i == 0:
                dur -= 3.0
            out.append((case, band, t, t + dur, kind))
            t += dur
        if rnd == 3:
            out.append(("lights_down", None, t, t + 20, "other"))
            t += 20
    final = "idle_standing" if script == "hard-a" else "idle_seated"
    out.append((final, None, t, t + 30, "quiet"))
    return out


def intervals_and_spans(script, phone_band="casual", touch_after_s=1.0, release_before_s=0.5):
    """Gold intervals (task blocks) and no-need spans (quiet blocks, and the phone under 'none')."""
    ivs, spans = [], []
    for case, band, a, b, kind in blocks(script, phone_band):
        if kind == "task":
            ivs.append({"case": case, "band": band, "on": a + touch_after_s, "off": b - release_before_s})
        elif kind in ("quiet", "nospan"):
            spans.append({"case": case, "start": a, "end": b})
    return ivs, spans


def scored_from(span_start, ivs, tau_off=60.0):
    offs = [iv["off"] for iv in ivs if iv["off"] <= span_start]
    return max(span_start, offs[-1] + tau_off) if offs else span_start
