"""The den's light direction: how each change of the light decision is played (the lighting-direction note).

The preview decides what each fixture is for (a task, a pre-aim or nothing) and where it points. This module decides how
each change looks: the fade's curve and time, the shape of a glide, the room's ambient scene, and how a hand-off or an
exit plays. Everything it plays is made of FC13 commands that a real C12 executes the same way:
- **A glide is one MOVE.** The board's planner (C12 v2 plan, section 5.1) moves both axes along one jerk-limited S-curve
  on a shared path parameter s, from rest to rest. `Glide` evaluates that same S-curve, so Home draws the motion the board
  makes. Its duration is den-core's `scurve.move_time`, and a parity test holds the two together.
- **A fade is a level train.** The board has no fade (`fade_supported: false`): it sets each MOVE's level at once. So a
  fade is a run of stationary MOVEs at the fixture's angles, at most one per tick and only when the level's 0-254 code
  changes. `Ramp` is the curve the train samples.
- **The room's other light** (each fixture's glow zones and downlight in Home today; Hue later, through den_lighting) is
  one ambient scene. It dims under a task light and comes back up when the task ends. It's published, never written to
  Home Assistant.

Standard library only. Pure: no clock, file or network.
"""

import math
import statistics


# ---- The direction's settings: preview-owned, beside den-core's. Every one is changeable from Home's panel. ----------

SETTINGS = {
    "fade_in_s": 2.0,             # the spot's rise on a new task: it blooms (visible at once, full in 2 s)
    "fade_out_s": 1.0,            # the spot lets go when the task ends (book closed, phone): no hurry
    "leave_fade_s": 0.3,          # when you stand up or leave, it gets out of the way: dark within 1 s of standing
                                  # (Stop, Off and expiry stay instant)
    "crossfade_s": 1.2,           # a hand-off: the old fixture's fall and the new one's rise overlap
    "shift_m": 0.06,              # how far the book's settled place must move before a lit spot glides after it
    "shift_hold_s": 1.5,          # the window whose median is the book's settled place (a page turn is shorter)
    "page_lift": 0.2,             # the page's centre: this fraction of the way from the hands toward the eyes
    "ambient_level": 0.75,        # the room's glow when nobody is doing a task (Home's own display default)
    "ambient_task_scale": 0.4,    # under a task light, the glow drops to this fraction
    "ambient_dim_s": 4.0,         # the room dims more slowly than the spot rises, so the eye follows it
    "ambient_delay_s": 0.5,       # and starts a beat after the spot, so the spot leads
    "ambient_restore_s": 1.5,     # when the task ends the room comes back as the spot goes out: no one sits in the dark
}
LIMITS = {"page_lift": (0.0, 0.5), "ambient_level": (0.0, 1.0), "ambient_task_scale": (0.0, 1.0),
          "fade_in_s": (0.0, 10.0), "fade_out_s": (0.0, 3.0), "leave_fade_s": (0.0, 1.0), "crossfade_s": (0.0, 5.0),
          "ambient_dim_s": (0.0, 20.0), "ambient_delay_s": (0.0, 5.0), "ambient_restore_s": (0.0, 10.0),
          "shift_m": (0.0, 1.0), "shift_hold_s": (0.0, 10.0)}

AMBIENT_CCT_K = (3000, 2700)    # the house glow's white, and the warmer white it dims to under a task (dim to warm)
LEVEL_CODES = 254               # the board's spot level, 0-254 (contract 5's rounding: floor(x * 254 + 0.5))
# The level train's cap, per fixture: one level MOVE per step. A virtual fixture has no radio, so it steps each 0.1 s
# tick. A real one shares HA, ZHA and Zigbee with one frame in flight, and until that path is measured the firmware's
# rule for one follower holds: 2 commands a second, Stop and keep-lit included (a lit step renews the lease itself, so
# no keep-lit goes while a train runs). A profile's own level_step_s wins. The timed MOVE (flag bit 4, fade_ms) will
# replace the train with one MOVE per cue where the board has it.
LEVEL_STEP_S = 0.1
RADIO_STEP_S = 0.5


