"""Living Lights story S simulation: synthetic nights and recorded nights
through the real packages. See README.md in this directory.

    LL_SIM_PACKAGES=<ha-config dir> LL_SIM_LABEL=new LL_SIM_NIGHTS=<dir> \
      ~/.venvs/ha-sim/bin/python -m pytest tools/lighting-sim/test_sim.py -o asyncio_mode=auto -p no:cacheprovider

Every scenario writes a JSON report under LL_SIM_REPORT/<label>/. Assertions
run only with LL_SIM_ASSERT=1 (they describe the NEW packages; the OLD
packages are run for the comparison column and are expected to fail them).
"""
from __future__ import annotations

import collections
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
from harness import (Timeline, apply_production_toggles, run_timeline,  # noqa: E402
                     set_toggle, setup_sim)

REPO = HERE.parents[1]
PACKAGES = pathlib.Path(os.environ.get("LL_SIM_PACKAGES", str(REPO / "ha-config"))).resolve()
LABEL = os.environ.get("LL_SIM_LABEL", "new")
REPORT = pathlib.Path(os.environ.get(
    "LL_SIM_REPORT", str(pathlib.Path.home() / "vjepa-home" / "experiments" / "lighting-sim" / "reports"))) / LABEL
NIGHTS = os.environ.get("LL_SIM_NIGHTS")
ASSERT = os.environ.get("LL_SIM_ASSERT") == "1"
STEP_S = int(os.environ.get("LL_SIM_STEP_S", "20"))
TZ = ZoneInfo("America/Los_Angeles")
IDLE = dt.timedelta(minutes=15)
HOLD = dt.timedelta(seconds=90)
ZONE_LIGHTS = {   # from the pilots' "Actuates [...]" lines; zones without a pilot drive no light
    "dining_left": ["light.dining_table_left"], "dining_right": ["light.dining_table_right"],
    "front_door": ["light.front_right"], "front_left": ["light.front_left"],
    "island_left": ["light.island_left"], "island_right": ["light.island_right"],
    "office": ["light.office"], "sink": ["light.sink"],
    "sofa": ["light.front_left", "light.front_right", "light.rear_left", "light.rear_right"],
    "weights": ["light.front_right", "light.rear_right"],
}


def at(day: dt.date, hhmm: str, plus_days: int = 0) -> dt.datetime:
    h, m = hhmm.split(":")
    return dt.datetime.combine(day + dt.timedelta(days=plus_days), dt.time(int(h), int(m)), tzinfo=TZ)


def night_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    return at(day, "22:00"), at(day, "09:00", 1)


@pytest.fixture
async def sim(hass, request):
    """Home Assistant instance plus a freezegun clock started at the
    scenario's first instant (loop clock frozen as well; see harness.settle)."""
    from freezegun import freeze_time
    holder = SimpleNamespace(hass=hass, freezer=None, lights=None, recorder=None)

    async def start(when: dt.datetime, initial: dict[str, str] | None = None):
        ctx = freeze_time(when)
        holder.freezer = ctx.start()
        request.addfinalizer(ctx.stop)
        holder.lights, holder.recorder, _ = await setup_sim(hass, PACKAGES, initial)
        await apply_production_toggles(hass)
        return holder

    holder.start = start
    return holder


HOURS = os.environ.get("LL_SIM_HOURS")   # diagnostics: truncate every scenario


def truncate(tl: Timeline) -> Timeline:
    if HOURS:
        tl.end = min(tl.end, tl.start + dt.timedelta(hours=float(HOURS)))
    return tl


async def run(holder, tl: Timeline):
    return await run_timeline(holder.hass, holder.freezer, truncate(tl), step_s=STEP_S)


