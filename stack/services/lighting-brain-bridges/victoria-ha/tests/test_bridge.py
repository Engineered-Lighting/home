"""Timeline replays: Frigate MQTT in, contract v1 events out."""

from __future__ import annotations

import json

from helpers import DEN_ZONES, FIXTURES, T0, apply, load_timeline, make_bridge, rel, replay
from victoria_ha_bridge.core import CONTRACT_ZONES, EVENTS_SCHEMA, BridgeConfig
from victoria_ha_bridge.ha_seed import subject_values, default_entities

COMMON = {"type", "home", "room", "source", "observed_at", "sent_at", "ttl_s"}
EXTRA = {
    "occupancy": {"present", "people_count"},
    "zone": {"zone", "present", "people_count"},
    "health": {"component", "ok", "detail"},
}


def changes(events, *fields):
    """Collapse heartbeats: keep (time, fields...) whenever the fields change."""
    out, last = [], None
    for e in events:
        key = tuple(e[f] for f in fields)
        if key != last:
            out.append((rel(e), *key))
            last = key
    return out


def test_every_event_matches_contract_v1():
    bridge, clock, brain = make_bridge(DEN_ZONES)
    replay(bridge, clock, load_timeline("zones_desk_to_sofa.json"), until=600)

    assert brain.batches
    for _t, body in brain.batches:
        assert body["schema"] == EVENTS_SCHEMA
        for e in body["events"]:
            assert set(e) == COMMON | EXTRA[e["type"]], e
            assert (e["home"], e["room"], e["source"]) == ("victoria", "den", "victoria-ha-bridge")
            assert isinstance(e["observed_at"], float) and isinstance(e["sent_at"], float)
            assert e["sent_at"] >= e["observed_at"]
            if e["type"] == "health":
                assert e["component"] == "frigate" and e["ttl_s"] == 150
            else:
                assert e["ttl_s"] == 90
                assert isinstance(e["present"], bool)
                assert e["people_count"] is None or isinstance(e["people_count"], int)
            if e["type"] == "zone":
                assert e["zone"] in CONTRACT_ZONES


def test_enter_then_leave_emits_changes_and_heartbeats():
    bridge, clock, brain = make_bridge()
    replay(bridge, clock, load_timeline("enter_leave.json"), until=600)

    occupancy = brain.of("occupancy")
    assert changes(occupancy, "present", "people_count") == [(5.0, True, 1), (200.0, False, 0)]
    times = [rel(e) for e in occupancy]
    assert times == [5, 35, 65, 95, 125, 155, 185, 200, 230, 260, 290, 320, 350, 380, 410, 440, 470,
                     500, 530, 560, 590]
    # Delivered in the same tick it was observed while the brain is up.
    assert all(e["sent_at"] == e["observed_at"] for e in occupancy)
    assert brain.of("zone") == []  # no zones configured yet


def test_heartbeat_cadence_is_30_s_for_state_and_60_s_for_health():
    bridge, clock, brain = make_bridge()
    replay(bridge, clock, load_timeline("enter_leave.json"), until=600)

    health_times = [rel(e) for e in brain.of("health")]
    assert health_times == [0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600]
    assert all(e["ok"] is True and e["detail"] is None for e in brain.of("health"))

    occupancy_times = [rel(e) for e in brain.of("occupancy")]
    gaps = [b - a for a, b in zip(occupancy_times, occupancy_times[1:])]
    # Every gap is a 30 s heartbeat except the one cut short by the 200 s change.
    assert sorted(set(gaps)) == [15, 30]


def test_zone_changes_desk_to_sofa_and_empty_room_clears_zones():
    bridge, clock, brain = make_bridge(DEN_ZONES)
    replay(bridge, clock, load_timeline("zones_desk_to_sofa.json"), until=600)

    desk = changes(brain.of("zone", zone="desk"), "present", "people_count")
    sofa = changes(brain.of("zone", zone="sofa"), "present", "people_count")
    assert desk == [(12.0, True, 1), (101.0, False, 0)]
    # Sofa is cleared at 300 s by the camera reaching zero, before Frigate's own
    # zone message at 300.5 s, which then changes nothing.
    assert sofa == [(100.0, True, 1), (300.0, False, 0)]
    assert changes(brain.of("occupancy"), "present") == [(10.0, True), (300.0, False)]
    # A zone stays unknown (no events) until Frigate first reports it.
    assert rel(brain.of("zone", zone="sofa")[0]) == 100.0


