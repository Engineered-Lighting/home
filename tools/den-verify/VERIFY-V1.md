# VERIFY-V1: rung 10, did the light land where the model said?

Rung 10 checks, after each scene change on the den's four Hue bulbs, that the
light landed where rung 8's light model predicted. It reads `decision`,
`light_state` and `light_model` records and writes two things back:
`Decision.verify` and `LightState.residual`. Field names are those of
`docs/architecture/live-v1/schemas.json`; the few keys the contracts leave
unnamed are listed under "Keys the contracts do not name" and sit behind an
open slot.

This note was committed before any code, and revised on 2026-11-05 after the
pipeline review, with no session yet run: the held rule, which holds pin a
bulb, the rule for dims, the meter's noise in the level check and which read
frames can be used. Which points count now comes from predictions and times alone,
never from a reading. Revised again on 2026-11-06 to read rung 8's and rung 9's real
records, with the numbers below as they were: what "verified" means, unknown state, a
person's change before a decision, several evenings in one run and unknown causes.
Nothing here has run on real light.
Running needs the owner's go-ahead; building does not.

## What is scored

**Points.** A verify point is a surface: every key of `Decision.predicted.surfaces`,
every `Decision.targets[].surface_id`, and every `Decision.recipients[].targets[]`
of kind `task` or `layer`. Eyes are never points (see "Eyes").

For each point at the verify frame:

- `camera_estimate` is the frame's `surface_estimates[].value` for that
  surface, in the frame's `unit`.
- The prediction is the light model's, for the **actual resulting state**: the
  read-back of every channel in `Decision.applied_scene`, the channels it does
  not list taken from `Decision.inputs.actuator_state`. The decision's own copy
  of the holds (`Decision.inputs.hold_set`) marks its channels held and pins only
  a channel `applied_scene` gives no read-back for: a read-back is this decision's
  own and comes after its copy, and a game's vacancy turns a held bulb off and
  keeps the hold. Each `Correction.hold` past that copy's revision and in force at
  the frame then pins over them, in revision order. A Correction at or below the
  copy's revision is already in it, or has lapsed; nothing writes `ended` after
  capture, so the copy is what says so. With no revision on either side, a hold
  counts only if it began after the decision. Requested values are never used.
  A lit bulb with no level, a bulb never seen (a null entry), and a read-back with no
  level and no pin to take are unknown, never 0. Every point that bulb can reach has
  no prediction, whatever the reading: `unverifiable`, not a hit or a miss, with
  the bulb named in the point's `why` and the decision in `decisions.unknown_state`.
  The state before only matters through the read frame, so under `zero` a bulb
  unknown before stops only its increments, and widens no bound.
- `residual` = (measured − predicted) / predicted, null when the prediction is
  not positive.
- `status`:
  - `within` when |measured − predicted| ≤ `tolerance` × predicted (inclusive,
    compared in exact rationals so 345 against 300 is within), or ≤ the wider
    bound the prediction itself inherits (see "The background");
  - `outside` otherwise;
  - `prior_only` when the estimate's `basis` is `prior`;
  - `unverifiable` when the surface is missing from the frame, its usable share
    (1 − `clipped_frac`) is below `v_min`, its unit differs from the model's,
    nothing is lit there and nothing changed, or under `reference_frame` the
    frame the decision read is stale (see "Read frames"). Its `residual` is then
    null.

A `fallback_patch` estimate is scored here, because a fallback patch is a
verify aid. It never counts as delivered service: the gold share below treats
it as unverifiable.

**The background.** Rung 8's model predicts electric light only; "rung 8 does
not model daylight" (its PLAN.md). The prediction is therefore
D(p) + Ê(p; state), and the background D comes from the slot `background`:

