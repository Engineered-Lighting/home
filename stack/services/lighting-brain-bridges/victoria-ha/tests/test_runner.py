"""The runner's queue handling and the paho callbacks, without paho or a broker."""

from __future__ import annotations

import queue
from types import SimpleNamespace

from helpers import make_bridge
from victoria_ha_bridge.__main__ import MqttSource, pump


def test_pump_dispatches_queued_items_and_reseeds_on_connect():
    bridge, _clock, brain = make_bridge()
    inbox: queue.Queue = queue.Queue()
    reseeds = []
    inbox.put(("connection", True))
    inbox.put(("message", "frigate/available", b"online", True))
    inbox.put(("message", "frigate/den_oak/person", b"2", False))

    pump(bridge, inbox, lambda: reseeds.append(1), wait_s=0)

    assert reseeds == [1]
    assert inbox.empty()
    assert brain.of("occupancy")[-1]["people_count"] == 2


def test_pump_ticks_when_idle():
    bridge, clock, brain = make_bridge()
    clock.now += 30  # broker never connected
    pump(bridge, queue.Queue(), lambda: None, wait_s=0)
    assert brain.of("health", ok=False)


def test_mqtt_callbacks_subscribe_and_queue_for_both_paho_versions():
    inbox: queue.Queue = queue.Queue()
    settings = SimpleNamespace(mqtt=None)
    source = MqttSource(settings, ["frigate/available", "frigate/den_oak/person"], inbox)
    subscribed = []
    client = SimpleNamespace(subscribe=subscribed.append)

    source._on_connect(client, None, {}, 0)          # paho 1.x: rc
    source._on_connect(client, None, {}, 0, None)    # paho 2.x: reason code, properties
    source._on_connect(client, None, {}, 5)          # refused: no subscribe, nothing queued
    source._on_disconnect(client, None, 1)
    source._on_message(client, None, SimpleNamespace(topic="frigate/den_oak/person", payload=b"1", retain=0))

    assert subscribed == [[("frigate/available", 0), ("frigate/den_oak/person", 0)]] * 2
    items = [inbox.get_nowait() for _ in range(inbox.qsize())]
    assert items == [("connection", True), ("connection", True), ("connection", False),
                     ("message", "frigate/den_oak/person", b"1", False)]
