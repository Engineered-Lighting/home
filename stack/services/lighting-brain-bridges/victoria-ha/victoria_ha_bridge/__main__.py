"""Run the bridge: `python -m victoria_ha_bridge --config /path/to/bridge.json`.

One thread owns the bridge. paho-mqtt's network thread only queues messages.
"""

from __future__ import annotations

import argparse
import logging
import queue
import signal
import time
from typing import Any, Callable

from .config import Settings, load
from .core import Bridge
from .ha_seed import fetch_states, subject_values
from .transport import DryRunTransport, HttpTransport

log = logging.getLogger("victoria_ha_bridge")

TICK_S = 1.0


class MqttSource:
    """Thin paho-mqtt adapter (1.6 or 2.x). Imported lazily so tests need no paho."""

    def __init__(self, settings: Settings, topics: list[str], inbox: "queue.Queue[tuple[Any, ...]]") -> None:
        self._settings = settings.mqtt
        self._topics = topics
        self._inbox = inbox
        self._client: Any = None

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        s = self._settings
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=s.client_id)
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id=s.client_id)
        if s.username:
            client.username_pw_set(s.username, s.password)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.connect_async(s.host, s.port, keepalive=s.keepalive_s)
        client.loop_start()
        self._client = client

    def stop(self) -> None:
        if self._client is not None:
            self._client.disconnect()
            self._client.loop_stop()

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, reason: Any, _properties: Any = None) -> None:
        if reason == 0:
            client.subscribe([(topic, 0) for topic in self._topics])
            self._inbox.put(("connection", True))
        else:
            log.warning("mqtt connect refused: %s", reason)

    def _on_disconnect(self, *_args: Any) -> None:
        self._inbox.put(("connection", False))

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        self._inbox.put(("message", message.topic, message.payload, bool(message.retain)))


def seed_from_ha(bridge: Bridge, settings: Settings) -> None:
    if settings.ha_seed is None:
        return
    states = fetch_states(settings.ha_seed.url, settings.ha_seed.token)
    if states:
        bridge.seed(subject_values(states, settings.ha_seed.entities))


def pump(bridge: Bridge, inbox: "queue.Queue[tuple[Any, ...]]", reseed: Callable[[], None],
         wait_s: float = TICK_S) -> None:
    """Handle everything queued (waiting up to wait_s for the first item), else just tick."""
    try:
        item = inbox.get(timeout=wait_s)
    except queue.Empty:
        bridge.tick()
        return
    while True:
        if item[0] == "connection":
            bridge.on_connection(item[1])
            if item[1]:
                reseed()
        else:
            bridge.on_message(item[1], item[2], retained=item[3])
        try:
            item = inbox.get_nowait()
        except queue.Empty:
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="victoria_ha_bridge", description=__doc__)
    parser.add_argument("--config", required=True, help="private JSON config (see bridge.example.json)")
    parser.add_argument("--dry-run", action="store_true", help="print event batches instead of sending them")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    settings = load(args.config, dry_run=args.dry_run)
    transport = DryRunTransport() if args.dry_run else HttpTransport(
        settings.events_url, settings.brain_token,
        timeout_s=settings.brain_timeout_s, ca_file=settings.brain_ca_file)
    bridge = Bridge(settings.bridge, time.time, transport)

    inbox: "queue.Queue[tuple[Any, ...]]" = queue.Queue()
    source = MqttSource(settings, bridge.subscriptions(), inbox)
    stopping = False

    def stop(*_args: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log.info("bridge starting: camera %s, zones %s -> %s",
             settings.bridge.camera, settings.bridge.zones or "none", "stdout" if args.dry_run else settings.events_url)
    source.start()
    try:
        while not stopping:
            pump(bridge, inbox, lambda: seed_from_ha(bridge, settings))
    finally:
        source.stop()
        log.info("bridge stopped: %s", bridge.outbox.stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
