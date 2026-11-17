"""Bounded, pure walking-light proposals. No actuator, model, clock or file access.

The existing tracker owns velocity and covariance. Motion.forecast owns their
propagation, including observation age. This consumer only asks when a fixture
could illuminate a qualified virtual floor patch and checks that patch's bounds.
Intent is diagnostic until a separately reviewed replay demonstrates a benefit.
"""
import math
from collections.abc import Mapping

import direction
import motion


SCHEMA = "den-path-lighting-1"
MAX_FIXTURES, MAX_TRACKS, MAX_VERTICES, MAX_OBSTACLES = 16, 16, 64, 16
MAX_ITERATIONS = 16
SIGMA_MULTIPLIER = 3.0  # a conservative envelope, never a confidence percentage
MIN_SPEED_MPS = 0.10
KEYS = ("service_run_id", "camera_epoch", "frame_id", "track_id", "association_revision",
        "transform_id", "scan_revision", "depth_calibration_sha256")


def _field(value, key, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _number(value, low=0.0, high=60.0):
    if not _finite(value) or not low <= value <= high:
        raise ValueError("invalid bounded number")
    return float(value)


def _vector(value, size):
    if not isinstance(value, (tuple, list)) or len(value) != size:
        raise ValueError("invalid vector")
    return tuple(_number(v, -100000.0, 100000.0) for v in value)


def _identifier(value):
    if not isinstance(value, str) or not 0 < len(value) <= 256:
        raise ValueError("invalid identifier")
    return value


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return math.dist(point, a)
    fraction = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(point[0] - a[0] - fraction * dx, point[1] - a[1] - fraction * dy)


def _intersects(a, b, c, d):
    values = (_cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b))
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True
    return any(abs(v) <= 1e-10 and _distance(p, x, y) <= 1e-10
               for v, p, x, y in ((values[0], c, a, b), (values[1], d, a, b),
                                 (values[2], a, c, d), (values[3], b, c, d)))


def _edges(polygon):
    return zip(polygon, polygon[1:] + polygon[:1])


def _polygon(value):
    if not isinstance(value, (list, tuple)) or not 3 <= len(value) <= MAX_VERTICES:
        raise ValueError("invalid polygon size")
    polygon = tuple(_vector(p, 2) for p in value)
    edges = tuple(_edges(polygon))
    if any(math.dist(a, b) < 1e-6 for a, b in edges):
        raise ValueError("degenerate polygon edge")
    if abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)) < 1e-8:
        raise ValueError("degenerate polygon")
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if j > i + 1 and (i, j) != (0, len(edges) - 1) and _intersects(a, b, c, d):
                raise ValueError("self-intersecting polygon")
    return polygon


def _inside(point, polygon):
    inside = False
    x, y = point
    for a, b in _edges(polygon):
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            inside = not inside
    return inside


def _segment_distance(a, b, c, d):
    if _intersects(a, b, c, d):
        return 0.0
    return min(_distance(a, c, d), _distance(b, c, d), _distance(c, a, b), _distance(d, a, b))


def _route_clear(start, target, radius, floor):
    polygon = floor["walkable_polygon_m"]
    if not _inside(start, polygon) or not _inside(target, polygon):
        return False
    if any(_segment_distance(start, target, a, b) <= radius for a, b in _edges(polygon)):
        return False
    for obstacle in floor["obstacles_m"]:
        if (_inside(start, obstacle) or _inside(target, obstacle)
                or any(_segment_distance(start, target, a, b) <= radius for a, b in _edges(obstacle))):
            return False
    return True


