---
title: The den spot is directed, not switched
target: backend
type: changed
---

The den spotlight preview (`tools/den-live/preview.py`) now plays every change of its light decision the way a director of photography would: it uses the same FC13 commands a real C12 fixture executes, and Home can draw the same motion. The rules for when a spot lights are unchanged. The new `direction.py` decides how each change looks.
- **It blooms on.** A new task light rises over 2 s on a quarter-sine curve: visible with the yes itself, and half way at a third of its time. The fade curves are the C12's own (ease_out, ease_in_out, linear, as `c12.move` names them), so a fade played as a train is the one a board with the timed MOVE plays from a single command. The board has no fade, so the rise is a level train: one stationary MOVE each time the 0–254 level changes. A virtual fixture sends at most one a 0.1 s tick. A real one sends at most one every 0.5 s, the firmware's 2-commands-a-second rule; the steps renew the lease themselves, so no keep-lit goes between them. The light Home shows is always the last MOVE the trace committed.
- **One MOVE per cue on a board that can fade.** Where `c12.get_profile` advertises the timed MOVE (firmware v2-b and later) and the codec can encode it:
  - each fade is one MOVE carrying `fade_ms` and its curve;
  - a dark move followed by a rise is one MOVE that blooms on arrival;
  - an exit during a glide joins the flight;
  - a lit glide takes a 1 s floor (`glide_min_s`): the same S-curve, slower.

  The published light follows the board's own fade. Virtual fixtures and boards without it, such as Light 1 on v2-a, keep the capped level train.
- **It glides once.** The aim is the page, not the wrists: between the hands and a fifth of the way toward the eyes. A settled median replaces den-core's first-sample shift filter. A page turn never moves a lit spot. A real shift moves it once, as one lit MOVE, after the book comes to rest. Each glide is published with the S-curve the board flies (`glide`: from, to, start, duration and path limits). The S-curve is the firmware's own (its duration.py at 62ea043, with a separate deceleration limit and the timed MOVE's exact time-stretch), checked against all 36 of its planner vectors.
- **It doesn't flip-flop.** A seated reader's facing is averaged over 3 s, so a side-on fixture isn't flipped in and out of "in front of you" by the shoulders' frame-to-frame swing. A lit fixture keeps its job while its beam's centre clears the head by 12 cm (admission still needs 15 cm). A fixture already pre-aimed keeps the pre-aim. While a spot is lit, the runner-up waits on the same page, dark, so a hand-off is a 1.2 s crossfade and never a dark move.
- **It leaves gracefully.** Standing up fades out in 0.3 s, dark within 1 s of standing. A task that ends while you're still seated (an answer, the phone patience) fades out over 1 s and is dark at its deadline. A hand-off crossfades. No exit keeps a beam on past the validity a snapshot already promised. Unchanged and still instant: Stop, Off, expiry, a stale or unsafe snapshot, and another person binding. A fixture in the middle of a glide gets one level-0 MOVE to its own target, because the board holds a MOVE that arrives during a move until it lands.
- **The room follows the spot.** The snapshot gains `ambient`. It carries the room glow now (`level`, `scale`, `cct_k`) and the cue it's on (`goal_level`, `goal_scale`, `goal_cct_k`, `phase`, `ramp_s` and `ramp_starts_mono`), so a bulb that fades itself can take one plan per cue. Under a task light, the room's glow dims to 40% (and warms to 2700 K) over 4 s, starting a beat after the spot. It comes back over 1.5 s as the spot leaves. It's published only: nothing is written to Home Assistant, and the real Hue lights are untouched.
- **New settings,** all changeable from Home: `fade_in_s` 2.0, `fade_out_s` 1.0, `leave_fade_s` 0.3, `crossfade_s` 1.2, `shift_m` 0.06, `shift_hold_s` 1.5, `page_lift` 0.2, and the ambient levels and times.
- **Before and after**, on a synthetic reading session at the camera's live rate, lag and ask pace (30 runs per case):
  - the beam's centre on the page goes from 19.5 cm to 3.5 cm off at p50, and from 47% to 96% of the time within half the beam's width;
  - blinks while reading go from 0.9–1.8 per session to none with a clean or noisy pose, and from 1.9 to 1.3 with one-frame pose dropouts, which still darken by the rule;
  - the time from the yes to first light goes from 0.74 s to 0 s at p50 (0.36 s with event-driven asks, where the yes can land during a dark pre-aim glide).