def analyze(name: str, holder, timeline: Timeline, snapshots, extra: dict | None = None) -> dict:
    day = timeline.start.date() + dt.timedelta(days=1)
    m_start, m_end = at(day, "00:00"), at(day, "08:00")
    asleep = [c for c in holder.recorder.changes if c["entity"] == "input_boolean.living_lights_asleep"
              and dt.datetime.fromisoformat(c["t"]) > timeline.start + dt.timedelta(minutes=1)]
    latch_on = [dt.datetime.fromisoformat(c["t"]) for c in asleep if c["to"] == "on"]
    latch_off = [dt.datetime.fromisoformat(c["t"]) for c in asleep if c["to"] == "off"]
    woke = [c["t"] for c in holder.recorder.changes
            if c["entity"] == "input_boolean.living_lights_woke_up_today" and c["to"] == "on"]
    energize = [c for c in holder.recorder.changes
                if c["entity"].startswith("input_text.living_lights_override_text_") and "morning-" in str(c["to"])]

    def asleep_at(t: dt.datetime) -> bool:
        state = timeline.initial.get("input_boolean.living_lights_asleep", "off") == "on"
        for c in asleep:
            if dt.datetime.fromisoformat(c["t"]) <= t:
                state = c["to"] == "on"
        return state

    turn_ons = [c for c in holder.lights.calls
                if c["service"] == "turn_on" and c["from"] == "off" and (c["brightness_pct"] or 0) > 0
                and m_start <= dt.datetime.fromisoformat(c["t"]) < m_end]
    zone_events = [(t, ent[len("binary_sensor."):-len("_person_occupancy")], st) for t, ent, st in timeline.events
                   if ent.startswith("binary_sensor.") and ent.endswith("_person_occupancy")
                   and ent[len("binary_sensor."):-len("_person_occupancy")] in ZONE_LIGHTS]

    def lights_of_occupied_zones(t: dt.datetime) -> set[str]:
        """Lights whose zone has raw occupancy on at t or within the 90 s stable hold."""
        allowed: set[str] = set()
        state: dict[str, tuple[bool, dt.datetime | None]] = {}
        for when, zone, st in sorted(zone_events, key=lambda e: e[0]):
            if when > t:
                break
            state[zone] = (st == "on", when)
        for zone, (on, when) in state.items():
            if on or (when is not None and t - when <= HOLD):
                allowed.update(ZONE_LIGHTS[zone])
        # The generator's KITCHEN_PERSON_FALLBACK_ZONES: camera-level kitchen
        # occupancy counts as presence for every kitchen zone.
        cam_state = None
        for when, ent, st in sorted(timeline.events, key=lambda e: e[0]):
            if when > t:
                break
            if ent == "binary_sensor.kitchen_person_occupancy":
                cam_state = (st == "on", when)
        if cam_state and (cam_state[0] or t - cam_state[1] <= HOLD):
            for zone in ("sink", "island_left", "island_right"):
                allowed.update(ZONE_LIGHTS[zone])
        return allowed

    # A light coming on in an OCCUPIED zone while asleep is the asleep cap doing
    # its job (navigable light); the defect is light in an EMPTY zone.
    turn_ons_asleep = [c for c in turn_ons if asleep_at(dt.datetime.fromisoformat(c["t"]))
                       and c["entity"] not in lights_of_occupied_zones(dt.datetime.fromisoformat(c["t"]))]
    occupancy_on = sorted(t for t, ent, st in timeline.events
                          if ent.endswith("_person_occupancy") and st == "on" and "binary_sensor." in ent)
    occupancy_off = sorted(t for t, ent, st in timeline.events
                           if ent.endswith("_person_occupancy") and st == "off" and "binary_sensor." in ent)
    false_latches = []
    for t in latch_on:
        recent_off = [x for x in occupancy_off if x <= t]
        last_seen = max(recent_off) if recent_off else None
        still_on = any(a <= t and not any(a < b <= t for b in occupancy_off) for a in occupancy_on)
        gap_min = (t - last_seen).total_seconds() / 60 if last_seen else None
        if still_on or (gap_min is not None and gap_min < 15):
            false_latches.append({"t": t.isoformat(), "minutes_since_last_seen": gap_min, "someone_visible": still_on})

    def lights_at(hhmm: str) -> dict:
        target = at(day, hhmm)
        best = None
        for s in snapshots:
            if dt.datetime.fromisoformat(s["t"]) <= target:
                best = s
        return best["lights_on"] if best else {}

    result = {
        "label": LABEL, "scenario": name, "packages": str(PACKAGES), "step_s": STEP_S,
        "window": [timeline.start.isoformat(), timeline.end.isoformat()],
        "latch_on": [t.isoformat() for t in latch_on], "latch_off": [t.isoformat() for t in latch_off],
        "false_latches": false_latches,
        "woke_up_today_at": woke, "morning_energize_writes": len(energize),
        "turn_ons_00_08": len(turn_ons), "turn_ons_00_08_while_asleep": len(turn_ons_asleep),
        "turn_ons_00_08_detail": [{"t": c["t"][11:19], "light": c["entity"], "pct": c["brightness_pct"],
                                   "asleep": asleep_at(dt.datetime.fromisoformat(c["t"])),
                                   "zone_occupied": c["entity"] in lights_of_occupied_zones(dt.datetime.fromisoformat(c["t"])),
                                   "by": holder.recorder.owner_of(c.get("context_id"), c.get("context_parent"))}
                                  for c in turn_ons],
        "empty_while_asleep_by_automation": dict(collections.Counter(
            holder.recorder.owner_of(c.get("context_id"), c.get("context_parent")) or "unknown" for c in turn_ons_asleep)),
        "lights_on_at_0300": lights_at("03:00"), "lights_on_at_0500": lights_at("05:00"),
        "lights_on_at_0700": lights_at("07:00"),
        "light_calls_total": len(holder.lights.calls),
        "classifier_changes": len(holder.recorder.classifier),
    }
    if extra:
        result.update(extra)
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / f"{name}.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


