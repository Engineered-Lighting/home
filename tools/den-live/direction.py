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
    "leave_fade_board_s": 0.0,    # the same on a real board, whose send path (about 0.3 s after detection's 0.6 s)
                                  # leaves plan 05's bar C (dark within 1.0 s on 4 of 5) no room for a fade: at once
                                  # (Stop, Off and expiry stay instant)
    "crossfade_s": 1.2,           # a hand-off: the old fixture's fall and the new one's rise overlap
    "glide_s": 2.0,               # a lit reframe after the reader moves travels like a dolly: the fixture's S-curve,
                                  # stretched to this long (never hurried past its own pace)
    "creep_s": 4.0,               # a small reframe while they read creeps, slow enough not to draw the eye
    "shift_m": 0.06,              # how far the book's settled place must move before a lit spot glides after it
    "shift_hold_s": 1.5,          # the window whose median is the book's settled place (a page turn is shorter)
    "page_up_m": 0.05,            # the page's centre: this far up the page from the hands (the page faces the eyes)
    "ambient_level": 0.75,        # the room's glow when nobody is doing a task (Home's own display default)
    "ambient_task_scale": 0.4,    # under a task light, the glow drops to this fraction
    "ambient_dim_s": 4.0,         # the room dims more slowly than the spot rises, so the eye follows it
    "ambient_delay_s": 0.5,       # and starts a beat after the spot, so the spot leads
    "ambient_restore_s": 1.5,     # when the task ends the room comes back as the spot goes out: no one sits in the dark
}
LIMITS = {"page_up_m": (0.0, 0.15), "ambient_level": (0.0, 1.0), "ambient_task_scale": (0.0, 1.0),
          "fade_in_s": (0.0, 10.0), "fade_out_s": (0.0, 2.5), "leave_fade_s": (0.0, 1.0), "leave_fade_board_s": (0.0, 1.0),
          "crossfade_s": (0.0, 5.0), "glide_s": (0.0, 10.0), "creep_s": (0.0, 20.0),
          "ambient_dim_s": (0.0, 20.0), "ambient_delay_s": (0.0, 5.0), "ambient_restore_s": (0.0, 10.0),
          "shift_m": (0.0, 1.0), "shift_hold_s": (0.0, 10.0)}

AMBIENT_CCT_K = (3000, 2700)    # the house glow's white, and the warmer white it dims to under a task (dim to warm)
CIE_KAPPA, CIE_EPSILON = 24389 / 27, 216 / 24389   # den-core's spot_light constants for output()
LEVEL_CODES = 254               # the board's spot level, 0-254 (contract 5's rounding: floor(x * 254 + 0.5))
# The level train's cap, per fixture: one level MOVE per step. A virtual fixture has no radio, so it steps each 0.1 s
# tick. A real one shares HA, ZHA and Zigbee with one frame in flight, and until that path is measured the firmware's
# rule for one follower holds: 2 commands a second, Stop and keep-lit included (a lit step renews the lease itself, so
# no keep-lit goes while a train runs). A profile's own level_step_s wins. The timed MOVE (flag bit 4, fade_ms) will
# replace the train with one MOVE per cue where the board has it.
LEVEL_STEP_S = 0.1
RADIO_STEP_S = 0.5
RADIO_PER_S = 2                 # every command a real fixture is sent, per rolling second (the firmware's rule);
                                # the direction keeps one free for a Stop or a cut, so its own commands, a train's
                                # steps included, go at most once a second
# A board without lit travel (lit_motion false) holds its aim while the page is within this fraction of half the beam's
# FWHM (at 0.5 the page keeps 84 % of the beam's centre light), because each re-aim there is a blink.
DARK_HOLD = 0.5
# A hand-off on boards with the timed MOVE, whose fade curves act on levels: the new fixture rises on ease_out over
# crossfade_s while the old falls on ease_in_out over out_x times that. Their light then never sums below the page's
# whole (at most 23 % over, half way), each in one MOVE. (Both on ease_in_out over the same time would leave 37 %.)
CROSSFADE = {"in": "ease_out", "out_x": 2.0}
# No exit fades for longer than this: a fade to dark sends no keep-lit (the desk sender treats a level-0 MOVE as unlit),
# and the board's lease lapses 3 s after the last one, which would cut a longer fade-out short.
EXIT_MAX_S = 2.5