def test_two_people_reports_counts():
    bridge, clock, brain = make_bridge(DEN_ZONES)
    replay(bridge, clock, load_timeline("two_people.json"), until=300)

    assert changes(brain.of("occupancy"), "people_count") == [(10.0, 1), (50.0, 2), (120.0, 1)]
    assert changes(brain.of("zone", zone="sofa"), "people_count") == [(11.0, 1), (52.0, 2), (121.0, 1)]
    assert all(e["present"] for e in brain.of("occupancy"))


def test_frigate_silent_marks_health_false_and_stops_occupancy_until_it_returns():
    bridge, clock, brain = make_bridge()
    replay(bridge, clock, load_timeline("frigate_silent.json"), until=500)

    health = changes(brain.of("health"), "ok", "detail")
    # Last message at 121 s; silence is "more than 150 s", so false at 272 s.
    assert health == [(0.0, True, None), (272.0, False, "no frigate messages within 150 s"), (400.0, True, None)]
    false_beats = [rel(e) for e in brain.of("health", ok=False)]
    assert false_beats == [272, 332, 392]

    occupancy_times = [rel(e) for e in brain.of("occupancy")]
    assert max(t for t in occupancy_times if t < 400) == 245  # nothing asserted while silent
    assert 400 in occupancy_times  # re-sent at once when Frigate is back
    assert brain.of("occupancy")[-1]["present"] is True


def test_frigate_restart_resets_counts_to_zero_then_follows_new_counts():
    bridge, clock, brain = make_bridge()
    replay(bridge, clock, load_timeline("frigate_restart.json"), until=300)

    assert changes(brain.of("health"), "ok", "detail") == [
        (0.0, True, None), (100.0, False, "frigate reported offline"), (160.0, True, None)]
    occupancy = brain.of("occupancy")
    assert not [e for e in occupancy if 100 <= rel(e) < 160]
    assert changes(occupancy, "present", "people_count") == [(5.0, True, 1), (160.0, False, 0), (165.0, True, 1)]


def test_retained_online_at_startup_does_not_invent_a_zero():
    bridge, clock, brain = make_bridge()
    steps = [s for s in load_timeline("enter_leave.json") if s.get("topic") != "frigate/den_oak/person"]
    replay(bridge, clock, steps, until=120)
    assert brain.of("health", ok=True)
    assert brain.of("occupancy") == []  # unknown is not "empty"


def test_waiting_for_frigate_then_silence():
    bridge, clock, brain = make_bridge()
    replay(bridge, clock, [{"at": 0, "connect": True}], until=200)
    health = brain.of("health")
    assert changes(health, "ok", "detail") == [(151.0, False, "no frigate messages within 150 s")]


def test_broker_unreachable_from_start_is_health_false_after_grace():
    bridge, clock, brain = make_bridge()
    replay(bridge, clock, [], until=30)
    assert changes(brain.of("health"), "ok", "detail") == [(10.0, False, "mqtt broker not connected")]


def test_mqtt_drop_marks_health_false_and_reconnect_forgets_stale_counts():
    bridge, clock, brain = make_bridge()
    steps = load_timeline("enter_leave.json")[:4] + [
        {"at": 5, "topic": "frigate/den_oak/person", "payload": "1"},
        {"at": 50, "connect": False},
        {"at": 100, "connect": True},
        {"at": 100, "topic": "frigate/available", "payload": "online", "retain": True},
        {"at": 100, "topic": "frigate/den_oak/detect/state", "payload": "ON", "retain": True},
    ]
    replay(bridge, clock, sorted(steps, key=lambda s: s["at"]), until=200)

    assert changes(brain.of("health"), "ok", "detail") == [
        (0.0, True, None), (60.0, False, "mqtt broker not connected"), (100.0, True, None)]
    # Counts are not retained, so after the reconnect the bridge knows nothing
    # and says nothing until Frigate or the HA seed tells it.
    assert max(rel(e) for e in brain.of("occupancy")) == 35
    assert bridge.snapshot()["values"] == {}


