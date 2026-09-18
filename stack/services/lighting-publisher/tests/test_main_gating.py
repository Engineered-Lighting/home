"""The process: its refusals, its connect sequence and its health gate.

The publisher is driven tick by tick with a fake clock, a fake MQTT client and
a fake observer transport; ``socket.socket`` raises in every test, so a
regression that opens a connection fails here rather than on the house.
"""
import datetime as dt
import json
import pathlib
import re
import tempfile
import unittest
from unittest import mock

from lighting_publisher import main as main_mod
from lighting_publisher.activity import load_zones
from lighting_publisher.inputs.mirror import MIRROR_HEARTBEAT_TOPIC
from lighting_publisher.inputs.observer import ObserverClient, ObserverError
from lighting_publisher.journal import Journal
from lighting_publisher.main import (EXIT_BIND_REFUSED, EXIT_CONFIG, BindRefused, Config,
                                     ConfigError, Publisher)
from lighting_publisher.publish.discovery import PAYLOAD_NONE, UNKNOWN_STATE
from lighting_publisher.stories import MIRROR_FRESH_S, OBSERVER_FRESH_S

T0 = dt.datetime(2026, 9, 18, 1, 0, tzinfo=dt.timezone.utc)


def s(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple] = []
        self.subscriptions: list[tuple] = []
        self.will: tuple | None = None

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))

    def will_set(self, topic, payload=None, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def topics(self) -> list[str]:
        return [row[0] for row in self.published]

    def last_for(self, topic: str):
        for row in reversed(self.published):
            if row[0] == topic:
                return row[1]
        return None

    def count_for(self, topic: str) -> int:
        """Publishes of a real payload; the startup clear sends an empty one."""
        return sum(1 for row in self.published if row[0] == topic and row[1] != "")


class FakeTransport:
    """The observer answers with this body, or raises while ``down``."""

    def __init__(self, body: bytes = b'{"cameras": {"living_room": {"person_present": false}}}'):
        self.body = body
        self.down = False
        self.calls = 0

    def fetch(self, url: str) -> bytes:
        self.calls += 1
        if self.down:
            raise ObserverError("observer unreachable (test)")
        return self.body


class Message:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


class NoSocketTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("socket.socket", side_effect=AssertionError("no sockets in tests"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.zones = load_zones()


class ConfigTest(NoSocketTest):
    def test_defaults_are_shadow_and_loopback(self):
        config = Config.from_env({})
        self.assertEqual(config.mode, "shadow")
        self.assertTrue(config.shadow)
        self.assertEqual(config.port, 8105)
        self.assertEqual(config.publish_bind_addr, "127.0.0.1")
        self.assertEqual(config.observer_url, "http://192.168.0.100:8767")

    def test_live_mode_drops_the_suffix(self):
        config = Config.from_env({"PUBLISHER_MODE": "live"})
        self.assertFalse(config.shadow)

    def test_a_published_lan_address_is_refused(self):
        with self.assertRaises(BindRefused):
            Config.from_env({"PUBLISH_BIND_ADDR": "192.168.0.100"})
        with self.assertRaises(BindRefused):
            Config.from_env({"PUBLISH_BIND_ADDR": "0.0.0.0"})

    def test_a_lan_listen_address_is_refused_but_the_container_default_is_not(self):
        with self.assertRaises(BindRefused):
            Config.from_env({"BIND_HOST": "192.168.0.100"})
        self.assertEqual(Config.from_env({"BIND_HOST": "0.0.0.0"}).bind_host, "0.0.0.0")
        self.assertEqual(Config.from_env({"BIND_HOST": "127.0.0.1"}).bind_host, "127.0.0.1")

    def test_main_exits_78_on_a_refused_bind(self):
        self.assertEqual(main_mod.main(env={"PUBLISH_BIND_ADDR": "192.168.0.100"}),
                         EXIT_BIND_REFUSED)
        self.assertEqual(main_mod.main(env={"BIND_HOST": "0.0.0.0", "PUBLISH_BIND_ADDR": "::"}),
                         EXIT_BIND_REFUSED)

    def test_main_exits_2_on_a_bad_mode_or_port(self):
        self.assertEqual(main_mod.main(env={"PUBLISHER_MODE": "loud"}), EXIT_CONFIG)
        self.assertEqual(main_mod.main(env={"PORT": "eight"}), EXIT_CONFIG)

    def test_a_secret_file_is_read_and_a_missing_one_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "pw"
            path.write_text("s3cret\n", encoding="utf-8")
            self.assertEqual(main_mod.read_secret(str(path)), "s3cret")
            with self.assertRaises(ConfigError) as caught:
                main_mod.read_secret(str(pathlib.Path(tmp) / "absent"))
            self.assertNotIn("s3cret", str(caught.exception))
        self.assertEqual(main_mod.read_secret(None), "")


class PublisherHarness(NoSocketTest):
    """A connected, healthy publisher driven by a fake clock."""

    def setUp(self) -> None:
        super().setUp()
        self.client = FakeClient()
        self.transport = FakeTransport()
        self.observer = ObserverClient("http://observer:8767/api/state", transport=self.transport)
        self.now = T0
        self.config = Config(mode="shadow", journal_dir=None, timezone="UTC")
        self.journal = Journal(None, enabled=False)
        self.publisher = Publisher(self.config, self.client, self.observer, self.zones,
                                   journal=self.journal, clock=lambda: self.now)

    def connect(self) -> None:
        self.publisher.on_connect()

    def heartbeat(self, at: dt.datetime | None = None) -> None:
        stamp = at or self.now
        self.publisher.on_message(message=Message(MIRROR_HEARTBEAT_TOPIC,
                                                  stamp.isoformat().encode("utf-8")))

    def frigate(self, topic: str, payload: bytes) -> None:
        self.publisher.on_message(message=Message(topic, payload))

    def tick(self, at: dt.datetime) -> dict:
        self.now = at
        return self.publisher.tick(at)

    def healthy_tick(self, at: dt.datetime) -> dict:
        self.now = at
        self.heartbeat(at)
        return self.publisher.tick(at)


class ConnectSequenceTest(PublisherHarness):
    def test_announce_then_clear_then_discovery_then_subscribe(self):
        self.connect()
        entities = self.publisher.entities
        topics = self.client.topics()
        self.assertEqual(topics[0], entities.availability_topic)
        self.assertEqual(self.client.published[0][1], "online")
        cleared = topics[1:1 + len(entities.state_topics())]
        self.assertEqual(set(cleared), set(entities.state_topics()))
        self.assertEqual({payload for _, payload, _, _ in self.client.published[1:1 + len(cleared)]},
                         {""})
        discovery = {topic for topic, _ in entities.discovery_messages()}
        self.assertTrue(discovery.issubset(set(topics)))
        self.assertEqual([t for t, _ in self.client.subscriptions],
                         ["living_lights/mirror/#", "frigate/+/person", "frigate/+/+/person"])

    def test_every_publish_is_retained(self):
        self.connect()
        self.assertTrue(all(row[3] for row in self.client.published))

    def test_disconnect_drops_health(self):
        self.connect()
        self.publisher.on_disconnect()
        self.assertFalse(self.publisher.connected)
        self.assertIn("mqtt_down", self.publisher.health.status(T0).reasons)

    def test_a_reconnect_republishes_state(self):
        self.connect()
        self.healthy_tick(s(1))
        before = self.client.count_for(self.publisher.entities.asleep.state_topic)
        self.publisher.on_disconnect()
        self.connect()
        self.healthy_tick(s(2))
        self.assertGreater(self.client.count_for(self.publisher.entities.asleep.state_topic),
                           before)

    def test_shutdown_publishes_offline(self):
        self.connect()
        self.publisher.shutdown(s(5))
        self.assertEqual(self.client.published[-1],
                         (self.publisher.entities.availability_topic, "offline", 0, True))


class HealthGateTest(PublisherHarness):
    def test_a_healthy_tick_publishes_beliefs_and_a_heartbeat(self):
        self.connect()
        record = self.healthy_tick(s(1))
        entities = self.publisher.entities
        self.assertEqual(record["event"], "decision")
        self.assertEqual(self.client.last_for(entities.tv.state_topic), "OFF")
        self.assertIn(self.client.last_for(entities.asleep.state_topic),
                      ("awake", "likely_asleep", "away"))
        self.assertEqual(self.client.last_for(entities.activity["sofa"].state_topic), "idle")
        self.assertTrue(record["heartbeat"])
        self.assertTrue(self.client.last_for(entities.heartbeat.state_topic))

    def test_the_heartbeat_is_once_a_minute(self):
        self.connect()
        self.healthy_tick(s(1))
        self.assertFalse(self.healthy_tick(s(30))["heartbeat"])
        self.assertTrue(self.healthy_tick(s(61))["heartbeat"])
        self.assertEqual(self.client.count_for(self.publisher.entities.heartbeat.state_topic), 2)

    def test_a_stale_mirror_stops_the_heartbeat_and_publishes_unknown(self):
        self.connect()
        self.healthy_tick(s(1))
        heartbeats = self.client.count_for(self.publisher.entities.heartbeat.state_topic)
        record = self.tick(s(1 + MIRROR_FRESH_S))
        self.assertEqual(record["event"], "gated")
        self.assertIn("mirror_stale", record["health"]["reasons"])
        self.assertFalse(record["heartbeat"])
        self.assertEqual(self.client.count_for(self.publisher.entities.heartbeat.state_topic),
                         heartbeats)
        self.assertEqual(self.client.last_for(self.publisher.entities.tv.state_topic),
                         PAYLOAD_NONE)
        self.assertEqual(self.client.last_for(self.publisher.entities.asleep.state_topic),
                         UNKNOWN_STATE)
        self.assertEqual(self.client.last_for(
            self.publisher.entities.activity["sink"].state_topic), UNKNOWN_STATE)

    def test_a_stale_observer_gates_the_tick(self):
        self.connect()
        self.healthy_tick(s(1))
        self.transport.down = True
        record = self.tick(s(1 + OBSERVER_FRESH_S))
        self.assertEqual(record["event"], "gated")
        self.assertIn("observer_stale", record["health"]["reasons"])

    def test_mqtt_down_gates_the_tick(self):
        self.connect()
        self.healthy_tick(s(1))
        self.publisher.on_disconnect()
        record = self.tick(s(2))
        self.assertEqual(record["event"], "gated")
        self.assertIn("mqtt_down", record["health"]["reasons"])

    def test_the_machines_do_not_advance_while_gated(self):
        self.connect()
        self.frigate("frigate/living_room/sofa/person", b"1")
        self.healthy_tick(s(1))
        state_before = self.publisher.tv.state
        self.publisher.on_disconnect()
        for offset in range(2, 8):
            self.tick(s(offset))
        self.assertEqual(self.publisher.tv.state, state_before)

    def test_recovery_restarts_the_clocks_so_silence_is_not_quiet(self):
        self.connect()
        self.healthy_tick(s(1))
        estimator_before = self.publisher.estimator
        self.publisher.on_disconnect()
        self.tick(s(3600))
        self.connect()
        record = self.healthy_tick(s(3601))
        self.assertTrue(record["recovered"])
        self.assertIsNot(self.publisher.estimator, estimator_before)
        self.assertEqual(self.publisher.estimator.state, estimator_before.state)
        self.assertLess(record["asleep"]["evidence"]["quiet_s"], 10)
        self.assertEqual(record["asleep"]["state"], "awake")


class DiscoveryCadenceTest(PublisherHarness):
    def test_discovery_is_republished_every_ten_minutes(self):
        self.connect()
        topic = self.publisher.entities.tv.discovery_topic
        self.assertEqual(self.client.count_for(topic), 1)
        self.healthy_tick(s(599))
        self.assertEqual(self.client.count_for(topic), 1)
        self.healthy_tick(s(600))
        self.assertEqual(self.client.count_for(topic), 2)

    def test_no_discovery_while_disconnected(self):
        self.connect()
        topic = self.publisher.entities.tv.discovery_topic
        self.publisher.on_disconnect()
        self.tick(s(1200))
        self.assertEqual(self.client.count_for(topic), 1)


class ObserverPollingTest(PublisherHarness):
    def test_the_observer_is_polled_every_five_seconds(self):
        self.connect()
        for offset in range(0, 11):
            self.healthy_tick(s(offset))
        self.assertEqual(self.transport.calls, 3)

    def test_a_failed_poll_leaves_the_observer_stale(self):
        self.transport.down = True
        self.connect()
        record = self.healthy_tick(s(1))
        self.assertEqual(record["event"], "gated")
        self.assertIn("observer_never_seen", record["health"]["reasons"])


class HealthzTest(PublisherHarness):
    def test_healthz_carries_the_three_flags_and_the_mode(self):
        self.connect()
        self.healthy_tick(s(1))
        payload = self.publisher.healthz()
        for key in ("ok", "mirror_fresh", "observer_fresh", "mqtt_up", "mode"):
            self.assertIn(key, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "shadow")
        json.dumps(payload, default=str)

    def test_healthz_reports_the_gate_reasons(self):
        self.connect()
        self.tick(s(1))
        payload = self.publisher.healthz()
        self.assertFalse(payload["ok"])
        self.assertIn("mirror_never_seen", payload["reasons"])


class JournalWritingTest(PublisherHarness):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.publisher.journal = Journal(pathlib.Path(self.tmp.name))

    def test_decisions_land_in_a_daily_file(self):
        self.connect()
        self.healthy_tick(s(1))
        files = sorted(pathlib.Path(self.tmp.name).iterdir())
        self.assertEqual(len(files), 1)
        rows = [json.loads(line) for line in files[0].read_text(encoding="ascii").splitlines()]
        self.assertEqual(rows[0]["event"], "decision")
        self.assertEqual(rows[0]["mode"], "shadow")

    def test_a_quiet_minute_still_leaves_one_snapshot(self):
        self.connect()
        self.healthy_tick(s(1))
        for offset in range(2, 60):
            self.healthy_tick(s(offset))
        rows = sum(1 for path in pathlib.Path(self.tmp.name).iterdir()
                   for line in path.read_text(encoding="ascii").splitlines())
        self.assertLess(rows, 20, "a quiet minute must not journal every tick")
        self.assertGreaterEqual(rows, 1)


class NoEgressTest(NoSocketTest):
    def test_no_module_imports_the_egress_gate_or_a_home_assistant_token(self):
        root = pathlib.Path(main_mod.__file__).resolve().parent
        pattern = re.compile(r"^\s*(from|import)\s+\S*lighting_beliefs", re.MULTILINE)
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="ascii")
            self.assertIsNone(pattern.search(text), path.name)
            self.assertNotIn("HA_TOKEN", text, path.name)
            self.assertNotIn("Authorization", text, path.name)

    def test_the_package_is_ascii(self):
        root = pathlib.Path(main_mod.__file__).resolve().parent
        for path in sorted(root.rglob("*.py")):
            path.read_text(encoding="ascii")


if __name__ == "__main__":
    unittest.main()