- `reference_frame` (proposed): D(p) = measured − Ê(p; state before), on the
  frame the decision read (`Decision.inputs.light_state`) with the state it read
  (`Decision.inputs.actuator_state`), unless that frame is stale (see "Read
  frames");
- `zero`: D(p) = 0, for runs after dark.

Under `reference_frame` the prediction works out as the read frame's measurement
plus the model's change, so it inherits the model's error on every bulb that
changed, at that bulb's brighter level, and that error does not shrink with the
level left. With the model 5% off and every bulb landing as commanded, a 0.8 to
0.1 dim reads −23% or +49%, and a crossfade of two bulbs with no net change reads
−18% with each bulb 10% off. So under `reference_frame` a point is judged to the
widest of `tolerance` × predicted, `change_allowance` × the brighter level of
every bulb that changed, summed, and the meter's noise (see "Measurement
uncertainty"). A bulb that did not change adds nothing: its error is in both
frames and cancels. One that moved by less than `tolerance` of its brighter level
counts in proportion to how far it moved, so at the proposed values it adds its
own move: a read-back step, a colour nudge or a whole-percent pin widens a bound by
about its own size, never by a whole bulb. For a bright-up the first is normally
the widest, so the ladder's ±15% stands; on a dim or an off the second usually is.
The record gives `level_bound` wherever the first is not the widest.

Every point is then a hit or a miss, whichever way the model is off. The rule
looks at no reading, so it cannot keep hits and drop misses. What it costs:

- A fault smaller than the allowance reads as a hit. At the proposed 0.15, one
  bulb of four stuck on a dim shallower than about 2.5:1 is a hit on the level (its
  increment may still read `outside`), and one stuck on a deeper dim or an off is a
  miss. That is the line the ladder's ±15% draws on the same bulbs going up, so a
  stuck bulb reads the same both ways. A model more than 15% off at a bulb's
  brighter level is a miss; rung 8's gate is a median of 8% and a p90 of 19%.
- `change_allowance` = 0, with the meter's bounds at 0, is the ladder's literal
  level check, with the false misses above. `zero` needs no allowance and is
  sharper after dark, because it reads the model's own level: there only an off,
  with no level to take a share of, uses it.

The report and the console give how many hits are beyond ±15% of the level and
what the hit rate would be if they had missed (`hits_beyond_level_tolerance`).

**Increments.** For each point, `measured_change` = Ê_after − Ê_before on the
camera (verify frame minus the decision's read frame) and `predicted_change` =
E_after − E_before from the light model alone (the contribution of the channels
whose state changed; never predicted-after minus measured-before). Steady
daylight cancels in the measured change, and both sides are in the same
camera units, so no camera scale `k_L` is needed: v0 runs relative
(`anchored` false) before rung 7 exists. Status is `within` when
|measured − predicted| ≤ `tolerance`·|predicted| + uncertainty, else `outside`.

**Spill.** At each visible other-person region (a track in `Decision.targets`
or the recipients' task targets that no channel this decision changed serves;
visible = in the frame with basis `measured` or `fallback_patch`):
`spill` = max(0, E_j − (T_j^hi + a_j)), with T_j^hi from `spill_band_top` and
a_j = `spill_allowance_lx`. Spill is in lux, so a relative frame gives
`unverifiable`. With uncertainty u: `within` when E_j + u ≤ T_j^hi + a_j,
`outside` when E_j − u > T_j^hi + a_j, otherwise `unverifiable`. Spill is
reported and never gated.

**Measurement uncertainty.** Each spill and increment entry carries its
uncertainty, computed only from bounds fixed before the run:
u(E) = `uncertainty_rel`·|E| + `uncertainty_abs_lx` (the lux term only when
anchored). An increment's is u(after) + u(before).

A level check carries it too, as the meter's noise: √(u(predicted)² + u(read
frame)²) under `reference_frame` and u(predicted) under `zero`, taken at the
prediction, not the reading. A reading inside that noise is not a miss. Where the
tolerance on the level is narrower than the noise (below about 130 lx at the
proposed bounds) and a change there is too (below about 15 to 20 lx), the meter
cannot tell a bulb that moved from one that did not, but it can tell either from a
bulb far off. Such a point still counts both ways, judged to the noise: a 40 to 25
lx dim that reads 125 lx is a miss, and one that stayed at 40 lx is a hit the meter
could not have told from a landing. Those hits lift nothing, so the gate reads the
rate without such points too (see "The gate"). The report and the console count
them, with their hits and misses and that rate (`below_meter_noise`). A fault inside
the noise on a larger change cannot be seen either: one bulb of four stuck on a 60
to 12 lx dim reads 12 lx high, and is a hit. Relative frames have no lux term, and
none of this changes their result.

**Held and superseded channels.** The ladder says a held or superseded channel
is excluded from misses. Here that means the channel, not the point: a person or
an earlier decision set it, so its light must not make a miss, and a free bulb's
fault must still show beside it. The prediction takes these channels where they
are. Under `reference_frame` one that stayed put is in both frames, so its light
and the model's error on it cancel; one that moved since the read frame adds its
brighter level to the allowance above, like any bulb that changed. Under `zero`
its error stays in the point's error, like every channel's, so a model far off on
a held bulb can still make a miss there.

Let H be a point's light from channels whose applied status is `held` or
`superseded`, or that a hold in force lists, each at its brighter level between the
read frame's state and the verify frame's, so a bulb held off still counts as held. A
point with H below `held_share_min` of its predicted light counts like any other.
At or above it, the slot `held_rule` says:

