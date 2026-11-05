# METHOD-V1: Stage 0 part (can the camera meter light?)

This is rung 8's method, version 1, Stage 0 part. It is committed alone, as
the first commit on `claude/rung-8-stage0`, before any code that reads it and
before any sweep frame. `meter.py` and `sweep.py` read every number they use
from the one fenced block below whose info string is `method-v1 stage0`.
`install.json` and every `response.json` record the sha256 of this file and of
that block's text. Stage 1 later adds its own part below (a `method-v1 stage1`
block); the Stage 0 block does not change after the first sweep frame.

The plan is `docs/plans/rung-8-light-model/PLAN.md` (Stage 0). It names each
statistic; this file fixes its number.

## Numbers

A line marked `open` holds a number the plan left open. Its value is a
proposal only. `sweep.py` refuses to start a sweep while any line is marked
`open`. The owner confirms or changes each one, and the marks come off in one
commit, before any sweep frame. The other lines carry numbers the plan fixes.

```method-v1 stage0
# meter-norm/v1, per pixel
white_balance_kelvin = 4000    # open: the one fixed white balance for every capture
clip_limit_r = 250             # open: a pixel is flagged when R reaches this code
clip_limit_g = 250             # open: the same for G
clip_limit_b = 250             # open: the same for B
normalise_code = 128           # the inverse response is scaled so that f(128) = 1
weight_r = 0.2126              # open: Rec. 709 luminance weights on linear values
weight_g = 0.7152              # open
weight_b = 0.0722              # open

# Pixels and regions
pixel_step = 4                 # open: every 4th pixel each way (160 x 90)
grid_cols = 4                  # the 4x4 grid
grid_rows = 4

# Noise tolerance (N); Stage 1 reuses it
noise_tolerance_codes = 2      # open

# Timing
countdown_s = 15               # about 15 s
still_span_s = 2               # open: how long frames must agree to count as still
still_wait_limit_s = 20        # about 20 s
run_limit_s = 100              # about 100 s, from the countdown's start to the release
hold_ttl_s = 30                # the PC asks for 30 s
renew_every_s = 10             # and renews every 10 s
poll_s = 0.2                   # open: den-take's polling interval
settle_limit_s = 2             # open: wait for a step's first agreeing frame
frames_per_step = 3            # open: frames kept per exposure

# Exposure steps
step_ratio_low = 0.0625        # 1/16 of auto's pick
step_ratio_high = 2            # 2 times auto's pick
step_count = 11                # 11 exposures, about 1.41x apart
max_exposure_us = 33000        # open: the plan says the 30 fps frame time (33333 us); DepthAI's manual range ends at 33000 us
iso_max = 1600                 # open: DepthAI's manual ISO range is 100 to 1600
exposure_quantum_us = 50       # open: the frame-time quantum the camera reports exposure in

# Response fit and global check
fit_iterations = 30            # open: alternating least squares rounds
fit_smoothing = 10             # open: the knot update's smoothing weight, per sample at a code
residual_max_codes = 4         # open: R
region_code_low = 16           # the region test's codes
region_code_high = 235
region_tolerance_percent = 5   # open: P
region_min_code_samples = 20   # open: a region supports a code with at least this many samples there
```

Each line is `name = number`, with an optional comment after `#`. Names and
numbers only; `meter.py` refuses a missing, extra or repeated name.

**The camera server's ranges are hard-coded.** `oak-capture-meter.py` accepts
a hold only inside its `METER_RANGES`: exposure 1 to 33000 µs, ISO 100 to
1600, white balance 1000 to 12000 K. Those numbers live in the server's code,
not in this block. So raising `max_exposure_us` or `iso_max` above them (or
moving `white_balance_kelvin` outside them) needs a server rebuild, a new
committed sha256 in `sweep.py`, and a reinstall under a new approval 1; a test
keeps this block inside the server's ranges.

## meter-norm/v1

meter-norm/v1 turns one pixel of one frame into relative linear light.

