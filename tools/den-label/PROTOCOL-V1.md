# PROTOCOL-V1: the N1 annotation protocol

This is rung N1's annotation protocol, version 1, and N1's gate. It is
committed alone, as the first den-label commit, before any label exists. The
owner okays it at approval 1 (see `docs/plans/n1-reference/PLAN.md`), after
the look at the commissioning take's contact sheet and before any staged take.
Until that okay it may be revised in place; once the first label exists it
never changes, and any change is a `PROTOCOL-V2.md`. `n1.py` reads every
setting it uses from the one fenced block below. Every draft, both manifests
and every freeze line record the sha256 of this file, and `label.py` refuses a
draft made under another sha256.

It rests on the owner's answers of 2026-11-02: any den footage may be kept on
household computers; the owner labels alone, blind, with a delayed self
re-mark; onset is contact-starting; R, N1 and N2 go first; the seed demand
gates.

## Settings

```n1-protocol-v1
# The phone's band: "casual" (the default) or "none", chosen at the okay.
phone_at_counter_band = "casual"
# Pause rule: a pause of this many seconds or less does not end an interval.
pause_s = 6
# The re-mark waits at least this many days after its label pass.
remark_days = 3
# A13's late-off band after a labelled offset; quiet seconds inside it are not scored.
tau_off_s = 60
# The starting pad. It may only grow, and only before the first fixture freeze.
pad_start_fraction = 0.2
pad_min_px = 16
```

The phone band differs from the architecture by default: A13 scores "on a
phone at the counter" as no need, and A7's example gives a self-lit screen a
visual demand of 0.2, under the 0.4 casual cut. If the owner picks `none`,
`phone_at_counter` becomes a no-need shortcut case, a quiet-span case, and
each round's phone block becomes a `tidying` block (see the plan's "Session
staging"). That is settled at the okay, before any take.

## The box rule (the input fixture)

The owner draws the tight box around the whole visible person, including arms,
hands and anything held. The tool adds a fixed pad and clips to the 640x360
image. The pad starts at `pad_start_fraction` of the box's width on each side
and of its height top and bottom, and at least `pad_min_px` px. It is one
setting for the whole fixture, may only grow, and only before the first
fixture freeze. The owner never draws a box around an object, a hand or a work
area, and never adds a keyframe because an activity starts. Keyframes go where
the tight box stops fitting the body. Straight lines in `t_s` fill in between
keyframes. A "person not in view" keyframe leaves frames empty until the next
keyframe; frames before the first keyframe are empty, and the last keyframe
holds to the end. The whole usable part is audited at up to 4x with the padded
box before the session can be frozen.

## Labels

1. **Engagement.** The person works with an object or surface they need to
   see. An interval runs from onset to offset.
2. **Onset (contact-starting).** The first frame showing the hand touching the
   worked object (`touch`). If a task starts with no touch, for example
   reading a page left on the desk, the onset is the first frame the person is
   settled facing it at working distance (`no_touch`).
3. **Offset.** The last frame of contact before contact with that object stops
   for more than `pause_s` s (`release`). For a `no_touch` task, it is the
   frame the person turns away or leaves working distance. An interval never
   crosses a cut or a segment edge. One running at a segment edge stops there,
   and label.py marks that end `carried`. An end is `carried` exactly when it
   sits on a segment's first or last frame.
4. **Pauses and switches.** A pause of `pause_s` s or less with the same
   object does not end the interval. Switching to a different task starts a
   new one. label.py refuses two intervals of the same case in one segment
   whose gap is `pause_s` s or less; the rule never applies across a cut.
5. **What onset delay is timed to.** From the labelled `touch` onset to the
   first estimator output for that interval whose `focused_engagement` state
   leaves `none` (first notice, `starting`). Only onsets marked sure count. The
   5 s dwell, from `starting` to `engaged`, is kept and reported separately. It
   is not part of onset delay. The target is a median of 3 s or less (A13).
   N2 does the timing.
