#!/usr/bin/env python3
"""Pure local tests for the workflow-scenario model.

The live W suite still requires Home Assistant, metrics-sidecar, and an
agent/model run. These tests only pin the local scenario definitions and
cross-cutting safety invariants that the live runner consumes.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import workflow_scenarios as ws  # noqa: E402


passes = 0
fails = 0
failures: list[tuple[str, str]] = []


def t(name: str, fn) -> None:
    global passes, fails
    try:
        fn()
        passes += 1
        print(f"  PASS  {name}")
    except AssertionError as exc:
        fails += 1
        failures.append((name, str(exc)))
        print(f"  FAIL  {name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        fails += 1
        failures.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")


def service_args(entity_id: str | None = "light.office", area_id: str | None = None,
                 domain: str = "light", service: str = "turn_on") -> dict:
    service_data: dict = {}
    if entity_id is not None:
        service_data["entity_id"] = entity_id
    if area_id is not None:
        service_data["area_id"] = area_id
    return {"list": [{"domain": domain, "service": service, "service_data": service_data}]}


def tool_call(name: str, args: dict | None) -> dict:
    return {"function": {"name": name, "arguments_parsed": args}}


print("\nWorkflow scenario contracts\n")


def test_registry_shape_and_filters() -> None:
    scenarios = ws.all_scenarios()
    ids = [scenario.id for scenario in scenarios]
    assert len(scenarios) == 20, f"expected 20 workflow scenarios, got {len(scenarios)}"
    assert len(ids) == len(set(ids)), f"duplicate scenario ids: {ids}"
    assert len(ws.scenarios_for(phase=1)) == 8, "phase 1 scenario count drifted"
    assert len(ws.scenarios_for(phase=2)) == 7, "phase 2 scenario count drifted"
    assert len(ws.scenarios_for(phase=3)) == 4, "phase 3 scenario count drifted"
    assert len(ws.scenarios_for(phase=4)) == 1, "phase 4 travel-mode scenario count drifted"
    assert [s.id for s in ws.scenarios_for(only_ids=["office_relax_vibe"])] == [
        "office_relax_vibe"
    ], "only_ids filter failed"
    assert {"office_relax_vibe", "refusal_no_entity", "perception_branching", "travel_mode_blocks_light_on"}.issubset(set(ids)), \
        "expected stretch/safety scenarios missing"


t("workflow scenario registry shape is stable", test_registry_shape_and_filters)


def test_write_gated_filter_keeps_default_live_suite_read_only() -> None:
    read_only = ws.scenarios_for(include_write_gated=False)
    write_gated = [scenario for scenario in ws.all_scenarios() if ws.scenario_is_write_gated(scenario)]
    assert read_only, "read-only suite must not be empty"
    assert write_gated, "write-gated suite must not be empty"
    assert all(not ws.scenario_is_write_gated(scenario) for scenario in read_only), \
        "default workflow suite must exclude mutating scenarios"
    assert all(s.safety_level == ws.SAFETY_READ_ONLY for s in read_only), \
        "read-only suite should contain only read-only scenarios"
    assert {s.id for s in write_gated}.issuperset({
        "office_dim_warm",
        "office_focus_vibe",
        "mute_blocks_response",
        "travel_mode_blocks_light_on",
    }), "expected write-gated scenarios are not labeled"
    assert ws.scenarios_for(only_ids=["office_dim_warm"], include_write_gated=False) == [], \
        "explicit write scenario should still require include_write_gated"


t("write-gated filter keeps the default live suite read-only",
  test_write_gated_filter_keeps_default_live_suite_read_only)


def test_fresh_visual_scenarios_accept_current_look_tool() -> None:
    visual_ids = {
        "find_then_describe",
        "perception_refresh_then_describe",
        "compound_multi_clause",
        "perception_branching",
    }
    scenarios = {scenario.id: scenario for scenario in ws.all_scenarios()}
    for scenario_id in visual_ids:
        scenario = scenarios[scenario_id]
        anyof_entries = [entry for entry in scenario.required_tools if isinstance(entry, ws.AnyOf)]
        assert anyof_entries, f"{scenario_id} should have a visual AnyOf assertion"
        names = {
            option.name
            for entry in anyof_entries
            for option in entry.options
        }
        assert "look" in names, f"{scenario_id} should accept the current look tool"


t("fresh visual scenarios accept the current look tool",
  test_fresh_visual_scenarios_accept_current_look_tool)


def _contract_text(contract) -> str:
    return " ".join(
        [
            contract.query,
            contract.category,
            *contract.required_checks,
            *contract.expected_actions,
            *contract.acceptable_fallbacks,
            *contract.forbidden_outcomes,
            contract.remaining_live_validation,
        ]
    ).lower()


def test_doc_planning_contract_catalog_covers_s58_to_s68() -> None:
    expected = {f"DOC-S{i}" for i in range(58, 69)}
    contracts = ws.doc_planning_contracts()
    ids = {contract.doc_id for contract in contracts}
    assert ids == expected, f"planning contract ids drifted: {sorted(ids)}"
    assert [contract.doc_id for contract in ws.doc_planning_contracts(["DOC-S66"])] == ["DOC-S66"], \
        "doc_planning_contracts filter failed"
    live_runner_ids = {scenario.id for scenario in ws.all_scenarios()}
    assert ids.isdisjoint(live_runner_ids), \
        "local-only DOC planning contracts must not be returned by all_scenarios()"
    for contract in contracts:
        assert contract.query.strip(), f"{contract.doc_id} missing query"
        assert contract.required_checks, f"{contract.doc_id} missing required checks"
        assert contract.expected_actions or contract.acceptable_fallbacks, \
            f"{contract.doc_id} must define actions or a graceful fallback"
        assert contract.forbidden_outcomes, f"{contract.doc_id} missing forbidden outcomes"
        assert "live/non-production" in contract.remaining_live_validation, \
            f"{contract.doc_id} must keep live validation caveat"


t("DOC-S58 through DOC-S68 planning contracts are complete and local-only",
  test_doc_planning_contract_catalog_covers_s58_to_s68)


def test_multi_step_planning_contracts_pin_required_scene_guards() -> None:
    by_id = {contract.doc_id: contract for contract in ws.doc_planning_contracts()}
    s58 = _contract_text(by_id["DOC-S58"])
    assert "movie" in s58 and "homeai_movie" in s58 and "pause media_player.living_room" in s58, \
        "S58 must pin movie mode, media pause, and movie helper behavior"
    s59 = _contract_text(by_id["DOC-S59"])
    assert "homeai_sleep" in s59 and "pause active sonos" in s59 and "hallucinate lock" in s59, \
        "S59 must pin sleep helper, media pause, and no lock hallucination"
    s60 = _contract_text(by_id["DOC-S60"])
    assert "morning_time_window" in s60 and "70-90 percent" in s60 and "asleep mode" in s60, \
        "S60 must pin morning window, bright neutral lights, and sleep unlatch"
    s61 = _contract_text(by_id["DOC-S61"])
    assert "co_resident" in s61 and "decline" in s61 and "guest is present" in s61, \
        "S61 must pin co-resident guard and decline branch"
    s62 = _contract_text(by_id["DOC-S62"])
    assert "host" in s62 and "at least 70 percent" in s62 and "volume too loud" in s62, \
        "S62 must pin host-scene light/media/volume bounds"


t("multi-step planning contracts pin scene-specific guards",
  test_multi_step_planning_contracts_pin_required_scene_guards)


def test_abstract_intent_contracts_pin_required_inference_guards() -> None:
    by_id = {contract.doc_id: contract for contract in ws.doc_planning_contracts()}
    s63 = _contract_text(by_id["DOC-S63"])
    assert "thermostat" in s63 and "2f" in s63 and "non-device alternative" in s63, \
        "S63 must pin thermostat action plus no-thermostat fallback"
    s64 = _contract_text(by_id["DOC-S64"])
    assert "current_room" in s64 and "bright neutral" in s64 and "hallucinated cover" in s64, \
        "S64 must pin current-room light action and optional cover guard"
    s65 = _contract_text(by_id["DOC-S65"])
    assert "30 percent" in s65 and "2700k" in s65 and "over-darken" in s65, \
        "S65 must pin wind-down light/music bounds"
    s66 = _contract_text(by_id["DOC-S66"])
    assert "current_room" in s66 and "30 percent" in s66 and "without muting" in s66, \
        "S66 must pin current-room volume reduction without mute"
    s67 = _contract_text(by_id["DOC-S67"])
    assert "cover_domain_availability" in s67 and "not wired" in s67 and "hallucinate cover" in s67, \
        "S67 must pin no-blinds graceful failure"
    s68 = _contract_text(by_id["DOC-S68"])
    assert "20-40 percent" in s68 and "2700k" in s68 and "non-contextual brightness" in s68, \
        "S68 must pin cozy ambience bounds"


t("abstract intent contracts pin inference and fallback guards",
  test_abstract_intent_contracts_pin_required_inference_guards)


def test_safe_entity_guard_allows_only_allowlisted_targets() -> None:
    assert ws.mutates_unsafe(service_args("light.office")) is False, \
        "allowlisted light.office should be safe"
    assert ws.mutates_unsafe(service_args(entity_id=None, area_id="office")) is False, \
        "allowlisted office area should be safe"
    assert ws.mutates_unsafe(service_args(entity_id=None, area_id=None)) is True, \
        "untargeted service call should be unsafe"
    assert ws.mutates_unsafe(service_args("light.kitchen")) is True, \
        "non-allowlisted light should be unsafe"
    assert ws.mutates_unsafe(service_args("cover.living_room_blinds", domain="cover", service="open_cover")) is True, \
        "hallucinated cover entity should be unsafe"


t("safe entity guard permits only allowlisted targets",
  test_safe_entity_guard_allows_only_allowlisted_targets)


def test_travel_mode_guard_blocks_energizing_service_calls() -> None:
    light_on = service_args("light.office", domain="light", service="turn_on")
    light_toggle = service_args("light.office", domain="light", service="toggle")
    switch_on = service_args("switch.test_plug", domain="switch", service="turn_on")
    light_off = service_args("light.office", domain="light", service="turn_off")
    media = service_args("media_player.living_room", domain="media_player", service="media_pause")
    assert ws.any_light_or_switch_on(light_on) is True, "light.turn_on should be blocked"
    assert ws.any_light_or_switch_on(light_toggle) is True, "light.toggle should be blocked"
    assert ws.any_light_or_switch_on(switch_on) is True, "switch.turn_on should be blocked"
    assert ws.any_light_or_switch_on(light_off) is False, "light.turn_off should remain allowed"
    assert ws.any_light_or_switch_on(media) is False, "media controls are not light energizing"

    travel = ws.scenarios_for(only_ids=["travel_mode_blocks_light_on"])[0]
    assert travel.safety_level == ws.SAFETY_TRAVEL_MODE, "travel scenario must be explicitly travel-gated"
    assert travel.setup_state and travel.teardown_state, "travel scenario must restore helper state"
    entities = ws.preflight_entities_for([travel])
    assert ws.TRAVEL_MODE_ENTITY in entities, "travel helper should be preflighted"
    assert not travel.forbidden_tools, \
        "tool-layer TravelModeBlocked is the safety boundary; live scenario validates block speech"
    assert travel.speech_must_match, "travel scenario must require block/refusal speech"
    assert travel.speech_must_not_match, "travel scenario must reject success phrasing"


t("travel mode guard blocks energizing light and switch service calls",
  test_travel_mode_guard_blocks_energizing_service_calls)


def test_live_runner_preserves_stateful_helper_state() -> None:
    runner = (TOOLS_DIR / "diagnose-identity.py").read_text(encoding="utf-8")
    assert "async def _snapshot_service_entities" in runner, \
        "live runner should snapshot helper states before scenario setup"
    assert "async def _restore_service_entity_snapshot" in runner, \
        "live runner should restore the original helper states after attempts"
    assert "list(sc.setup_state or []) + list(sc.teardown_state or [])" in runner, \
        "runner should snapshot every helper touched by setup or teardown"
    assert "await _restore_service_entity_snapshot(cfg, state_snapshot)" in runner, \
        "runner should prefer restoring the starting state over static teardown"


t("live runner preserves stateful helper state",
  test_live_runner_preserves_stateful_helper_state)


def test_cross_cutting_invariants_catch_planner_failures() -> None:
    assert ws.inv_non_empty_speech([], "done")[0] is True, "normal speech should pass"
    assert ws.inv_non_empty_speech([], "")[0] is False, "empty speech should fail"
    assert ws.inv_speech_length_cap([], "x" * 2001)[0] is False, "long speech should fail"
    assert ws.inv_parseable_args([tool_call("execute_services", None)], "")[0] is False, \
        "None arguments_parsed should fail"
    hallucinated = [
        tool_call(
            "execute_services",
            service_args("cover.living_room_blinds", domain="cover", service="open_cover"),
        )
    ]
    assert ws.inv_no_hallucinated_domain(hallucinated, "")[0] is False, \
        "cover.* should be flagged when no cover domain is known"
    safe = [tool_call("execute_services", service_args("light.office"))]
    assert ws.inv_no_hallucinated_domain(safe, "")[0] is True, \
        "known light domain should pass hallucinated-domain invariant"
    assert ws.SAFE_ENTITY_GUARD.name == "execute_services", "safe guard must target execute_services"
    assert ws.SAFE_ENTITY_GUARD.args_predicate(service_args("light.kitchen")) is True, \
        "safe guard predicate should flag unsafe service args"


t("cross-cutting invariants catch malformed or hallucinated planner output",
  test_cross_cutting_invariants_catch_planner_failures)


def test_phase3_mute_scenarios_are_scored_not_informational() -> None:
    scenarios = {scenario.id: scenario for scenario in ws.scenarios_for(phase=3)}
    assert all(s.pass_threshold > 0 for s in scenarios.values()), \
        "phase 3 mute scenarios must not hide harness gaps behind pass_threshold=0"
    assert scenarios["mute_blocks_response"].expect_silent is True, \
        "muted query should model silence as the expected successful response"
    assert scenarios["mute_blocks_response"].pass_threshold == 5, \
        "mute-block silence should be deterministic across all attempts"
    assert scenarios["mute_command_acknowledged"].pass_threshold == 5, \
        "mute command acknowledgement should be deterministic REST speech"
    assert "who is in the kitchen?" in scenarios["transient_unmute_one_turn"].sse_query_aliases, \
        "transient unmute should match the stripped model-side SSE user text"
    assert scenarios["transient_unmute_one_turn"].pass_threshold >= 3, \
        "transient unmute should remain a scored workflow scenario"


t("phase 3 mute scenarios are scored with explicit silence and SSE-alias semantics",
  test_phase3_mute_scenarios_are_scored_not_informational)


def test_relax_and_focus_light_predicates() -> None:
    relax = service_args("light.office")
    relax["list"][0]["service_data"].update({
        "brightness_pct": 30,
        "color_temp_kelvin": 2700,
    })
    focus = service_args("light.office")
    focus["list"][0]["service_data"].update({
        "brightness_pct": 90,
        "color_temp_kelvin": 5000,
    })
    assert ws.light_office_relax(relax) is True, "wind down/cozy should map to dim warm light"
    assert ws.light_office_relax(focus) is False, "focus lighting should not satisfy relax predicate"
    assert ws.light_office_focus(focus) is True, "deep work should map to bright cool light"
    assert ws.light_office_focus(relax) is False, "relax lighting should not satisfy focus predicate"


t("relax/cozy and focus predicates remain distinct",
  test_relax_and_focus_light_predicates)


def test_live_runner_applies_workflow_safety_guard() -> None:
    source = (TOOLS_DIR / "diagnose-identity.py").read_text(encoding="utf-8", errors="replace")
    assert "extra_forbidden = [ws_mod.SAFE_ENTITY_GUARD]" in source, \
        "workflow runner must attach SAFE_ENTITY_GUARD to every scenario"
    assert "include_write_gated=args.include_write_gated" in source, \
        "workflow runner must keep mutating scenarios behind an explicit CLI flag"
    assert "inconclusive_max_rate=args.inconclusive_max_rate" in source, \
        "workflow runner must enforce an inconclusive/trace-loss cap"
    assert "write_workflow_corpus" in source, \
        "workflow runner must write an attempt corpus for regression triage"
    assert "invariants = ws_mod.INVARIANTS" in source, \
        "workflow runner must apply workflow invariants"
    assert "expect_silent" in source and "expected silence but received assistant speech" in source, \
        "workflow runner must evaluate expected-silent mute scenarios as conclusive"
    assert 'query_aliases=getattr(sc, "sse_query_aliases", ())' in source, \
        "workflow runner must pass scenario SSE aliases to the completion matcher"


t("live workflow runner applies safety guard and invariants",
  test_live_runner_applies_workflow_safety_guard)


print(f"\n{passes} pass . {fails} fail")
if fails:
    print("\nFailures:")
    for name, message in failures:
        print(f"  - {name}: {message}")

sys.exit(0 if fails == 0 else 1)