1. **Decode.** The RGB plane is NV12, 640 x 360. For pixel (x, y), Y is byte
   `640·y + x` and U, V are bytes `640·360 + 640·(y // 2) + 2·(x // 2)` and
   the one after it. With y' = Y − 16, u = U − 128, v = V − 128, and `>>` an
   arithmetic shift, BT.601 limited range as the server does for its JPEG:
   R = (298·y' + 409·v + 128) >> 8, G = (298·y' − 100·u − 208·v + 128) >> 8,
   B = (298·y' + 516·u + 128) >> 8, each clipped to 0..255. These are the
   channel codes z_R, z_G, z_B.
2. **Flag.** The pixel is flagged in this frame if z_R ≥ `clip_limit_r`, or
   z_G ≥ `clip_limit_g`, or z_B ≥ `clip_limit_b`. A flagged pixel has no value
   in this frame. meter-norm/v1 does not say which pixels a reading averages:
   Stage 0's fit drops a pixel only from the frames that flag it, and Stage 1's
   reduction rule sets its session mask.
3. **Linearise.** Apply the measured inverse response f_c per channel (a table
   over codes 0..255, from Stage 0's `response.json`, with f_c(128) = 1).
4. **Expose.** Divide by t·G: t is the frame's own reported exposure in ms
   (`exposure_duration_seconds` × 1000) and G its reported ISO / 100.
5. **Combine.** value = `weight_r`·R + `weight_g`·G + `weight_b`·B, on the
   linear values from step 4.

White balance is held at `white_balance_kelvin` in every capture and is part of
the version. meter-norm/v1's parameters are those four numbers above (the
white balance, the three clipping limits), the weights, `normalise_code` and
the three fitted tables. `response.json` records them with the sha256 of their
canonical JSON. A rule change means a new version and a refit.

## Pixels, regions and agreement

Stage 0 uses only the sampled pixels: x = 0, `pixel_step`, 2·`pixel_step`, …
below 640, and y the same below 360. A pixel's region is row
`y·grid_rows // 360`, column `x·grid_cols // 640`.

A frame's **region means** are, for each region and channel, the mean code
over its sampled pixels (flagged or not). **Two frames agree** when every
region mean of one is within `noise_tolerance_codes` of the other's, in every
channel.

## The sweep

All times are on the PC's monotonic clock. The run starts at the countdown.

1. **Before anything.** `sweep.py` refuses to start (writing nothing) unless
   this file has no `open` line, `install.json` is in the Stage 0 folder with
   an after hash equal to `oak-capture-meter.py`'s committed sha256, the token
   file reads, `/health` answers 200, and one bundle shows `meter.held` false
   with the gain, white balance and lens keys present. It also applies step
   4's light check to that bundle's own exposure and ISO, so a den that is
   already too dark is refused before the countdown.

   A refusal that names the camera tells two cases apart. A request that
   cannot connect, times out or gets no whole reply is a network problem
   (README step 4's ssh tunnel). A camera that answers, but not 200, is
   reported as unhealthy with its status (and its own error, if it gives
   one); once the meter drop-in is installed, that refusal says to run the
   rollback now.
2. **Countdown** of `countdown_s`, while the owner steps out of view. No
   camera reads.
3. **Still scene.** Poll `/frame.npz` every `poll_s`; a bundle counts once (by
   its RGB sequence). The first frame is the anchor. A frame that does not
   agree with the anchor becomes the new anchor. The scene is still once a
   frame arrives `still_span_s` or more after the anchor, with every frame
   since the anchor agreeing with it. Not still within `still_wait_limit_s`
   of the countdown's end: void.
4. **Auto's pick.** t_pick and ISO_pick are the medians of the reported
   exposure (µs) and ISO over the frames from the anchor to the one that made
   the scene still. A frame without both is an error (void).
   **Light check, before any hold.** If step 5 would need an ISO above
   `iso_max` for any step, the room is too dark: `sweep.py` refuses with a
   plain "too dark: …" line that says to turn on one more den bulb at full,
   sends no `POST /meter`, writes no `response.json` and removes the new
   sweep folder. It is not a void and does not use up a rerun. With the
   numbers above, a sweep needs t_pick · ISO_pick ≤ `max_exposure_us` ·
   `iso_max` / `step_ratio_high`: auto exposure at or below ISO 800 at 33 ms.
   A step time below 1 µs is refused the same way as "too bright".
5. **Exposures.** For k = 0 … `step_count` − 1, the ratio is
   r_k = `step_ratio_low` · (`step_ratio_high` / `step_ratio_low`)^(k / (`step_count` − 1)).
   The step holds time t_k = t_pick · r_k at ISO_pick, rounded to whole µs.
   If t_k would pass `max_exposure_us`, it is `max_exposure_us` and the ISO is
   ISO_pick · r_k · t_pick / `max_exposure_us`, rounded. Step 4's light
   check has already refused a plan with an ISO above `iso_max` or a time
   below 1 µs. Step `step_count` + 1,
   the repeat, holds step 1's settings again. Every step holds
   `white_balance_kelvin`.
6. **Hold and step.** For each step, `POST /meter` with the step's exposure,
   ISO, white balance and `ttl_s` = `hold_ttl_s`; the reply gives the
   generation g. The settings are POSTed again whenever `renew_every_s` has
   passed since the last POST. After the reply, frames are skipped until one
   reports `meter.held` true, generation g, and passes gain agreement for the
   step. That frame and the next `frames_per_step` − 1 new frames are kept.
   If no frame agrees within `settle_limit_s` of the reply, the next
   `frames_per_step` new frames are kept as they are (and fail gain agreement).
7. **Release.** Once the repeat's frames are kept, the data is complete:
   `POST /meter/release`. The hold phase ends here on every path (complete,
   void or error), and the release is sent whenever anything was POSTed. Up
   to that point a Ctrl+C voids the sweep at its next check (within one camera
   request); from the end of the hold phase on, Ctrl+C is ignored until
   `response.json` is written, so a second Ctrl+C cannot lose the record or
   keep a void's frames.
8. **Repeat check.** The first kept frame of step 1 and the first kept frame
   of the repeat must agree. If not, the scene moved: void.

The kept frames are saved as their RGB plane and metadata only, in the sweep
folder's `frames\` and `frames.jsonl`.

## Void reasons

A void is neither a pass nor a fail. `response.json` records outcome `void`
and one reason; the sweep's frames are deleted. It also records
`frames_deleted`, true only when `frames\` and `frames.jsonl` are both really
gone, and `frames_left`, the ones that are not (to delete by hand).

| Reason | When |
|---|---|
| `repeat_mismatch` | Step 8 fails. |
| `still_scene_wait_over_limit` | Step 3 is not still within `still_wait_limit_s`. |
| `camera_503` | Any camera reply is 503 between the countdown's end and the repeat's last kept frame. |
| `camera_unreachable` | Any camera request cannot connect, times out or gets no whole HTTP reply in that time. |
| `new_camera_session` | A bundle's session differs from the first bundle's. |
| `run_over_limit` | More than `run_limit_s` passes between the countdown's start and the release. |
| `interrupted_or_errored_before_repeat` | Ctrl+C, a refused `POST /meter` (any reply but 200), or any other error before the repeat's frames are kept. |

The first reason found is the one recorded.

## The response fit

Per channel c, using the kept frames of steps 1 to `step_count` (not the
repeat):

- **Samples.** For each kept frame j and each sampled pixel i that the frame
  does not flag: (i, X_j, z), where z is the pixel's code in channel c and
  X_j = t_j · G_j from the frame's own reported exposure (ms) and ISO / 100. A
  frame without a reported ISO gives no samples (it fails gain agreement).
- **Start.** f(z) = z / `normalise_code` for z = 0 … 255.
- **Each of `fit_iterations` rounds:**
  1. E_i = Σ w(z)·f(z)·X / Σ w(z)·X² over pixel i's samples: least squares
     for E_i, each sample weighted by the hat w(z) = min(z, 255 − z), so codes
     near 0 and near clipping, where f is least certain, count least.
  2. For every code z with n_z > 0 samples, m_z = the mean of X·E_i over those
     samples (the knot's least-squares value).
  3. Smooth: f minimises Σ n_z·(f(z) − m_z)² + λ·Σ (f(z−1) − 2f(z) + f(z+1))²
     over all 256 codes, with λ = `fit_smoothing` times the mean n_z of the
     codes that have samples. A code with no samples is filled by this step.
     Fewer than two codes with samples is degenerate.
  4. Pool-adjacent-violators, weighted by n_z (an empty code weighs almost
     nothing), makes f non-decreasing; values below 0 become 0.
  5. Divide f by f(`normalise_code`). If that is not above 0, the fit is
     degenerate.
- **End.** E_i once more from the final f.

Why the weight and the smoothing: with exposures in one fixed ratio (√2), the
data fix the curve only up to a ripple that repeats every √2 in exposure.
Unsmoothed, a synthetic S-curve came back with ripples of 2 to 5%; with both,
within 1% at 1 code of noise.

The curve is piecewise linear between the integer codes. Its inverse F(y) is
the code where f reaches y, by linear interpolation between the codes either
side; F(y) = 0 at or below f(0) and 255 at or above f(255).

## The global check

- **Residual.** Per channel, the RMS over all samples of F(X_j·E_i) − z, in
  code values. It passes when at most `residual_max_codes` in every channel.
- **Region test.** Per region and channel, the same fit on that region's
  samples alone gives f_r. The region supports code z if
  `region_code_low` ≤ z ≤ `region_code_high`, it has at least
  `region_min_code_samples` samples at z, and the global f(z) > 0. With
  q_z = f_r(z) / f(z) over the supported codes, f_r is scaled by
  s = Σ q_z / Σ q_z², the least-squares scale on the ratio. The region passes
  when |s·q_z − 1| ≤ `region_tolerance_percent` / 100 at every supported
  code. A region that supports no code, or whose own fit is degenerate, is
  reported as untested and does not fail. The region test passes when every
  tested region passes in every channel.
- **Gain agreement.** For every kept frame of every step, the repeat
  included: it reports an exposure, an ISO and a white balance; the exposure
  (µs) is within `exposure_quantum_us` of the step's held exposure; the ISO
  and kelvin equal the held ones exactly.

## Verdict

Not void: Stage 0 **passes** when the residual passes in every channel, the
region test passes and every kept frame passes gain agreement. Otherwise it
**fails**. A degenerate global fit is a fail. `response.json` records each
statistic, the region results (tested or not), each frame's gain agreement,
and on a pass or fail the meter-norm/v1 parameters with their sha256.

An error after the data is complete (in the repeat check or the fit, after
the release) is neither a verdict nor a void: `response.json` records outcome
`error` with the detail, and the frames are kept.

**Recheck.** `sweep.py --recheck <sweep folder>` recomputes the repeat check
and the verdict from the saved `frames\` and `frames.jsonl` (each frame's
sha256 checked against `frames.jsonl`) with this file's numbers, and writes
`recheck-<n>.json` beside `response.json`, never over it. It records the
saved outcome, whether the two agree, and whether this file's Stage 0 block is
the one the sweep used. It refuses a void sweep (its frames are gone) and
refuses while any line is `open`. Timing (the still wait, the run limit) is
not recomputed: a saved pass, fail or error already met it.

Up to 3 reruns the same evening fall under approval 1 (the plan's Void
sweeps). A too-dark refusal is not a rerun. Each sweep writes its own
`sweep-<n>\` folder.

## What may change

Before the first sweep frame, this file may be revised in place, in its own
commit, and the code follows. After it, the Stage 0 block never changes: a new
Stage 0 rule is `METHOD-V2.md`, with a new approval 1 as the plan says.
