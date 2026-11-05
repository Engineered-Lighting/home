# VERIFY-V1: rung 10, did the light land where the model said?

Rung 10 checks, after each scene change on the den's four Hue bulbs, that the
light landed where rung 8's light model predicted. It reads `decision`,
`light_state` and `light_model` records and writes two things back:
`Decision.verify` and `LightState.residual`. Field names are those of
`docs/architecture/live-v1/schemas.json`; the few keys the contracts leave
unnamed are listed under "Keys the contracts do not name" and sit behind an
open slot.

This note was committed before any code. Nothing here has run on real light.
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
  not list taken from `Decision.inputs.actuator_state`, and then every hold in
  force at the frame (`Correction.hold.pinned`, at the hold set's revision)
  pinned over them. Requested values are never used.
- `residual` = (measured − predicted) / predicted.
- `status`:
  - `within` when |residual| ≤ `tolerance` (inclusive, compared in exact
    rationals so 345 against 300 is within);
  - `outside` otherwise;
  - `prior_only` when the estimate's `basis` is `prior`;
  - `unverifiable` when the surface is missing from the frame, its usable share
    (1 − `clipped_frac`) is below `v_min`, its unit differs from the model's,
    or the prediction is not positive.

A `fallback_patch` estimate is scored here, because a fallback patch is a
verify aid. It never counts as delivered service: the gold share below treats
it as unverifiable.

**The background.** Rung 8's model predicts electric light only; "rung 8 does
not model daylight" (its PLAN.md). The prediction is therefore
D(p) + Ê(p; state), and the background D comes from the slot `background`:

- `reference_frame` (proposed): D(p) = measured − Ê(p; state before), on the
  frame the decision read (`Decision.inputs.light_state`) with the state it read
  (`Decision.inputs.actuator_state`);
- `zero`: D(p) = 0, for runs after dark.

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

**Held and superseded channels.** A point where channels whose applied status
is `held` or `superseded`, or that a later hold pinned, give at least
`held_share_min` of the predicted light is still written with its status, but
an `outside` there is **excluded from misses** and explained as `held`. It is
never explained as drift, and its LightState residual never carries a `drift`
flag. A held point that is `within` counts as a hit (see owner questions).

**The gate.** A hit is a scored surface point (`within` or `outside`, basis
`measured` or `fallback_patch`, not excluded, admitted) that is `within`. The
hit rate is hits / (hits + misses). It passes at **≥ 85%**, compared exactly
(17 of 20 passes). Orientation: higher is better. The report adds a two-sided
95% interval from 10,000 session-bootstrap rounds (owner choice 6; a session is
a local calendar day) and the margin `delta_hit_rate`, which the owner has not
set.

**Misses explained.** Each counted miss gets one cause, tested in this order:

1. `shadow`: the verify frame is occupied, a person's position is within
   `shadow_radius_m` of the point (the nominal body envelope), and the residual
   is negative. Not admitted until `envelope-parameters` is decided.
2. `daylight`: the summed window daylight changed by at least
   `daylight_change_min` between the read frame and the verify frame, in the
   same direction as the residual.
3. `drift`: the increment at the point also misses in the same direction, the
   frame is not occupied, and no held or superseded channel contributes.
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
step. If there is none, `verify` is null with `verify_status` giving the
reason.

**Occupied seconds** are flagged: a frame inside an occupancy interval is
marked occupied in the report and in its residual `flags`. Occupied frames are
still scored; occupancy feeds the shadow explanation.

**LightState residuals.** Every frame that is not excluded, after the first
decision, gets `residual`: one entry per surface the model predicts, against
the actual resulting state of the latest decision in force at the hold set's
current revision. Case V11: after A's dim (c-190405 pins F1.spot at 50%,
revision 8), the applied scene predicts 368 lx at counter-1 until a re-solve,
and ls-190409's 357 lx is a −2.99% residual, flagged `held`, never `drift`.

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
  predicted in `unit`, and `flags` from `held`, `occupied`, `outside`,
  `daylight`, `shadow`, `drift`, `unexplained`, `fallback_patch`.
- `spill[]` and `increments[]` entries add `uncertainty`, in the block's `unit`.
- `explanation` is null when nothing missed, else a list of
  `{point_id, cause, basis}`.
- `verify` is null with a sibling `verify_status` when no verify frame exists.
- A LightState is named by an `id` key when its line has one, else
  `ls-HHMMSS` in local time (the contracts cite `ls-190409` but give
  LightState no id field).

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
held_share_min = open               # open, proposed 0.05; share of a point's light from held or superseded channels
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
