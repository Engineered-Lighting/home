"""Evening (story T) metrics for the Living Lights simulation.

`analyze_evening()` turns one simulated evening (the fake-light call list,
the ambient switch calls, the recorder, the per-minute snapshots and the
input timeline) into a JSON report under LL_SIM_REPORT/<label>/, the way
test_sim.py's `analyze()` does for nights. Every metric is computed by a
pure function over plain lists and dicts so it can be unit-tested without
Home Assistant (tests/living_lights/test_analyze_evening.py); only
`analyze_evening()` itself touches the harness objects.

Metric sources, stated once:

* watching episodes: `binary_sensor.living_lights_tv_watching` edges when a
  scenario fed that belief (initial state or timeline events); otherwise
  the TV on (state not in TV_OFF_STATES) AND the sofa raw occupancy on,
  both per the timeline (source "timeline"). `watching_source="timeline"`
  forces the timeline even when a belief was fed (T7: a stale belief must
  not create an artefact episode);
* turn_ons: `light.turn_on` calls that took a light from off to on with a
  brightness above 0 (brightness 0 is a turn-off), attributed through the
  recorder's context owner map;
* living_room_turn_ons_while_watching: those, for living-room lights,
  inside a watching episode;
* brighten_while_watching: any turn_on that raised a living-room light's
  brightness (levels tracked from the call list; a call without a
  brightness from off counts as a raise);
* time_to_dark_s: per episode, from the first instant tv_watching is on
  with the sofa occupied to the first instant every living-room light is
  off (0 when already dark, None when never);
* route: per errand (a non-living-room zone whose occupancy begins while
  watching or within the 30-min away hold after an episode),
  route_latency_s to the first turn_on that raises the zone light's
  tracked level or turns it on from off (a same-level re-set at the
  occupancy edge does not count), route_return_latency_s from vacancy to
  every zone light at or below its pre-errand level, route_off_latency_s
  from vacancy to every zone light off, and overshoot (its light above
  `input_number.living_lights_tv_route_pct`, 30 when the helper does not
  exist) within the errand plus 150 s;
* unavailable_flicker: turn_on calls within 120 s after `media_player.lg_tv`
  read `unavailable` while a watching episode was open, counting only the
  calls that changed the light's tracked level; unavailable_flicker_raw
  keeps every call (same-level tick re-sets included);
* call_digest / snapshot_digest: order-insensitive summaries of a call list
  (final non-None level per (instant, light), turn_off as 0) and of the
  per-minute snapshots, for comparing two runs whose same-instant dispatch
  order differs (the T7 toggle-on versus toggle-off gate);
* validity: `tv_measurable` (a recorded evening's validity block; synthetic
  scenarios are always measurable) decides whether a report may enter any
  score: `is_scorable(report)`;
* ambient_calls: switch.turn_on / turn_off counts (the ambient strips);
* max_lights_on_while_watching: over the per-minute snapshots inside
  episodes; lights_on_at: the last snapshot at or before each HH:MM;
* tv_playing_state_changes: recorder rows for
  `binary_sensor.living_lights_tv_playing`;
* attribution: turn_ons counted by owning automation or script.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import pathlib
from typing import Any, Callable, Iterable

TV_OFF_STATES = ("off", "standby", "unavailable", "unknown")
TV_WATCHING = "binary_sensor.living_lights_tv_watching"
TV_ENTITY = "media_player.lg_tv"
SOFA = "binary_sensor.sofa_person_occupancy"
ROUTE_HELPER = "input_number.living_lights_tv_route_pct"
DEFAULT_ROUTE_PCT = 30
LIVING_ROOM_LIGHTS = ("light.office", "light.front_left", "light.front_right", "light.rear_left",
                      "light.rear_right", "light.living_room_lights")
ZONE_LIGHTS = {   # from the pilots' "Actuates [...]" lines; zones without a pilot drive no light
    "dining_left": ["light.dining_table_left"], "dining_right": ["light.dining_table_right"],
    "front_door": ["light.front_right"], "front_left": ["light.front_left"],
    "island_left": ["light.island_left"], "island_right": ["light.island_right"],
    "office": ["light.office"], "sink": ["light.sink"],
    "sofa": ["light.front_left", "light.front_right", "light.rear_left", "light.rear_right"],
    "weights": ["light.front_right", "light.rear_right"],
}
LIVING_ROOM_ZONES = ("sofa", "front_left", "weights", "office", "front_door", "whole_living_room")
ERRAND_ZONES = tuple(z for z in ZONE_LIGHTS if z not in LIVING_ROOM_ZONES)
FLICKER_WINDOW_S = 120
ROUTE_SLACK_S = 150
ERRAND_HOLD_S = 1800     # an errand starts inside an episode or within this hold after it (the oracle's AWAY_HOLD)

LABEL = os.environ.get("LL_SIM_LABEL", "new")
REPORT_ROOT = pathlib.Path(os.environ.get(
    "LL_SIM_REPORT", str(pathlib.Path.home() / "vjepa-home" / "experiments" / "lighting-sim" / "reports")))

Interval = tuple[dt.datetime, dt.datetime]


# ----- time helpers -----
def parse_t(value: dt.datetime | str) -> dt.datetime:
    return value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(value)


def edges_of(initial: dict[str, str], events: Iterable[tuple], entity: str,
             start: dt.datetime) -> list[tuple[dt.datetime, str]]:
    """State edges of one entity: the initial value at `start` followed by the
    timeline events for it, sorted. `events` rows are (t, entity, state)."""
    out = [(start, initial.get(entity, "off"))]
    out += sorted((parse_t(t), st) for t, ent, st in events if ent == entity)
    return out


def on_intervals(edges: list[tuple[dt.datetime, str]], start: dt.datetime, end: dt.datetime,
                 on: Callable[[str], bool] = lambda s: s == "on") -> list[Interval]:
    """Intervals inside [start, end) where `on(state)` holds."""
    out: list[Interval] = []
    open_at: dt.datetime | None = None
    for t, st in sorted(edges, key=lambda e: e[0]):
        if on(st) and open_at is None:
            open_at = max(t, start)
        elif not on(st) and open_at is not None:
            if t > open_at:
                out.append((open_at, min(t, end)))
            open_at = None
    if open_at is not None and open_at < end:
        out.append((open_at, end))
    return [(a, b) for a, b in out if a < b]


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out = []
    for a0, a1 in a:
        for b0, b1 in b:
            lo, hi = max(a0, b0), min(a1, b1)
            if lo < hi:
                out.append((lo, hi))
    return sorted(out)


def in_intervals(t: dt.datetime, intervals: list[Interval]) -> bool:
    return any(a <= t < b for a, b in intervals)


def coalesce(intervals: list[Interval], gap_s: float) -> list[Interval]:
    """Merge intervals separated by less than `gap_s` seconds.

    A stable-occupancy sensor flickers: one visit to the kitchen can arrive as
    four intervals a few seconds apart, which turns one errand into four in
    every denominator that counts errands. Merging first means a 90 % bar is
    a statement about visits, not about sensor chatter. Shared with
    ``tools/tv-evening-postmortem.py`` so the two tools cannot drift.
    """
    out: list[Interval] = []
    for a, b in sorted(intervals):
        if out and (a - out[-1][1]).total_seconds() < gap_s:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
            continue
        out.append((a, b))
    return out


def errand_eligible(watching: list[Interval], hold_s: float = None) -> list[Interval]:
    """When an errand may open: inside a watching episode, or within the
    oracle's AWAY_HOLD after one ended.

    Someone who gets up as the credits roll is still on an errand from the
    film. This is the single definition of the errand denominator; the live
    post-mortem imports it rather than restating it, because the same evening
    scored by the two tools has to produce the same number.
    """
    hold = ERRAND_HOLD_S if hold_s is None else hold_s
    return coalesce([(a, b + dt.timedelta(seconds=hold)) for a, b in watching], 0)


def watching_intervals(initial: dict[str, str], events: Iterable[tuple], start: dt.datetime,
                       end: dt.datetime, source: str | None = None) -> tuple[list[Interval], str]:
    """Watching episodes and their source ("belief" or "timeline"). `source`
    None picks the belief when one was fed; "timeline" ignores the belief
    entity; "belief" requires it (empty episodes when it was not fed)."""
    if source not in (None, "belief", "timeline"):
        raise ValueError(f"watching source must be None, 'belief' or 'timeline', not {source!r}")
    events = list(events)
    fed = TV_WATCHING in initial or any(ent == TV_WATCHING for _, ent, _ in events)
    if source == "belief" or (source is None and fed):
        return on_intervals(edges_of(initial, events, TV_WATCHING, start), start, end), "belief"
    tv = on_intervals(edges_of(initial, events, TV_ENTITY, start), start, end,
                      on=lambda s: s not in TV_OFF_STATES)
    sofa = on_intervals(edges_of(initial, events, SOFA, start), start, end)
    return intersect(tv, sofa), "timeline"


# ----- call-list metrics -----
def is_turn_on(call: dict) -> bool:
    """An off-to-on light call with brightness above 0 (brightness 0 is off)."""
    return (call.get("service") == "turn_on" and call.get("from") == "off"
            and call.get("to", "on") == "on" and (call.get("brightness_pct") or 0) > 0)


def turn_ons(calls: list[dict], start: dt.datetime, end: dt.datetime,
             owner: Callable[[str | None, str | None], str | None] | None = None) -> list[dict]:
    out = []
    for c in calls:
        t = parse_t(c["t"])
        if start <= t < end and is_turn_on(c):
            out.append({"t": t.isoformat(), "light": c["entity"], "pct": c.get("brightness_pct"),
                        "by": owner(c.get("context_id"), c.get("context_parent")) if owner else None})
    return out


def living_room_turn_ons_while_watching(detail: list[dict], watching: list[Interval]) -> list[dict]:
    return [d for d in detail if d["light"] in LIVING_ROOM_LIGHTS and in_intervals(parse_t(d["t"]), watching)]


def light_levels(calls: list[dict], upto: dt.datetime | None = None) -> dict[str, int]:
    """Brightness per light implied by the call list (0 = off), applied in
    order up to and including `upto`."""
    levels: dict[str, int] = {}
    for c in calls:
        if upto is not None and parse_t(c["t"]) > upto:
            break
        levels[c["entity"]] = _level_after(c, levels.get(c["entity"], 0))
    return levels


def _level_after(call: dict, prev: int) -> int:
    if call.get("service") == "turn_off" or call.get("to") == "off":
        return 0
    pct = call.get("brightness_pct")
    if pct is None:
        return prev if prev > 0 else 1    # turn_on without a level: on at an unknown level
    return int(pct)


def brighten_while_watching(calls: list[dict], watching: list[Interval],
                            lights: Iterable[str] = LIVING_ROOM_LIGHTS) -> list[dict]:
    """turn_on calls that raised a living-room light's level inside an episode."""
    lights = set(lights)
    levels: dict[str, int] = {}
    out = []
    for c in calls:
        prev = levels.get(c["entity"], 0)
        new = _level_after(c, prev)
        levels[c["entity"]] = new
        t = parse_t(c["t"])
        if (c.get("service") == "turn_on" and c["entity"] in lights and new > prev
                and in_intervals(t, watching)):
            out.append({"t": t.isoformat(), "light": c["entity"], "from_pct": prev, "to_pct": new,
                        "context_id": c.get("context_id"), "context_parent": c.get("context_parent")})
    return out


