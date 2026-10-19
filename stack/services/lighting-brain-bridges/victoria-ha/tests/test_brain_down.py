"""Brain unreachable: buffer the latest event per subject briefly, drop what expired."""

from __future__ import annotations

from helpers import T0, FakeBrain, FakeClock, load_timeline, make_bridge, rel, replay
from victoria_ha_bridge.core import Outbox, Timing


def test_brain_down_buffers_latest_per_subject_and_delivers_on_recovery():
    bridge, clock, brain = make_bridge(down=lambda t: t < 100)
    replay(bridge, clock, load_timeline("enter_leave.json"), until=200)

    # Exponential backoff from 2 s, capped at 30 s.
    assert brain.attempts[:8] == [0, 2, 6, 14, 30, 60, 90, 120]
    first_t, first = brain.batches[0]
    assert first_t == 120
    # Only the newest event per subject survives; older heartbeats were superseded.
    assert sorted((e["type"], rel(e), rel(e, "sent_at")) for e in first["events"]) == [
        ("health", 120.0, 120.0), ("occupancy", 95.0, 120.0)]
    assert bridge.outbox.stats.superseded >= 4
    # Back to normal cadence afterwards.
    assert [rel(e) for e in brain.of("occupancy")][1:3] == [125, 155]
    assert bridge.snapshot()["brain_reachable"] is True


def test_events_past_their_ttl_are_dropped_not_sent():
    # Frigate goes silent while the brain is down: the last occupancy heartbeat
    # (245 s, ttl 90 s) has expired by the time the brain is back at 400 s.
    bridge, clock, brain = make_bridge(down=lambda t: 200 <= t < 390)
    replay(bridge, clock, load_timeline("frigate_silent.json"), until=420)

    late = [e for t, body in brain.batches if t >= 390 for e in body["events"]]
    assert not [e for e in late if e["type"] == "occupancy" and rel(e) < 400]
    assert bridge.outbox.stats.dropped_expired >= 1
    for e in late:
        assert e["sent_at"] < e["observed_at"] + e["ttl_s"]


def test_outbox_expiry_without_refresh():
    clock = FakeClock()
    brain = FakeBrain(clock, down=lambda t: t < 200)
    outbox = Outbox(brain, Timing())
    outbox.put("occupancy", {"type": "occupancy", "observed_at": T0, "ttl_s": 90.0})
    for t in range(0, 201):
        clock.now = T0 + t
        outbox.flush(clock.now)
    assert outbox.pending == []
    assert outbox.stats.dropped_expired == 1
    assert brain.batches == []


def test_rejected_batch_is_dropped_not_retried():
    clock = FakeClock()
    brain = FakeBrain(clock, reject_status=422)
    outbox = Outbox(brain, Timing())
    outbox.put("health:frigate", {"type": "health", "observed_at": T0, "ttl_s": 150.0})
    outbox.flush(T0)
    outbox.flush(T0 + 1)
    assert brain.attempts == [0.0]
    assert outbox.pending == []
    assert outbox.stats.dropped_rejected == 1


def test_on_change_while_down_waits_for_the_next_retry():
    bridge, clock, brain = make_bridge(down=lambda t: t < 10)
    replay(bridge, clock, load_timeline("enter_leave.json"), until=20)
    # Change at 5 s is held (next retry at 6 s fails, then 14 s succeeds).
    _t, body = brain.batches[0]
    assert _t == 14
    occupancy = [e for e in body["events"] if e["type"] == "occupancy"]
    assert [(rel(e), e["present"]) for e in occupancy] == [(5.0, True)]