def level_step_s(profile):
    """Seconds between a fixture's level MOVEs."""
    given = profile.get("level_step_s")
    if isinstance(given, (int, float)) and given > 0:
        return float(given)
    return LEVEL_STEP_S if profile.get("fictional") else RADIO_STEP_S
SEATED_AIM_M = 0.03             # a dark pre-aim for a seated person tracks the book this closely: dark moves are free
DARK_WINDOW_S = 0.6             # and on a shorter window, so the book coming up off the lap is followed before the yes
SETTLE_S = 0.5                  # the book's settled place must stay past shift_m this long before a lit spot glides
STILL_S = 0.8                   # and the book must be at rest over this window: the glide goes once, to where it rests
WRISTS_LOST_S = 2.0             # without both wrists for this long, the page falls back to den-core's task point
FACING_WINDOW_S = 3.0           # a seated person's facing is the circular mean over this window
GLARE_KEEP_M = 0.12             # a lit fixture keeps its job while its beam's centre clears the head by this much
                                # (admission still needs den-core's 0.15 m): head jitter can't flip it


def level_code(level):
    """The board's 0-254 spot level, as contract 5 rounds it."""
    return math.floor(min(1.0, max(0.0, level)) * LEVEL_CODES + 0.5)


# ---- The board's S-curve ---------------------------------------------------------------------------------------------

def _time_to_speed(v, a, j):
    return v / a + a / j if v >= a * a / j else 2.0 * math.sqrt(v / j)


class SCurve:
    """A rest-to-rest move of the shared path parameter s from 0 to 1, limited by speed v, acceleration a and jerk j
    (per second, per second squared, per second cubed of s): seven segments of constant jerk. Its duration is den-core's
    scurve.scurve_time(1, v, a, j), found the same way, and s(t) integrates the jerk profile."""

    def __init__(self, v, a, j):
        if not (v > 0 and a > 0 and j > 0):
            raise ValueError("speed, acceleration and jerk limits must all be above 0")
        self.limits = (v, a, j)
        peak = v
        if v * _time_to_speed(v, a, j) > 1.0:   # never reaches v: den-core's bisection for the peak speed
            low, high = 0.0, v
            for _ in range(100):
                middle = (low + high) / 2.0
                if middle * _time_to_speed(middle, a, j) < 1.0:
                    low = middle
                else:
                    high = middle
            peak = high
        if peak >= a * a / j:
            jerk_s, flat_s = a / j, peak / a - a / j
        else:
            jerk_s, flat_s = math.sqrt(peak / j), 0.0
        accelerate = 2.0 * jerk_s + flat_s
        cruise = max(0.0, (1.0 - peak * accelerate) / peak)
        self.segments = ((jerk_s, j), (flat_s, 0.0), (jerk_s, -j), (cruise, 0.0), (jerk_s, -j), (flat_s, 0.0), (jerk_s, j))
        self.duration = 2.0 * accelerate + cruise
        self.peak = peak
        self.total = self._integrate(self.duration)[0] or 1.0

    def _integrate(self, t):
        position = speed = acceleration = 0.0
        for length, jerk in self.segments:
            if t <= 0:
                break
            step = min(length, t)
            if step <= 0:
                continue
            position += speed * step + acceleration * step * step / 2.0 + jerk * step ** 3 / 6.0
            speed += acceleration * step + jerk * step * step / 2.0
            acceleration += jerk * step
            t -= step
        return position, speed

    def s(self, t):
        """The path parameter at t seconds into the move: 0 before, 1 after."""
        if t <= 0:
            return 0.0
        if t >= self.duration:
            return 1.0
        return min(1.0, max(0.0, self._integrate(t)[0] / self.total))

    def speed(self, t):
        """ds/dt at t (per second)."""
        if t <= 0 or t >= self.duration:
            return 0.0
        return self._integrate(t)[1] / self.total