def settled_after(calls: list[dict], t0: dt.datetime, lights: Iterable[str], end: dt.datetime,
                  ceiling: dict[str, int] | None = None) -> float | None:
    """Seconds from t0 until every light in `lights` is at or below its
    `ceiling` level (0 when absent, so the default is "off"): 0 when already
    there at t0 (calls at t0 included), None when never before `end`."""
    lights = set(lights)
    ceiling = ceiling or {}
    levels = {k: v for k, v in light_levels(calls, upto=t0).items() if k in lights}

    def settled() -> bool:
        return all(v <= ceiling.get(k, 0) for k, v in levels.items())

    if settled():
        return 0.0
    for c in calls:
        t = parse_t(c["t"])
        if t <= t0 or c["entity"] not in lights:
            continue
        if t >= end:
            break
        levels[c["entity"]] = _level_after(c, levels.get(c["entity"], 0))
        if settled():
            return (t - t0).total_seconds()
    return None


def dark_after(calls: list[dict], t0: dt.datetime, lights: Iterable[str], end: dt.datetime) -> float | None:
    """Seconds from t0 until every light in `lights` is off (0 when already
    dark at t0, None when never before `end`)."""
    return settled_after(calls, t0, lights, end)


def level_changes(calls: list[dict], lights: Iterable[str] | None = None) -> list[tuple[dt.datetime, dict, int, int]]:
    """(t, call, level before, level after) for every call, levels tracked
    from the start of the call list; restricted to `lights` when given."""
    keep = set(lights) if lights is not None else None
    levels: dict[str, int] = {}
    out = []
    for c in calls:
        prev = levels.get(c["entity"], 0)
        new = _level_after(c, prev)
        levels[c["entity"]] = new
        if keep is None or c["entity"] in keep:
            out.append((parse_t(c["t"]), c, prev, new))
    return out