def capabilities(profile):
    """The board's capabilities as c12.get_profile reports them (firmware 62ea043, FC13 attribute 0x0012):
    timed_move, fade_curves, join_flight and at_rest. A profile without them (a virtual fixture, a v2-a board) has
    none."""
    caps = profile.get("capabilities")
    return {key: bool(isinstance(caps, dict) and caps.get(key)) for key in ("timed_move", "fade_curves", "join_flight",
                                                                         "at_rest")}


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
PAGE_MIN_AHEAD_M = 0.02         # the hands must be this far ahead of the eyes to give the page a direction
WRISTS_LOST_S = 2.0             # without both wrists for this long, the page falls back to den-core's task point
FACING_WINDOW_S = 3.0           # a seated person's facing is the circular mean over this window
GLARE_KEEP_M = 0.12             # a lit fixture keeps its job while its beam's centre clears the head by this much
                                # (admission still needs den-core's 0.15 m): head jitter can't flip it
AIM_KEEP_M = 0.12               # and while the beam as it points now clears the head (and where it's going) by this


def level_code(level):
    """The board's 0-254 spot level, as contract 5 rounds it."""
    return math.floor(min(1.0, max(0.0, level)) * LEVEL_CODES + 0.5)


# ---- The board's S-curve ---------------------------------------------------------------------------------------------

class _Ramp:
    """One side of the S-curve: rest to peak speed vp under acceleration A and jerk J (the firmware's Ramp,
    tools/c12v2/duration.py at 62ea043, arithmetic for arithmetic)."""

    def __init__(self, vp, A, J):
        self.vp, self.A, self.J = vp, A, J
        if vp <= 0:
            self.tj = self.T = 0.0
        elif vp * J >= A * A:
            self.tj, self.T = A / J, vp / A + A / J
        else:
            self.tj = math.sqrt(vp / J)
            self.T = 2 * self.tj

    def distance(self):
        return self.vp * self.T / 2

    def at(self, t):
        """(distance, speed) t seconds into the ramp."""
        if self.T <= 0 or t <= 0:
            return 0.0, 0.0
        if t >= self.T:
            return self.distance(), self.vp
        J, tj, T, vp = self.J, self.tj, self.T, self.vp
        alim = J * tj
        if t < tj:
            return J * t ** 3 / 6, J * t * t / 2
        if t < T - tj:
            return alim / 6 * (3 * t * t - 3 * t * tj + tj * tj), alim * (t - tj / 2)
        r = T - t
        return self.distance() - vp * r + J * r ** 3 / 6, vp - J * r * r / 2


class SCurve:
    """A rest-to-rest move of the shared path parameter s from 0 to 1 under speed v, acceleration a, jerk j and
    deceleration d (per second, squared, cubed and squared, of s; d defaults to a): the board's own S-curve, ported
    from the firmware's duration.py (62ea043) with its 100-step bisection, and checked against its planner vectors.
    With d = a its duration is den-core's scurve.scurve_time(1, v, a, j)."""

    def __init__(self, v, a, j, d=None):
        d = a if d is None else d
        if not (v > 0 and a > 0 and j > 0 and d > 0):
            raise ValueError("speed, acceleration, jerk and deceleration limits must all be above 0")
        self.limits = (v, a, j, d)
        up, down = _Ramp(v, a, j), _Ramp(v, d, j)
        if up.distance() + down.distance() <= 1.0:
            peak, cruise = v, (1.0 - up.distance() - down.distance()) / v
        else:
            low, high = 0.0, v
            for _ in range(100):
                middle = (low + high) / 2
                if _Ramp(middle, a, j).distance() + _Ramp(middle, d, j).distance() > 1.0:
                    high = middle
                else:
                    low = middle
            peak, cruise = low, 0.0
            up, down = _Ramp(low, a, j), _Ramp(low, d, j)
        self.up, self.down, self.peak, self.cruise = up, down, peak, cruise
        self.duration = up.T + cruise + down.T

    def _at(self, t):
        if t <= 0:
            return 0.0, 0.0
        if t >= self.duration:
            return 1.0, 0.0
        if t < self.up.T:
            return self.up.at(t)
        if t < self.up.T + self.cruise:
            return self.up.distance() + self.peak * (t - self.up.T), self.peak
        gone, speed = self.down.at(self.duration - t)
        return 1.0 - gone, speed

    def s(self, t):
        """The path parameter at t seconds into the move: 0 before, 1 from the end on (exactly, as the board clamps)."""
        return min(1.0, max(0.0, self._at(t)[0]))

    def speed(self, t):
        """ds/dt at t (per second)."""
        return self._at(t)[1]