# ───────────────────────── synthetic scenarios ─────────────────────────
BASE = dt.date(2026, 9, 13)      # a Sunday evening
WEEKDAY = dt.date(2026, 9, 15)   # a Tuesday evening (working-hours logic is weekday-only)


def evening_then_bed(day: dt.date, tv_off: bool = True) -> Timeline:
    """TV from 22:05, sofa 22:10-23:45, sink 23:47-23:52, front_left 23:52-23:56,
    then nobody until 07:30 when the kitchen is occupied 07:30-07:50."""
    start, end = night_bounds(day)
    tl = Timeline(start, end)
    tl.at(at(day, "22:05"), "media_player.lg_tv", "on")
    tl.occupy("sofa", at(day, "22:10"), at(day, "23:45"))
    tl.occupy("sink", at(day, "23:47"), at(day, "23:52"))
    tl.occupy("front_left", at(day, "23:52"), at(day, "23:56"))
    if tv_off:
        tl.at(at(day, "23:50"), "media_player.lg_tv", "off")
    tl.occupy("sink", at(day, "07:30", 1), at(day, "07:50", 1))
    return tl


async def test_s1_evening_then_bed(sim):
    tl = evening_then_bed(BASE)
    holder = await sim.start(tl.start, tl.initial)
    snaps = await run(holder, tl)
    r = analyze("S1_evening_then_bed", holder, tl, snaps)
    if ASSERT:
        assert r["latch_on"], "the asleep latch must fire once the house is quiet"
        first = dt.datetime.fromisoformat(r["latch_on"][0])
        assert at(BASE, "00:05", 1) <= first <= at(BASE, "00:25", 1), r["latch_on"]
        assert r["turn_ons_00_08_while_asleep"] == 0, r["turn_ons_00_08_detail"]
        assert r["lights_on_at_0300"] == {}, r["lights_on_at_0300"]
        assert not r["false_latches"], r["false_latches"]


async def test_s2_tv_left_on(sim):
    tl = evening_then_bed(BASE, tv_off=False)
    holder = await sim.start(tl.start, tl.initial)
    snaps = await run(holder, tl)
    r = analyze("S2_tv_left_on", holder, tl, snaps)
    if ASSERT:
        assert r["latch_on"], "a TV left on must not keep the house awake"
        assert r["turn_ons_00_08_while_asleep"] == 0, r["turn_ons_00_08_detail"]


async def test_s3_night_excursion(sim):
    tl = evening_then_bed(BASE)
    tl.occupy("sink", at(BASE, "03:00", 1), at(BASE, "03:04", 1))
    holder = await sim.start(tl.start, tl.initial)
    snaps = await run(holder, tl)
    r = analyze("S3_night_excursion", holder, tl, snaps)
    if ASSERT:
        assert r["latch_on"]
        assert not any(at(BASE, "00:30", 1) <= dt.datetime.fromisoformat(t) <= at(BASE, "07:00", 1)
                       for t in r["latch_off"]), "a 4-minute trip to the kitchen must not clear the latch"
        assert r["lights_on_at_0500"] == {}, r["lights_on_at_0500"]
        night_lights = {c["light"] for c in r["turn_ons_00_08_detail"] if "03:0" in c["t"]}
        assert night_lights <= {"light.sink", "light.island_left", "light.island_right"}, night_lights


async def test_s4_sleeping_late(sim):
    start, end = night_bounds(WEEKDAY)
    end = at(WEEKDAY, "11:00", 1)
    tl = Timeline(start, end)
    tl.at(at(WEEKDAY, "22:05"), "media_player.lg_tv", "on")
    tl.occupy("sofa", at(WEEKDAY, "22:10"), at(WEEKDAY, "23:45"))
    tl.at(at(WEEKDAY, "23:50"), "media_player.lg_tv", "off")
    tl.occupy("sink", at(WEEKDAY, "10:30", 1), at(WEEKDAY, "10:50", 1))
    holder = await sim.start(tl.start, tl.initial)
    snaps = await run(holder, tl)
    r = analyze("S4_sleeping_late", holder, tl, snaps)
    if ASSERT:
        assert r["latch_on"]
        assert r["turn_ons_00_08_while_asleep"] == 0, r["turn_ons_00_08_detail"]
        assert r["lights_on_at_0700"] == {}, r["lights_on_at_0700"]
        assert not any(dt.datetime.fromisoformat(t) < at(WEEKDAY, "09:00", 1) for t in r["latch_off"]), r["latch_off"]