- `free_bulbs_count` (proposed): the point counts, hit or miss, unless what this
  decision answers for there is inside the point's bound, so that its free bulbs
  failing outright could not show. That is the free bulbs' light, or, when a held
  channel moved past the bound since the read frame, only their own change, since
  the rest of the error may be that channel's. Such a point is left out both ways.
  With one bulb of four held at 17% of the light, a stuck free bulb reads 21% low
  and is a miss, and one point in A's shadow costs one point of twenty, as they do
  with no hold. A bulb A turned off by hand, on a decision that changed nothing at
  that point, leaves the point out. A game's vacancy off, with free bulbs going off
  beside the held one, counts: landed it is a hit, and with any bulb left on it is a
  miss once that bulb's light is past the bound.
- `exclude_both`: every such point is left out both ways. This is simpler and is
  the plainer reading of the ladder's words, but during a hold a free bulb's fault
  goes uncounted: one free decision at 20 of 20 and one held decision with two free
  bulbs stuck 41% low reads 1.0, a pass.

Neither rule looks at the reading. Keeping hits and dropping misses lets any hold
lift a failing session to a pass; dropping hits and keeping misses lets one far
point sink a good one. An `outside` left out is explained as `held`. A point where
a held or superseded channel gave any light, before or after, is never explained
as drift, and its LightState residual never carries a `drift` flag. The report
gives the points left out, in total and by status (`excluded_held_or_superseded`,
`excluded_held_by_status`), and the console says how many. A run with nothing left
to count is `insufficient`, never a pass.

**Verified.** A decision counts as **verified** when something was judged: at
least one of its points read `within` or `outside` against a prediction and was not
left out under the held rule, so that `verify.within_tolerance` is true or false.
Nothing else makes it verified: not having a verify frame, and not having a `verify`
block. A decision with a verify frame and nothing judged keeps its block, each point
giving its `why`. Any decision not verified has a sibling `verify_status`, and the
report lists it in `decisions.not_verified` with the reason and its kind: no read
frame, no frame after settling, no surface map, no `applied_scene`, unknown state,
read frame stale, superseded, model error, nothing readable, nothing lit, left out,
no surface named, another light model or no settle time. A verified decision may
still be out of the hit rate, as one not admitted is, so the console's first line
gives both counts and then the points the hit rate counted. Nothing counted reads
`insufficient`, however many decisions there are.

**The gate.** A hit is a scored surface point (`within` or `outside`, basis
`measured` or `fallback_patch`, not excluded, admitted) that is `within`. The
hit rate is hits / (hits + misses). It passes at **≥ 85%**, compared exactly
(17 of 20 passes). Orientation: higher is better. Where some points have a change
below the meter's noise, the rate on the others must pass too, and with no other
point counted a rate that would pass reads `insufficient`. The report adds a two-sided
95% interval from 10,000 session-bootstrap rounds (owner choice 6; a session is
a local calendar day with at least one point counted, and the report gives how
many days had none) and the margin `delta_hit_rate`, which the owner has not set.

**Misses explained.** Each counted miss gets one cause, tested in this order:

1. `shadow`: the verify frame is occupied, a person's position is within
   `shadow_radius_m` of the point (the nominal body envelope), and the residual
   is negative. Not admitted until `envelope-parameters` is decided.
2. `daylight`: the summed window daylight changed by at least
   `daylight_change_min` between the read frame and the verify frame, in the
   same direction as the residual.
3. `drift`: the increment at the point also misses in the same direction, the
   frame is not occupied, and no held or superseded channel gave any light there.
4. `unexplained`.

The report gives the share explained (daylight, shadow, drift) and the count
of each.

**Eyes.** No eye point is ever written as verified. Every eye in
`Decision.predicted.eyes` and every recipients target of kind `eye` goes to
`not_verified` as `eye-<track>`. The report gives per-point status counts and
unverifiable coverage: (unverifiable + prior_only) / surface points, and
(unverifiable + prior_only + eyes) / all targets.

**Gold need time.** The report gives the verifiable share of gold need time at
the owner's marks, because it bounds delivered-service evidence at rung 11. A
gold second counts as verifiable when its `R_eval` mark has `placement_sd` ≤
`tau_r_cm` (owner choice 7, strict), and the latest frame within
`max_frame_gap_s` is settled and shows the mark's surface with basis `measured`
and a usable share ≥ `v_min` (owner choice 8). The remaining seconds are split
into `geometry`, `no_frame`, `mid_fade` and `unverifiable`.

## On which frames