def time_to_dark(calls: list[dict], watching: list[Interval], sofa: list[Interval],
                 end: dt.datetime) -> list[dict]:
    """Per watching episode: time from tv_watching on with the sofa occupied to
    every living-room light off."""
    out = []
    for a, b in watching:
        starts = intersect([(a, b)], sofa)
        if not starts:
            out.append({"episode": [a.isoformat(), b.isoformat()], "sofa_from": None, "time_to_dark_s": None})
            continue
        t0 = starts[0][0]
        out.append({"episode": [a.isoformat(), b.isoformat()], "sofa_from": t0.isoformat(),
                    "time_to_dark_s": dark_after(calls, t0, LIVING_ROOM_LIGHTS, end)})
    return out


def zone_intervals(initial: dict[str, str], events: Iterable[tuple], zone: str,
                   start: dt.datetime, end: dt.datetime) -> list[Interval]:
    return on_intervals(edges_of(initial, events, f"binary_sensor.{zone}_person_occupancy", start), start, end)


def route_metrics(calls: list[dict], initial: dict[str, str], events: Iterable[tuple],
                  watching: list[Interval], start: dt.datetime, end: dt.datetime,
                  route_pct: int = DEFAULT_ROUTE_PCT, hold_s: int = ERRAND_HOLD_S) -> list[dict]:
    """One row per errand: an errand zone's occupancy interval that begins
    inside a watching episode or within `hold_s` after one ended (a
    timeline-derived episode ends the moment the sofa empties).

    Occupancy intervals in the same zone closer together than the turn-off bar
    are one errand, not several: a stable-occupancy sensor that flickers must
    not multiply the denominator. ``errand_eligible`` and ``coalesce`` are
    shared with ``tools/tv-evening-postmortem.py``, which scores the real
    house, so the two cannot drift apart on what an errand is.
    """
    events = list(events)
    held = errand_eligible(watching, hold_s)
    out = []
    for zone in ERRAND_ZONES:
        lights = set(ZONE_LIGHTS[zone])
        changes = level_changes(calls, lights)
        for a, b in coalesce(zone_intervals(initial, events, zone, start, end), ROUTE_SLACK_S):
            if not in_intervals(a, held):
                continue
            # The first turn_on at or after the occupancy edge that raised the
            # zone light's tracked level (from off counts; a same-level re-set
            # does not).
            first_on = next((t for t, c, prev, new in changes
                             if t >= a and c.get("service") == "turn_on" and new > prev), None)
            # Levels strictly before the errand: the pre-errand floor.
            before = {k: v for k, v in light_levels(calls, upto=a - dt.timedelta(microseconds=1)).items()
                      if k in lights}
            slack_end = min(b + dt.timedelta(seconds=ROUTE_SLACK_S), end)
            overshoot = [{"t": c["t"], "light": c["entity"], "pct": c.get("brightness_pct")} for c in calls
                         if c["entity"] in lights and c.get("service") == "turn_on"
                         and a <= parse_t(c["t"]) < slack_end and (c.get("brightness_pct") or 0) > route_pct]
            out.append({"zone": zone, "from": a.isoformat(), "to": b.isoformat(),
                        "route_latency_s": (first_on - a).total_seconds() if first_on else None,
                        "pre_errand_levels": before,
                        "route_return_latency_s": settled_after(calls, b, lights, end, ceiling=before),
                        "route_off_latency_s": dark_after(calls, b, lights, end),
                        "overshoot": overshoot, "route_pct": route_pct})
    return out


