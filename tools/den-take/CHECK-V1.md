# CHECK-V1: capture-clock check for den takes

This is rung R's check method, version 1. It is committed alone, as the first
commit on `claude/rung-r`, before any camera read. `check.py` reads every
number it uses from the one fenced block below. `take.py` reads the slate
length from it, and `slate.html` repeats the four pattern numbers (a test keeps
them equal). `take.json` and every `check-N.json` record this file's sha256.
`check.py` refuses a take recorded under a different CHECK file.

## Basis

R keeps den footage on the owner's PC. It rests on `choices.md` item 1:

> recording-retention: OTHER: "we can keep any footage not just ones of me".
> Read as: any den footage may be kept on household computers, anyone may
> appear, kept until the owner deletes it; replaces the 48-hour rule for den
> recordings. Published/shared material still blurs faces and never identifies
> anyone. (Interpretation stated to owner; not contradicted yet.)

The owner labels alone (item 2), and R, N1 and N2 run first (item 4). R keeps
no metering frames, so the open metering part of recording-retention does not
apply to it.

## Tolerances

```check-v1
# Phone slate pattern (slate.html holds the same four numbers)
slate_period_s = 10
slate_flash_s = 0.5
slate_phase_step_s = 0.02
slate_marker_second = 0

# Slate recording and fit
slate_length_s = 20
slate_pixel_step = 4
screen_std_fraction = 0.5
class_percentile_low = 10
class_percentile_high = 90
lit_above = 0.75
dark_below = 0.25
fit_grid_s = 0.001
epsilon_s = 0.025
min_flashes = 12
max_window_s = 0.10
elapsed_bound_s = 0.20
max_slate_separation_s = 1800

# Two-clock rule (B4)
max_two_clock_step_s = 0.005
max_two_clock_line_s = 0.02
max_two_clock_slope_ppm = 1000

# Cuts
gap_cut_s = 1.0
stale_pad_s = 1.0
person_pad_s = 1.0
```

## Capture time

A frame is one saved `/frame.npz` bundle. Its time t_host is its RGB plane's
`host_monotonic_timestamp_seconds`, from the same bundle as the pixels.
`t_s` = t_host minus the t_host of the first kept frame. t_device is the RGB
plane's `device_timestamp_seconds`. PC times (cues, `recv_pc_mono_s`) are the
PC's `time.perf_counter()`. `recv_pc_unix` gives approximate wall time, which
is reported only.

## A. Phone slate

**Pattern**, on the phone's clock (`Date.now()`). In whole second s, with
k = s mod `slate_period_s`: white from `slate_phase_step_s` x k s after the
second, for `slate_flash_s` s, and black otherwise. When k =
`slate_marker_second` the whole second is black. A pattern change is any
switch between white and black.

**One slate attempt** records `slate_length_s` s. Its fit:

1. Use the frames received (`recv_pc_mono_s`) from the attempt's "Slate" cue
   to its end, in frame order.
2. Take every `slate_pixel_step`-th pixel of the Y plane in both directions (160x90).
3. The screen is the set of pixels whose temporal standard deviation over those
   frames is at least `screen_std_fraction` of the largest. There is no screen
   if the largest is 0.
4. Each frame's mean over the screen gets x = (mean - p_lo) / (p_hi - p_lo),
   where p_lo and p_hi are the `class_percentile_low`th and
   `class_percentile_high`th percentiles (linear interpolation) of those means.
   The frame is lit if x > `lit_above`, dark if x < `dark_below`, and
   unconstrained otherwise. There is no screen if p_hi = p_lo.
5. Flashes seen: the number of runs of lit frames, in frame order, separated by
   at least one dark frame.
6. An offset c (camera minus phone) on a `fit_grid_s` grid over [-5, 5) s is
   consistent if every lit or dark frame matches the pattern at phone time
   t_host - c. A frame within `epsilon_s` of a pattern change is skipped.