def path_limits(delta_deg, axes):
    """The shared path parameter's (v, a, j, d) for a move of (d_pan, d_tilt) degrees under the profile's per-axis
    limits (v_max, a_max, j_max, and d_max, which defaults to a_max): whichever axis is tighter, limit by limit, as the
    board plans it. None for no move."""
    moving = [(abs(dd), axis) for dd, axis in zip(delta_deg, axes) if abs(dd) > 1e-9]
    if not moving:
        return None
    return (min(axis["v_max"] / dd for dd, axis in moving), min(axis["a_max"] / dd for dd, axis in moving),
            min(axis["j_max"] / dd for dd, axis in moving),
            min(axis.get("d_max", axis["a_max"]) / dd for dd, axis in moving))


def stretch(limits, k):
    """A timed MOVE's time stretch (firmware 62ea043, motion.md): the same S-curve k times slower is the S-curve of
    speed / k, acceleration and deceleration / k^2, and jerk / k^3."""
    v, a, j, d = limits
    return v / k, a / (k * k), j / (k ** 3), d / (k * k)


class Glide:
    """One MOVE's motion from start_cdeg to target_cdeg (integer centidegrees, contract 5's joints) begun at `started`
    (the coordinator's monotonic seconds), as the board flies it. min_duration_s is a timed MOVE's floor: the board
    stretches its S-curve to at least that long (k = max(1, min_duration / T)), and the published limits are the
    stretched ones, so Home draws the move the board makes from the same duration function. settle_s is the profile's
    settle: the board arrives once both axes have stayed within 0.2 degrees that long after the S-curve ends (its
    predicted_ms is the two together), so a dark move relights, and a MOVE waiting on this one starts, no sooner."""

    def __init__(self, start_cdeg, target_cdeg, axes, started, min_duration_s=0.0, settle_s=0.0):
        self.start, self.target, self.started = tuple(start_cdeg), tuple(target_cdeg), started
        self.settle = max(0.0, settle_s)
        limits = path_limits(tuple((b - a) / 100 for a, b in zip(self.start, self.target)), axes)
        self.curve = SCurve(*limits) if limits else None
        if self.curve is not None and min_duration_s > self.curve.duration > 0:
            self.curve = SCurve(*stretch(limits, min_duration_s / self.curve.duration))
        self.duration = self.curve.duration if self.curve else 0.0

    def at(self, now):
        """The joints at `now`, rounded to centidegrees as the board reports them."""
        s = self.curve.s(now - self.started) if self.curve else 1.0
        return tuple(round(a + (b - a) * s) for a, b in zip(self.start, self.target))

    def done(self, now):
        return now >= self.arrival

    @property
    def arrival(self):
        """The earliest the board reports it arrived: the S-curve, then the settle."""
        return self.started + self.duration + self.settle

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


# ---- The pace of a lit move -------------------------------------------------------------------------------------------

# A lit spot holds its frame while the page is this well inside the beam (a fraction of half its FWHM: at 0.25 the page
# keeps 96 % of the centre's light), so the light never drifts while someone reads; it reframes once they've moved. In
# the reading sim that's about one lit move a session (the reframe after the reader's shift); 0.35 holds through the
# shift itself and leaves the page off-centre.
FRAME_HOLD = 0.25
GLIDE_NEAR_M = 0.08             # a reframe for a page moved this little or less creeps over creep_s
GLIDE_FAR_M = 0.20              # one moved this far or more travels over glide_s; between them, in proportion


def glide_seconds(moved_m, settings):
    """How long a lit reframe takes, like a camera move, from how far the page moved: a small correction creeps, so the
    eye doesn't catch it; a big one, after the reader has shifted, is a dolly's deliberate move. The fixture's own
    S-curve is stretched to this time (a timed MOVE's min_duration), so it starts and lands as softly as ever."""
    x = min(1.0, max(0.0, (moved_m - GLIDE_NEAR_M) / (GLIDE_FAR_M - GLIDE_NEAR_M)))
    return settings["creep_s"] + (settings["glide_s"] - settings["creep_s"]) * x