async def test_s5_leaving_at_2300(sim):
    start, end = night_bounds(BASE)
    tl = Timeline(start, end)
    tl.occupy("sofa", at(BASE, "22:10"), at(BASE, "22:55"))
    tl.occupy("front_door", at(BASE, "22:56"), at(BASE, "22:58"))
    tl.at(at(BASE, "23:01"), "person.engineeredlighting", "not_home")
    tl.at(at(BASE, "23:01"), "input_boolean.user_at_home", "off")
    tl.at(at(BASE, "07:00", 1), "person.engineeredlighting", "home")
    tl.at(at(BASE, "07:00", 1), "input_boolean.user_at_home", "on")
    tl.occupy("front_door", at(BASE, "07:00", 1), at(BASE, "07:03", 1))
    holder = await sim.start(tl.start, tl.initial)
    snaps = await run(holder, tl)
    r = analyze("S5_leaving_at_2300", holder, tl, snaps)
    if ASSERT:
        assert not any(dt.datetime.fromisoformat(t) < at(BASE, "07:00", 1) for t in r["latch_on"]), r["latch_on"]
        assert r["lights_on_at_0300"] == {}, r["lights_on_at_0300"]


async def test_s6_guest_on_sofa(sim):
    start, end = night_bounds(BASE)
    tl = Timeline(start, end)
    tl.occupy("sofa", at(BASE, "22:10"), at(BASE, "07:00", 1))
    holder = await sim.start(tl.start, tl.initial)
    snaps = await run(holder, tl)
    analyze("S6_guest_on_sofa", holder, tl, snaps, {"expectation": "known limitation: a person on the sofa all night keeps any_occupied on, so the latch cannot fire; report only"})


async def test_s7_stuck_zone(sim):
    tl = evening_then_bed(BASE)
    tl.at(at(BASE, "22:30"), "binary_sensor.office_person_occupancy", "on")   # never clears
    holder = await sim.start(tl.start, tl.initial)
    snaps = await run(holder, tl)
    analyze("S7_stuck_zone", holder, tl, snaps, {"expectation": "known limitation: a stuck raw occupancy sensor blocks the latch; report only"})


async def test_s8_tick_false_latch(sim):
    """The 2026-09-13 pattern: sofa until 23:47, kitchen 23:52-00:05, kitchen again
    00:11-00:21, then quiet. The old tick path latched at 00:10; the new one must
    wait for a full quiet window after 00:21."""
    start, end = night_bounds(BASE)
    tl = Timeline(start, end)
    tl.at(at(BASE, "22:05"), "media_player.lg_tv", "on")
    tl.occupy("sofa", at(BASE, "22:01"), at(BASE, "23:47"))
    tl.at(at(BASE, "00:05", 1), "media_player.lg_tv", "off")
    tl.occupy("whole_kitchen", at(BASE, "23:52"), at(BASE, "00:05", 1))
    tl.occupy("whole_kitchen", at(BASE, "00:11", 1), at(BASE, "00:21", 1))
    holder = await sim.start(tl.start, tl.initial)
    snaps = await run(holder, tl)
    r = analyze("S8_tick_false_latch", holder, tl, snaps)
    if ASSERT:
        assert r["latch_on"], "the latch must fire after the house goes quiet"
        first = dt.datetime.fromisoformat(r["latch_on"][0])
        assert first >= at(BASE, "00:35", 1), f"latched too early: {r['latch_on']}"
        assert first <= at(BASE, "00:50", 1), r["latch_on"]
        assert not r["false_latches"], r["false_latches"]


# ───────────────────────── recorded nights ─────────────────────────
def night_files() -> list[pathlib.Path]:
    if not NIGHTS:
        return []
    return sorted(pathlib.Path(NIGHTS).glob("*.json"))


@pytest.mark.parametrize("night_file", night_files(), ids=lambda p: p.stem)
async def test_recorded_night(sim, night_file):
    doc = json.loads(night_file.read_text())
    start = dt.datetime.fromisoformat(doc["start"]); end = dt.datetime.fromisoformat(doc["end"])
    initial = dict(doc["initial"])
    initial["input_boolean.living_lights_asleep"] = "on" if doc.get("initial_asleep_recorded") else "off"
    tl = Timeline(start, end, initial)
    for ev in doc["events"]:
        tl.at(ev["t"], ev["entity"], ev["state"])
    holder = await sim.start(tl.start, tl.initial)
    if initial["input_boolean.living_lights_asleep"] == "on":
        await set_toggle(holder.hass, "input_boolean.living_lights_asleep", True)
    snaps = await run(holder, tl)
    actual = doc["actual"]
    analyze(f"night_{doc['night']}", holder, tl, snaps, {
        "actual_asleep_transitions": actual["asleep_transitions"],
        "actual_turn_ons_00_08": actual["turn_on_count_00_08"],
        "input_edges": len(doc["events"]),
    })
