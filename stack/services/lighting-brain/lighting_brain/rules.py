"""Rules v1.1: a table-driven, pure function of (inputs, settings, clock).

``evaluate(inputs, overrides, geometry, settings, now, previous)`` derives a
small set of facts once, then runs the RULES table in order. Each rule only
writes the outputs it owns and records its "why". Nothing here reads the
wall clock or mutates its arguments; ``previous`` (the last evaluation) is
only used for smoothing: keeping a lit spot on the same fixture and target,
proximity levels inside their deadband, and the named nearest fixture, when
nothing meaningful moved.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Callable

from .aim import Reader, SpotPlan, add, norm, place_reader, plan_spot, scale, sub
from .geometry import Geometry
from .inputs import COST_WINDOW_S, Inputs
from .settings import Settings

MODES = ("off", "ambient", "night_ambient", "reading", "degraded_ambient")
# Most significant first: picks the rule a decision is attributed to.
RULE_ORDER = ("manual_pause", "room_off", "degraded_ambient", "reading_spot_on", "reading_spot_off",
              "proximity_ambient", "occupied_ambient")


@dataclass(frozen=True)
class Ambient:
    radial_level: float
    down_level: float
    cct_k: int
    fade_s: float


@dataclass(frozen=True)
class Spot:
    on: bool
    level: float
    cct_k: int
    target_m: tuple[float, float, float] | None
    fade_s: float


@dataclass(frozen=True)
class FixtureOutput:
    fixture_id: str
    ambient: Ambient
    spot: Spot
    ambient_rule: str
    spot_rule: str
    paused_until: float | None = None
    pause_reason: str | None = None


@dataclass(frozen=True)
class Override:
    fixture_id: str
    reason: str
    at: float
    until: float
    held: FixtureOutput | None = None


@dataclass(frozen=True)
class Facts:
    now: float
    room_present: bool
    vacant_since: float | None
    vacant_for: float  # 0 while present (or unknown), inf if never occupied
    room_off_due: bool
    occupancy_unknown: bool
    last_evidence_at: float | None
    zones_present: dict  # optional zone events: display and presence only
    positions: tuple  # fresh room-frame person points (person_position, pose shoulders)
    position_age: float | None
    at_sofa: bool
    people: int | None
    night: bool
    health: dict
    degraded_why: str | None
    activity_label: str | None
    activity_confidence: float | None
    activity_age: float | None
    activity_zone_hint: str | None
    reading_ok: bool
    reading_why: str
    pose_fresh: bool
    pose_age: float | None
    reader: Reader | None
    reader_why: str | None
    jev_calls_last_hour: int
    est_usd_last_hour: float | None


@dataclass(frozen=True)
class Evaluation:
    at: float
    mode: str
    mode_rule: str
    why: str
    fixtures: tuple[FixtureOutput, ...]
    rule_whys: dict
    facts: Facts
    spot_plan: SpotPlan | None = None
    ended_overrides: tuple[tuple[str, str], ...] = ()
    active_overrides: tuple[str, ...] = ()
    nearest_fixture: str | None = None

    def fixture(self, fixture_id: str) -> FixtureOutput | None:
        return next((f for f in self.fixtures if f.fixture_id == fixture_id), None)

    @property
    def spot_fixture(self) -> FixtureOutput | None:
        return next((f for f in self.fixtures if f.spot.on and f.paused_until is None), None)


@dataclass
class Work:
    """Scratch outputs the rules fill in, local to one evaluate() call."""
    ambient: dict
    spot: dict
    ambient_rule: dict
    spot_rule: dict
    mode: str = "off"
    mode_rule: str = "room_off"
    why: str = ""
    halted: bool = False
    spot_block: tuple[str, str] | None = None
    spot_plan: SpotPlan | None = None
    nearest: str | None = None
    paused: dict = field(default_factory=dict)
    ended: list = field(default_factory=list)
    rule_whys: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    name: str
    condition: str
    apply: Callable[[Facts, Work, "Context"], None]


@dataclass(frozen=True)
class Context:
    geometry: Geometry
    settings: Settings
    overrides: dict
    previous: Evaluation | None


def short_name(fixture_id: str) -> str:
    match = re.search(r"(?:fixture|mount)-(\d{2})", fixture_id)
    return f"fixture {match.group(1)}" if match else fixture_id


def _duration(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} min"
    return f"{seconds:g} s"


def _off_ambient(fade_s: float, cct_k: int) -> Ambient:
    return Ambient(0.0, 0.0, cct_k, fade_s)


def _off_spot(settings: Settings, fade_s: float) -> Spot:
    return Spot(False, 0.0, settings.spot_cct_k, None, fade_s)


# ---------------------------------------------------------------- facts


def derive_facts(inputs: Inputs, geometry: Geometry, settings: Settings, now: float) -> Facts:
    health = {}
    for component in ("jev", "deep_stack", "frigate", "camera"):
        obs = inputs.health.get(component)
        health[component] = bool(obs and obs.fresh(now) and obs.data["ok"])
    degraded_why = None
    if not health["jev"]:
        degraded_why = "JEV offline" if inputs.health.get("jev") else "JEV not reporting"
    elif not health["deep_stack"]:
        degraded_why = "Deep stack offline" if inputs.health.get("deep_stack") else "Deep stack not reporting"
    # Frigate reported, and is not (freshly) ok: an expired occupancy or zone
    # input then means "unknown", not "absent" (CONTRACT "Producer notes").
    frigate_down = inputs.health.get("frigate") is not None and not health["frigate"]

    act = inputs.activity if inputs.activity and inputs.activity.fresh(now) else None
    occ = inputs.occupancy
    # Zone events are optional (v1.1): they count as presence when sent.
    zones_present = {name: bool(obs and obs.fresh(now) and obs.data["present"])
                     for name, obs in ((z, inputs.zones.get(z)) for z in ("desk", "sofa"))}

    occ_fresh = occ is not None and occ.fresh(now)
    occupancy_unknown = frigate_down and not occ_fresh and not any(
        obs.fresh(now) for obs in inputs.zones.values())
    candidates = [inputs.occupancy_last_present_at, *inputs.zone_last_present_at.values()]
    known = [t for t in candidates if t is not None]
    evidence = known + ([inputs.last_person_evidence_at] if inputs.last_person_evidence_at is not None else [])
    last_evidence_at = max(evidence) if evidence else None

    if occupancy_unknown:
        # Hold; room_off waits for unknown_occupancy_off_s without any person evidence.
        room_present = False
        vacant_since, vacant_for = None, 0.0
        room_off_due = last_evidence_at is None or now - last_evidence_at >= settings.unknown_occupancy_off_s
    else:
        room_present = bool(occ_fresh and occ.data["present"]) or any(zones_present.values())
        vacant_since = None if room_present else (max(known) if known else None)
        if room_present:
            vacant_for = 0.0
        elif vacant_since is None:
            vacant_for = math.inf
        else:
            vacant_for = max(0.0, now - vacant_since)
        room_off_due = vacant_for >= settings.room_off_after_s

    pose = inputs.pose if inputs.pose and inputs.pose.fresh(now) else None
    position_obs = inputs.position if inputs.position and inputs.position.fresh(now) else None
    positions = []
    if position_obs:
        positions.extend(tuple(p["position_m"]) for p in position_obs.data["people"])
    if pose:
        for p in pose.data["people"]:
            left, right = p["shoulders_m"].get("left"), p["shoulders_m"].get("right")
            if left is not None and right is not None:
                positions.append(scale(add(left, right), 0.5))
    ages = [o.age(now) for o in (inputs.position, inputs.pose) if o is not None]
    sofa = geometry.zones.get("sofa")
    seat = sofa.seat_m if sofa else None
    at_sofa = seat is not None and any(
        math.hypot(x - seat[0], y - seat[1]) <= settings.pose_seat_max_m for x, y, _z in positions)

    counts = []
    if occ and occ.fresh(now):
        counts.append(occ.data.get("people_count"))
    for obs in inputs.zones.values():
        if obs.fresh(now) and obs.data["present"]:
            counts.append(obs.data.get("people_count"))
    if act:
        counts.append(act.data.get("person_count"))
    if pose:
        counts.append(pose.data.get("person_count"))
    if position_obs:
        counts.append(position_obs.data.get("person_count"))
    counts = [c for c in counts if c is not None]
    people = max(counts) if counts else None

    run = inputs.reading
    reading_now = bool(act and act.data["label"] == "reading" and run and run.ended_at is None)
    if run is None:
        reading_ok, reading_why = False, "Not reading"
    else:
        qualified = run.end(now) - run.since >= settings.reading_hold_on_s
        since_last = now - run.last_at
        if qualified and (reading_now or since_last < settings.reading_off_after_s):
            reading_ok, reading_why = True, "Reading"
        elif reading_now:
            reading_ok, reading_why = False, f"Reading for under {_duration(settings.reading_hold_on_s)}"
        elif qualified:
            reading_ok, reading_why = False, f"No reading for {_duration(settings.reading_off_after_s)}"
        else:
            reading_ok, reading_why = False, "Not reading"

    reader, reader_why = None, None
    if pose is None:
        reader_why = "Pose stale"
    else:
        # The people-count rule uses person_count; this only needs one placed
        # person to aim at (AL-3 projects at most one today).
        people_posed = pose.data["people"]
        if len(people_posed) != 1:
            reader_why = "Pose has no placed person" if not people_posed else "Pose needs exactly one person"
        else:
            person = people_posed[0]
            reader, problem = place_reader(person["shoulders_m"].get("left"), person["shoulders_m"].get("right"),
                                           person.get("head_m"), person.get("head_pitch_deg"),
                                           geometry.camera_m, settings)
            if reader is None:
                reader_why = {"shoulders_missing": "Pose has no shoulders",
                              "shoulders_too_close": "Pose shoulders unclear"}.get(problem, problem)
            else:
                seat = geometry.zones.get("sofa").seat_m if geometry.zones.get("sofa") else None
                if seat is not None and math.hypot(reader.shoulder_mid[0] - seat[0],
                                                   reader.shoulder_mid[1] - seat[1]) > settings.pose_seat_max_m:
                    reader, reader_why = None, "Pose not at sofa"

    window = [c for c in inputs.cost if now - COST_WINDOW_S <= c[0] <= now]
    usd = [c[2] for c in window if c[2] is not None]
    return Facts(
        now=now, room_present=room_present, vacant_since=vacant_since, vacant_for=vacant_for,
        room_off_due=room_off_due, occupancy_unknown=occupancy_unknown, last_evidence_at=last_evidence_at,
        zones_present=zones_present, positions=tuple(positions), position_age=min(ages) if ages else None,
        at_sofa=at_sofa, people=people, night=settings.is_night(now),
        health=health, degraded_why=degraded_why,
        activity_label=act.data["label"] if act else None,
        activity_confidence=act.data.get("confidence") if act else None,
        activity_age=inputs.activity.age(now) if inputs.activity else None,
        activity_zone_hint=act.data.get("zone_hint") if act else None,
        reading_ok=reading_ok, reading_why=reading_why,
        pose_fresh=pose is not None, pose_age=inputs.pose.age(now) if inputs.pose else None,
        reader=reader, reader_why=reader_why,
        jev_calls_last_hour=sum(c[1] for c in window),
        est_usd_last_hour=round(sum(usd), 6) if usd else None,
    )


# ---------------------------------------------------------------- rules


def rule_room_off(facts: Facts, work: Work, ctx: Context) -> None:
    s = ctx.settings
    if not facts.room_off_due:
        return
    for fid in work.ambient:
        work.ambient[fid] = _off_ambient(s.room_off_fade_s, s.ambient_cct_k)
        work.spot[fid] = _off_spot(s, s.room_off_fade_s)
        work.ambient_rule[fid] = work.spot_rule[fid] = "room_off"
    if facts.occupancy_unknown and facts.last_evidence_at is not None:
        why = f"No one seen for {_duration(s.unknown_occupancy_off_s)} · all off"
    elif math.isfinite(facts.vacant_for) and not facts.occupancy_unknown:
        why = f"Room empty for {_duration(s.room_off_after_s)} · all off"
    else:
        why = "Room empty · all off"
    work.mode, work.mode_rule, work.why, work.halted = "off", "room_off", why, True
    work.rule_whys["room_off"] = why


def proximity_level(distance_m: float, settings: Settings) -> float:
    """Linear from prox_far_level at >= prox_far_m to prox_near_level at <= prox_near_m."""
    if distance_m >= settings.prox_far_m:
        return settings.prox_far_level
    if distance_m <= settings.prox_near_m:
        return settings.prox_near_level
    t = (settings.prox_far_m - distance_m) / (settings.prox_far_m - settings.prox_near_m)
    return settings.prox_far_level + t * (settings.prox_near_level - settings.prox_far_level)


def rule_proximity_ambient(facts: Facts, work: Work, ctx: Context) -> None:
    """Ambient on every fixture whenever the room isn't off: brighter the
    closer the nearest fresh person position is (proximity_ambient), or a flat
    occupied_level with occupancy but no position (occupied_ambient)."""
    if work.halted:
        return
    s, previous = ctx.settings, ctx.previous
    cct = s.night_cct_k if facts.night else s.ambient_cct_k
    factor = s.night_factor if facts.night else 1.0
    distances = {}
    for fixture in ctx.geometry.fixtures:
        fid = fixture.fixture_id
        if facts.positions and fixture.aim_origin_m is not None:
            distances[fid] = min(norm(sub(p, fixture.aim_origin_m)) for p in facts.positions)
            rule, raw, fade = "proximity_ambient", proximity_level(distances[fid], s), s.prox_fade_s
        else:
            rule, raw, fade = "occupied_ambient", s.occupied_level, s.ambient_fade_s
        level = round(raw * factor, 3)
        before = previous.fixture(fid) if previous else None
        if (before is not None and before.paused_until is None and before.ambient_rule == rule
                and before.ambient.cct_k == cct and abs(before.ambient.radial_level - level) < s.prox_deadband):
            level = before.ambient.radial_level  # inside the deadband: no flicker
        work.ambient[fid] = Ambient(level, level, cct, fade)
        work.ambient_rule[fid] = rule

    nearest = None
    if distances:
        nearest = min(distances, key=lambda f: (distances[f], f))
        kept = previous.nearest_fixture if previous else None
        if kept in distances and distances[kept] - distances[nearest] < s.prox_nearest_hysteresis_m:
            nearest = kept
    work.nearest = nearest
    night = "Night · " if facts.night else ""
    if not facts.room_present and not facts.occupancy_unknown:
        why, rule = "Room just emptied · ambient until off", "occupied_ambient"
    elif nearest is not None and distances[nearest] < s.prox_far_m:
        why, rule = f"{night}Near {short_name(nearest)} · brighter", "proximity_ambient"
    elif distances:
        why, rule = f"{night}Room occupied · ambient by distance", "proximity_ambient"
    else:
        why, rule = f"{night}Room occupied · ambient", "occupied_ambient"
    work.mode = "night_ambient" if facts.night else "ambient"
    work.mode_rule, work.why = rule, why
    work.rule_whys[rule] = why


SENSOR_HOLD_WHY = "Occupancy sensor offline · holding"


def rule_degraded_ambient(facts: Facts, work: Work, ctx: Context) -> None:
    if work.halted:
        return
    if facts.degraded_why is not None:
        why = f"{facts.degraded_why} · ambient only"
        work.spot_block = ("degraded_ambient", why)
        work.rule_whys["degraded_ambient"] = why
        if work.mode != "off":
            work.mode, work.mode_rule, work.why = "degraded_ambient", "degraded_ambient", why
    if facts.occupancy_unknown:
        # Hold what was lit; a spot already on may stay (while its reading
        # preconditions hold) but no new spot starts without the sensor.
        work.rule_whys["degraded_ambient"] = SENSOR_HOLD_WHY
        work.mode, work.mode_rule, work.why = "degraded_ambient", "degraded_ambient", SENSOR_HOLD_WHY
        previous = ctx.previous.spot_fixture if ctx.previous else None
        if previous is None and work.spot_block is None:
            work.spot_block = ("degraded_ambient", SENSOR_HOLD_WHY)


def _spot_precondition(facts: Facts, ctx: Context) -> str | None:
    """First reading_spot_on precondition that fails, as a short why."""
    if not facts.room_present and not facts.occupancy_unknown:
        return "Room empty"
    if facts.people is not None and facts.people >= 2:
        return f"{facts.people} people"
    # v1.1: a fresh position near the sofa seat, or JEV's zone_hint=sofa.
    if not (facts.at_sofa or facts.activity_zone_hint == "sofa"):
        return "No one at sofa"
    if facts.people is None:
        return "People count unknown"
    if facts.activity_zone_hint == "desk":
        return "Activity at desk"
    if not facts.reading_ok:
        return facts.reading_why
    if not facts.pose_fresh or facts.reader is None:
        return facts.reader_why or "Pose stale"
    return None


def rule_reading_spot_on(facts: Facts, work: Work, ctx: Context) -> None:
    if work.halted or work.spot_block:
        return
    problem = _spot_precondition(facts, ctx)
    if problem:
        suffix = "ambient only" if problem.endswith("people") and work.mode != "off" else "no spotlight"
        work.spot_block = ("reading_spot_off", f"{problem} · {suffix}")
        return
    s, previous = ctx.settings, ctx.previous
    prev_spot = previous.spot_fixture if previous else None
    plan = plan_spot(facts.reader, ctx.geometry.fixtures, s, frozenset(work.paused),
                     prefer=prev_spot.fixture_id if prev_spot else None)
    work.spot_plan = plan
    if not plan.ok:
        reasons = plan.reason.split(":", 1)[1] if ":" in plan.reason else plan.reason
        detail = "beam through head" if "beam_through_head" in reasons else reasons.replace("_", " ")
        work.spot_block = ("reading_spot_off", f"No clear fixture ({detail}) · no spotlight")
        return
    target = plan.target_m
    if (prev_spot and prev_spot.fixture_id == plan.fixture_id and prev_spot.spot.target_m is not None
            and norm(sub(target, prev_spot.spot.target_m)) < s.spot_target_deadband_m):
        target = prev_spot.spot.target_m
    target = tuple(round(v, 3) for v in target)
    work.spot[plan.fixture_id] = Spot(True, s.spot_level, s.spot_cct_k, target, s.spot_fade_s)
    work.spot_rule[plan.fixture_id] = "reading_spot_on"
    work.rule_whys["reading_spot_on"] = f"Reading at sofa · spotlight on from {short_name(plan.fixture_id)}"
    if facts.occupancy_unknown:
        return  # keep the sensor-offline mode and why
    holding = facts.activity_label != "reading"
    why = "Reading at sofa · spotlight holding" if holding else "Reading at sofa · spotlight on"
    work.mode, work.mode_rule, work.why = "reading", "reading_spot_on", why


def rule_reading_spot_off(facts: Facts, work: Work, ctx: Context) -> None:
    if work.halted:
        return
    s = ctx.settings
    for fid, spot in work.spot.items():
        if spot is None:
            work.spot[fid] = _off_spot(s, s.spot_fade_s)
            work.spot_rule[fid] = work.spot_block[0] if work.spot_block else "reading_spot_off"
    if work.spot_block:
        rule, why = work.spot_block
        work.rule_whys.setdefault(rule, why)
        work.rule_whys.setdefault("reading_spot_off", why)
        if rule == "reading_spot_off" and why.endswith("ambient only") and work.mode in ("ambient", "night_ambient"):
            work.why = why


def rule_manual_pause(facts: Facts, work: Work, ctx: Context) -> None:
    for fid, override in sorted(ctx.overrides.items()):
        if fid not in work.ambient:
            continue
        if fid not in work.paused:
            continue
        held = override.held
        ambient = held.ambient if held else work.ambient[fid]
        spot = held.spot if held else work.spot[fid]
        work.ambient[fid], work.spot[fid] = ambient, spot
        work.ambient_rule[fid] = work.spot_rule[fid] = "manual_pause"
    if work.paused:
        names = ", ".join(short_name(fid) for fid in sorted(work.paused))
        work.rule_whys["manual_pause"] = f"Manual change in Home · {names} paused"


RULES: tuple[Rule, ...] = (
    Rule("room_off", "no occupancy for room_off_after_s (unknown occupancy: no person evidence for "
                     "unknown_occupancy_off_s)", rule_room_off),
    Rule("proximity_ambient", "room not off: level by distance to the nearest fresh position (no position: "
                              "occupied_level); night scales the level and lowers CCT", rule_proximity_ambient),
    Rule("degraded_ambient", "JEV or deep stack unhealthy or stale: ambient only; occupancy unknown "
                             "(Frigate down): hold", rule_degraded_ambient),
    Rule("reading_spot_on", "reading held >= reading_hold_on_s, sofa, one person, healthy, fresh pose", rule_reading_spot_on),
    Rule("reading_spot_off", "no reading for reading_off_after_s, or any precondition lost", rule_reading_spot_off),
    Rule("manual_pause", "override: fixture untouched until the room empties or pause_max_s", rule_manual_pause),
)


def _active_overrides(facts: Facts, overrides: dict, settings: Settings) -> tuple[dict, list]:
    active, ended = {}, []
    for fid, override in overrides.items():
        if facts.now >= override.until:
            ended.append((fid, f"{short_name(fid)} back to Auto · {_duration(settings.pause_max_s)} passed"))
        elif (facts.vacant_for >= settings.room_off_after_s and facts.vacant_since is not None
              and facts.vacant_since > override.at):
            ended.append((fid, f"{short_name(fid)} back to Auto · room empty"))
        else:
            active[fid] = override.until
    return active, ended


def evaluate(inputs: Inputs, overrides: dict, geometry: Geometry, settings: Settings, now: float,
             previous: Evaluation | None = None) -> Evaluation:
    facts = derive_facts(inputs, geometry, settings, now)
    ids = geometry.fixture_ids
    work = Work(ambient={fid: None for fid in ids}, spot={fid: None for fid in ids},
                ambient_rule={}, spot_rule={})
    ctx = Context(geometry, settings, overrides, previous)
    work.paused, work.ended = _active_overrides(facts, overrides, settings)
    for rule in RULES:
        rule.apply(facts, work, ctx)
    fixtures = []
    for fid in ids:
        paused_until = work.paused.get(fid)
        fixtures.append(FixtureOutput(
            fixture_id=fid, ambient=work.ambient[fid], spot=work.spot[fid],
            ambient_rule=work.ambient_rule.get(fid, "occupied_ambient"),
            spot_rule=work.spot_rule.get(fid, "reading_spot_off"),
            paused_until=paused_until,
            pause_reason=overrides[fid].reason if paused_until is not None else None))
    return Evaluation(at=now, mode=work.mode, mode_rule=work.mode_rule, why=work.why, fixtures=tuple(fixtures),
                      rule_whys=dict(work.rule_whys), facts=facts, spot_plan=work.spot_plan,
                      ended_overrides=tuple(work.ended), active_overrides=tuple(sorted(work.paused)),
                      nearest_fixture=work.nearest)


def held_output(evaluation: Evaluation | None, fixture_id: str) -> FixtureOutput | None:
    if evaluation is None:
        return None
    current = evaluation.fixture(fixture_id)
    return replace(current, paused_until=None, pause_reason=None) if current else None