def _config(value):
    if value is None:
        return {"mode": "off", "cadence_s": 1.0}
    if not isinstance(value, Mapping) or set(value) - {"mode", "cadence_s", "floor", "timing"}:
        raise ValueError("invalid walking configuration")
    mode = value.get("mode", "off")
    if mode not in ("off", "shadow", "virtual"):
        raise ValueError("invalid walking mode")
    result = {"mode": mode, "cadence_s": _number(value.get("cadence_s", 1.0), 1.0, 60.0)}
    if mode == "off":
        return result
    raw = value.get("floor")
    required = {"schema", "profile_id", "qualification", "transform_id", "scan_revision",
                "depth_calibration_sha256", "floor_z_m", "walkable_polygon_m", "obstacles_m",
                "beam_radius_m", "max_sigma_m"}
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise ValueError("missing qualified floor")
    if raw["schema"] != "den-walkable-floor-1" or raw["qualification"] != "virtual":
        raise ValueError("floor is not qualified for virtual use")
    floor = {key: _identifier(raw[key]) for key in
             ("profile_id", "transform_id", "scan_revision", "depth_calibration_sha256")}
    floor.update(schema=raw["schema"], qualification="virtual", floor_z_m=_number(raw["floor_z_m"], -100, 100),
                 walkable_polygon_m=_polygon(raw["walkable_polygon_m"]),
                 beam_radius_m=_number(raw["beam_radius_m"], 0.01, 3.0),
                 max_sigma_m=_number(raw["max_sigma_m"], 0.001, 1.0))
    obstacles = raw["obstacles_m"]
    if not isinstance(obstacles, (tuple, list)) or len(obstacles) > MAX_OBSTACLES:
        raise ValueError("invalid obstacle count")
    floor["obstacles_m"] = tuple(_polygon(p) for p in obstacles)
    if sum(map(len, floor["obstacles_m"])) + len(floor["walkable_polygon_m"]) > 256:
        raise ValueError("too many floor vertices")
    timing = value.get("timing")
    if not isinstance(timing, Mapping) or not 0 < len(timing) <= MAX_FIXTURES:
        raise ValueError("missing bounded fixture timing")
    result["timing"] = {}
    for name, row in timing.items():
        _identifier(name)
        if (not isinstance(row, Mapping) or set(row) != {"basis", "queue_bound_s", "command_bound_s", "transport_bound_s"}
                or row["basis"] != "virtual_profile"):
            raise ValueError("invalid fixture timing basis")
        result["timing"][name] = {"basis": "virtual_profile", **{
            key: _number(row[key], 0, 2) for key in ("queue_bound_s", "command_bound_s", "transport_bound_s")}}
    result["floor"] = floor
    return result


def _head(track):
    value = _field(_field(track, "head_point_m"), "xyz")
    return _vector(value, 3) if value is not None else None


def _eyes(track):
    pose = _field(track, "supported_pose", {})
    if not isinstance(pose, Mapping) or len(pose) > 64:
        raise ValueError("invalid_eye_evidence")
    eyes = []
    for name in ("left_eye", "right_eye"):
        value = pose.get(name)
        if isinstance(value, (tuple, list)) and len(value) == 2 and value[1] == "supported":
            eyes.append(_vector(value[0], 3))
    return eyes


def _segment_distance_3d(a, b, c, d):
    """Distance between two closed segments, including interior crossings."""
    u, v, w = [y - x for x, y in zip(a, b)], [y - x for x, y in zip(c, d)], [x - y for x, y in zip(a, c)]
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))
    aa, ee, ff = dot(u, u), dot(v, v), dot(v, w)
    clamp = lambda value: max(0.0, min(1.0, value))
    if aa <= 1e-20 and ee <= 1e-20:
        return math.dist(a, c)
    if aa <= 1e-20:
        s, t = 0.0, clamp(ff / ee)
    else:
        cc = dot(u, w)
        if ee <= 1e-20:
            s, t = clamp(-cc / aa), 0.0
        else:
            bb = dot(u, v)
            denominator = aa * ee - bb * bb
            s = clamp((bb * ff - cc * ee) / denominator) if denominator > 1e-20 else 0.0
            t = (bb * s + ff) / ee
            if t < 0:
                s, t = clamp(-cc / aa), 0.0
            elif t > 1:
                s, t = clamp((bb - cc) / aa), 1.0
    return math.sqrt(sum((w[i] + s * u[i] - t * v[i]) ** 2 for i in range(3)))


