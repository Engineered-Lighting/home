"""Discovery, retained publishing and the journal: what the broker is told.

No socket is opened: ``socket.socket`` is patched to raise in every test, the
MQTT client is a fake that records publishes, and the journal writes into a
temporary directory.
"""
import datetime as dt
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from lighting_publisher.activity import load_zones
from lighting_publisher.journal import FILE_PREFIX, Journal
from lighting_publisher.publish.discovery import (AVAILABILITY_OFFLINE, AVAILABILITY_ONLINE,
                                                  SHADOW_SUFFIX, build_entities)
from lighting_publisher.publish.state import StatePublisher

T0 = dt.datetime(2026, 9, 17, 22, 0, tzinfo=dt.timezone.utc)


def s(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


class FakeClient:
    """Records what the publisher would have sent."""

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


class NoSocketTest(unittest.TestCase):
    """Every test in this file refuses to open a socket."""

    def setUp(self) -> None:
        patcher = mock.patch("socket.socket", side_effect=AssertionError("no sockets in tests"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.zones = load_zones()


class EntitySetTest(NoSocketTest):
    def test_live_object_ids_are_what_home_assistant_reads(self):
        entities = build_entities(self.zones, shadow=False)
        self.assertEqual(entities.tv.object_id, "living_lights_tv_watching")
        self.assertEqual(entities.tv.component, "binary_sensor")
        self.assertEqual(entities.asleep.object_id, "living_lights_asleep_estimator")
        self.assertEqual(entities.heartbeat.object_id, "lighting_publisher_heartbeat")
        self.assertEqual(entities.activity["sofa"].object_id, "living_room_sofa_activity")
        self.assertEqual(entities.activity["sink"].object_id, "kitchen_sink_activity")

    def test_shadow_suffixes_every_object_id_including_the_heartbeat(self):
        entities = build_entities(self.zones, shadow=True)
        for entity in entities.all():
            self.assertTrue(entity.object_id.endswith(SHADOW_SUFFIX), entity.key)
            self.assertTrue(entity.unique_id.endswith(SHADOW_SUFFIX), entity.key)
        self.assertEqual(entities.heartbeat.object_id, "lighting_publisher_heartbeat_shadow")

    def test_the_two_modes_share_no_topic(self):
        live = build_entities(self.zones, shadow=False)
        shadow = build_entities(self.zones, shadow=True)
        self.assertEqual(set(live.state_topics()) & set(shadow.state_topics()), set())
        self.assertNotEqual(live.availability_topic, shadow.availability_topic)
        live_discovery = {topic for topic, _ in live.discovery_messages()}
        shadow_discovery = {topic for topic, _ in shadow.discovery_messages()}
        self.assertEqual(live_discovery & shadow_discovery, set())

    def test_unique_ids_are_unique(self):
        for shadow in (False, True):
            entities = build_entities(self.zones, shadow=shadow)
            ids = [entity.unique_id for entity in entities.all()]
            self.assertEqual(len(ids), len(set(ids)))

    def test_one_entity_per_zone_plus_three(self):
        entities = build_entities(self.zones, shadow=True)
        self.assertEqual(len(entities.all()), len(self.zones.zones) + 3)
        self.assertEqual(len(entities.belief_entities()), len(self.zones.zones) + 2)
        self.assertNotIn(entities.heartbeat, entities.belief_entities())


class DiscoveryPayloadTest(NoSocketTest):
    def test_payload_carries_unique_id_object_id_and_availability(self):
        entities = build_entities(self.zones, shadow=True)
        messages = dict(entities.discovery_messages())
        payload = json.loads(messages[entities.tv.discovery_topic])
        self.assertEqual(payload["object_id"], "living_lights_tv_watching_shadow")
        self.assertEqual(payload["unique_id"], "lighting_publisher_living_lights_tv_watching_shadow")
        self.assertEqual(payload["state_topic"], entities.tv.state_topic)
        self.assertEqual(payload["json_attributes_topic"], entities.tv.attributes_topic)
        self.assertEqual(payload["availability_topic"], entities.availability_topic)
        self.assertEqual(payload["payload_available"], AVAILABILITY_ONLINE)
        self.assertEqual(payload["payload_not_available"], AVAILABILITY_OFFLINE)
        self.assertEqual(payload["payload_on"], "ON")
        self.assertEqual(payload["payload_off"], "OFF")

    def test_heartbeat_is_a_timestamp_sensor_without_attributes(self):
        entities = build_entities(self.zones, shadow=False)
        payload = json.loads(dict(entities.discovery_messages())[entities.heartbeat.discovery_topic])
        self.assertEqual(payload["device_class"], "timestamp")
        self.assertNotIn("json_attributes_topic", payload)

    def test_discovery_topics_are_under_the_prefix(self):
        entities = build_entities(self.zones, shadow=False, discovery_prefix="homeassistant")
        for topic, _ in entities.discovery_messages():
            self.assertTrue(topic.startswith("homeassistant/"))
            self.assertTrue(topic.endswith("/config"))

    def test_removal_is_an_empty_payload_on_every_discovery_topic(self):
        entities = build_entities(self.zones, shadow=True)
        removals = entities.removal_messages()
        self.assertEqual(len(removals), len(entities.all()))
        self.assertEqual({payload for _, payload in removals}, {""})
        self.assertEqual({topic for topic, _ in removals},
                         {topic for topic, _ in entities.discovery_messages()})


class StatePublisherTest(NoSocketTest):
    def setUp(self) -> None:
        super().setUp()
        self.client = FakeClient()
        self.state = StatePublisher(self.client, repeat_s=60)

    def test_publishes_on_change_and_suppresses_a_repeat(self):
        self.assertTrue(self.state.publish("t", "ON", s(0)))
        self.assertFalse(self.state.publish("t", "ON", s(30)))
        self.assertTrue(self.state.publish("t", "OFF", s(31)))
        self.assertEqual(len(self.client.published), 2)

    def test_republishes_after_sixty_seconds_unchanged(self):
        self.state.publish("t", "ON", s(0))
        self.assertFalse(self.state.publish("t", "ON", s(59)))
        self.assertTrue(self.state.publish("t", "ON", s(60)))

    def test_everything_is_retained(self):
        self.state.publish("t", "ON", s(0))
        self.assertTrue(self.client.published[0][3])

    def test_changed_only_never_repeats(self):
        self.assertTrue(self.state.changed_only("a", "x", s(0)))
        self.assertFalse(self.state.changed_only("a", "x", s(10_000)))

    def test_clear_sends_an_empty_retained_payload_and_forgets(self):
        self.state.publish("t", "ON", s(0))
        self.state.clear(["t"])
        self.assertEqual(self.client.published[-1], ("t", "", 0, True))
        self.assertTrue(self.state.publish("t", "ON", s(1)))

    def test_forget_republishes_after_a_reconnect(self):
        self.state.publish("t", "ON", s(0))
        self.state.forget()
        self.assertTrue(self.state.publish("t", "ON", s(1)))

    def test_announce_and_shutdown(self):
        self.state.announce("avail")
        self.state.shutdown("avail")
        self.assertEqual(self.client.published[0], ("avail", AVAILABILITY_ONLINE, 0, True))
        self.assertEqual(self.client.published[1], ("avail", AVAILABILITY_OFFLINE, 0, True))


class JournalTest(NoSocketTest):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)

    def test_daily_file_named_for_the_record(self):
        journal = Journal(self.dir)
        self.assertTrue(journal.write({"event": "decision"}, T0))
        path = self.dir / f"{FILE_PREFIX}-2026-09-17.jsonl"
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text(encoding="ascii"))["event"], "decision")

    def test_a_new_day_opens_a_new_file(self):
        journal = Journal(self.dir)
        journal.write({"n": 1}, T0)
        journal.write({"n": 2}, T0 + dt.timedelta(days=1))
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(names, [f"{FILE_PREFIX}-2026-09-17.jsonl", f"{FILE_PREFIX}-2026-09-18.jsonl"])

    def test_rotation_removes_files_older_than_thirty_days(self):
        journal = Journal(self.dir, retention_days=30)
        old = self.dir / f"{FILE_PREFIX}-2026-08-01.jsonl"
        keep = self.dir / f"{FILE_PREFIX}-2026-09-01.jsonl"
        stranger = self.dir / "notes.txt"
        for path in (old, keep, stranger):
            path.write_text("{}\n", encoding="ascii")
        removed = journal.rotate(T0)
        self.assertEqual(removed, [old.name])
        self.assertTrue(keep.exists())
        self.assertTrue(stranger.exists())

    def test_a_write_failure_is_counted_not_raised(self):
        journal = Journal(self.dir / "missing" / "deeper")
        with mock.patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")):
            self.assertFalse(journal.write({"n": 1}, T0))
        # The startup rotation fails first (no directory), then the write does;
        # both are counted, neither raises.
        self.assertGreaterEqual(journal.errors, 1)
        self.assertEqual(journal.as_dict()["last_error"], "PermissionError")
        self.assertEqual(journal.writes, 0)

    def test_disabled_journal_writes_nothing(self):
        journal = Journal(None, enabled=False)
        self.assertFalse(journal.write({"n": 1}, T0))
        self.assertEqual(journal.rotate(T0), [])


if __name__ == "__main__":
    unittest.main()