# ---- Fades -------------------------------------------------------------------------------------------------------

def ease(curve, x):
    """0 to 1 over x in 0..1, on the C12's own fade curves (the timed MOVE's flag bits 5-6, firmware 62ea043), named as
    c12.move's fade_curve names them, so a fade the coordinator plays as a level train is the one the board will play
    from a single timed MOVE. Levels are already close to perceived lightness (Home's dimming curve is CIE lightness), so
    the curves act on what the eye sees.
    - ease_out, sin(x pi/2): rises and exits. A rise is visible at its first step (8 % of the goal 0.1 s into a 2 s
      rise), half way at a third of its time, and settles softly; an exit lets most of the light go first;
    - ease_in_out, (1 - cos(x pi))/2: crossfades and the room's glow, with no visible start or landing;
    - linear."""
    x = min(1.0, max(0.0, x))
    if curve == "ease_out":
        return math.sin(math.pi * x / 2.0)
    if curve == "ease_in_out":
        return (1.0 - math.cos(math.pi * x)) / 2.0
    return x


def output(level):
    """A 0-1 level's relative light output: CIE lightness read inversely, den-core's dimming curve
    (spot_light.dimming_fraction, lit-model.mjs's assumed curve). Level 0.5 gives 18 % of full."""
    lightness = 100.0 * min(1.0, max(0.0, level)) if math.isfinite(level) else 0.0
    return ((lightness + 16.0) / 116.0) ** 3 if lightness > CIE_KAPPA * CIE_EPSILON else lightness / CIE_KAPPA


def level_for_output(fraction):
    """output()'s inverse: the level that gives this fraction of full light."""
    fraction = min(1.0, max(0.0, fraction))
    lightness = 116.0 * fraction ** (1.0 / 3.0) - 16.0 if fraction > CIE_EPSILON else CIE_KAPPA * fraction
    return min(1.0, max(0.0, lightness / 100.0))


class Ramp:
    """A level (or any value) moving from `start` to `end` over `duration` seconds on `curve`, begun at `started`.
    paused() moves its clock on: a ramp waits while its fixture glides (one MOVE, one level).

    space "light" eases the light itself rather than the level: a crossfade's two ramps then add up to the same light
    on the page all the way through. Eased in levels, two complementary ramps give 37 % of the light half way, because
    level 0.5 gives 18 % (output()). The board's own fade curves act on levels, so a light ramp is always a train."""

    def __init__(self, start, end, duration, curve, started, purpose, space="level"):
        self.start, self.end, self.duration, self.curve = start, end, max(0.0, duration), curve
        self.started, self.purpose, self.space = started, purpose, space
        self.sent = False   # a timed MOVE carries it: the board plays it, and the coordinator follows its curve

    def value(self, now):
        if self.duration <= 0:
            return self.end
        x = ease(self.curve, (now - self.started) / self.duration)
        if self.space == "light":
            low, high = output(self.start), output(self.end)
            return level_for_output(low + (high - low) * x)
        return self.start + (self.end - self.start) * x

    def done(self, now):
        return now - self.started >= self.duration

    def paused(self, seconds):
        self.started += seconds

    def published(self, now):
        return {"from": round(self.start, 4), "to": round(self.end, 4), "start_mono": self.started,
                "duration_s": self.duration, "curve": self.curve, "purpose": self.purpose,
                "value": round(self.value(now), 4), "board": self.sent, "space": self.space}


# ---- The aim: where the page is ---------------------------------------------------------------------------------------