def _beam_obstructed(origin, target, radius, floor):
    # Unknown obstacle heights are conservatively infinite vertical prisms.
    return any(_inside(origin[:2], obstacle) or _inside(target[:2], obstacle)
               or any(_segment_distance(origin[:2], target[:2], a, b) <= radius
                      for a, b in _edges(obstacle)) for obstacle in floor["obstacles_m"])


def _source(state, subject):
    return dict(subject, basis=state.basis, observed_mono=state.observed_mono,
        available_mono=state.available_mono, valid_until_mono=state.valid_until_mono,
        velocity_mps=list(state.velocity_mps), source_obs_ids=list(state.source_obs_ids),
        time_status=state.time_status, time_qualified=state.time_qualified,
        timing_evidence_id=state.timing_evidence_id)


def _held(names):
    if isinstance(names, Mapping):
        names = names.keys()
    if not isinstance(names, (tuple, list, set, frozenset)) and not hasattr(names, "__iter__"):
        raise ValueError("invalid manual holds")
    result = set()
    for i, hold in enumerate(names):
        if i >= 128:
            raise ValueError("too many manual holds")
        if isinstance(hold, str):
            result.add(hold)
        elif isinstance(hold, (tuple, list)) and len(hold) == 2:
            if hold[1] == "spot":
                result.add(hold[0])
        else:
            raise ValueError("invalid manual hold")
    return result