def unavailable_flicker(calls: list[dict], initial: dict[str, str], events: Iterable[tuple],
                        watching: list[Interval], start: dt.datetime, end: dt.datetime,
                        window_s: int = FLICKER_WINDOW_S) -> list[dict]:
    """turn_on calls within `window_s` after the TV read unavailable while an
    episode was open (the belief or the timeline kept watching on).
    `turn_ons` holds only the calls that changed the light's tracked level;
    `turn_ons_raw` counts every call (same-level tick re-sets included)."""
    out = []
    changes = level_changes(calls)
    for t, st in edges_of(initial, list(events), TV_ENTITY, start)[1:]:
        if st != "unavailable" or not in_intervals(t, watching):
            continue
        raw = [(c, prev, new) for ct, c, prev, new in changes
               if c.get("service") == "turn_on" and c.get("to", "on") == "on"
               and t <= ct < t + dt.timedelta(seconds=window_s)]
        hits = [{"t": c["t"], "light": c["entity"], "pct": c.get("brightness_pct"), "from_pct": prev, "to_pct": new}
                for c, prev, new in raw if new != prev]
        out.append({"unavailable_at": t.isoformat(), "turn_ons": hits, "turn_ons_raw": len(raw)})
    return out


def ambient_calls(switch_calls: list[dict], start: dt.datetime, end: dt.datetime) -> dict[str, int]:
    counts = collections.Counter(c["service"] for c in switch_calls if start <= parse_t(c["t"]) < end)
    return {"on": counts.get("turn_on", 0), "off": counts.get("turn_off", 0)}