def path_limits(delta_deg, axes):
    """The shared path parameter's (v, a, j) for a move of (d_pan, d_tilt) degrees under the profile's per-axis limits:
    whichever axis is tighter, as the board plans it. None for no move."""
    moving = [(abs(d), axis) for d, axis in zip(delta_deg, axes) if abs(d) > 1e-9]
    if not moving:
        return None
    return (min(axis["v_max"] / d for d, axis in moving), min(axis["a_max"] / d for d, axis in moving),
            min(axis["j_max"] / d for d, axis in moving))


class Glide:
    """One MOVE's motion from start_cdeg to target_cdeg (integer centidegrees, contract 5's joints) begun at `started`
    (the coordinator's monotonic seconds), as the board flies it."""

    def __init__(self, start_cdeg, target_cdeg, axes, started):
        self.start, self.target, self.started = tuple(start_cdeg), tuple(target_cdeg), started
        limits = path_limits(tuple((b - a) / 100 for a, b in zip(self.start, self.target)), axes)
        self.curve = SCurve(*limits) if limits else None
        self.duration = self.curve.duration if self.curve else 0.0

    def at(self, now):
        """The joints at `now`, rounded to centidegrees as the board reports them."""
        s = self.curve.s(now - self.started) if self.curve else 1.0
        return tuple(round(a + (b - a) * s) for a, b in zip(self.start, self.target))

    def done(self, now):
        return now - self.started >= self.duration

    def published(self):
        """For Home (den-virtual-spots-1's additive `glide`): the move in Home's renderer degrees (the joints negated over
        100, as pan_deg and tilt_deg are), its start on this run's monotonic clock, and the path limits Home's own copy of
        the S-curve needs."""
        if self.curve is None:
            return None
        return {"from_deg": [-self.start[0] / 100, -self.start[1] / 100],
                "to_deg": [-self.target[0] / 100, -self.target[1] / 100],
                "start_mono": self.started, "duration_s": round(self.duration, 6),
                "limits": [round(x, 9) for x in self.curve.limits], "shape": "scurve-7"}


# ---- Fades -------------------------------------------------------------------------------------------------------

def ease(curve, x):
    """0 to 1 over x in 0..1. Levels are already close to perceived lightness (Home's dimming curve is CIE lightness), so
    the curves act on what the eye sees.
    - bloom: a quarter sine, for a rise: visible at the first step (8 % of the goal at 0.1 s into a 2 s rise), half way
      by a third of the time, and settling softly, the way a warm lamp comes up;
    - smooth: a cosine in-out, for crossfades: no visible start or landing, so two fixtures trade evenly;
    - out: fast at first, soft at the end, for exits: most of the light goes in the first half;
    - linear."""
    x = min(1.0, max(0.0, x))
    if curve == "bloom":
        return math.sin(math.pi * x / 2.0)
    if curve == "smooth":
        return (1.0 - math.cos(math.pi * x)) / 2.0
    if curve == "out":
        return 1.0 - (1.0 - x) ** 2
    return x


class Ramp:
    """A level (or any value) moving from `start` to `end` over `duration` seconds on `curve`, begun at `started`.
    paused() moves its clock on: a ramp waits while its fixture glides (one MOVE, one level)."""

    def __init__(self, start, end, duration, curve, started, purpose):
        self.start, self.end, self.duration, self.curve = start, end, max(0.0, duration), curve
        self.started, self.purpose = started, purpose

    def value(self, now):
        if self.duration <= 0:
            return self.end
        return self.start + (self.end - self.start) * ease(self.curve, (now - self.started) / self.duration)

    def done(self, now):
        return now - self.started >= self.duration

    def paused(self, seconds):
        self.started += seconds

    def published(self, now):
        return {"from": round(self.start, 4), "to": round(self.end, 4), "start_mono": self.started,
                "duration_s": self.duration, "curve": self.curve, "purpose": self.purpose,
                "value": round(self.value(now), 4)}