def page_point(joints, up_m):
    """The page's centre for a seated reader. A book is read square to the line of sight and held by its lower half, so
    its centre is `up_m` up the page from the wrists' midpoint. "Up the page" lies in the vertical plane through the
    eyes and the hands, at right angles to the line between them. So the hold sets the direction: a book at the chest
    is tipped back to the face, and its centre is ahead of and above the hands. A book in the lap lies nearly flat on
    the thighs, and its centre is almost straight ahead of them. Never back toward the eyes. None without both wrists
    and a head."""
    def joint(name):
        row = (joints or {}).get(name)
        return tuple(row[:3]) if row and len(row) >= 4 and row[3] >= 0.5 else None
    left, right = joint("left_wrist"), joint("right_wrist")
    face = [p for p in (joint(n) for n in ("nose", "left_eye", "right_eye", "left_ear", "right_ear")) if p]
    if not (left and right and face):
        return None
    hands = tuple((left[i] + right[i]) / 2.0 for i in range(3))
    eyes = tuple(statistics.median(p[i] for p in face) for i in range(3))
    sight = [hands[i] - eyes[i] for i in range(3)]
    ahead = math.hypot(sight[0], sight[1])
    length = math.hypot(ahead, sight[2])
    if ahead < PAGE_MIN_AHEAD_M:
        return hands   # hands straight below the eyes: no page direction to go by
    # The line of sight is (ahead, down) in its vertical plane; up the page is that turned a quarter toward up.
    forward = (sight[0] / ahead, sight[1] / ahead)
    up = (-sight[2] / length, ahead / length)
    return (hands[0] + up_m * up[0] * forward[0], hands[1] + up_m * up[0] * forward[1], hands[2] + up_m * up[1])


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
        self.cct_goal = None
        # A cue's warmth once a learned warmth is in play: (scale0, k0, scale1, k1, started, duration).
        self.cue = None

    def direct(self, task, settings, now, goal=None, cct_k=None):
        """goal and cct_k: the room glow the owner taught for this situation (plan 04b), as a scale of the house level
        and a warmth, fixed by the preview for the cue; None keeps the direction's own."""
        goal = goal if goal is not None else settings["ambient_task_scale"] if task else 1.0
        if (goal, cct_k) == (self.goal, self.cct_goal):
            return
        current = self.value(now)
        k0 = self._warmth(current, settings, now)
        k1 = cct_k if cct_k is not None else self.kelvin(goal, settings)
        self.goal, self.cct_goal = goal, cct_k
        if goal < current:
            self.ramp = Ramp(current, goal, settings["ambient_dim_s"], "ease_in_out", now + settings["ambient_delay_s"],
                             "ambient_dim")
        else:
            self.ramp = Ramp(current, goal, settings["ambient_restore_s"], "ease_in_out", now, "ambient_restore")
        # Off the direction's own curve (a learned warmth now, or one still being left), the warmth runs from where it
        # is to the cue's warmth: on a line in the cue's scale, or, when the level doesn't change, on the cue's own
        # time and curve. It never jumps.
        self.cue = (None if cct_k is None and k0 == self.kelvin(current, settings)
                    else (current, k0, goal, k1, self.ramp.started, self.ramp.duration))

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

    def _warmth(self, scale, settings, now=None):
        """The warmth at this scale (and, for a cue that holds the level, at `now`; None is where the cue lands)."""
        if self.cue is None:
            return self.kelvin(scale, settings)
        s0, k0, s1, k1, started, duration = self.cue
        if abs(s1 - s0) > 1e-6:
            return round(k0 + (k1 - k0) * min(1.0, max(0.0, (scale - s0) / (s1 - s0))))
        if now is None or duration <= 0:
            return k1
        return round(k0 + (k1 - k0) * ease("ease_in_out", (now - started) / duration))

    def published(self, settings, now):
        """The one ambient record (Home's glow now, den_lighting's bulb plan later): the value now, and the cue it's on.
        phase: dim, restore or steady; goal_*: where the cue lands; ramp_s and ramp_starts_mono: its time (a dim starts
        ambient_delay_s after it's called, so ramp_starts_mono can be ahead of now)."""
        ramp = self.ramp
        scale = self.value(now)
        out = {"level": round(min(1.0, settings["ambient_level"] * scale), 4), "scale": round(scale, 4),
               "cct_k": self._warmth(scale, settings, now),
               "goal_level": round(min(1.0, settings["ambient_level"] * self.goal), 4),
               "goal_scale": round(self.goal, 4), "goal_cct_k": self._warmth(self.goal, settings),
               "phase": "steady", "ramp_s": 0.0, "ramp_starts_mono": None}
        if ramp is not None and self.ramp is not None:
            out.update(phase="dim" if ramp.end < ramp.start else "restore", ramp_s=ramp.duration,
                       ramp_starts_mono=ramp.started, cue=ramp.purpose)
        return out