def max_lights_on_while_watching(snapshots: list[dict], watching: list[Interval]) -> int:
    best = 0
    for s in snapshots:
        if in_intervals(parse_t(s["t"]), watching):
            best = max(best, len(s.get("lights_on") or {}))
    return best


def lights_on_at(snapshots: list[dict], target: dt.datetime) -> dict:
    best = None
    for s in snapshots:
        if parse_t(s["t"]) <= target:
            best = s
    return dict(best["lights_on"]) if best else {}


def state_changes(changes: list[dict], entity: str, start: dt.datetime | None = None,
                  end: dt.datetime | None = None) -> list[dict]:
    out = []
    for c in changes:
        if c["entity"] != entity:
            continue
        t = parse_t(c["t"])
        if (start is None or t >= start) and (end is None or t < end):
            out.append(c)
    return out


def attribution(detail: list[dict]) -> dict[str, int]:
    return dict(collections.Counter(d.get("by") or "unknown" for d in detail))


# ----- run comparison -----
def call_digest(calls: list[dict]) -> list[list]:
    """Summary of a light call list that same-instant dispatch order across
    lights (which the simulator does not keep stable between runs) cannot
    change: one [instant, light, level] per (instant, light), the level
    being the last non-None brightness_pct among that instant's calls for
    the light (turn_off is 0; None when every call there was a bare
    turn_on). A real difference (an extra call, a light ending an instant at
    another level) does change it."""
    final: dict[tuple[str, str], int | None] = {}
    for c in calls:
        key = (parse_t(c["t"]).isoformat(), c["entity"])
        level = 0 if c.get("service") == "turn_off" else c.get("brightness_pct")
        if level is not None:
            level = int(level)
        if key not in final or level is not None:
            final[key] = level
    return [[t, light, level] for (t, light), level in sorted(final.items())]


def snapshot_digest(snapshots: list[dict]) -> list[list]:
    """[t, lights_on sorted as [light, pct] pairs, switches_on] per snapshot."""
    out = []
    for s in snapshots:
        lit = s.get("lights_on") or {}
        out.append([parse_t(s["t"]).isoformat(), [[k, int(v)] for k, v in sorted(lit.items())],
                    sorted(s.get("switches_on") or [])])
    return out