# ---- The aim: where the page is ---------------------------------------------------------------------------------------

def page_point(joints, lift):
    """The page's centre for a seated reader: between the wrists, raised `lift` of the way toward the eyes (a book is held
    by its lower half, its page tilted to the face). None without both wrists and a head."""
    def joint(name):
        row = (joints or {}).get(name)
        return tuple(row[:3]) if row and len(row) >= 4 and row[3] >= 0.5 else None
    left, right = joint("left_wrist"), joint("right_wrist")
    face = [p for p in (joint(n) for n in ("nose", "left_eye", "right_eye", "left_ear", "right_ear")) if p]
    if not (left and right and face):
        return None
    hands = tuple((left[i] + right[i]) / 2.0 for i in range(3))
    eyes = tuple(statistics.median(p[i] for p in face) for i in range(3))
    return tuple(hands[i] + lift * (eyes[i] - hands[i]) for i in range(3))


class AimFilter:
    """The book's settled place, and the aim that follows it.

    Each frame's page point joins a window of `hold_s` seconds, and the window's median (per axis) is where the book is.
    The aim moves when that place has stayed more than `shift_m` away for SETTLE_S and the book has come to rest
    (STILL_S of frames within shift_m of their median), to where it rests. So a page turn (one hand, under a second) or
    a fidget never moves a lit spot, and a real shift moves it once, as one glide, never as a chase.

    Unlike den-core's ShiftFilter it never locks on the first point it saw: the first aim is the window's median. While
    the spot is dark (pre-aim) the aim follows the median of the last DARK_WINDOW_S within SEATED_AIM_M at once, so a
    light that comes on is already on the page. A frame without both wrists keeps the last page; only after
    WRISTS_LOST_S does den-core's own task point (shoulders, or the seat mark) stand in, so the two bases never mix in
    one window."""

    def __init__(self, shift_m, hold_s):
        self.shift_m, self.hold_s = shift_m, hold_s
        self.aim = self.basis = None
        self.samples = []
        self.away_since = None
        self.page_at = None

    def estimate(self, window=None):
        samples = self.samples
        if window is not None and samples:
            newest = samples[-1][0]
            samples = [(s, p) for s, p in samples if s > newest - window]
        if not samples:
            return None
        return tuple(statistics.median(p[i] for _, p in samples) for i in range(3))

    def update(self, t, page, fallback, fallback_basis, lit):
        """Feed one frame: `page` (page_point, or None) and den-core's task point as the fallback. Returns the aim and
        its basis."""
        if page is not None:
            point, basis, self.page_at = page, "page", t
        elif self.page_at is not None and t - self.page_at < WRISTS_LOST_S:
            point, basis = None, "page"          # a frame without both wrists: the page stays where it was
        else:
            point, basis = fallback, fallback_basis
        if point is not None:
            if basis != self.basis:
                self.samples = []
            self.basis = basis
            self.samples = [(s, p) for s, p in self.samples if s > t - self.hold_s] + [(t, tuple(point))]
        estimate = self.estimate(None if lit else DARK_WINDOW_S)
        if estimate is None:
            return self.aim, self.basis
        if self.aim is None:
            self.aim, self.away_since = estimate, None
            return self.aim, self.basis
        distance = math.dist(estimate, self.aim)
        if not lit:
            if distance > SEATED_AIM_M:
                self.aim = estimate
            self.away_since = None
        elif distance > self.shift_m:
            if self.away_since is None:
                self.away_since = t
            recent = [p for s, p in self.samples if s > t - STILL_S]
            if t - self.away_since >= SETTLE_S and len(recent) >= 2:
                place = tuple(statistics.median(p[i] for p in recent) for i in range(3))
                if all(math.dist(p, place) <= self.shift_m for p in recent) and math.dist(place, self.aim) > self.shift_m:
                    self.aim, self.away_since = place, None
        else:
            self.away_since = None
        return self.aim, self.basis


