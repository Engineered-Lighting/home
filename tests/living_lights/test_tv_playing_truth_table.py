"""Story T fail-safe: binary_sensor.living_lights_tv_playing rendered through
Home Assistant's own template engine over every input combination.

Run: python3 -m unittest tests/living_lights/test_tv_playing_truth_table.py

The generated templates (the tv_playing state, its reason attribute, its
delay_off, and the publisher_fresh state) are pulled out of the checked-in
observability package and rendered by a helper process under the ha-sim
virtualenv (/home/marcelo-lima/.venvs/ha-sim) through
homeassistant.helpers.template.Template against a test Home Assistant state
machine, over lg_tv {on, off, standby, unavailable, unknown} x belief toggle
{on, off} x tv_watching {on, off, unavailable, missing} x heartbeat {fresh,
stale, unavailable, missing} x sofa stable {on, off} x the sensor's own
previous render {absent, lg_tv on, lg_tv off, unavailable 10 min ago,
unavailable 30 min 1 s ago, unavailable with no drop time}; the last axis is
what the sofa hold (refinement (a)) reads back through `this.attributes`.
The same helper then loads the two sensors as real trigger-based template
entities and drives lg_tv through an unavailable blip, an explicit off, a
dead publisher, a belief that says "not watching", and a seated viewer
through a long unavailable drop, so the delay_off hold and the 30-minute
sofa hold are proven and not assumed. The expected table is derived from the
rule the plan states (a belief may darken or route, never brighten an
occupied sofa; a stale publisher means the legacy rule; a seated viewer holds
an unavailable TV that was on for at most 30 minutes) and checked invariant
by invariant.

Skips cleanly when the ha-sim virtualenv is absent. Never contacts Home
Assistant; nothing here talks to the network.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import subprocess
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
OBS = REPO / "ha-config" / "packages" / "living_lights_observability.yaml"
VENV_PYTHON = Path("/home/marcelo-lima/.venvs/ha-sim/bin/python")
NOW = "2026-09-13T20:00:05+00:00"

TV_OFF_STATES = ("off", "standby", "unavailable", "unknown")
LG_TV_AXIS = ("on", "off", "standby", "unavailable", "unknown")
BELIEF_AXIS = (True, False)
WATCHING_AXIS = ("on", "off", "unavailable", "missing")
HEARTBEAT_AXIS = ("fresh", "stale", "unavailable", "missing")
SOFA_AXIS = (True, False)
# The sensor's own previous render: what `this.attributes` says about the
# lg_tv state it last saw and when lg_tv went unavailable from an on state.
PREV_AXIS = ("absent", "on", "off", "held", "expired", "carried_none")
PREV_ATTRS = {
    "absent": None,                                     # no state object yet
    "on": {"lg_tv": "on", "unavailable_since": None},   # the drop happens now
    "off": {"lg_tv": "off", "unavailable_since": None},  # unavailable after off: no hold
    "held": {"lg_tv": "unavailable", "unavailable_since": -600},     # 10 min into a drop
    "expired": {"lg_tv": "unavailable", "unavailable_since": -1801},  # one second past the hold
    "carried_none": {"lg_tv": "unavailable", "unavailable_since": None},
}
# Extra heartbeat shapes for the publisher_fresh sensor on its own.
FRESH_KINDS = {"fresh": True, "boundary_fresh": True, "future_fresh": True,
               "boundary_stale": False, "stale": False, "unavailable": False,
               "unknown": False, "garbage": False, "naive": False, "missing": False}

# The helper that runs under the ha-sim venv. Kept inline so this test owns
# its only dependency; it is the same recipe the simulator harness uses.
RENDERER = r'''
import asyncio
import datetime as dt
import json
import sys

import freezegun
from pytest_homeassistant_custom_component import patch_time
from pytest_homeassistant_custom_component.common import (async_fire_time_changed,
                                                          async_test_home_assistant)
from homeassistant.helpers.template import Template, TemplateStateFromEntityId
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

# The pytest plugin's freezegun fixes, so Home Assistant's utcnow and its loop
# timers follow the frozen clock the way they do in the simulator.
freezegun.api.FakeDate = patch_time.HAFakeDate
freezegun.api.datetime_to_fakedatetime = patch_time.ha_datetime_to_fakedatetime
freezegun.api.FakeDatetime = patch_time.HAFakeDatetime
freezegun.api._get_module_attributes = patch_time.ha_get_module_attributes
freezegun.api._get_module_attributes_hash = patch_time.ha_get_module_attributes_hash
freezegun.api._should_use_real_time = patch_time.ha_should_use_real_time
freezegun.api.get_current_time = patch_time.ha_get_current_time

LG_TV = "media_player.lg_tv"
BELIEF = "input_boolean.living_lights_actuate_from_belief_changes"
WATCHING = "binary_sensor.living_lights_tv_watching"
HEARTBEAT = "sensor.lighting_publisher_heartbeat"
FRESH = "binary_sensor.living_lights_publisher_fresh"
SOFA = "binary_sensor.living_room_sofa_person_occupancy_stable"
PLAYING = "binary_sensor.living_lights_tv_playing"


def heartbeat_value(kind, now):
    return {
        "fresh": (now - dt.timedelta(seconds=60)).isoformat(),
        "boundary_fresh": (now - dt.timedelta(seconds=180)).isoformat(),
        "boundary_stale": (now - dt.timedelta(seconds=181)).isoformat(),
        "future_fresh": (now + dt.timedelta(seconds=60)).isoformat(),
        "stale": (now - dt.timedelta(seconds=600)).isoformat(),
        "unavailable": "unavailable",
        "unknown": "unknown",
        "garbage": "not-a-time",
        "naive": (now - dt.timedelta(seconds=60)).replace(tzinfo=None).isoformat(),
        "missing": None,
    }[kind]


def render(hass, tpl):
    # `this` the way a trigger-based template entity sees it: its own state
    # object, or an unknown State with no attributes before the first render.
    variables = {"this": TemplateStateFromEntityId(hass, PLAYING)}
    return str(Template(tpl, hass).async_render(variables, parse_result=False)).strip()


def set_or_remove(hass, entity_id, value):
    if value is None:
        hass.states.async_remove(entity_id)
    else:
        hass.states.async_set(entity_id, value)


async def truth_table(hass, spec, now):
    out = {}
    for case in spec["cases"]:
        hass.states.async_set(LG_TV, case["lg_tv"])
        hass.states.async_set(BELIEF, "on" if case["belief"] else "off")
        set_or_remove(hass, WATCHING, None if case["tv_watching"] == "missing" else case["tv_watching"])
        set_or_remove(hass, HEARTBEAT, heartbeat_value(case["heartbeat"], now))
        hass.states.async_set(SOFA, "on" if case["sofa"] else "off")
        prev = case["prev"]
        if prev is None:
            hass.states.async_remove(PLAYING)
        else:
            attrs = dict(prev)
            if isinstance(attrs["unavailable_since"], int):
                attrs["unavailable_since"] = (now + dt.timedelta(seconds=attrs["unavailable_since"])).isoformat()
            hass.states.async_set(PLAYING, "on", attrs)
        fresh = render(hass, spec["publisher_fresh"])
        hass.states.async_set(FRESH, "on" if fresh == "True" else "off")
        out[case["id"]] = {
            "fresh": fresh,
            "playing": render(hass, spec["tv_playing"]["state"]),
            "reason": render(hass, spec["tv_playing"]["reason"]),
            "delay_off": render(hass, spec["tv_playing"]["delay_off"]),
            "since": render(hass, spec["tv_playing"]["since"]),
        }
    return out


async def timeline(hass, frz, spec, now):
    for eid in (LG_TV, BELIEF, WATCHING, HEARTBEAT, SOFA, FRESH, PLAYING):
        hass.states.async_remove(eid)
    hass.states.async_set(LG_TV, "off")
    hass.states.async_set(BELIEF, "off")
    hass.states.async_set(SOFA, "off")
    assert await async_setup_component(hass, "template", {"template": spec["blocks"]})
    await hass.async_block_till_done()
    clock = {"now": now}
    rows = []

    async def step(seconds, label):
        clock["now"] = clock["now"] + dt.timedelta(seconds=seconds)
        frz.move_to(clock["now"])
        async_fire_time_changed(hass, clock["now"])
        await hass.async_block_till_done()
        playing = hass.states.get(PLAYING)
        fresh = hass.states.get(FRESH)
        rows.append({"label": label, "t": clock["now"].isoformat(),
                     "playing": playing.state if playing else None,
                     "reason": playing.attributes.get("reason") if playing else None,
                     "since": playing.attributes.get("unavailable_since") if playing else None,
                     "fresh": fresh.state if fresh else None})

    hass.states.async_set(LG_TV, "on"); await step(1, "on")
    hass.states.async_set(LG_TV, "unavailable"); await step(1, "blip+1s")
    await step(60, "blip+61s")
    await step(35, "blip+96s")
    hass.states.async_set(LG_TV, "on"); await step(1, "on again")
    hass.states.async_set(LG_TV, "unavailable"); await step(30, "short blip+30s")
    hass.states.async_set(LG_TV, "on"); await step(1, "on after short blip")
    hass.states.async_set(LG_TV, "off"); await step(1, "explicit off+1s")
    hass.states.async_set(LG_TV, "on"); await step(1, "on before standby")
    hass.states.async_set(LG_TV, "standby"); await step(1, "standby+1s")
    hass.states.async_set(LG_TV, "on")
    hass.states.async_set(BELIEF, "on")
    hass.states.async_set(WATCHING, "on")
    hass.states.async_set(HEARTBEAT, dt_util.now().isoformat()); await step(1, "belief live on")
    hass.states.async_set(LG_TV, "unavailable")
    for i in range(12):
        hass.states.async_set(HEARTBEAT, dt_util.now().isoformat())
        await step(60, "bridged blip minute %d" % (i + 1))
    for i in range(5):
        await step(60, "publisher silent minute %d" % (i + 1))
    hass.states.async_set(LG_TV, "on"); await step(1, "on, publisher dead")
    hass.states.async_set(HEARTBEAT, dt_util.now().isoformat()); await step(1, "heartbeat back")
    hass.states.async_set(WATCHING, "off"); await step(1, "belief says off, sofa empty")
    hass.states.async_set(SOFA, "on"); await step(1, "sofa occupied")
    # Refinement (a): a seated viewer holds an unavailable TV that was on.
    hass.states.async_set(BELIEF, "off"); await step(1, "legacy, sofa seated")
    hass.states.async_set(LG_TV, "unavailable"); await step(1, "sofa hold+1s")
    for i in range(1, 35):
        await step(60, "sofa hold minute %d" % i)
    hass.states.async_set(LG_TV, "on"); await step(1, "on after sofa hold")
    hass.states.async_set(LG_TV, "unavailable"); await step(1, "second sofa hold+1s")
    await step(300, "second sofa hold+5min")
    hass.states.async_set(SOFA, "off"); await step(1, "sofa left+1s")
    await step(95, "sofa left+96s")
    hass.states.async_set(SOFA, "on"); await step(1, "sofa back, still unavailable")
    hass.states.async_set(LG_TV, "on"); await step(1, "on before real off")
    hass.states.async_set(LG_TV, "off"); await step(1, "real off, sofa seated")
    hass.states.async_set(LG_TV, "unavailable"); await step(1, "unavailable from off, sofa seated")
    await step(120, "unavailable from off+2min")
    return rows


async def main():
    spec = json.load(sys.stdin)
    now = dt.datetime.fromisoformat(spec["now"])
    with freezegun.freeze_time(now) as frz:
        async with async_test_home_assistant() as hass:
            hass.loop.slow_callback_duration = 1e9
            result = {"cases": await truth_table(hass, spec, now)}
            result["timeline"] = await timeline(hass, frz, spec, now)
    json.dump(result, sys.stdout)


asyncio.run(main())
'''


def _sensor(doc: dict, unique_id: str) -> dict:
    for block in doc["template"]:
        for sensor in block.get("binary_sensor", []):
            if sensor.get("unique_id") == unique_id:
                return sensor
    raise KeyError(unique_id)


def _blocks(doc: dict) -> list:
    return [b for b in doc["template"]
            if any(s.get("unique_id") in ("living_lights_tv_playing", "living_lights_publisher_fresh")
                   for s in b.get("binary_sensor", []))]


def case_id(lg_tv: str, belief: bool, watching: str, heartbeat: str, sofa: bool, prev: str = "absent") -> str:
    return (f"tv={lg_tv} belief={'on' if belief else 'off'} watching={watching} hb={heartbeat}"
            f" sofa={'on' if sofa else 'off'} prev={prev}")


def legacy_rule(lg_tv: str) -> bool:
    """What the classifier did before M2: the TV plays unless it reads off."""
    return lg_tv not in TV_OFF_STATES


def expected_hold(lg_tv: str, sofa: bool, prev: str) -> bool:
    """Refinement (a): lg_tv unavailable/unknown, the sofa seated, and the
    drop from an on state at most 30 minutes ago (just now when the previous
    render saw the set on; the remembered time when it already saw the drop)."""
    return lg_tv in ("unavailable", "unknown") and sofa and prev in ("on", "held")


def expected_playing(lg_tv: str, belief: bool, watching: str, fresh: bool, sofa: bool,
                     prev: str = "absent") -> bool:
    """The plan's rule, spelled out once here and asserted invariant by
    invariant below (so a wrong rule and a wrong template cannot agree)."""
    live = belief and fresh
    bridged = lg_tv in ("unavailable", "unknown") and live and watching == "on"
    seen_on = legacy_rule(lg_tv) or bridged or expected_hold(lg_tv, sofa, prev)
    return seen_on and (not belief or not fresh or watching != "off" or sofa)


def expected_reason(lg_tv: str, belief: bool, watching: str, fresh: bool, sofa: bool,
                    prev: str = "absent") -> str:
    live = belief and fresh
    bridged = lg_tv in ("unavailable", "unknown") and live and watching == "on"
    hold = expected_hold(lg_tv, sofa, prev)
    seen_on = legacy_rule(lg_tv) or bridged or hold
    if not seen_on and lg_tv in ("unavailable", "unknown") and live:
        return "unavailable_not_watching"
    if not seen_on and lg_tv in ("unavailable", "unknown"):
        return "unavailable"
    if not seen_on:
        return "tv_off"
    if bridged:
        return "belief_bridges_unavailable"
    if hold:
        return "sofa_holds_unavailable"
    if not belief:
        return "legacy"
    if not fresh:
        return "publisher_stale"
    if watching != "off":
        return "belief_watching"
    if sofa:
        return "sofa_occupied"
    return "belief_unattended"


class _Rendered:
    """Runs the helper once per test module; every test reads the result."""
    result: dict | None = None
    cases: list[dict] = []

    @classmethod
    def load(cls) -> dict:
        if cls.result is not None:
            return cls.result
        doc = yaml.safe_load(OBS.read_text(encoding="utf-8"))
        playing = _sensor(doc, "living_lights_tv_playing")
        fresh = _sensor(doc, "living_lights_publisher_fresh")
        cases = []
        for lg_tv, belief, watching, hb, sofa, prev in itertools.product(
                LG_TV_AXIS, BELIEF_AXIS, WATCHING_AXIS, HEARTBEAT_AXIS, SOFA_AXIS, PREV_AXIS):
            cases.append({"id": case_id(lg_tv, belief, watching, hb, sofa, prev), "lg_tv": lg_tv,
                          "belief": belief, "tv_watching": watching, "heartbeat": hb, "sofa": sofa,
                          "prev": PREV_ATTRS[prev]})
        for kind in FRESH_KINDS:
            cases.append({"id": f"fresh-kind={kind}", "lg_tv": "on", "belief": True,
                          "tv_watching": "off", "heartbeat": kind, "sofa": False, "prev": None})
        spec = {"now": NOW, "publisher_fresh": fresh["state"],
                "tv_playing": {"state": playing["state"], "reason": playing["attributes"]["reason"],
                               "delay_off": playing["delay_off"],
                               "since": playing["attributes"]["unavailable_since"]},
                "cases": cases, "blocks": _blocks(doc)}
        proc = subprocess.run([str(VENV_PYTHON), "-c", RENDERER], input=json.dumps(spec),
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise AssertionError("renderer failed under the ha-sim venv:\n" + proc.stderr[-4000:])
        cls.result = json.loads(proc.stdout)
        cls.cases = cases
        return cls.result


class TvPlayingTruthTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not VENV_PYTHON.exists():
            raise unittest.SkipTest(f"ha-sim venv absent: {VENV_PYTHON}")
        cls.rendered = _Rendered.load()
        cls.rows = {c["id"]: cls.rendered["cases"][c["id"]] for c in _Rendered.cases}

    def _grid(self):
        for lg_tv, belief, watching, hb, sofa, prev in itertools.product(
                LG_TV_AXIS, BELIEF_AXIS, WATCHING_AXIS, HEARTBEAT_AXIS, SOFA_AXIS, PREV_AXIS):
            row = self.rows[case_id(lg_tv, belief, watching, hb, sofa, prev)]
            yield lg_tv, belief, watching, hb == "fresh", sofa, prev, row

    def test_every_render_is_a_clean_boolean(self):
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            with self.subTest(case=(lg_tv, belief, watching, fresh, sofa, prev)):
                self.assertIn(row["playing"], ("True", "False"))
                self.assertIn(row["fresh"], ("True", "False"))
                self.assertEqual(row["fresh"] == "True", fresh)

    def test_full_table_matches_the_rule(self):
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            with self.subTest(case=(lg_tv, belief, watching, fresh, sofa, prev)):
                self.assertEqual(row["playing"] == "True",
                                 expected_playing(lg_tv, belief, watching, fresh, sofa, prev))
                self.assertEqual(row["reason"], expected_reason(lg_tv, belief, watching, fresh, sofa, prev))

    def test_belief_toggle_off_is_the_legacy_rule_plus_the_sofa_hold(self):
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            if belief:
                continue
            with self.subTest(case=(lg_tv, watching, fresh, sofa, prev)):
                self.assertEqual(row["playing"] == "True",
                                 legacy_rule(lg_tv) or expected_hold(lg_tv, sofa, prev))
                self.assertIn(row["reason"], ("legacy", "tv_off", "unavailable", "sofa_holds_unavailable"))

    def test_dead_publisher_is_the_legacy_rule_plus_the_sofa_hold(self):
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            if fresh:
                continue
            with self.subTest(case=(lg_tv, belief, watching, sofa, prev)):
                self.assertEqual(row["playing"] == "True",
                                 legacy_rule(lg_tv) or expected_hold(lg_tv, sofa, prev))

    def test_a_belief_never_brightens_an_occupied_sofa(self):
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            if not (sofa and legacy_rule(lg_tv)):
                continue
            with self.subTest(case=(lg_tv, belief, watching, fresh, prev)):
                self.assertEqual(row["playing"], "True")

    def test_a_belief_never_invents_a_tv(self):
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            if lg_tv not in ("off", "standby"):
                continue
            with self.subTest(case=(lg_tv, belief, watching, fresh, sofa, prev)):
                self.assertEqual(row["playing"], "False")
                self.assertEqual(row["reason"], "tv_off")

    def test_live_belief_only_darkens_or_routes(self):
        """The two and only two places a live belief changes the legacy
        answer: it keeps the room dark through an unavailable blip (darken),
        and it releases an unattended TV when the sofa is empty (route)."""
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            live = belief and fresh
            if not live:
                continue
            actual = row["playing"] == "True"
            with self.subTest(case=(lg_tv, watching, sofa, prev)):
                if lg_tv in ("unavailable", "unknown") and watching == "on":
                    self.assertTrue(actual)
                    self.assertEqual(row["reason"], "belief_bridges_unavailable")
                elif lg_tv == "on" and watching == "off" and not sofa:
                    self.assertFalse(actual)
                    self.assertEqual(row["reason"], "belief_unattended")
                else:
                    self.assertEqual(actual, legacy_rule(lg_tv) or expected_hold(lg_tv, sofa, prev))

    def test_tv_watching_unavailable_or_missing_is_treated_as_not_off(self):
        """A belief entity that has vanished must not release the TV."""
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            if not (belief and fresh and lg_tv == "on" and watching in ("unavailable", "missing")):
                continue
            with self.subTest(case=(watching, sofa, prev)):
                self.assertEqual(row["playing"], "True")
                self.assertEqual(row["reason"], "belief_watching")

    def test_delay_off_is_90s_only_while_lg_tv_reads_unavailable_or_unknown(self):
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            with self.subTest(case=(lg_tv, prev)):
                self.assertEqual(row["delay_off"], "90" if lg_tv in ("unavailable", "unknown") else "0")

    def test_sofa_hold_needs_a_seated_viewer_a_set_that_was_on_and_thirty_minutes(self):
        """Refinement (a), the stateless half: the hold is a term of tv_seen_on
        only for lg_tv unavailable/unknown, only with the sofa stable sensor
        on, only when the previous render saw the set on (the drop is now) or
        already remembered a drop at most 30 minutes old; never after an
        explicit off, never without a remembered drop time, and it changes
        nothing while lg_tv reads on, off or standby."""
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            actual = row["playing"] == "True"
            with self.subTest(case=(lg_tv, belief, watching, fresh, sofa, prev)):
                if lg_tv not in ("unavailable", "unknown"):
                    base = self.rows[case_id(lg_tv, belief, watching, "fresh" if fresh else "stale", sofa, "absent")]
                    self.assertEqual((row["playing"], row["reason"]), (base["playing"], base["reason"]))
                    continue
                bridged = belief and fresh and watching == "on"
                if not sofa or prev in ("absent", "off", "expired", "carried_none"):
                    self.assertEqual(actual, bridged)
                    self.assertNotEqual(row["reason"], "sofa_holds_unavailable")
                else:
                    self.assertTrue(actual)
                    self.assertEqual(row["reason"], "belief_bridges_unavailable" if bridged else "sofa_holds_unavailable")

    def test_unavailable_since_attribute_is_set_on_the_drop_carried_and_cleared(self):
        now = dt.datetime.fromisoformat(NOW)
        for lg_tv, belief, watching, fresh, sofa, prev, row in self._grid():
            with self.subTest(case=(lg_tv, prev, sofa)):
                since = row["since"]
                if lg_tv not in ("unavailable", "unknown") or prev in ("absent", "off", "carried_none"):
                    self.assertEqual(since, "None")
                    continue
                # a datetime, aware, equal to the drop: now for a set that was
                # on, the remembered time (even an expired one) otherwise
                parsed = dt.datetime.fromisoformat(since)
                self.assertIsNotNone(parsed.tzinfo)
                offset = {"on": 0, "held": -600, "expired": -1801}[prev]
                self.assertEqual(parsed, now + dt.timedelta(seconds=offset))

    def test_publisher_fresh_heartbeat_shapes(self):
        for kind, want in FRESH_KINDS.items():
            row = self.rows[f"fresh-kind={kind}"]
            with self.subTest(kind=kind):
                self.assertEqual(row["fresh"] == "True", want)
                # belief on, tv on, watching off, sofa empty: fresh releases,
                # anything else falls back to the legacy answer (on).
                self.assertEqual(row["playing"], "False" if want else "True")
                self.assertEqual(row["reason"], "belief_unattended" if want else "publisher_stale")


class TvPlayingTimelineTests(unittest.TestCase):
    """The generated blocks as real trigger template entities under a frozen
    clock: the unavailable hold, the instant release on explicit off, the
    belief bridge through a 12-minute blip, a publisher that goes silent."""

    @classmethod
    def setUpClass(cls):
        if not VENV_PYTHON.exists():
            raise unittest.SkipTest(f"ha-sim venv absent: {VENV_PYTHON}")
        cls.rows = {r["label"]: r for r in _Rendered.load()["timeline"]}

    def _row(self, label: str) -> dict:
        self.assertIn(label, self.rows)
        return self.rows[label]

    def test_unavailable_blip_holds_on_for_90s_then_releases(self):
        self.assertEqual(self._row("on")["playing"], "on")
        self.assertEqual(self._row("blip+1s")["playing"], "on")
        self.assertEqual(self._row("blip+1s")["reason"], "unavailable")
        self.assertEqual(self._row("blip+61s")["playing"], "on")
        self.assertEqual(self._row("blip+96s")["playing"], "off")

    def test_short_blip_never_drops(self):
        self.assertEqual(self._row("short blip+30s")["playing"], "on")
        self.assertEqual(self._row("on after short blip")["playing"], "on")
        self.assertEqual(self._row("on after short blip")["reason"], "legacy")

    def test_explicit_off_and_standby_release_at_once(self):
        self.assertEqual(self._row("explicit off+1s")["playing"], "off")
        self.assertEqual(self._row("on before standby")["playing"], "on")
        self.assertEqual(self._row("standby+1s")["playing"], "off")

    def test_live_belief_bridges_a_twelve_minute_blip(self):
        self.assertEqual(self._row("belief live on")["fresh"], "on")
        self.assertEqual(self._row("belief live on")["reason"], "belief_watching")
        for i in range(1, 13):
            row = self._row(f"bridged blip minute {i}")
            self.assertEqual((row["playing"], row["reason"], row["fresh"]),
                             ("on", "belief_bridges_unavailable", "on"), row)

    def test_silent_publisher_is_noticed_by_the_minute_tick(self):
        # 180 s after the last heartbeat the freshness sensor drops on its
        # own tick (the heartbeat entity never changes), and the bridge goes
        # with it after the 90 s unavailable hold.
        self.assertEqual(self._row("publisher silent minute 1")["fresh"], "on")
        self.assertEqual(self._row("publisher silent minute 3")["fresh"], "off")
        self.assertEqual(self._row("publisher silent minute 5")["fresh"], "off")
        self.assertEqual(self._row("publisher silent minute 5")["playing"], "off")
        self.assertEqual(self._row("on, publisher dead")["playing"], "on")
        self.assertEqual(self._row("on, publisher dead")["reason"], "publisher_stale")

    def test_belief_release_and_sofa_veto(self):
        self.assertEqual(self._row("heartbeat back")["reason"], "belief_watching")
        self.assertEqual(self._row("belief says off, sofa empty")["playing"], "off")
        self.assertEqual(self._row("belief says off, sofa empty")["reason"], "belief_unattended")
        self.assertEqual(self._row("sofa occupied")["playing"], "on")
        self.assertEqual(self._row("sofa occupied")["reason"], "sofa_occupied")

    def test_seated_viewer_holds_a_long_unavailable_drop_for_thirty_minutes(self):
        """Refinement (a) as a real entity: the drop time is remembered in the
        sensor's own attribute across the minute ticks, the hold survives
        well past the 90 s delay_off, and it ends 30 min after the drop."""
        self.assertEqual(self._row("legacy, sofa seated")["reason"], "legacy")
        self.assertEqual(self._row("legacy, sofa seated")["since"], None)
        first = self._row("sofa hold+1s")
        self.assertEqual((first["playing"], first["reason"]), ("on", "sofa_holds_unavailable"))
        self.assertIsNotNone(first["since"])
        drop = dt.datetime.fromisoformat(first["since"])
        for i in range(1, 30):
            row = self._row(f"sofa hold minute {i}")
            self.assertEqual((row["playing"], row["reason"]), ("on", "sofa_holds_unavailable"), row)
            self.assertEqual(dt.datetime.fromisoformat(row["since"]), drop, row)
        self.assertEqual(self._row("sofa hold minute 30")["playing"], "on")
        # past the hold the state template releases; delay_off adds 90 s
        self.assertEqual(self._row("sofa hold minute 34")["playing"], "off")
        self.assertEqual(self._row("sofa hold minute 34")["reason"], "unavailable")
        self.assertEqual(dt.datetime.fromisoformat(self._row("sofa hold minute 34")["since"]), drop)
        self.assertEqual(self._row("on after sofa hold")["reason"], "legacy")
        self.assertEqual(self._row("on after sofa hold")["since"], None)

    def test_sofa_hold_ends_when_the_viewer_leaves_and_resumes_when_they_return(self):
        self.assertEqual(self._row("second sofa hold+1s")["reason"], "sofa_holds_unavailable")
        self.assertEqual(self._row("second sofa hold+5min")["playing"], "on")
        self.assertEqual(self._row("second sofa hold+5min")["reason"], "sofa_holds_unavailable")
        # the sofa empties: the state releases at once, delay_off holds 90 s
        self.assertEqual(self._row("sofa left+1s")["reason"], "unavailable")
        self.assertEqual(self._row("sofa left+1s")["playing"], "on")
        self.assertEqual(self._row("sofa left+96s")["playing"], "off")
        # back within 30 min of the same drop: the remembered time still holds
        back = self._row("sofa back, still unavailable")
        self.assertEqual((back["playing"], back["reason"]), ("on", "sofa_holds_unavailable"))

    def test_sofa_hold_never_follows_an_explicit_off(self):
        self.assertEqual(self._row("on before real off")["reason"], "legacy")
        off = self._row("real off, sofa seated")
        self.assertEqual((off["playing"], off["reason"], off["since"]), ("off", "tv_off", None))
        after = self._row("unavailable from off, sofa seated")
        self.assertEqual((after["playing"], after["reason"], after["since"]), ("off", "unavailable", None))
        self.assertEqual(self._row("unavailable from off+2min")["playing"], "off")


if __name__ == "__main__":
    unittest.main()