def digest_diff(a: list[list], b: list[list], limit: int = 20) -> list[dict]:
    """Rows present in only one of two digests (call or snapshot), at most
    `limit`, each tagged with the side it came from."""
    key = lambda row: json.dumps(row, sort_keys=True, default=str)
    sa, sb = {key(r): r for r in a}, {key(r): r for r in b}
    out = [{"only_in": "a", "row": r} for k, r in sa.items() if k not in sb]
    out += [{"only_in": "b", "row": r} for k, r in sb.items() if k not in sa]
    return out[:limit]


# ----- service-call targets -----
def target_entities(data: dict) -> list[str]:
    """The entity ids a service call addresses: `entity_id` as a string or a
    list, at the top level or under `target` (how Home Assistant delivers a
    `target: entity_id:` block); empty when the call names none."""
    raw = data.get("entity_id")
    if raw is None:
        raw = (data.get("target") or {}).get("entity_id")
    if raw is None:
        return []
    return [raw] if isinstance(raw, str) else [str(e) for e in raw]


# ----- validity -----
SYNTHETIC_VALIDITY = {"tv_measurable": True, "tv_source": "synthetic", "notes": []}


def validity_of(doc: dict | None) -> dict:
    """The validity block a report carries: a recorded evening's own
    (schema living-lights-sim-evening/v1; missing keys default to not
    measurable), or SYNTHETIC_VALIDITY for a scripted scenario."""
    if doc is None:
        return dict(SYNTHETIC_VALIDITY)
    v = doc.get("validity") or {}
    return {"tv_measurable": bool(v.get("tv_measurable", False)),
            "tv_source": v.get("tv_source", "none"),
            "notes": list(v.get("notes") or [])}


def is_scorable(report: dict) -> bool:
    """Whether a report may enter a score: its validity says the TV was
    measurable. A report without a validity block is not scorable."""
    return bool((report.get("validity") or {}).get("tv_measurable", False))