class FacingFilter:
    """A seated person's facing for the eye rule: the circular mean of the last FACING_WINDOW_S of frames. Both sofa
    sockets sit within about 3 degrees of a reader's side-on line, and a shoulder-based facing swings several degrees
    frame to frame, so the raw value flips the choice. Someone seated turns slowly; standing, the raw facing is used."""

    def __init__(self):
        self.samples = []

    def update(self, t, facing, seated):
        if not seated:
            self.samples = []
            return facing
        if facing is not None:
            self.samples = [(s, f) for s, f in self.samples if s > t - FACING_WINDOW_S] + [(t, tuple(facing[:2]))]
        if not self.samples:
            return facing
        x = sum(f[0] for _, f in self.samples)
        y = sum(f[1] for _, f in self.samples)
        length = math.hypot(x, y)
        return (x / length, y / length) if length > 1e-9 else facing


# ---- The room's ambient scene -----------------------------------------------------------------------------------------

class Ambient:
    """The room's glow: the house level while nobody is at a task, dimmed (and warmed) under a task light. One cue at a
    time: going down it waits ambient_delay_s and takes ambient_dim_s; coming up it starts at once and takes
    ambient_restore_s. `scale` is the fraction of the house level, which is what den_lighting would apply to Hue later."""

    def __init__(self):
        self.scale = 1.0
        self.ramp = None
        self.goal = 1.0

    def direct(self, task, settings, now):
        goal = settings["ambient_task_scale"] if task else 1.0
        if goal == self.goal:
            return
        current = self.value(now)
        self.goal = goal
        if goal < current:
            self.ramp = Ramp(current, goal, settings["ambient_dim_s"], "smooth", now + settings["ambient_delay_s"],
                             "ambient_dim")
        else:
            self.ramp = Ramp(current, goal, settings["ambient_restore_s"], "smooth", now, "ambient_restore")

    def value(self, now):
        if self.ramp is None:
            return self.scale
        if now < self.ramp.started:
            return self.ramp.start
        self.scale = self.ramp.value(now)
        if self.ramp.done(now):
            self.ramp = None
        return self.scale

    def kelvin(self, scale, settings):
        """Dim to warm: 3000 K at the house level, 2700 K at the task scale."""
        task = settings["ambient_task_scale"]
        warm = 0.0 if task >= 1.0 else min(1.0, max(0.0, (1.0 - scale) / (1.0 - task)))
        return round(AMBIENT_CCT_K[0] + (AMBIENT_CCT_K[1] - AMBIENT_CCT_K[0]) * warm)

    def published(self, settings, now):
        """The one ambient record (Home's glow now, den_lighting's bulb plan later): the value now, and the cue it's on.
        phase: dim, restore or steady; goal_*: where the cue lands; ramp_s and ramp_starts_mono: its time (a dim starts
        ambient_delay_s after it's called, so ramp_starts_mono can be ahead of now)."""
        ramp = self.ramp
        scale = self.value(now)
        out = {"level": round(settings["ambient_level"] * scale, 4), "scale": round(scale, 4),
               "cct_k": self.kelvin(scale, settings), "goal_level": round(settings["ambient_level"] * self.goal, 4),
               "goal_scale": round(self.goal, 4), "goal_cct_k": self.kelvin(self.goal, settings),
               "phase": "steady", "ramp_s": 0.0, "ramp_starts_mono": None}
        if ramp is not None and self.ramp is not None:
            out.update(phase="dim" if ramp.end < ramp.start else "restore", ramp_s=ramp.duration,
                       ramp_starts_mono=ramp.started, cue=ramp.purpose)
        return out