**The settle rule.** A step went out when it has a `sent_at`. Its end is:

- for a step of kind `move`, `fade`, `fade_in` or `fade_out` that logs
  `result.at`, that time (the actuator reported completion);
- otherwise `sent_at` + the fade + `settle_margin_s`, with the fade from the
  step's `transition_s` if it logs one, else from the policy:
  `den_lighting@…` steps fade `fade_s_den_lighting` (2 s), or
  `fade_s_den_lighting_restore_on` (5 s) for a `restore` step to an "on" target
  (den_lighting `policy.py:226` on `claude/den-core`); `den-light-probe…` steps
  and `probe` steps fade `fade_s_probe` (0 s; rung 8's probes send
  `transition: 0`). Any other step with no logged fade cannot be settled, and
  its decision is not verified.

A decision has settled at the latest end of its steps that went out (its `ts`
when none did). A person's change (a Correction) is busy from `ts` −
`action.settle_s` to `ts` + `settle_margin_s`.

**Verify frames.** A frame is excluded as **mid-fade** or **mid-move** when it
falls between a step's `sent_at` and its end, for any decision, or inside a
person's change. A decision's verify frame is the first frame at or after its
settle time that is not excluded and comes before the next decision's first
step. If the next decision sends a step before this one has settled, it was
superseded before it settled; if no frame qualifies, likewise `verify` is null
with `verify_status` giving the reason.

**Read frames.** The background and the increments take the frame the decision
read, so it has to show the state the decision read. It is stale when it falls
inside a step's window, or is older than a step another decision sent before this
one: a Hue bulb reports its target at once, so `actuator_state` already shows that
step and the frame does not, and D would take up the difference. With frames 5 s
apart and a perfect model, a perfect landing read 0 of 6 on such a frame and a bulb
that stayed stuck read 6 of 6. A person's change whose window starts at or before the
decision and ends after the read frame makes that frame stale too, for the same
reason: a Correction at t+2, a decision at t+4.7 and a read frame at t read 0 of 6 on a
perfect landing. One whose window starts after the decision, or ends by the read frame,
does not. A stale frame gives no reference, whatever is read later. Under
`reference_frame` the decision's points are `unverifiable` and a LightState under it
has no residual. Under `zero` the level check stands and only its increments are
`unverifiable`. The report lists these decisions (`decisions.read_frame_stale`) and the
console says how many.

**Occupied seconds** are flagged: a frame inside an occupancy interval is
marked occupied in the report and in its residual `flags`. Occupied frames are
still scored; occupancy feeds the shadow explanation.

**LightState residuals.** Every frame that is not excluded, after the first
decision, gets `residual`: one entry per surface the frame measures and the model
predicts, against the actual resulting state of the latest decision in force at
the hold set's current revision. Where the prediction is not positive the entry
keeps its absolute `residual` with `residual_pct` null, so a bulb left on after an
off is recorded whichever way the model is off. Case V11: after A's dim (c-190405
pins F1.spot at 50%, revision 8), the applied scene predicts 368 lx at counter-1
until a re-solve, and ls-190409's 357 lx is a −2.99% residual, flagged `held`,
never `drift`.

## Pending owner decisions, built as named options

Cases that depend on these are reported but not admitted as pass or fail:

| Decision | Options in the register | What depends on it here |
|---|---|---|
| `envelope-parameters` | `set` | The shadow explanation's nominal envelope (`shadow_radius_m`). A `shadow` cause is reported with `admitted: false`. |
| `fixture-capabilities` | `set` | Hue landing on a command sent mid-transition. A decision with a step sent while an earlier step on the same channel was still fading is verified but left out of the hit rate. |
| `promotion-margins` | `two-sided-95` (owner choice 6), `one-sided-95` | The interval is two-sided 95% from 10,000 rounds; δ is the open slot `delta_hit_rate`. |

