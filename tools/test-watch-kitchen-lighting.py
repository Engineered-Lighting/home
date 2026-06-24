#!/usr/bin/env python3
"""Pure-function tests for watch-kitchen-lighting.py.

These tests avoid live Home Assistant websocket/REST calls. They validate the
watcher's event filtering and diagnosis hints for manual override, asleep, and
Frigate/occupancy gaps.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
sys.modules.setdefault("websockets", SimpleNamespace(connect=None))
SPEC = importlib.util.spec_from_file_location(
    "watch_kitchen_lighting", HERE / "watch-kitchen-lighting.py"
)
wkl = importlib.util.module_from_spec(SPEC)
sys.modules["watch_kitchen_lighting"] = wkl
SPEC.loader.exec_module(wkl)


PASSES = 0
FAILS = 0


def assert_(condition: bool, label: str) -> None:
    global PASSES, FAILS
    if condition:
        PASSES += 1
        print(f"[OK] {label}")
    else:
        FAILS += 1
        print(f"[FAIL] {label}", file=sys.stderr)
        raise AssertionError(label)


def light_state(state: str, pct: float | None = None) -> dict:
    row = {"state": state}
    if pct is not None:
        row["brightness_pct"] = pct
    return row


def light_call(service: str = "turn_on", entity_id: str | list[str] = "light.sink", **service_data: object) -> dict:
    return {
        "event_type": "call_service",
        "data": {
            "domain": "light",
            "service": service,
            "service_data": service_data,
            "target": {"entity_id": entity_id},
        },
    }


def summary_text(
    final_snapshot: dict,
    light_samples: dict | None = None,
    frigate_events: list[dict] | None = None,
    call_services: list[dict] | None = None,
    manual_events: list[dict] | None = None,
) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        md_path = tmpdir / "summary.md"
        jsonl_path = tmpdir / "raw.jsonl"
        wkl.write_summary(
            md_path=md_path,
            jsonl_path=jsonl_path,
            duration_s=60,
            counters={},
            entity_changes={},
            final_snapshot=final_snapshot,
            light_samples=light_samples or {},
            occupancy_samples=[],
            frigate_events=frigate_events or [],
            call_services=call_services or [],
            manual_events=manual_events or [],
        )
        return md_path.read_text(encoding="utf-8")


def test_compact_state_and_lines() -> None:
    compact = wkl.compact_state(
        {
            "state": "on",
            "last_changed": "2026-06-23T00:00:00+00:00",
            "last_updated": "2026-06-23T00:00:01+00:00",
            "attributes": {
                "brightness": 128,
                "color_temp_kelvin": 2700,
                "predicted_brightness_pct": 80,
                "ignored": "drop",
            },
        }
    )
    assert_(compact["brightness_pct"] == 50.2, "compact_state derives brightness_pct")
    assert_(compact["color_temp_kelvin"] == 2700, "compact_state keeps useful light attrs")
    assert_("ignored" not in compact, "compact_state drops noisy attrs")
    assert_(wkl.compact_state(None) == {"missing": True}, "compact_state marks missing")
    line = wkl.light_line({"light.sink": light_state("on", 100)})
    assert_("sink=on 100%" in line, "light_line formats known light brightness")
    occ = wkl.occupancy_line({"binary_sensor.kitchen_motion": {"state": "on"}})
    assert_("kitchen_motion=on" in occ, "occupancy_line formats broad occupancy")


def test_event_filters_and_summaries() -> None:
    nested = {"target": {"entity_id": ["light.sink", {"other": "input_boolean.living_lights_asleep"}]}}
    assert_("light.sink" in wkl.nested_entity_ids(nested), "nested_entity_ids finds nested light")
    assert_("input_boolean.living_lights_asleep" in wkl.nested_entity_ids(nested), "nested_entity_ids finds nested helper")
    assert_(wkl.interesting_call_service({"domain": "light", "service_data": {}, "target": {}}), "all-light call is interesting")
    assert_(wkl.interesting_call_service({"domain": "light", "target": {"entity_id": "light.sink"}}), "kitchen light call is interesting")
    assert_(not wkl.interesting_call_service({"domain": "light", "target": {"entity_id": "light.bedroom"}}), "non-kitchen light call is ignored")
    assert_(wkl.interesting_call_service({"domain": "input_boolean", "target": {"entity_id": "input_boolean.living_lights_asleep"}}), "watched helper call is interesting")
    assert_(wkl.interesting_frigate_event({"after": {"camera": "kitchen", "label": "person"}}), "kitchen Frigate event is interesting")
    assert_(wkl.interesting_frigate_event({"after": {"current_zones": ["island_left"]}}), "kitchen zone Frigate event is interesting")
    assert_(not wkl.interesting_frigate_event({"after": {"camera": "driveway", "label": "car"}}), "unrelated Frigate event is ignored")

    state_summary = wkl.summarize_event_payload(
        {"event_type": "state_changed", "data": {"entity_id": "light.sink", "new_state": {"state": "on", "attributes": {"brightness": 255}}}}
    )
    assert_(state_summary["entity_id"] == "light.sink", "state_changed summary keeps entity id")
    assert_(state_summary["new_state"]["brightness_pct"] == 100.0, "state_changed summary compacts brightness")
    call_summary = wkl.summarize_event_payload({"event_type": "call_service", "data": {"domain": "light", "service": "turn_on", "target": {"entity_id": "light.sink"}}})
    assert_(call_summary["domain"] == "light" and call_summary["service"] == "turn_on", "call_service summary keeps domain/service")
    frigate_summary = wkl.summarize_event_payload({"event_type": "frigate_event", "data": {"after": {"camera": "kitchen", "label": "person", "entered_zones": ["sink"]}}})
    assert_(frigate_summary["camera"] == "kitchen" and frigate_summary["label"] == "person", "frigate summary lifts after camera/label")
    manual_summary = wkl.summarize_event_payload(
        {
            "event_type": "living_lights_manual_hold_armed",
            "data": {
                "zone": "sink",
                "light_entity": "light.sink",
                "debug_match_ok": "False",
                "raw_noise": "drop",
            },
        }
    )
    assert_(manual_summary["zone"] == "sink" and manual_summary["debug_match_ok"] == "False", "manual hold summary keeps diagnostics")
    assert_("raw_noise" not in manual_summary, "manual hold summary drops noisy raw payload")


def test_diagnosis_hints_occupancy_asleep_and_frigate() -> None:
    text = summary_text(
        {
            "binary_sensor.kitchen_motion": {"state": "on"},
            "binary_sensor.sink_person_occupancy": {"state": "off"},
            "binary_sensor.island_left_person_occupancy": {"state": "off"},
            "binary_sensor.island_right_person_occupancy": {"state": "off"},
            "binary_sensor.whole_kitchen_person_occupancy": {"state": "off"},
            "input_boolean.living_lights_asleep": {"state": "on"},
            "light.sink": light_state("off"),
            "light.island_left": light_state("off"),
            "light.island_right": light_state("off"),
        }
    )
    assert_("Broad kitchen occupancy/motion is on" in text, "diagnoses broad-vs-zone occupancy gap")
    assert_("Asleep is on while the kitchen zones are vacant" in text, "diagnoses asleep + vacant zone")
    assert_("No Frigate bus events were seen" in text, "diagnoses missing Frigate bus events")


def test_diagnosis_hints_high_command_did_not_stick() -> None:
    text = summary_text(
        {
            "input_boolean.living_lights_asleep": {"state": "on"},
            "light.sink": light_state("off"),
            "light.island_left": light_state("on", 20),
            "light.island_right": light_state("on", 15),
        },
        light_samples={"light.sink": [light_state("on", 100), light_state("off")]},
        frigate_events=[{"data": {"camera": "kitchen", "label": "person"}}],
        call_services=[light_call(brightness_pct=100)],
    )
    assert_("requested high brightness" in text, "diagnoses high brightness service call that did not stick")
    assert_("manual override hold/cooldown" in text, "names manual override hold/cooldown focus")
    assert_("Asleep was still on while brightness failed to stick" in text, "names asleep precedence focus")
    assert_("No living_lights_manual_hold_armed event was captured" in text, "diagnoses missing fast manual hold event")


def test_diagnosis_hints_later_low_cascade_overwrite() -> None:
    text = summary_text(
        {
            "input_boolean.living_lights_asleep": {"state": "on"},
            "light.sink": light_state("on", 30),
            "light.island_left": light_state("on", 30),
            "light.island_right": light_state("on", 30),
        },
        light_samples={"light.sink": [light_state("on", 100), light_state("on", 30)]},
        frigate_events=[{"data": {"camera": "kitchen", "label": "person"}}],
        call_services=[
            light_call(brightness_pct=100, entity_id="light.sink"),
            light_call(brightness_pct=30, entity_id="light.sink", color_temp_kelvin=2000, transition=10.0),
        ],
    )
    assert_("later low kitchen light.turn_on call followed the high user command" in text, "diagnoses later low cascade overwrite")
    assert_("automation/cascade write likely overwrote the manual command" in text, "names overwrite source")
    assert_("No living_lights_manual_hold_armed event was captured" in text, "later overwrite diagnosis notices missing hold event")


def test_manual_hold_event_is_reported_and_suppresses_missing_hold_hint() -> None:
    text = summary_text(
        {
            "input_boolean.living_lights_asleep": {"state": "on"},
            "light.sink": light_state("on", 30),
            "light.island_left": light_state("on", 30),
            "light.island_right": light_state("on", 30),
        },
        light_samples={"light.sink": [light_state("on", 100), light_state("on", 30)]},
        call_services=[light_call(brightness_pct=100, entity_id="light.sink")],
        manual_events=[
            {
                "event_type": "living_lights_manual_hold_armed",
                "utc": "2026-06-23T12:00:00+00:00",
                "data": {"zone": "sink", "light_entity": "light.sink", "debug_match_ok": "False"},
            }
        ],
    )
    assert_("## Manual Hold Events" in text, "summary includes manual hold section")
    assert_("living_lights_manual_hold_armed sink light.sink" in text, "summary reports manual hold event")
    assert_("No living_lights_manual_hold_armed event was captured" not in text, "hold event suppresses missing-hold diagnosis")


def test_diagnosis_hints_observed_drop_without_call() -> None:
    text = summary_text(
        {
            "input_boolean.living_lights_asleep": {"state": "off"},
            "light.sink": light_state("on", 10),
            "light.island_left": light_state("on", 80),
        },
        light_samples={"light.sink": [light_state("on", 75), light_state("on", 10)]},
        frigate_events=[{"data": {"camera": "kitchen", "label": "person"}}],
    )
    assert_("reached high brightness" in text, "diagnoses sampled brightness drop")
    assert_("which later service call or classifier transition won" in text, "points at event-order investigation")


def main() -> int:
    tests = [
        test_compact_state_and_lines,
        test_event_filters_and_summaries,
        test_diagnosis_hints_occupancy_asleep_and_frigate,
        test_diagnosis_hints_high_command_did_not_stick,
        test_diagnosis_hints_later_low_cascade_overwrite,
        test_manual_hold_event_is_reported_and_suppresses_missing_hold_hint,
        test_diagnosis_hints_observed_drop_without_call,
    ]
    for test in tests:
        test()
    print(f"{PASSES} pass . {FAILS} fail")
    return 0 if FAILS == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
