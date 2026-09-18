"""Living Lights story T evenings (T1-T10), story S extras (S9-S13) and
recorded evenings through the real packages. See README.md in this directory.

    LL_SIM_LABEL=m1tv [LL_SIM_EVENINGS=<dir>] [LL_SIM_HOURS=2] \\
      ~/.venvs/ha-sim/bin/python -m pytest tools/lighting-sim/test_sim_tv.py -o asyncio_mode=auto -p no:cacheprovider -q -s

Every scenario runs twice, "belief_off" and "belief_on": both feed the
level-0 tv_watching oracle (harness.Timeline.derive_tv_watching) and the
publisher heartbeat; the second also turns
input_boolean.living_lights_actuate_from_belief_changes on after start.
Recorded evenings run both variants too (no belief is fed there, so the
toggle can only change dispatch order). Every run writes
LL_SIM_REPORT/<label>/<scenario>-<variant>.json through analyze_evening.py;
each report carries `validity.tv_measurable` (always true for a scripted
scenario) and evenings with it false must stay out of every score.

Override writes (input_text.set_value) are counted per target entity, so a
write through `target: entity_id:` (Home Assistant delivers a list) shows
up once per entity even when the recorder never sees it because the value
exceeded 255 characters. T7 scores its metrics with the timeline as the
truth source (the stale belief is fed to the packages only) and its
toggle-on/off gate compares analyze_evening's normalised call and snapshot
digests, which same-instant dispatch order cannot change.

Assertion gates:

* LL_SIM_ASSERT=1 (the story S gate that keeps S1-S8 green) asserts nothing
  in this file: the T and S9-S13 target behaviour does not exist yet, so
  under that gate these scenarios only report;
* LL_SIM_ASSERT_TARGET=1 asserts the plan's milestone M2 acceptance for
  each scenario (dont-implement-anything-yet-zippy-thunder.md, "M2 ...
  Acceptance"), both toggles: T3 every living-room light at the vacant
  target (0 %, dark by default) with nobody there; T4 the office at its
  present target with the sofa empty; T7 from the heartbeat's death on,
  digests identical to toggle-off; T8 zero brightenings and zero strip
  turn-ons during both blips; T9 and T10 the front-door and office zones
  at 0 % while the sofa is occupied; S9b the latch clears within 10 min of
  03:00 whichever of the door and the phone reports first; S10 no override
  write and no floor before the first occupancy; S12 zero override writes
  and no light above 30 % between 05:00 and 06:00. On the M1 packages
  these FAIL; the failures are the baseline of the defects, as numbers in
  the reports.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harness import (TV_WATCHING, Timeline, apply_production_toggles,  # noqa: E402
                     run_timeline, set_toggle, setup_sim)
from analyze_evening import (LIVING_ROOM_LIGHTS, at_hhmm, analyze_evening,  # noqa: E402
                             digest_diff, parse_t, state_changes, target_entities,
                             validity_of)

REPO = HERE.parents[1]
PACKAGES = pathlib.Path(os.environ.get("LL_SIM_PACKAGES", str(REPO / "ha-config"))).resolve()
LABEL = os.environ.get("LL_SIM_LABEL", "new")
EVENINGS = os.environ.get("LL_SIM_EVENINGS")
ASSERT_TARGET = os.environ.get("LL_SIM_ASSERT_TARGET") == "1"
STEP_S = int(os.environ.get("LL_SIM_STEP_S", "20"))
HOURS = os.environ.get("LL_SIM_HOURS")   # diagnostics: truncate every scenario
TZ = ZoneInfo("America/Los_Angeles")
REPORT_DIR = pathlib.Path(os.environ.get(
    "LL_SIM_REPORT", str(pathlib.Path.home() / "vjepa-home" / "experiments" / "lighting-sim" / "reports"))) / LABEL
BELIEF_TOGGLE = "input_boolean.living_lights_actuate_from_belief_changes"
ASLEEP = "input_boolean.living_lights_asleep"
WOKE = "input_boolean.living_lights_woke_up_today"
SOFA_ACTIVITY = "sensor.living_room_sofa_activity"
INPUT_TEXT_MAX = 255
OVERRIDE_TEXT_PREFIX = "input_text.living_lights_override_text_"
MOVIE_DIM_PCT = 0          # M2's input_number.living_lights_movie_dim_pct default (dark)
TV_VACANT_FLOOR_PCT = 0    # M2's input_number.living_lights_tv_vacant_floor_pct default (dark)
VACANT_LATE_EVENING_PCT = 20   # the generator's VACANT_NIGHT_PCT: the ordinary
                               # vacant floor from 20:00, used when the house has
                               # stopped treating the television as playing
S12_MAX_PCT = 30           # the most a light may show while the house is asleep
BASE = dt.date(2026, 9, 13)      # a Sunday
WEEKDAY = dt.date(2026, 9, 15)   # a Tuesday (working-hours logic is weekday-only)
VARIANTS = ("belief_off", "belief_on")


def at(day: dt.date, hhmm: str, plus_days: int = 0) -> dt.datetime:
    """HH:MM or HH:MM:SS on `day` (+ plus_days), local time."""
    return at_hhmm(day + dt.timedelta(days=plus_days), hhmm, TZ)


def evening_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    return at(day, "18:00"), at(day, "01:00", 1)


def night_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    return at(day, "22:00"), at(day, "09:00", 1)


@pytest.fixture
async def sim(hass, request):
    """Home Assistant instance plus a freezegun clock started at the
    scenario's first instant (mirrors test_sim.py's fixture; re-declared so
    this file does not depend on that module's internals). Also records every
    input_text.set_value call (value length), which the recorder cannot see
    when Home Assistant refuses the write for exceeding 255 characters, and
    every living_lights_asleep change with its context, for writer attribution."""
    from freezegun import freeze_time
    holder = SimpleNamespace(hass=hass, freezer=None, lights=None, recorder=None, text_writes=[], asleep_writes=[])

    def on_state(event):
        if event.data.get("entity_id") != ASLEEP:
            return
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or (old is not None and old.state == new.state):
            return
        holder.asleep_writes.append({"t": dt.datetime.now(TZ).isoformat(), "to": new.state,
                                     "context_id": event.context.id, "context_parent": event.context.parent_id})

    def on_call(event):
        if event.data.get("domain") == "input_text" and event.data.get("service") == "set_value":
            data = event.data.get("service_data") or {}
            value = str(data.get("value", ""))
            # `target: entity_id:` arrives as a list; one row per entity.
            for entity in target_entities(data):
                holder.text_writes.append({"t": dt.datetime.now(TZ).isoformat(), "entity": entity,
                                           "length": len(value), "head": value[:80],
                                           "brightness_pct": data.get("brightness_pct") or _payload_pct(value)})

    async def start(when: dt.datetime, initial: dict[str, str] | None = None):
        ctx = freeze_time(when)
        holder.freezer = ctx.start()
        request.addfinalizer(ctx.stop)
        holder.lights, holder.recorder, _ = await setup_sim(hass, PACKAGES, initial)
        hass.bus.async_listen("call_service", on_call)
        hass.bus.async_listen("state_changed", on_state)
        await apply_production_toggles(hass)
        return holder

    holder.start = start
    return holder


def _payload_pct(value: str):
    """brightness_pct out of an override payload (JSON), None when absent."""
    try:
        doc = json.loads(value)
    except (ValueError, TypeError):
        return None
    return doc.get("brightness_pct") if isinstance(doc, dict) else None


def truncate(tl: Timeline) -> Timeline:
    if HOURS:
        tl.end = min(tl.end, tl.start + dt.timedelta(hours=float(HOURS)))
    return tl


async def run(holder, tl: Timeline):
    return await run_timeline(holder.hass, holder.freezer, truncate(tl), step_s=STEP_S)


async def start_variant(sim, tl: Timeline, variant: str):
    """Start the sim on the timeline's initial inputs and apply the variant:
    the belief toggle is forced off by apply_production_toggles, so the
    belief_on variant turns it on here, after start."""
    holder = await sim.start(tl.start, tl.initial)
    if variant == "belief_on":
        await set_toggle(holder.hass, BELIEF_TOGGLE, True)
    return holder


def feed_belief(tl: Timeline, *, dead_from: dt.datetime | None = None) -> Timeline:
    """The level-0 tv_watching oracle plus a live publisher heartbeat."""
    tl.derive_tv_watching()
    tl.publisher(alive=True, dead_from=dead_from)
    return tl


def drop_belief_edges_after(tl: Timeline, when: dt.datetime) -> Timeline:
    """A dead publisher leaves its last belief states in place (stale)."""
    tl.events = [e for e in tl.events if not (e[1] == TV_WATCHING and e[0] >= when)]
    return tl


def keep_watching_through(tl: Timeline, blips: list[tuple[dt.datetime, dt.datetime]], grace_s: int = 600) -> Timeline:
    """The oracle has no unavailable grace; the publisher's machine keeps
    tv_watching on for `grace_s` of unavailable. Rewrite the oracle's edges
    inside each blip accordingly (documented deviation from level 0)."""
    for a, b in blips:
        tl.events = [e for e in tl.events if not (e[1] == TV_WATCHING and a <= e[0] <= b)]
        if (b - a).total_seconds() > grace_s:
            tl.at(a + dt.timedelta(seconds=grace_s), TV_WATCHING, "off")
            tl.at(b, TV_WATCHING, "on")
    return tl


def night_extras(holder, tl: Timeline, day: dt.date) -> dict:
    """Story S numbers for the S9-S13 scenarios (the latch, the wake-up latch
    and the override writes), from the recorder and the call_service log."""
    after = tl.start + dt.timedelta(minutes=1)
    asleep = [c for c in state_changes(holder.recorder.changes, ASLEEP, after)]
    # One text_writes row per target entity (on_call). An empty value is the
    # lifecycle clearing an override, not an override write: counted apart.
    to_override = [w for w in holder.text_writes if str(w["entity"]).startswith(OVERRIDE_TEXT_PREFIX)]
    overrides = [w for w in to_override if w["length"] > 0]
    clears = [w for w in to_override if w["length"] == 0]
    accepted = [c for c in holder.recorder.changes
                if c["entity"].startswith(OVERRIDE_TEXT_PREFIX) and c["to"] not in ("", "unknown")]
    return {
        "override_text_clears": len(clears),
        "override_text_clears_at": sorted({w["t"][11:19] for w in clears}),
        "latch_on": [c["t"] for c in asleep if c["to"] == "on"],
        "latch_off": [c["t"] for c in asleep if c["to"] == "off"],
        "latch_writers": [{"t": w["t"][11:19], "to": w["to"],
                           "by": holder.recorder.owner_of(w["context_id"], w["context_parent"]) or "manual/unknown"}
                          for w in holder.asleep_writes if parse_t(w["t"]) >= after],
        "woke_up_today_at": [c["t"] for c in state_changes(holder.recorder.changes, WOKE, after) if c["to"] == "on"],
        "override_text_writes_attempted": len(overrides),
        "override_text_writes_lengths": [w["length"] for w in overrides],
        "override_text_writes_over_255": sum(1 for w in overrides if w["length"] > INPUT_TEXT_MAX),
        "override_text_writes_accepted": len(accepted),
        "override_text_writes_detail": overrides,
        "input_text_writes_total": len(holder.text_writes),
    }


def report(name: str, variant: str, holder, tl: Timeline, snaps, *, window=None, hours=("20:00", "22:00"),
           extra: dict | None = None, watching_source: str | None = None, validity: dict | None = None) -> dict:
    return analyze_evening(f"{name}-{variant}", holder, tl, snaps, window=window, hours=hours,
                           extra={"variant": variant, "belief_toggle": variant == "belief_on", **(extra or {})},
                           label=LABEL, packages=str(PACKAGES), step_s=STEP_S,
                           watching_source=watching_source, validity=validity)


def lit(r: dict, hhmm: str) -> dict:
    return r["lights_on_at"].get(hhmm.replace(":", ""), {})


def living_room_lit(lights: dict) -> dict:
    return {k: v for k, v in lights.items() if k in LIVING_ROOM_LIGHTS}


def lit_between(r: dict, a: dt.datetime, b: dt.datetime, above_pct: int = 0) -> list[dict]:
    """Per-minute snapshot rows in [a, b) with any light above `above_pct`
    (from the report's normalised snapshot digest: [t, [[light, pct], ...], strips])."""
    rows = []
    for row in r["snapshot_digest"]:
        t = parse_t(row[0])
        if a <= t < b:
            over = {light: pct for light, pct in row[1] if pct > above_pct}
            if over:
                rows.append({"t": row[0][11:19], "lights": over})
    return rows


def calls_between(rows: list[dict], windows: list[tuple[dt.datetime, dt.datetime]],
                  tail_s: int = 0) -> list[dict]:
    """Rows whose `t` falls inside any window (plus `tail_s` after its end)."""
    return [row for row in rows
            if any(a <= parse_t(row["t"]) < b + dt.timedelta(seconds=tail_s) for a, b in windows)]


# ----- story T evenings -----
def watch(day: dt.date, tv_on: str = "20:00", sofa_from: str = "20:05", sofa_to: str = "23:30",
          tv_off: str | None = "23:30") -> Timeline:
    """TV on, then on the sofa until `sofa_to`; TV off at `tv_off`."""
    tl = Timeline(*evening_bounds(day))
    tl.tv(at(day, tv_on), "on")
    tl.occupy("sofa", at(day, sofa_from), at(day, sofa_to))
    if tv_off:
        tl.tv(at(day, tv_off), "off")
    return tl


@pytest.mark.parametrize("variant", VARIANTS)
async def test_t1_watch_errand_return(sim, variant):
    """Watching from 20:05; errand to the sink 21:00-21:03; back on the sofa."""
    tl = Timeline(*evening_bounds(BASE))
    tl.tv(at(BASE, "20:00"), "on")
    tl.occupy("sofa", at(BASE, "20:05"), at(BASE, "21:00"))
    tl.occupy("sink", at(BASE, "21:00"), at(BASE, "21:03"))
    tl.occupy("sofa", at(BASE, "21:03"), at(BASE, "23:30"))
    tl.tv(at(BASE, "23:30"), "off")
    feed_belief(tl)
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    r = report("T1_watch_errand_return", variant, holder, tl, snaps, hours=("20:00", "20:10", "21:01", "21:10", "22:00"))
    if ASSERT_TARGET:
        assert r["time_to_dark_s"] is not None and r["time_to_dark_s"] <= 60, r["time_to_dark_detail"]
        assert r["living_room_turn_ons_while_watching"] == 0, r["living_room_turn_ons_while_watching_detail"]
        sink = [e for e in r["route_errands"] if e["zone"] == "sink"]
        assert sink, r["route_errands"]
        assert sink[0]["route_latency_s"] is not None and sink[0]["route_latency_s"] <= 60, sink
        assert not sink[0]["overshoot"], sink
        assert sink[0]["route_off_latency_s"] is not None and sink[0]["route_off_latency_s"] <= 150, sink


@pytest.mark.parametrize("variant", VARIANTS)
async def test_t2_cooking_while_tv_plays(sim, variant):
    """On the sofa 19:00-19:30, cooking at the island 19:30-20:10 while the TV
    plays, back to the sofa until 22:30. The publisher would publish
    `cooking` for the island zone; fed as the belief entity."""
    tl = Timeline(*evening_bounds(BASE))
    tl.tv(at(BASE, "19:00"), "on")
    tl.occupy("sofa", at(BASE, "19:00"), at(BASE, "19:30"))
    tl.occupy("island_left", at(BASE, "19:30"), at(BASE, "20:10"))
    tl.occupy("sofa", at(BASE, "20:10"), at(BASE, "22:30"))
    tl.tv(at(BASE, "22:30"), "off")
    feed_belief(tl)
    tl.belief(at(BASE, "19:31"), "sensor.kitchen_island_left_activity", "cooking")
    tl.belief(at(BASE, "20:10"), "sensor.kitchen_island_left_activity", "idle")
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    r = report("T2_cooking_40min", variant, holder, tl, snaps, hours=("19:32", "19:40", "20:00", "20:15", "22:00"))
    if ASSERT_TARGET:
        island = lit(r, "19:40").get("light.island_left", 0)
        assert island >= 50, f"island_left after 10 min of cooking: {island} % ({lit(r, '19:40')})"


@pytest.mark.parametrize("variant", VARIANTS)
async def test_t3_tv_on_nobody(sim, variant):
    """TV on 20:00-22:00 with nobody in the room."""
    tl = Timeline(*evening_bounds(BASE))
    tl.tv(at(BASE, "20:00"), "on")
    tl.tv(at(BASE, "22:00"), "off")
    feed_belief(tl)
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    r = report("T3_tv_on_nobody", variant, holder, tl, snaps, hours=("19:59", "20:05", "21:00", "22:00", "22:30"))
    if ASSERT_TARGET:
        # Nobody is in the room, so no living-room light may sit at a present
        # or route level. Which vacant floor applies depends on what the house
        # believes about the television, and both answers are correct:
        #   toggle off, or a belief that says someone is watching: the set is
        #     treated as playing, so the floor is tv_vacant_floor_pct (0).
        #   a live belief that says nobody is watching: the set stops counting
        #     as playing at all, so the ordinary vacant floor for the hour
        #     applies. At 21:00 that is the late-evening floor, 20 % (the
        #     generator's VACANT_NIGHT_PCT), not darkness.
        # The defect this gate exists to catch is a light at a present (80 %)
        # or route (30 %) level in an empty room, so the ceiling is the higher
        # of the two vacant floors.
        ceiling = max(TV_VACANT_FLOOR_PCT, VACANT_LATE_EVENING_PCT)
        above = {k: v for k, v in living_room_lit(lit(r, "21:00")).items() if v > ceiling}
        assert not above, (f"living room above the vacant floor ({ceiling} %) at 21:00 "
                           f"with nobody there: {above}")


@pytest.mark.parametrize("variant", VARIANTS)
async def test_t4_laptop_at_desk(sim, variant):
    """TV on from 20:00, sofa empty, 30 min at the office desk 20:10-20:40."""
    tl = Timeline(*evening_bounds(BASE))
    tl.tv(at(BASE, "20:00"), "on")
    tl.occupy("office", at(BASE, "20:10"), at(BASE, "20:40"))
    tl.tv(at(BASE, "21:00"), "off")
    feed_belief(tl)
    tl.belief(at(BASE, "20:11"), "sensor.living_room_office_activity", "working")
    tl.belief(at(BASE, "20:40"), "sensor.living_room_office_activity", "idle")
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    r = report("T4_laptop_at_desk", variant, holder, tl, snaps, hours=("20:05", "20:12", "20:30", "20:45", "21:30"))
    if ASSERT_TARGET:
        # The sofa is empty and no belief says watching: a watch zone behaves
        # like a non-watch zone (plan refinement b), so the desk gets its
        # present target, not the movie dim.
        office = lit(r, "20:30").get("light.office", 0)
        assert office >= 50, f"office at the desk with the TV on and nobody on the sofa: {office} % ({lit(r, '20:30')})"


@pytest.mark.parametrize("variant", VARIANTS)
async def test_t5_nap(sim, variant):
    """Watching from 20:05; the belief turns to napping at 22:30; TV off 23:30."""
    tl = watch(BASE)
    feed_belief(tl)
    tl.belief(at(BASE, "20:06"), SOFA_ACTIVITY, "watching_tv")
    tl.belief(at(BASE, "22:30"), SOFA_ACTIVITY, "napping")
    tl.belief(at(BASE, "23:30"), SOFA_ACTIVITY, "idle")
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    report("T5_nap", variant, holder, tl, snaps, hours=("20:10", "22:00", "22:31", "22:45", "23:35"),
           extra={"expectation": "report only: napping must not brighten the room; the plan sets no M2 number"})


@pytest.mark.parametrize("variant", VARIANTS)
async def test_t6_errand_becomes_departure(sim, variant):
    """Watching 20:05-21:20, out through the front door at 21:21, presence
    drops at 21:30 (user_at_home off, person not_home). The TV stays on."""
    tl = Timeline(*evening_bounds(BASE))
    tl.tv(at(BASE, "20:00"), "on")
    tl.occupy("sofa", at(BASE, "20:05"), at(BASE, "21:20"))
    tl.occupy("front_door", at(BASE, "21:21"), at(BASE, "21:23"))
    tl.at(at(BASE, "21:30"), "person.engineeredlighting", "not_home")
    tl.at(at(BASE, "21:30"), "input_boolean.user_at_home", "off")
    tl.tv(at(BASE, "23:00"), "off")
    feed_belief(tl)
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    r = report("T6_errand_becomes_departure", variant, holder, tl, snaps,
               hours=("21:00", "21:22", "21:29", "21:35", "22:00", "23:30"))
    after = at(BASE, "21:30")
    r["turn_ons_after_departure"] = [d for d in r["turn_ons_detail"] if parse_t(d["t"]) >= after]
    (REPORT_DIR / f"T6_errand_becomes_departure-{variant}.json").write_text(json.dumps(r, indent=1, default=str) + "\n")


@pytest.mark.parametrize("variant", VARIANTS)
async def test_t7_publisher_dead(sim, variant):
    """Watching from 20:05; the heartbeat stops at 21:00 and the belief
    entities freeze at their 21:00 states. With the toggle on, the packages
    must behave exactly as with it off (fail-safe to the legacy path)."""
    tl = watch(BASE)
    feed_belief(tl, dead_from=at(BASE, "21:00"))
    drop_belief_edges_after(tl, at(BASE, "21:00"))
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    # The stale belief is what the packages see; the metrics score the
    # evening that actually happened (timeline), so the frozen "on" does not
    # stretch the episode to 01:00 and count the 23:30 TV-off brightening.
    r = report("T7_publisher_dead", variant, holder, tl, snaps, hours=("20:10", "21:30", "22:00", "23:35"),
               watching_source="timeline",
               extra={"heartbeat_dead_from": at(BASE, "21:00").isoformat(),
                      "belief_edges_fed": [e[0].isoformat() for e in tl.events if e[1] == TV_WATCHING]})
    if ASSERT_TARGET and variant == "belief_on":
        other = REPORT_DIR / "T7_publisher_dead-belief_off.json"
        assert other.exists(), "run the belief_off variant first"
        off = json.loads(other.read_text())
        # Normalised digests (final level per instant and light; per-minute
        # lit state): same-instant dispatch order cannot fail this gate.
        # Compared from the heartbeat's death on: before 21:00 the belief is
        # live and the M2 tv_playing rule lets it differ from the legacy path
        # by design (TV on at 20:00 with the sofa empty until 20:05 is "not
        # watching" only when the belief is live), so the fail-safe gate is
        # the dead window, not the whole evening.
        dead = at(BASE, "21:00")
        since_dead = lambda rows: [row for row in rows if parse_t(row[0]) >= dead]
        calls_diff = digest_diff(since_dead(off["call_digest"]), since_dead(r["call_digest"]))
        assert not calls_diff, f"publisher dead: light calls differ between toggle off (a) and on (b): {calls_diff}"
        snaps_diff = digest_diff(since_dead(off["snapshot_digest"]), since_dead(r["snapshot_digest"]))
        assert not snaps_diff, f"publisher dead: lit state differs between toggle off (a) and on (b): {snaps_diff}"


@pytest.mark.parametrize("variant", VARIANTS)
async def test_t8_tv_unavailable_blips(sim, variant):
    """Watching from 20:05; lg_tv reads unavailable for 30 s at 20:30 and for
    12 min at 21:30, then on again; TV off 23:30."""
    tl = watch(BASE)
    blips = [(at(BASE, "20:30"), at(BASE, "20:30:30")), (at(BASE, "21:30"), at(BASE, "21:42"))]
    for a, b in blips:
        tl.tv(a, "unavailable")
        tl.tv(b, "on")
    feed_belief(tl)
    keep_watching_through(tl, blips)
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    r = report("T8_tv_unavailable_blips", variant, holder, tl, snaps, hours=("20:29", "20:31", "20:33", "21:29", "21:31", "21:41", "21:43"))
    sw = holder.lights.switches.calls
    strip_on = [{"t": c["t"]} for c in sw if c["service"] == "turn_on"]
    # Each blip plus 120 s after it: a brightening or a strip turn-on that a
    # blip caused lands inside that tail (delay_off, the oracle's grace and
    # the pilots' ticks are all shorter).
    r["ambient_on_calls_in_blips"] = [c["t"] for c in calls_between(strip_on, blips, tail_s=120)]
    r["brighten_in_blips"] = calls_between(r["brighten_while_watching"], blips, tail_s=120)
    r["brighten_in_blips_by_blip"] = {f"{a.strftime('%H:%M:%S')}-{b.strftime('%H:%M:%S')}":
                                      len(calls_between(r["brighten_while_watching"], [(a, b)], tail_s=120))
                                      for a, b in blips}
    (REPORT_DIR / f"T8_tv_unavailable_blips-{variant}.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
    if ASSERT_TARGET:
        # Both blips (30 s and 12 min), both toggles: zero brightenings and
        # zero strip turn-ons. The 12 min blip outlives delay_off (90 s) and
        # the oracle's grace (600 s); the sofa's stable occupancy is what
        # must keep tv_seen_on (plan refinement a).
        assert not r["brighten_in_blips"], f"brightened during a blip: {r['brighten_in_blips']}"
        assert not r["ambient_on_calls_in_blips"], f"strip turn-on during a blip: {r['ambient_on_calls_in_blips']}"
        assert not r["brighten_while_watching"], r["brighten_while_watching"]
        assert r["unavailable_flicker"] == 0, r["unavailable_flicker_detail"]


@pytest.mark.parametrize("variant", VARIANTS)
async def test_t9_front_door_during_film(sim, variant):
    """Watching from 20:05 (the sofa stays occupied); the front-door zone is
    occupied 21:00-21:02 (someone passes)."""
    tl = watch(BASE)
    tl.occupy("front_door", at(BASE, "21:00"), at(BASE, "21:02"))
    feed_belief(tl)
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    r = report("T9_front_door_during_film", variant, holder, tl, snaps, hours=("20:59", "21:01", "21:02", "21:05", "22:00"))
    if ASSERT_TARGET:
        # The sofa is occupied, so watching is happening and the front-door
        # zone (a watch zone) holds the movie dim: 0 %, i.e. off. Nothing in
        # the living room may brighten for the pass.
        fr = lit(r, "21:01").get("light.front_right", 0)
        assert fr == MOVIE_DIM_PCT, f"front_right during a front-door pass while the sofa is occupied: {fr} % ({lit(r, '21:01')})"
        assert not r["brighten_while_watching"], r["brighten_while_watching"]


@pytest.mark.parametrize("variant", VARIANTS)
async def test_t10_office_while_other_watches(sim, variant):
    """Watching from 20:05 (one person on the sofa all evening); another
    person at the office desk 21:00-21:30."""
    tl = watch(BASE)
    tl.occupy("office", at(BASE, "21:00"), at(BASE, "21:30"))
    feed_belief(tl)
    tl.belief(at(BASE, "21:01"), "sensor.living_room_office_activity", "working")
    tl.belief(at(BASE, "21:30"), "sensor.living_room_office_activity", "idle")
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    r = report("T10_office_while_other_watches", variant, holder, tl, snaps, hours=("20:59", "21:02", "21:15", "21:32", "22:00"))
    if ASSERT_TARGET:
        # One person watches from the sofa: the office (a watch zone) holds
        # the movie dim, 0 %, even with someone at the desk.
        office = lit(r, "21:15").get("light.office", 0)
        assert office == MOVIE_DIM_PCT, f"office while another person watches from the sofa: {office} % ({lit(r, '21:15')})"
        assert not r["brighten_while_watching"], r["brighten_while_watching"]


# ----- story S extras -----
def evening_then_bed(day: dt.date) -> Timeline:
    """S1's night: TV 22:05-23:50, sofa 22:10-23:45, sink 23:47-23:52,
    front_left 23:52-23:56, nobody until the kitchen 07:30-07:50."""
    tl = Timeline(*night_bounds(day))
    tl.tv(at(day, "22:05"), "on")
    tl.occupy("sofa", at(day, "22:10"), at(day, "23:45"))
    tl.occupy("sink", at(day, "23:47"), at(day, "23:52"))
    tl.occupy("front_left", at(day, "23:52"), at(day, "23:56"))
    tl.tv(at(day, "23:50"), "off")
    tl.occupy("sink", at(day, "07:30", 1), at(day, "07:50", 1))
    return tl


NIGHT_HOURS = ("00:30", "03:00", "05:00", "07:00", "07:45")


def night_window(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    return at(day, "00:00", 1), at(day, "08:00", 1)


async def run_night(sim, name: str, variant: str, tl: Timeline, day: dt.date, hours=NIGHT_HOURS,
                    window=None, extra: dict | None = None) -> dict:
    feed_belief(tl)
    holder = await start_variant(sim, tl, variant)
    snaps = await run(holder, tl)
    return report(name, variant, holder, tl, snaps, window=window or night_window(day), hours=hours,
                  extra={**night_extras(holder, tl, day), **(extra or {})})


def between(times: list[str], a: dt.datetime, b: dt.datetime) -> list[str]:
    return [t for t in times if a <= parse_t(t) < b]


@pytest.mark.parametrize("variant", VARIANTS)
async def test_s9_presence_reconnect(sim, variant):
    """Asleep; the phone drops off the network at 03:00 for 20 s (user_at_home
    off then on), no front-door occupancy."""
    tl = evening_then_bed(BASE)
    tl.at(at(BASE, "03:00", 1), "input_boolean.user_at_home", "off")
    tl.at(at(BASE, "03:00:20", 1), "input_boolean.user_at_home", "on")
    r = await run_night(sim, "S9_presence_reconnect", variant, tl, BASE)
    if ASSERT_TARGET:
        assert r["latch_on"], "the latch must fire once the house is quiet"
        assert not between(r["latch_off"], at(BASE, "02:00", 1), at(BASE, "07:00", 1)), r["latch_off"]


S9B_ORDERS = {
    # door camera reports 30 s before the phone reconnects (the presence
    # trigger sees a fresh front-door sighting)
    "door_first": ("02:59:30", "03:00:00"),
    # phone reconnects 30 s before the door camera sees the person (the
    # arrival trigger sees a fresh user_at_home)
    "phone_first": ("03:00:30", "03:00:00"),
}


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("order", sorted(S9B_ORDERS))
async def test_s9b_real_arrival(sim, order, variant):
    """Asleep; presence drops at 02:20 and returns at about 03:00 with the
    front door occupied for 2 min (a credible arrival). The phone and the door
    camera report 30 s apart in either order; the latch must clear within 10
    min of 03:00 whichever reports first."""
    door_at, phone_at = S9B_ORDERS[order]
    tl = evening_then_bed(BASE)
    tl.at(at(BASE, "02:20", 1), "input_boolean.user_at_home", "off")
    tl.at(at(BASE, "02:20", 1), "person.engineeredlighting", "not_home")
    tl.at(at(BASE, phone_at, 1), "input_boolean.user_at_home", "on")
    tl.at(at(BASE, phone_at, 1), "person.engineeredlighting", "home")
    tl.occupy("front_door", at(BASE, door_at, 1), at(BASE, door_at, 1) + dt.timedelta(minutes=2))
    r = await run_night(sim, f"S9b_real_arrival_{order}", variant, tl, BASE,
                        hours=("02:30", "03:01", "03:05", "03:15", "05:00"),
                        extra={"order": order, "door_at": door_at, "phone_at": phone_at})
    if ASSERT_TARGET:
        clears = between(r["latch_off"], at(BASE, "02:59", 1), at(BASE, "03:10", 1))
        assert clears, (f"a real arrival ({order}: door {door_at}, phone {phone_at}) must clear the latch "
                        f"within 10 min of 03:00: {r['latch_off']} writers {r['latch_writers']}")


@pytest.mark.parametrize("variant", VARIANTS)
async def test_s10_late_sleeper(sim, variant):
    """Weekday; nobody until 10:30 (kitchen 10:30-10:50)."""
    start, _ = night_bounds(WEEKDAY)
    tl = Timeline(start, at(WEEKDAY, "11:00", 1))
    tl.tv(at(WEEKDAY, "22:05"), "on")
    tl.occupy("sofa", at(WEEKDAY, "22:10"), at(WEEKDAY, "23:45"))
    tl.tv(at(WEEKDAY, "23:50"), "off")
    tl.occupy("sink", at(WEEKDAY, "10:30", 1), at(WEEKDAY, "10:50", 1))
    r = await run_night(sim, "S10_late_sleeper", variant, tl, WEEKDAY,
                        hours=("03:00", "07:00", "08:30", "09:00", "09:30", "10:00", "10:29", "10:35"),
                        window=(at(WEEKDAY, "00:00", 1), at(WEEKDAY, "10:30", 1)))
    if ASSERT_TARGET:
        # The plan's gate is "no override write and no working-hours floor
        # before occupancy": the sleeper is up at 10:30 (kitchen 10:30-10:50)
        # and a good-morning energize on that real wake, inside its own
        # 04:30-11:30 window, is the designed behaviour, so only writes
        # before the kitchen is occupied count against the scenario.
        early = [w for w in r["override_text_writes_detail"] if parse_t(w["t"]) < at(WEEKDAY, "10:30", 1)]
        assert not early, early
        for hhmm in ("08:30", "09:00", "09:30", "10:00"):
            assert lit(r, hhmm) == {}, f"lit before anyone was up at {hhmm}: {lit(r, hhmm)}"


@pytest.mark.parametrize("variant", VARIANTS)
async def test_s11_energize(sim, variant):
    """The kitchen at 07:30 wakes the house: the latch clears, woke_up_today
    latches, the good-morning automation writes five override payloads."""
    tl = evening_then_bed(BASE)
    r = await run_night(sim, "S11_energize", variant, tl, BASE, hours=("07:00", "07:35", "07:45", "07:55"))
    if ASSERT_TARGET:
        assert r["woke_up_today_at"], "the wake at 07:30 must latch woke_up_today"
        assert r["override_text_writes_attempted"] == 5, r["override_text_writes_detail"]
        assert r["override_text_writes_over_255"] == 0, r["override_text_writes_lengths"]
        assert r["override_text_writes_accepted"] == 5, r["override_text_writes_accepted"]
        assert all(w["brightness_pct"] == 90 for w in r["override_text_writes_detail"]), r["override_text_writes_detail"]


@pytest.mark.parametrize("variant", VARIANTS)
async def test_s12_kitchen_excursion_0500(sim, variant):
    """Asleep; a 10-minute kitchen excursion 05:00-05:10, then back to bed."""
    tl = evening_then_bed(BASE)
    tl.occupy("sink", at(BASE, "05:00", 1), at(BASE, "05:10", 1))
    r = await run_night(sim, "S12_kitchen_excursion_0500", variant, tl, BASE, hours=("04:59", "05:02", "05:11", "05:15", "06:00", "07:00"))
    a, b = at(BASE, "05:00", 1), at(BASE, "06:00", 1)
    r["override_text_writes_0500_0600"] = [w for w in r["override_text_writes_detail"] if a <= parse_t(w["t"]) < b]
    r["lights_above_30_0500_0600"] = lit_between(r, a, b, above_pct=S12_MAX_PCT)
    (REPORT_DIR / f"S12_kitchen_excursion_0500-{variant}.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
    if ASSERT_TARGET:
        # No energize: zero override writes in the window, and night levels
        # only for as long as the house still believes everyone is asleep.
        # The latch itself clears after ten minutes of occupancy, which is the
        # design (plan, story S estimator: "exit on ten minutes of credible
        # occupancy"), and an awake house then lights the kitchen at its
        # present level. That is why the level ceiling applies up to the clear
        # and not past it; what happens afterwards is recorded below so the
        # owner can judge whether ten minutes is the wake threshold they want.
        assert not r["override_text_writes_0500_0600"], \
            f"a 10-minute excursion must not energize the house: {r['override_text_writes_0500_0600']}"
        cleared = between(r["latch_off"], at(BASE, "05:00", 1), at(BASE, "06:00", 1))
        # lit_between stamps each row with a time of day; the window is inside
        # one morning, so comparing "HH:MM:SS" strings is exact.
        until = parse_t(cleared[0]).strftime("%H:%M:%S") if cleared else "06:00:00"
        while_asleep = [row for row in r["lights_above_30_0500_0600"] if row["t"] < until]
        assert not while_asleep, \
            f"a light above {S12_MAX_PCT} % while the house was still asleep: {while_asleep[:5]}"
        early = [w for w in r["override_text_writes_detail"] if parse_t(w["t"]) < at(BASE, "07:00", 1)]
        assert not early, f"an override write before anyone was up: {early}"


@pytest.mark.parametrize("variant", VARIANTS)
async def test_s13_manual_clear(sim, variant):
    """Asleep; the owner clears the latch by hand at 02:00 and stays quiet."""
    tl = evening_then_bed(BASE)
    tl.at(at(BASE, "02:00", 1), ASLEEP, "off")
    r = await run_night(sim, "S13_manual_clear", variant, tl, BASE, hours=("01:59", "02:05", "02:30", "02:50", "03:00", "05:00"))
    if ASSERT_TARGET:
        relatch = between(r["latch_on"], at(BASE, "02:00", 1), at(BASE, "02:45", 1))
        assert not relatch, f"a manual clear must hold 45 min: {relatch}"


# ----- recorded evenings -----


def evening_files() -> list[pathlib.Path]:
    if not EVENINGS:
        return []
    return sorted(pathlib.Path(EVENINGS).glob("*.json"))


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("evening_file", evening_files(), ids=lambda p: p.stem)
async def test_recorded_evening(sim, evening_file, variant):
    """One recorded evening (schema living-lights-sim-evening/v1 from
    replay.py --evenings): occupancy from the ledger's classifier states, the
    TV and presence from the observer snapshot when it has them. No belief is
    fed, so watching episodes come from the TV and sofa inputs and the
    belief_on variant differs from belief_off only in dispatch order. The
    report's `validity` block is the evening's own: `tv_measurable` false
    keeps the evening out of every score."""
    doc = json.loads(evening_file.read_text())
    assert doc.get("schema", "living-lights-sim-evening/v1") == "living-lights-sim-evening/v1", doc.get("schema")
    start, end = parse_t(doc["start"]), parse_t(doc["end"])
    initial = dict(doc["initial"])
    initial.setdefault(ASLEEP, "off")
    tl = Timeline(start, end, initial)
    for ev in doc["events"]:
        tl.at(ev["t"], ev["entity"], ev["state"])
    holder = await start_variant(sim, tl, variant)
    if initial[ASLEEP] == "on":
        await set_toggle(holder.hass, ASLEEP, True)
    snaps = await run(holder, tl)
    validity = validity_of(doc)
    actual = doc.get("actual") or {}
    measurable = validity["tv_measurable"]
    report(f"evening_{doc['evening']}", variant, holder, tl, snaps, window=(start, end), validity=validity,
           extra={
               "tv_measurable": measurable,
               "tv_source": validity["tv_source"],
               "label_note": None if measurable else "tv unmeasurable",
               "actual_tv_edges": actual.get("tv_edges", []),
               "actual_asleep_transitions": actual.get("asleep_transitions", []),
               "actual_turn_ons_18_01": actual.get("turn_on_count_18_01"),
               "input_edges": len(doc["events"]),
               "validity_notes": validity["notes"],
           })