Not depended on: `compensation` (verify reads only `pinned`, so P0 to P3 give
the same verify; `hold_limited` is never a verify miss), `attribution-thresholds`
(spill reads the Decision's `served` lists as written and is never gated) and
`photometry-commissioning` (until it is answered every frame is relative, and
spill stays `unverifiable`).

## Keys the contracts do not name

Behind the slot `record_extensions`, proposed `verify-ext/v1`:

- `LightState.residual[]` is typed `{surface_id, measured − predicted}`. The
  tool writes `{surface_id, predicted, residual, residual_pct, unit, against
  {decision_id, hold_set_revision}, flags[]}`, with `residual` = measured −
  predicted in `unit`, `residual_pct` null when the prediction is not positive,
  and `flags` from `held`, `occupied`, `outside`,
  `daylight`, `shadow`, `drift`, `unexplained`, `fallback_patch`,
  `wide_tolerance` (`within` only by the wider bound), `below_noise` (the change
  there is below the meter's noise).
- `spill[]` and `increments[]` entries add `uncertainty`, in the block's `unit`.
- `points[]` entries add `level_bound`, in the block's `unit`, only where the
  point is judged to more than `tolerance` × predicted, and `why`, only where the
  point is `unverifiable`.
- `explanation` is null when nothing missed, else a list of
  `{point_id, cause, basis}`.
- `verify` is null with a sibling `verify_status` when no verify frame exists; a
  decision with a frame and nothing judged keeps `verify` and has `verify_status`
  too. A frame with no residual (mid-fade, before any decision, under a decision
  whose read frame is stale, or with no surface it reads and a prediction reaches)
  keeps `residual: []` with a sibling `residual_status`.
- A LightState is named by an `id` key when its line has one, else
  `ls-HHMMSS` in local time, with hundredths for a frame off the whole second
  (the contracts cite `ls-190409` but give LightState no id field). Rung 9 derives
  `ls-<ts to the millisecond>` instead, and either reads. Two frames may share an
  `ls-HHMMSS` id on different days, so frames are keyed by `ts`.
- `Decision.inputs.light_state_ts`, when it is a number, says which frame the
  decision read: the one at that `ts`, within 0.0005 s. Else `inputs.light_state`
  does: the frame of that id within 12 hours of the decision. A verify frame, and a
  decision in force for a residual, stay within 12 hours too, so one evening's
  decision never reads the next evening's first frame.

## Inputs the contracts do not define

Tool-local JSON Lines, all synthetic in tests:

- **occupancy**: `{start, end, people: [{track_id, xy}]}`, Unix seconds and
  room-frame metres;
- **gold**: `{episode_id, track_id, start, end, R_eval: {surface_id,
  placement_sd, source}}`, following `EvalRecord.gold_set.R_eval`.

Clock strings (`19:03:32.55`) in Decision and Correction times are read as
local time on the day of a numeric anchor in the same record (its read frame,
else a `person_state` id), at a UTC offset given on the command line or taken
from the PC's time zone.

## The numbers

Each line is `name = value  # note`. A value is a number or a quoted string;
`open` marks an empty slot, with its proposed value in the note. The tool
refuses a scored run while any slot is open; `--preview` runs on the proposed
values and writes no records. The file reads the same with CRLF or LF.

```verify-v1
# Fixed by the contracts or the owner
tolerance = 0.15                    # ladder rung 10: relative, either way
hit_rate_min = 0.85                 # ladder rung 10
fade_s_den_lighting = 2             # den_lighting policy.py:226
fade_s_den_lighting_restore_on = 5  # policy.py:226, an "on" target while restoring
fade_s_probe = 0                    # rung 8 probes send transition 0
v_min = 0.5                         # owner choice 8: at least 50% of a patch usable
tau_r_cm = 5                        # owner choice 7: strict, tau_R 5 cm
interval_level = 0.95               # owner choice 6: two-sided 95%
bootstrap_rounds = 10000            # owner choice 6
# Open: the contracts leave these to the owner
settle_margin_s = open              # open, proposed 1; Hue landing after a fade (den_lighting's own readback window is 3)
background = open                   # open, proposed "reference_frame"; or "zero"
change_allowance = open             # open, proposed 0.15; the read-frame prediction's share of each changed bulb's brighter level (rung 8's gate: median 8%, p90 19%); 0 is the ladder's literal level check
held_share_min = open               # open, proposed 0.05; held or superseded share of a point's light, from which held_rule applies
held_rule = open                    # open, proposed "free_bulbs_count"; or "exclude_both"; which held-lit points count
uncertainty_rel = open              # open, proposed 0.03; per frame, a fraction of the estimate
uncertainty_abs_lx = open           # open, proposed 10; anchored frames only, rung 7's meter tolerance once set
spill_band_top = open               # open, proposed "target_lx"; T_hi is Decision.targets[track].lx
spill_allowance_lx = open           # open, proposed 30; the walkthrough's illustrative allowance
daylight_change_min = open          # open, proposed 0.05; summed window level, in the window unit
shadow_radius_m = open              # open, proposed 0.5; nominal body envelope, pending envelope-parameters
max_frame_gap_s = open              # open, proposed 5; metering frames come every few seconds
delta_hit_rate = open               # open, proposed 0.05; promotion-margins leaves every delta open
record_extensions = open            # open, proposed "verify-ext/v1"; the keys listed above
```