def test_ha_seed_fills_unknown_counts_after_reconnect_but_never_overrides_mqtt():
    bridge, clock, brain = make_bridge(DEN_ZONES)
    replay(bridge, clock, load_timeline("enter_leave.json")[:3], until=2)
    apply(bridge, {"topic": "frigate/den_desk/person", "payload": "0"})

    states = json.loads((FIXTURES / "ha" / "api_states_den.json").read_text(encoding="utf-8"))
    seeded = bridge.seed(subject_values(states, default_entities("den_oak", DEN_ZONES)))

    assert sorted(seeded) == ["occupancy", "zone:sofa"]  # desk came from MQTT
    assert bridge.snapshot()["values"] == {
        "occupancy": (True, 1), "zone:sofa": (True, 1), "zone:desk": (False, 0)}
    assert changes(brain.of("zone", zone="sofa"), "present") == [(2.0, True)]


def test_ha_seed_today_uses_the_binary_sensor_with_unknown_count():
    bridge, clock, brain = make_bridge()
    replay(bridge, clock, load_timeline("enter_leave.json")[:3], until=1)
    states = json.loads((FIXTURES / "ha" / "api_states_today.json").read_text(encoding="utf-8"))
    bridge.seed(subject_values(states, default_entities("den_oak", {})))
    event = brain.of("occupancy")[-1]
    assert (event["present"], event["people_count"]) == (True, None)


def test_detection_off_or_no_frames_is_unhealthy():
    stats = json.loads((FIXTURES / "frigate_stats_den_oak.json").read_text(encoding="utf-8"))
    bridge, clock, brain = make_bridge()
    replay(bridge, clock, load_timeline("enter_leave.json")[:4], until=5)

    clock.now = T0 + 10
    apply(bridge, {"topic": "frigate/den_oak/detect/state", "payload": "OFF"})
    clock.now = T0 + 20
    apply(bridge, {"topic": "frigate/den_oak/detect/state", "payload": "ON"})
    stats["cameras"]["den_oak"]["camera_fps"] = 0.0
    clock.now = T0 + 30
    apply(bridge, {"topic": "frigate/stats", "payload": json.dumps(stats)})
    stats["cameras"] = {"other_cam": {"camera_fps": 5.0}}
    clock.now = T0 + 40
    apply(bridge, {"topic": "frigate/stats", "payload": json.dumps(stats)})

    assert changes(brain.of("health"), "ok", "detail") == [
        (0.0, True, None),
        (10.0, False, "detection is off for den_oak"),
        (20.0, True, None),
        (30.0, False, "camera den_oak has no frames (camera_fps 0)"),
        (40.0, False, "camera den_oak missing from frigate stats"),
    ]


def test_bad_payloads_are_ignored():
    bridge, clock, brain = make_bridge()
    replay(bridge, clock, load_timeline("enter_leave.json")[:4], until=2)
    apply(bridge, {"topic": "frigate/den_oak/person", "payload": "not-a-number"})
    apply(bridge, {"topic": "frigate/stats", "payload": "{broken"})
    assert brain.of("occupancy") == []
    assert brain.of("health")[-1]["ok"] is True


def test_unlisted_zones_are_never_reported_and_bad_zone_names_are_refused():
    bridge, clock, brain = make_bridge({"sofa": "den_sofa"})
    replay(bridge, clock, load_timeline("zones_desk_to_sofa.json"), until=200)
    assert {e["zone"] for e in brain.of("zone")} == {"sofa"}
    assert "frigate/den_desk/person" not in bridge.subscriptions()

    try:
        BridgeConfig(zones={"kitchen": "den_kitchen"})
    except ValueError as exc:
        assert "kitchen" in str(exc)
    else:
        raise AssertionError("unknown contract zone accepted")


def test_subscriptions_are_exact_topics():
    bridge, _clock, _brain = make_bridge(DEN_ZONES)
    assert bridge.subscriptions() == [
        "frigate/available", "frigate/stats", "frigate/den_oak/detect/state",
        "frigate/den_oak/person", "frigate/den_desk/person", "frigate/den_sofa/person"]
