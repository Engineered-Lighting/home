"""Room metres to a fixture's joint angles, in the frame the fixture firmware takes (coordinator plan C7).

Frame c12-fixture-joints-1: tilt 0 is level, positive up; pan 0 is straight ahead (the fixture's forward,
Home's horizontal_world_reference), positive is a left turn (counter-clockwise seen from above). Pan never
wraps past the back, so a move from +170 to -170 goes the long way.

The two other conventions in the repo, pinned by tests:
- the lighting brain's aim.py: pan positive to the fixture's right, tilt positive down, so pan = -pan_aim
  and tilt = -tilt_aim;
- Home's compile_target: tilt 0 straight down, so tilt = tilt_compile - 90.

Standard library only. Pure.
"""
import math

FRAME = "c12-fixture-joints-1"
INSET_DEG = 5.0       # the host uses the fixture's range inset by this much
MAX_ROUTE_DEG = 180.0  # the firmware's pass mark for one continuous move


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def basis(reference):
    """(forward, left, up) unit vectors in the room from Home's horizontal_world_reference."""
    length = math.hypot(reference[0], reference[1])
    if length < 1e-9:
        raise ValueError("horizontal_world_reference has no horizontal part")
    forward = (reference[0] / length, reference[1] / length, 0.0)
    return forward, (-forward[1], forward[0], 0.0), (0.0, 0.0, 1.0)


def room_to_joints(target, pivot, reference):
    """(pan_deg, tilt_deg) that point the beam from pivot at target."""
    forward, left, up = basis(reference)
    d = _sub(target, pivot)
    x, y, z = _dot(d, forward), _dot(d, left), _dot(d, up)
    horizontal = math.hypot(x, y)
    if horizontal < 1e-9:
        raise ValueError("pan_direction_ambiguous: the target is straight above or below the pivot")
    return math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, horizontal))


def direction(pan_deg, tilt_deg, reference):
    """The beam's unit direction in the room for these joint angles."""
    forward, left, up = basis(reference)
    p, t = math.radians(pan_deg), math.radians(tilt_deg)
    return tuple(math.cos(t) * (math.cos(p) * forward[i] + math.sin(p) * left[i]) + math.sin(t) * up[i] for i in range(3))


def to_cdeg(deg):
    """Centidegrees, rounded half away from zero."""
    return int(math.copysign(math.floor(abs(deg) * 100.0 + 0.5), deg))


def in_range(pan_deg, tilt_deg, rng, inset=INSET_DEG):
    """True when both angles sit inside the profile's range, inset by `inset` degrees."""
    return (rng["pan"][0] + inset <= pan_deg <= rng["pan"][1] - inset
            and rng["tilt"][0] + inset <= tilt_deg <= rng["tilt"][1] - inset)


def route(start, end):
    """(d_pan, d_tilt) for a move. Pan never wraps, so the route is the plain difference."""
    return end[0] - start[0], end[1] - start[1]


def route_ok(start, end):
    """False when either axis would travel past the firmware's 180 degree pass mark."""
    d_pan, d_tilt = route(start, end)
    return abs(d_pan) <= MAX_ROUTE_DEG and abs(d_tilt) <= MAX_ROUTE_DEG