def at_hhmm(day: dt.date, hhmm: str, tz) -> dt.datetime:
    parts = [int(x) for x in hhmm.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return dt.datetime.combine(day, dt.time(*parts), tzinfo=tz)


def default_window(start: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """18:00 on the timeline's first calendar day to 01:00 the next day."""
    day = start.date()
    return at_hhmm(day, "18:00", start.tzinfo), at_hhmm(day + dt.timedelta(days=1), "01:00", start.tzinfo)


def analyze_calls(*, calls: list[dict], switch_calls: list[dict], changes: list[dict], snapshots: list[dict],
                  initial: dict[str, str], events: Iterable[tuple], window: tuple[dt.datetime, dt.datetime],
                  owner=None, route_pct: int = DEFAULT_ROUTE_PCT,
                  hours: Iterable[str] = ("20:00", "22:00"), watching_source: str | None = None,
                  validity: dict | None = None) -> dict[str, Any]:
    """Every metric from plain data (no Home Assistant). `watching_source`
    forces the episode source ("timeline" or "belief"); `validity` is the
    evening's validity block (synthetic when None)."""
    start, end = window
    events = list(events)
    watching, source = watching_intervals(initial, events, start, end, watching_source)
    sofa = on_intervals(edges_of(initial, events, SOFA, start), start, end)
    detail = turn_ons(calls, start, end, owner)
    lr_watch = living_room_turn_ons_while_watching(detail, watching)
    routes = route_metrics(calls, initial, events, watching, start, end, route_pct)
    flicker = unavailable_flicker(calls, initial, events, watching, start, end)
    ttd = time_to_dark(calls, watching, sofa, end)
    first_ttd = next((row["time_to_dark_s"] for row in ttd if row["sofa_from"] is not None), None)
    tv_playing = state_changes(changes, "binary_sensor.living_lights_tv_playing", start, end)
    lit_at = {}
    for hhmm in hours:
        target = at_hhmm(start.date(), hhmm, start.tzinfo)
        if target < start:
            target += dt.timedelta(days=1)
        lit_at[hhmm.replace(":", "")] = lights_on_at(snapshots, target)
    validity = dict(SYNTHETIC_VALIDITY) if validity is None else validity
    return {
        "window": [start.isoformat(), end.isoformat()],
        "validity": validity,
        "scorable": bool(validity.get("tv_measurable", False)),
        "watching_source": source,
        "watching_episodes": [[a.isoformat(), b.isoformat()] for a, b in watching],
        "watching_minutes": round(sum((b - a).total_seconds() for a, b in watching) / 60, 1),
        "turn_ons": len(detail),
        "turn_ons_detail": detail,
        "turn_ons_by_automation": attribution(detail),
        "living_room_turn_ons_while_watching": len(lr_watch),
        "living_room_turn_ons_while_watching_detail": lr_watch,
        "living_room_turn_ons_while_watching_by_automation": attribution(lr_watch),
        "brighten_while_watching": brighten_while_watching(calls, watching),
        "time_to_dark_s": first_ttd,
        "time_to_dark_detail": ttd,
        "route_pct": route_pct,
        "route_errands": routes,
        "route_overshoot": sum(len(r["overshoot"]) for r in routes),
        "route_latency_s": [r["route_latency_s"] for r in routes],
        "route_return_latency_s": [r["route_return_latency_s"] for r in routes],
        "route_off_latency_s": [r["route_off_latency_s"] for r in routes],
        "unavailable_flicker": sum(len(f["turn_ons"]) for f in flicker),
        "unavailable_flicker_raw": sum(f["turn_ons_raw"] for f in flicker),
        "unavailable_flicker_detail": flicker,
        "call_digest": call_digest([c for c in calls if start <= parse_t(c["t"]) < end]),
        "snapshot_digest": snapshot_digest([s for s in snapshots if start <= parse_t(s["t"]) < end]),
        "ambient_calls": ambient_calls(switch_calls, start, end),
        "max_lights_on_while_watching": max_lights_on_while_watching(snapshots, watching),
        "lights_on_at": lit_at,
        "tv_playing_state_changes": len(tv_playing),
        "tv_playing_state_changes_detail": tv_playing,
        "light_calls_total": len(calls),
        "switch_calls_total": len(switch_calls),
    }


def route_pct_from_hass(hass) -> int:
    st = hass.states.get(ROUTE_HELPER) if hass is not None else None
    if st is None or st.state in ("unknown", "unavailable", ""):
        return DEFAULT_ROUTE_PCT
    try:
        return int(float(st.state))
    except ValueError:
        return DEFAULT_ROUTE_PCT


def analyze_evening(name: str, holder, timeline, snapshots: list[dict], *,
                    window: tuple[dt.datetime, dt.datetime] | None = None,
                    extra: dict | None = None, hours: Iterable[str] = ("20:00", "22:00"),
                    label: str = LABEL, report_root: pathlib.Path = REPORT_ROOT,
                    packages: str | None = None, step_s: int | None = None,
                    watching_source: str | None = None, validity: dict | None = None) -> dict:
    """Compute the evening metrics for a finished simulation and write
    REPORT_ROOT/<label>/<name>.json. `holder` is the sim fixture's holder
    (.hass, .lights with .switches, .recorder); `timeline` the harness
    Timeline that was run; `watching_source` and `validity` as in
    analyze_calls()."""
    if window is None:
        window = default_window(timeline.start)
    result = {"label": label, "scenario": name, "packages": packages, "step_s": step_s,
              "sim_window": [timeline.start.isoformat(), timeline.end.isoformat()]}
    result.update(analyze_calls(
        calls=holder.lights.calls, switch_calls=getattr(holder.lights, "switches").calls,
        changes=holder.recorder.changes, snapshots=snapshots,
        initial=timeline.initial, events=timeline.events, window=window,
        owner=holder.recorder.owner_of, route_pct=route_pct_from_hass(holder.hass), hours=hours,
        watching_source=watching_source, validity=validity))
    result["classifier_changes"] = len(holder.recorder.classifier)
    if extra:
        result.update(extra)
    out = report_root / label
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(result, indent=1, default=str) + "\n")
    return result