An attempt is **unreadable** if one of its frames repeats an earlier frame's
`rgb_nv12` sha256 (a stale image: `stale_frame`), it has no screen
(`no_screen`), it has fewer than `min_flashes` flashes (`few_flashes`), it has
no consistent offset (`no_consistent_offset`), or its consistent offsets do not
form one window (wrapping at +-5 s) whose width (last minus first) is at most
`max_window_s` (`wide_window`). `take.py`'s peek runs the same fit, looks for
repeats among the attempt's own frames, retries only an unreadable attempt, at
most twice more, and never speaks the offset. `check.py` refits and reports
every attempt and judges the first readable attempt of each slate (start and
end). A slate passes if it has a readable attempt. Its c is the judged
attempt's window midpoint.

**Elapsed bound:** |c_end - c_start| (wrapped into [-5, 5) s) +
(width_start + width_end) / 2 <= `elapsed_bound_s`. It is gated. The first
frames of the two judged slates are at most `max_slate_separation_s` apart in
`t_s`.

## B. Rules on every kept frame

1. `take.json` says `complete`.
2. Every bundle's session id equals the `/health` session before and after.
3. RGB sequence, t_host and t_device all strictly increase, in frame order.
4. Two clocks: d = t_host - t_device changes by at most `max_two_clock_step_s`
   between consecutive frames. The least-squares line of d against t_host has
   a slope within +-`max_two_clock_slope_ppm` ppm, and every d is within
   `max_two_clock_line_s` of it.
5. Every file's sha256 equals its `frames.csv` row, and the row's rgb_seq,
   t_host_s and t_device_s equal the bundle's. `frames.csv`'s own sha256 and
   row count equal the ones `take.json` recorded.

## Cuts: reported, not failed

Cuts are half-open [start, end) in `t_s`. Cuts of one kind that overlap or
touch are merged.

- **gap:** consecutive frames at a and b with b - a > `gap_cut_s` cut [a, b).
- **stale:** a frame whose `rgb_nv12` sha256 equals an earlier frame's, at t,
  cuts [t - `stale_pad_s`, t + `stale_pad_s`).
- **second_person:** only when every rule in A and B passes, and only at an
  interactive console, the owner reviews one thumbnail per second (thumbnail k
  is the first frame at or after k s) and types spans `mm:ss-mm:ss, ...` or
  `none`. A span names the first and last thumbnail showing any part of a
  second real person; a hand or the edge of a body counts; if in doubt, cut. A
  span a-b cuts [a - `person_pad_s`, b + `person_pad_s`), which covers every
  frame between the clean thumbnails on either side: `01:10-01:20` cuts
  [69, 81). A lone `mm:ss` is a one-thumbnail span.

## Usable part

It runs from the "Begin" cue to the end slate's "Hold the phone" cue, each
mapped to the `t_s` of the first frame received at or after it, minus every
cut. Phone handling and every slate attempt fall outside it.

## Verdict

A take FAILs if any rule in A or B fails, and is kept with that verdict.
Otherwise it PASSes. Reported only: approximate wall time, c, frames per
second, MB/s and the gap count. A re-run writes the next `check-N.json`; the
newest one counts.

A **retained session** is a take whose newest `check-N.json` says PASS and
which is not a commissioning take. Only retained sessions go to N1 and N2. FAIL
and commissioning takes stay on disk as footage outside R's output.

## What commissioning may change

Only `epsilon_s`, `min_flashes`, `lit_above`, `dark_below` and
`slate_length_s`. `max_window_s` may only get smaller, except that it may rise
by twice any rise in `epsilon_s` (every window is at least 2 x `epsilon_s`
wide). `elapsed_bound_s` and the B rules never change. A change is committed
as `CHECK-V2.md` (and `check.py`'s `METHOD_FILE` then names it), and a new
commissioning take must pass under it before the first retained take. After
that, nothing changes.