6. **Frame time.** Every mark is a frame. Its time is that frame's `t_s`. At
   about 4 frames a second, marks are quantised to 0.2 to 0.27 s. The true
   touch lies between the previous frame and the marked one.
7. **Demand bands and cut points.** One band per interval, set by the task:
   - `fine`: needs small detail at hand distance. Reading print, mail,
     writing, fine handwork.
   - `casual`: needs to see, but not small detail. Wiping, tidying, handling
     things without reading them, and by default using a phone (its screen
     lights itself).
   - `none`: every usable second outside intervals and uncertain spans.

   On N2's `visual_demand` scale the cut points are the seed demand gates:
   fine at 0.6 or more, casual from 0.4 to under 0.6, none under 0.4. The owner
   labels bands, never numbers. If demand clearly changes within one task, set
   `sure.band` false. Under `phone_at_counter_band = "none"`, phone use is not
   an interval: it is a quiet span with case `phone_at_counter`.
8. **Cases.** Each interval has one case: `sorting_mail`, `still_reader`,
   `wiping`, `phone_at_counter` (under the `casual` phone band), `tidying`, or
   `other`. The first four are the shortcut cases, where a cheap cue (motion,
   posture or place) points the wrong way. Quiet spans have `idle_seated` or
   `idle_standing`, and under the `none` phone band `phone_at_counter`, which
   stays a shortcut case as a no-need span: a person is present and not
   engaged, and the right answer is no engagement. `low_light` spans mark time
   with the room dim. Spans need not be frame-exact.
9. **Quiet seconds and τ_off.** A quiet second tests "no engagement" only if
   it is more than `tau_off_s` s (A13's late-off band, which isn't counted)
   after the latest labelled offset before it. Earlier quiet seconds are
   labelled the same way, and N2 does not score them as quiet. `status` shows
   the quiet seconds past τ_off per split and case.
10. **Uncertainty.** Never guess. If unsure of a boundary or a band, set that
    `sure` flag to false; it is left out of that measure only. If unsure
    whether the person is engaged at all, mark an `uncertain` span. It is left
    out of all scoring and counted in the manifest's `uncertain_s`. Quiet and
    uncertain spans never overlap an interval or each other; `low_light`
    spans may overlap anything.
11. **Who labels.** The owner, alone. Every item records `by: "owner"`, and
    every file records its pass and times. Results are reported as
    single-labeller results with a delayed self re-mark.
12. **Blind.** Labels are made and frozen before any estimator output exists
    for those sessions. The label page shows only frames: no boxes, no cue
    times, no estimator output. N2 may open its output only after the labels
    freeze line is on `origin`.

## Split

- By whole session, assigned by rule when `label.py sessions` registers a
  session, before any pass and long before any tuning.
- Within each staged script name (`hard-a`, `hard-b`), sessions alternate
  development, held-out, development, held-out, in recording order
  (`take.json`'s `started_unix`). The first of each script is development.
- `split.json` is append-only and chained by hash. A session's split never
  changes. Sessions added later continue the same alternation and arrive in a
  new labels version.
- N2 tunes on development only, and reads held-out labels only in its scoring
  run. `status` shows held-out counts, never held-out contents.

## Re-mark

- The re-marked session is the second development session in recording order
  (normally the first `hard-b`). The owner re-marks it from scratch in label
  mode at least `remark_days` days after its label pass was marked end to end,
  with the first labels hidden.
- Two intervals match if their overlap in `t_s` is at least half of each one's
  length, paired greedily by the largest overlap. n1.py reports matches, and
  band and case agreement over matches. It reports onset and offset
  differences (median and p90 of the absolute difference) only over ends that
  both passes marked sure and placed by hand: onsets `touch` or `no_touch`,
  offsets `release` or `no_touch`. Carried ends are left out of the
  differences and counted separately, and so are unsure ends.
- The first-pass labels stay the gold. The re-mark only reports consistency.