class Planner:
    """One bounded consumer, retaining only the last plan and source watermarks.

    Fixtures use Preview's rows: profile, current/ranges (centidegrees), origin,
    horizontal reference and optional glide/pending. Outputs are proposals only.
    Every call rechecks safety, even while the ordinary one-second cadence waits.
    """
    def __init__(self, core, config=None):
        self.core = core
        self.problem = None
        try:
            self.config = _config(config)
        except (ValueError, TypeError, OverflowError):
            self.config, self.problem = {"mode": "off", "cadence_s": 1.0}, "invalid_configuration"
        self.last_plan = None
        self.last_planned_mono = None
        self.last_now = None
        self.scope = None
        self.watermark = (-1, -1)
        self.retired = []
        self.receipt = None

    def evaluate(self, snapshot, fixtures, now, *, on, stopped, manual_holds=(), reading_active=False,
                 subject_binding=None, intent=None):
        mode = self.config["mode"]
        result = {"schema": SCHEMA, "mode": mode, "status": "abstained", "reason": "no_motion",
                  "evaluated_mono": now if _finite(now) else None, "original_valid_until_mono": None,
                  "valid_until_mono": None,
                  "subject": None, "evidence": {"motion": None, "intent": {
                      "status": "advisory_not_admitted" if intent is not None else "absent"}},
                  "proposals": [], "virtual_beams": []}

        def clear(reason, status="abstained"):
            self.last_plan = None
            result.update(status=status, reason=reason)
            if result["evidence"]["motion"] is not None:
                result["evidence"]["motion"] = dict(result["evidence"]["motion"],
                    disposition="ignored", selected=False, reason=reason)
            return result

        if self.problem:
            return clear(self.problem, "blocked")
        if type(on) is not bool or type(stopped) is not bool or type(reading_active) is not bool:
            return clear("invalid_controls", "blocked")
        if mode == "off" or not on or stopped:
            return clear("stopped" if stopped else "off", "off")
        if reading_active:
            return clear("reading_active")
        if not _finite(now) or (self.last_now is not None and now < self.last_now):
            return clear("invalid_clock", "blocked")
        self.last_now = now
        try:
            state, subject, heads = self._subject(snapshot, now, subject_binding)
            result["subject"] = subject
            result["original_valid_until_mono"] = result["valid_until_mono"] = state.valid_until_mono
            result["evidence"]["motion"] = _source(state, subject)
            floor = self.config["floor"]
            if any(subject[key] != floor[key] for key in ("transform_id", "scan_revision", "depth_calibration_sha256")):
                return clear("floor_lineage_mismatch")
            if math.hypot(*state.velocity_mps) < MIN_SPEED_MPS:
                return clear("stationary")
            if not isinstance(fixtures, Mapping) or not 0 < len(fixtures) <= MAX_FIXTURES:
                return clear("invalid_fixtures", "blocked")
            for name in fixtures:
                _identifier(name)
            held = _held(manual_holds)
            previous = self.last_plan
            if previous:
                same = all(previous["subject"][key] == subject[key] for key in KEYS if key != "frame_id")
                old = previous["velocity"]
                cosine = sum(a * b for a, b in zip(old, state.velocity_mps)) / (math.hypot(*old) * math.hypot(*state.velocity_mps))
                if not same or cosine < math.cos(math.pi / 4):
                    self.last_plan = None
                    previous = None
                    if self.last_planned_mono is not None and now < self.last_planned_mono + self.config["cadence_s"]:
                        return clear("motion_changed")
            waiting = self.last_planned_mono is not None and now < self.last_planned_mono + self.config["cadence_s"] - 1e-9
            if waiting:
                if previous:
                    try:
                        unchanged = self._same_motion(fixtures[previous["proposal"]["fixture_id"]],
                                                      previous["motion_signature"], now)
                    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError):
                        unchanged = False
                    if not unchanged:
                        return clear("fixture_motion_changed")
                    if self._still_safe(previous, state, fixtures, held, heads, now):
                        return self._publish(result, previous["proposal"], "cadence")
                return clear("cadence")
            reasons, candidates = {}, []
            for name in sorted(fixtures):
                if name in held:
                    reasons[name] = "manual_hold"
                    continue
                try:
                    candidates.append(self._proposal(name, fixtures[name], state, subject, heads, now))
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError) as error:
                    reasons[name] = str(error) if isinstance(error, ValueError) else "invalid_fixture"
            result["fixture_reasons"] = reasons
            if not candidates:
                return clear("no_achievable_fixture")
            proposal = min(candidates, key=lambda p: (p["arrival_mono"], p["fixture_id"]))
            selected_row = fixtures[proposal["fixture_id"]]
            observed_motion = self._motion_signature(selected_row, now)
            self.last_planned_mono = now
            self.last_plan = {"proposal": proposal, "subject": subject, "velocity": state.velocity_mps,
                              "fixture_signature": self._signature(selected_row), "motion_signature": observed_motion}
            return self._publish(result, proposal, "eligible")
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError) as error:
            return clear(str(error) if isinstance(error, ValueError) else "invalid_evidence")

    def _subject(self, snapshot, now, binding):
        if _field(snapshot, "status") != "fresh" or _field(snapshot, "presence") != "present":
            raise ValueError("stale_or_unknown_presence")
        frame = _field(snapshot, "frame")
        tracks = _field(snapshot, "tracks")
        if not isinstance(tracks, (list, tuple)) or not 0 < len(tracks) <= MAX_TRACKS:
            raise ValueError("ambiguous_subject")
        scope = (_field(snapshot, "service_run_id"), _field(snapshot, "camera_epoch"))
        _identifier(scope[0])
        if type(scope[1]) is not int or not 0 <= scope[1] <= 2 ** 63 - 1:
            raise ValueError("invalid_source_counter")
        if scope != (_field(frame, "service_run_id"), _field(frame, "camera_epoch")):
            raise ValueError("snapshot_lineage_mismatch")
        if scope in self.retired:
            raise ValueError("retired_producer")
        if self.scope is not None and scope[0] == self.scope[0] and scope[1] < self.scope[1]:
            raise ValueError("source_replayed")
        counters = (_field(snapshot, "seq"), _field(frame, "frame_id"))
        if any(type(v) is not int or not 0 <= v <= 2 ** 63 - 1 for v in counters):
            raise ValueError("invalid_source_counter")
        if self.scope == scope and any(a < b for a, b in zip(counters, self.watermark)):
            raise ValueError("source_replayed")
        if self.scope != scope:
            if self.scope is not None:
                self.retired = (self.retired + [self.scope])[-8:]
            self.scope, self.watermark = scope, (-1, -1)
        self.watermark = counters
        if binding is not None and (not isinstance(binding, Mapping) or len(binding) > len(KEYS) or set(binding) - set(KEYS)
                                   or not set(KEYS).difference({"frame_id"}) <= set(binding)):
            raise ValueError("invalid_subject_binding")
        eligible = []
        for track in tracks:
            candidate = {key: _field(track if key in ("track_id", "association_revision") else frame, key) for key in KEYS}
            if binding is None or all(candidate.get(key) == value for key, value in binding.items()):
                eligible.append((track, candidate))
        if len(eligible) != 1:
            raise ValueError("ambiguous_subject")
        track, subject = eligible[0]
        for key in ("service_run_id", "track_id", "transform_id", "scan_revision", "depth_calibration_sha256"):
            _identifier(subject[key])
        if type(subject["association_revision"]) is not int or not 0 <= subject["association_revision"] <= 2 ** 63 - 1:
            raise ValueError("invalid_source_counter")
        if binding is None and len(tracks) != 1:
            raise ValueError("ambiguous_subject")
        if (_field(track, "state") != "confirmed" or _field(track, "association_status") != "confident"
                or _field(track, "position_basis") != "stereo" or not _field(track, "current_obs_ids")):
            raise ValueError("unsafe_association")
        if _field(_field(track, "seated"), "state") is not False:
            raise ValueError("seated_or_unknown_posture")
        state = self._bound_motion(track, frame, now)
        receipt_key = tuple(subject[key] for key in KEYS)
        if self.receipt is not None and self.receipt[0] == receipt_key and self.receipt[1] != state:
            raise ValueError("source_receipt_changed")
        self.receipt = receipt_key, state
        heads = []
        for person in tracks:
            if (_field(person, "state") != "confirmed" or _field(person, "association_status") != "confident"
                    or not _field(person, "current_obs_ids") or _field(person, "position_basis") != "stereo"):
                raise ValueError("unknown_bystander")
            head = _head(person)
            if head is None:
                raise ValueError("unknown_head")
            person_motion = self._bound_motion(person, frame, now)
            heads.append({"head": head, "eyes": _eyes(person), "motion": person_motion})
        return state, subject, heads

    @staticmethod
    def _bound_motion(track, frame, now):
        state = _field(track, "motion")
        observations = _field(track, "current_obs_ids")
        if (not isinstance(state, motion.MotionState)
                or not motion.matches(state, frame, _field(track, "track_id"), _field(track, "association_revision"))
                or _field(frame, "capture_mono") != state.observed_mono
                or len(state.source_obs_ids) > 64 or any(len(value) > 256 for value in state.source_obs_ids)
                or not isinstance(observations, (tuple, list)) or len(observations) > 64
                or tuple(observations) != state.source_obs_ids
                or motion.forecast(state, now, frame=frame, track_id=_field(track, "track_id"),
                    association_revision=_field(track, "association_revision"), horizons_s=(0.0,)) is None):
            raise ValueError("missing_or_stale_motion")
        return state

    def _sample(self, state, now, horizon):
        forecast = motion.forecast(state, now, horizons_s=(horizon,))
        if forecast is None:
            raise ValueError("unsupported_horizon")
        sample = forecast.samples[0]
        if sample.sigma_m > self.config["floor"]["max_sigma_m"]:
            raise ValueError("uncertainty_too_large")
        return sample

    def _fixture(self, row):
        profile = row["profile"]
        _identifier(profile["label"])
        origin, reference = _vector(row["aim_origin_m"], 3), _vector(row["horizontal_world_reference"], 3)
        if math.hypot(*reference[:2]) < 1e-9:
            raise ValueError("invalid_fixture_reference")
        current = _vector(row["current"], 2)
        if not isinstance(row["ranges"], (tuple, list)) or len(row["ranges"]) != 2:
            raise ValueError("invalid_fixture_ranges")
        ranges = tuple(_vector(pair, 2) for pair in row["ranges"])
        if any(lo >= hi or not lo <= v <= hi for v, (lo, hi) in zip(current, ranges)):
            raise ValueError("invalid_fixture_ranges")
        axes = profile["axes"]
        if not isinstance(axes, (list, tuple)) or len(axes) != 2:
            raise ValueError("invalid_fixture_axes")
        for axis in axes:
            for key in ("v_max", "a_max", "j_max"):
                _number(axis[key], 1e-9, 1e8)
            if "d_max" in axis:
                _number(axis["d_max"], 1e-9, 1e8)
        settle = _number(profile.get("settle_s"), 0, 2)
        half = math.radians(_number(profile.get("beam_fwhm_deg"), 0.01, 120) / 2)
        if row.get("pending") is not None:
            raise ValueError("fixture_pending")
        return profile, origin, reference, current, ranges, settle, half

    def _proposal(self, name, row, state, subject, heads, now):
        floor = self.config["floor"]
        timing = self.config["timing"].get(name)
        if timing is None:
            raise ValueError("missing_fixture_timing")
        profile, origin, reference, current, ranges, settle, half = self._fixture(row)
        height = origin[2] - floor["floor_z_m"]
        if height <= 0:
            raise ValueError("fixture_below_floor")
        glide_wait = 0.0
        glide = row.get("glide")
        if glide is not None:
            if not isinstance(glide, direction.Glide):
                raise ValueError("unknown_current_motion")
            # Always wait for rest; this consumer does not invent in-flight velocity.
            glide_wait = max(0.0, glide.arrival - now)
            current = glide.target
        last = now - self.config["cadence_s"] if self.last_planned_mono is None else self.last_planned_mono
        cadence_wait = max(0.0, last + self.config["cadence_s"] - now)
        fixed = cadence_wait + glide_wait + sum(timing[key] for key in
            ("queue_bound_s", "command_bound_s", "transport_bound_s"))
        if fixed > motion.MAX_HORIZON_S:
            raise ValueError("unsupported_horizon")
        horizon = fixed
        answer = None
        for _ in range(MAX_ITERATIONS):
            sample = self._sample(state, now, horizon)
            envelope = SIGMA_MULTIPLIER * sample.sigma_m
            speed = math.hypot(*state.velocity_mps)
            lead = min(speed * 0.2, max(0.0, floor["beam_radius_m"] - envelope) / 2)
            xy = tuple(p + lead * v / speed for p, v in zip(sample.xy_m, state.velocity_mps))
            target = (*xy, floor["floor_z_m"])
            joints = tuple(map(self.core.joints.to_cdeg, self.core.joints.room_to_joints(target, origin, reference)))
            if (any(not lo <= v <= hi for v, (lo, hi) in zip(joints, ranges))
                    or any(abs(a - b) > 18000 for a, b in zip(joints, current))):
                raise ValueError("unreachable_target")
            planned = direction.Glide(current, joints, profile["axes"], now + fixed, settle_s=settle)
            actual = fixed + planned.duration + settle
            if actual > motion.MAX_HORIZON_S or now + actual >= state.valid_until_mono:
                raise ValueError("arrival_after_evidence_expiry")
            answer = sample, target, joints, planned, envelope, lead
            if abs(actual - horizon) <= 0.002:
                # Bound the residual by checking the forecast at both endpoints.
                horizon = max(actual, horizon)
                break
            horizon = actual
        else:
            raise ValueError("arrival_not_converged")
        sample, target, joints, planned, envelope, lead = answer
        # The fixed-point tolerance is an interval, not permission to hide travel.
        residual = abs(sample.at_mono - planned.arrival)
        envelope += speed * residual
        radius, inner = self._footprint(origin, target, half)
        if radius > floor["beam_radius_m"]:
            raise ValueError("footprint_exceeds_bound")
        if envelope + lead > inner:
            raise ValueError("uncertainty_outside_beam")
        if not _route_clear(state.xy_m, target[:2], radius + envelope, floor):
            raise ValueError("route_or_patch_obstructed")
        if _beam_obstructed(origin, target, radius + envelope, floor):
            raise ValueError("beam_obstacle_prism")
        if not self._heads_clear(origin, target, heads, envelope, now, max(sample.at_mono, planned.arrival)):
            raise ValueError("beam_near_head")
        arrival = max(sample.at_mono, planned.arrival)
        expiry = min(head["motion"].valid_until_mono for head in heads)
        if arrival >= expiry:
            raise ValueError("arrival_after_evidence_expiry")
        return {"fixture_id": name, "target_m": list(target), "joints_cdeg": list(joints),
                "start_mono": now + fixed, "arrival_mono": arrival,
                "valid_until_mono": expiry, "profile_id": profile["label"],
                "floor_profile_id": floor["profile_id"], "source_motion": _source(state, subject),
                "precision_qualified": False, "authority": "virtual_only",
                "uncertainty": {"sigma_m": sample.sigma_m, "envelope_m": envelope,
                    "sigma_multiplier": SIGMA_MULTIPLIER, "calibrated_probability": False,
                    "covariance_m2": [list(v) for v in sample.covariance_m2]},
                "timing": {"basis": "virtual_profile", "observation_age_s": now - state.observed_mono,
                    "cadence_wait_s": cadence_wait, "current_motion_wait_s": glide_wait,
                    **{key: timing[key] for key in ("queue_bound_s", "command_bound_s", "transport_bound_s")},
                    "motor_s": planned.duration, "settle_s": settle, "fade_to_useful_s": 0.0,
                    "forecast_horizon_s": arrival - now, "solve_residual_s": residual},
                "footprint": {"radius_m": radius, "inner_radius_m": inner,
                    "basis": "virtual_fwhm_floor_projection", "heading_lead_m": lead},
                "branch": "geometric_velocity"}

    @staticmethod
    def _footprint(origin, target, half):
        height = origin[2] - target[2]
        angle = math.atan2(math.dist(origin[:2], target[:2]), height)
        if angle + half >= math.pi / 2 - 1e-6:
            raise ValueError("unbounded_floor_footprint")
        radius = height * (math.tan(angle + half) - math.tan(angle))
        inner = height * (math.tan(angle) - math.tan(angle - half))
        return radius, inner

    def _heads_clear(self, origin, target, heads, envelope, now, arrival):
        for person in heads:
            state = person["motion"]
            horizon = max(0.0, arrival - now)
            times = (0.0,) if horizon == 0 else (0.0, horizon)
            forecast = motion.forecast(state, now, horizons_s=times)
            if forecast is None:
                return False
            samples = forecast.samples
            sigma = max(sample.sigma_m for sample in samples)
            if sigma > self.config["floor"]["max_sigma_m"]:
                return False
            margin = 0.15 + envelope + SIGMA_MULTIPLIER * sigma
            # Without both supported eye points, expand the head clearance by
            # Preview's existing 10 cm eye offset, without inventing facing.
            head_margin = margin + (0.1 if len(person["eyes"]) != 2 else 0.0)
            for point, clearance in [(person["head"], head_margin), *((eye, margin) for eye in person["eyes"])]:
                ends = [tuple(point[i] + (sample.xy_m[i] - state.xy_m[i] if i < 2 else 0.0)
                              for i in range(3)) for sample in (samples[0], samples[-1])]
                if (any(self.core.eye.centre_clear(origin, target, end) < clearance for end in ends)
                        or _segment_distance_3d(origin, target, *ends) < clearance):
                    return False
        return True

    def _signature(self, row):
        profile, origin, reference, _current, ranges, settle, half = self._fixture(row)
        axes = tuple(tuple(axis.get(key) for key in ("v_max", "a_max", "j_max", "d_max"))
                     for axis in profile["axes"])
        return profile["label"], axes, origin, reference, ranges, settle, half

    @staticmethod
    def _glide_signature(glide):
        if not isinstance(glide, direction.Glide):
            raise ValueError("unknown_current_motion")
        return (_vector(glide.start, 2), _vector(glide.target, 2),
                _number(glide.started, 0, 1e12), _number(glide.duration, 0, 1e12),
                _number(glide.settle, 0, 2))

    def _motion_signature(self, row, now):
        if row.get("pending") is not None:
            raise ValueError("fixture_pending")
        current = _vector(row["current"], 2)
        glide = row.get("glide")
        flight = self._glide_signature(glide) if glide is not None else None
        if glide is not None and current != tuple(glide.at(now)):
            raise ValueError("fixture_motion_unobserved")
        # The reference fences a replacement command even if its numeric flight
        # happens to equal the old one. The scalar signature catches mutation.
        return {"current": current, "glide": glide, "flight": flight, "pending": None}

    def _same_motion(self, row, observed, now):
        if row.get("pending") is not None:
            return False
        current, glide = _vector(row["current"], 2), row.get("glide")
        known = observed["glide"]
        if known is None:
            return glide is None and current == observed["current"]
        if self._glide_signature(known) != observed["flight"]:
            return False
        if glide is known:
            return current == tuple(known.at(now))
        return glide is None and now >= known.arrival and current == tuple(known.target)

    def _still_safe(self, previous, state, fixtures, held, heads, now):
        proposal = previous["proposal"]
        name = proposal["fixture_id"]
        if (name in held or name not in fixtures or now >= proposal["valid_until_mono"]
                or state.valid_until_mono < proposal["valid_until_mono"]):
            return False
        try:
            row = fixtures[name]
            profile, origin, _reference, _current, _ranges, _settle, _half = self._fixture(row)
            if self._signature(row) != previous["fixture_signature"]:
                return False
            if not self._same_motion(row, previous["motion_signature"], now):
                return False
            sample = self._sample(state, now, max(0.0, proposal["arrival_mono"] - now))
            envelope = SIGMA_MULTIPLIER * sample.sigma_m
            if math.dist(sample.xy_m, proposal["target_m"][:2]) + envelope > proposal["footprint"]["inner_radius_m"]:
                return False
            return (_route_clear(state.xy_m, proposal["target_m"][:2],
                                 proposal["footprint"]["radius_m"] + envelope, self.config["floor"])
                    and not _beam_obstructed(origin, proposal["target_m"],
                                            proposal["footprint"]["radius_m"] + envelope, self.config["floor"])
                    and all(head["motion"].valid_until_mono >= proposal["valid_until_mono"] for head in heads)
                    and self._heads_clear(origin, proposal["target_m"], heads, envelope, now, proposal["arrival_mono"]))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError):
            return False

    def _publish(self, result, proposal, status):
        result.update(status=status, reason="geometric_velocity", proposals=[proposal])
        current_source, plan_source = result["evidence"]["motion"], proposal["source_motion"]
        if current_source != plan_source:
            result["validation_source"] = dict(current_source, disposition="consumed", selected=False,
                                               reason="cadence_safety_validation")
        result["evidence"]["motion"] = dict(plan_source, disposition="consumed", selected=True,
                                            reason="geometric_velocity")
        result["subject"] = {key: proposal["source_motion"][key] for key in KEYS}
        result["original_valid_until_mono"] = result["valid_until_mono"] = proposal["valid_until_mono"]
        # These are separate virtual annotations, never automatic fixture state.
        if self.config["mode"] == "virtual":
            result["virtual_beams"] = [dict(proposal, radius_m=proposal["footprint"]["radius_m"])]
        return result
