"""The two input adapters: the MQTT mirror, Frigate's person topics, the observer.

Nothing here opens a socket (``socket.socket`` raises in every test) and the
observer is driven by a fake transport, so the contract these tests hold is
the parsing, the freshness and the refusals -- never a live broker or a live
observer.
"""
import datetime as dt
import json
import unittest
from unittest import mock

from lighting_publisher.activity import ZoneMap, load_zones
from lighting_publisher.inputs.mirror import (MIRROR_HEARTBEAT_TOPIC, STABLE_OCCUPANCY_HOLD_S,
                                              FrigateState, MirrorState, parse_time)
from lighting_publisher.inputs.observer import (ObserverClient, ObserverError, ObserverReading,
                                                UrllibTransport, parse_reading)

T0 = dt.datetime(2026, 9, 17, 22, 0, tzinfo=dt.timezone.utc)


def s(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def mirror_payload(entity_id: str, state: str, changed_at: dt.datetime,
                   attributes: dict | None = None) -> bytes:
    return json.dumps({"entity_id": entity_id, "state": state,
                       "changed_at": changed_at.isoformat(),
                       "attributes": attributes or {}}).encode("utf-8")


def mirror_topic(entity_id: str) -> str:
    return "living_lights/mirror/" + entity_id.replace(".", "/")


class NoSocketTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("socket.socket", side_effect=AssertionError("no sockets in tests"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.zones = load_zones()


class MirrorTest(NoSocketTest):
    def setUp(self) -> None:
        super().setUp()
        self.mirror = MirrorState()

    def feed(self, entity_id: str, state: str, changed_at: dt.datetime, now: dt.datetime,
             attributes: dict | None = None) -> str | None:
        return self.mirror.apply(mirror_topic(entity_id),
                                 mirror_payload(entity_id, state, changed_at, attributes),
                                 now)

    def test_reads_the_deployed_topic_shape(self):
        kind = self.feed("media_player.lg_tv", "playing", s(-5), s(0),
                         {"source": "hdmi1"})
        self.assertEqual(kind, "entity")
        self.assertEqual(self.mirror.tv_state, "playing")
        entity = self.mirror.entity("media_player.lg_tv")
        self.assertEqual(entity.changed_at, s(-5))
        self.assertEqual(entity.attributes["source"], "hdmi1")

    def test_every_contract_entity_is_understood(self):
        self.feed("input_boolean.user_at_home", "on", s(-60), s(0))
        self.feed("input_boolean.living_lights_asleep", "off", s(-120), s(0))
        self.feed("sensor.living_lights_profile", "overnight", s(-30), s(0))
        self.feed("input_boolean.living_lights_actuate_from_belief_changes", "on", s(-1), s(0))
        self.feed("input_boolean.living_lights_typesafe_egress_enabled", "off", s(-1), s(0))
        self.feed("input_boolean.living_lights_asleep_from_estimator", "off", s(-1), s(0))
        self.assertIs(self.mirror.user_at_home, True)
        self.assertEqual(self.mirror.user_at_home_changed, s(-60))
        self.assertIs(self.mirror.asleep_latch, False)
        self.assertEqual(self.mirror.profile, "overnight")
        self.assertIs(self.mirror.actuate_from_beliefs, True)
        self.assertIs(self.mirror.egress_enabled, False)
        self.assertIs(self.mirror.asleep_from_estimator, False)

    def test_unknown_state_is_not_a_boolean_and_not_a_profile(self):
        self.feed("input_boolean.user_at_home", "unavailable", s(-1), s(0))
        self.feed("sensor.living_lights_profile", "unknown", s(-1), s(0))
        self.assertIsNone(self.mirror.user_at_home)
        self.assertIsNone(self.mirror.profile)

    def test_heartbeat_topic_carries_the_iso_time(self):
        kind = self.mirror.apply(MIRROR_HEARTBEAT_TOPIC, s(-3).isoformat().encode("utf-8"), s(0))
        self.assertEqual(kind, "heartbeat")
        self.assertEqual(self.mirror.heartbeat_at, s(-3))

    def test_an_unparsable_heartbeat_still_proves_the_mirror_is_alive(self):
        self.mirror.apply(MIRROR_HEARTBEAT_TOPIC, b"not a time", s(0))
        self.assertEqual(self.mirror.heartbeat_at, s(0))

    def test_last_command_is_the_newest_known_zone_helper(self):
        self.feed("input_text.living_lights_zone_sofa_last_command_id", "cmd-1-a", s(-300), s(0))
        self.feed("input_text.living_lights_zone_sink_last_command_id", "cmd-2-b", s(-60), s(0))
        self.feed("input_text.living_lights_zone_office_last_command_id", "unknown", s(-1), s(0))
        self.assertEqual(self.mirror.last_command_at(), s(-60))
        self.assertEqual(sorted(self.mirror.command_helpers()), ["office", "sink", "sofa"])

    def test_no_command_helper_means_no_brighten(self):
        self.assertIsNone(self.mirror.last_command_at())

    def test_the_asleep_writer_is_read_when_it_is_mirrored(self):
        self.assertIsNone(self.mirror.asleep_writer)
        self.feed("input_text.living_lights_asleep_writer", "manual", s(-10), s(0))
        self.assertEqual(self.mirror.asleep_writer, "manual")

    def test_malformed_messages_are_counted_not_raised(self):
        self.assertIsNone(self.mirror.apply(mirror_topic("sensor.x"), b"{not json", s(0)))
        self.assertIsNone(self.mirror.apply(mirror_topic("sensor.x"), b"[]", s(0)))
        self.assertIsNone(self.mirror.apply("living_lights/mirror/too/many/parts", b"{}", s(0)))
        self.assertIsNone(self.mirror.apply(mirror_topic("sensor.x"),
                                            json.dumps({"state": 3}).encode(), s(0)))
        self.assertEqual(self.mirror.malformed, 4)
        self.assertEqual(self.mirror.entities, {})

    def test_a_foreign_topic_is_not_ours(self):
        self.assertIsNone(self.mirror.apply("frigate/kitchen/person", b"1", s(0)))

    def test_the_topic_constants_agree_with_their_bases(self):
        from lighting_publisher.inputs import mirror as mirror_mod

        self.assertEqual(mirror_mod.MIRROR_TOPIC, mirror_mod.MIRROR_BASE + "/#")
        self.assertEqual(mirror_mod.MIRROR_HEARTBEAT_TOPIC,
                         mirror_mod.MIRROR_BASE + "/heartbeat")
        self.assertEqual(mirror_mod.FRIGATE_CAMERA_TOPIC,
                         mirror_mod.FRIGATE_BASE + "/+/person")
        self.assertEqual(mirror_mod.FRIGATE_ZONE_TOPIC,
                         mirror_mod.FRIGATE_BASE + "/+/+/person")

    def test_parse_time_needs_a_zone(self):
        self.assertIsNone(parse_time("2026-09-17T22:00:00"))
        self.assertEqual(parse_time("2026-09-17T22:00:00Z"), T0)
        self.assertIsNone(parse_time(None))


class FrigateTest(NoSocketTest):
    def setUp(self) -> None:
        super().setUp()
        self.frigate = FrigateState(self.zones)

    def test_camera_person_counts_and_edges(self):
        self.assertEqual(self.frigate.apply("frigate/kitchen/person", b"1", s(0)), "camera")
        self.assertTrue(self.frigate.camera_person("kitchen"))
        self.assertEqual(self.frigate.camera_changed("kitchen"), s(0))
        self.frigate.apply("frigate/kitchen/person", b"1", s(10))
        self.assertEqual(self.frigate.camera_changed("kitchen"), s(0), "no change, no edge")
        self.frigate.apply("frigate/kitchen/person", b"2", s(20))
        self.assertEqual(self.frigate.camera_changed("kitchen"), s(20))
        self.frigate.apply("frigate/kitchen/person", b"0", s(30))
        self.assertFalse(self.frigate.camera_person("kitchen"))

    def test_zone_topics_fill_the_occupancy_map(self):
        self.frigate.apply("frigate/living_room/sofa/person", b"1", s(0))
        self.assertTrue(self.frigate.zone_occupied("sofa"))
        occupancy = self.frigate.occupancy()
        self.assertEqual(set(occupancy), set(self.zones.zones))
        self.assertTrue(occupancy["sofa"])
        self.assertFalse(occupancy["sink"])

    def test_a_zone_arrives_at_two_segments_because_frigate_names_zones_globally(self):
        """The shape the house actually publishes.

        Frigate 0.17.2 does not namespace a zone under its camera: the sofa's
        person count is ``frigate/sofa/person``. Every test below this one was
        written against ``frigate/living_room/sofa/person``, which Frigate
        never sends, so the publisher read no zone occupancy at all from the
        live broker and the sofa guard that makes UNATTENDED unreachable could
        never have held.
        """
        self.assertEqual(self.frigate.apply("frigate/sofa/person", b"1", s(0)), "zone")
        self.assertTrue(self.frigate.zone_occupied("sofa"))
        self.assertTrue(self.frigate.sofa_stable(s(1)))

    def test_two_segment_zone_names_keep_frigates_own_capitalisation(self):
        for topic, zone in (("frigate/Whole_Living_Room/person", "whole_living_room"),
                            ("frigate/Front_Door/person", "front_door"),
                            ("frigate/Dining_Left/person", "dining_left"),
                            ("frigate/Island_Right/person", "island_right")):
            with self.subTest(topic=topic):
                frigate = FrigateState(self.zones)
                self.assertEqual(frigate.apply(topic, b"1", s(0)), "zone")
                self.assertTrue(frigate.zone_occupied(zone))

    def test_every_living_room_zone_frigate_publishes_reaches_the_map(self):
        """The living-room zones as Frigate spells them on this house."""
        for topic in ("frigate/sofa/person", "frigate/front_left/person",
                      "frigate/weights/person", "frigate/office/person",
                      "frigate/Front_Door/person", "frigate/Whole_Living_Room/person"):
            with self.subTest(topic=topic):
                frigate = FrigateState(self.zones)
                self.assertEqual(frigate.apply(topic, b"1", s(0)), "zone")
                self.assertTrue(frigate.living_room_occupied())

    def test_a_camera_name_wins_over_a_zone_of_the_same_name(self):
        """Disjoint today; the rule keeps a camera added later out of a zone."""
        zones = ZoneMap(zones={**dict(self.zones.zones), "kitchen": "kitchen"},
                        cameras=self.zones.cameras, dominates=self.zones.dominates)
        frigate = FrigateState(zones)
        self.assertEqual(frigate.apply("frigate/kitchen/person", b"1", s(0)), "camera")
        self.assertTrue(frigate.camera_person("kitchen"))
        self.assertFalse(frigate.zone_occupied("kitchen"))

    def test_a_two_segment_name_that_is_neither_is_ignored(self):
        for topic in ("frigate/e28/person", "frigate/workshop_zone/person",
                      "frigate/nursery/person"):
            with self.subTest(topic=topic):
                self.assertIsNone(self.frigate.apply(topic, b"1", s(0)))
        self.assertEqual(self.frigate.ignored, 3)
        self.assertEqual(self.frigate.messages, 0)

    def test_zone_names_are_matched_case_insensitively(self):
        self.assertEqual(self.frigate.apply("frigate/living_room/Front_Door/person", b"1", s(0)),
                         "zone")
        self.assertTrue(self.frigate.front_door_occupied())
        self.assertEqual(self.frigate.front_door_changed(), s(0))

    def test_a_zone_on_the_wrong_camera_is_ignored(self):
        self.assertIsNone(self.frigate.apply("frigate/kitchen/sofa/person", b"1", s(0)))
        self.assertFalse(self.frigate.zone_occupied("sofa"))
        self.assertEqual(self.frigate.ignored, 1)

    def test_cameras_outside_the_zone_map_are_ignored(self):
        self.assertIsNone(self.frigate.apply("frigate/driveway/person", b"1", s(0)))
        self.assertIsNone(self.frigate.apply("frigate/workshop/workshop_zone/person", b"1", s(0)))
        self.assertEqual(self.frigate.ignored, 2)
        self.assertFalse(self.frigate.camera_seen("driveway"))

    def test_stable_occupancy_holds_for_ninety_seconds(self):
        self.frigate.apply("frigate/living_room/sofa/person", b"1", s(0))
        self.frigate.apply("frigate/living_room/sofa/person", b"0", s(100))
        self.assertTrue(self.frigate.sofa_stable(s(100 + STABLE_OCCUPANCY_HOLD_S - 1)))
        self.assertFalse(self.frigate.sofa_stable(s(100 + STABLE_OCCUPANCY_HOLD_S)))

    def test_a_zone_never_seen_is_not_stable(self):
        self.assertFalse(self.frigate.sofa_stable(s(0)))

    def test_living_room_occupancy_is_any_of_its_zones(self):
        self.assertFalse(self.frigate.living_room_occupied())
        self.frigate.apply("frigate/living_room/office/person", b"1", s(0))
        self.assertTrue(self.frigate.living_room_occupied())

    def test_malformed_counts_are_counted(self):
        self.assertIsNone(self.frigate.apply("frigate/kitchen/person", b"many", s(0)))
        self.assertEqual(self.frigate.malformed, 1)
        self.assertIsNone(self.frigate.apply("frigate/kitchen/events", b"1", s(0)))


class FakeTransport:
    """A transport that returns a canned body or raises."""

    def __init__(self, body: bytes | None = None, error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.body or b"{}"


class ObserverTest(NoSocketTest):
    def test_the_live_payload_shape_is_read_from_latest(self):
        """The shape this observer actually serves.

        Its ``cameras`` map carries stream health and an inference gate, not
        presence; the detector results live under ``latest.<camera>.<worker>``.
        Reading only ``cameras`` gave three cameras and zero readings against
        the live observer, so the corroboration the night guard rests on would
        never have been satisfied.
        """
        payload = {
            "now": 1789752758.9,
            "cameras": {
                "living_room": {"frame_age": 0.4, "health": "ok",
                                "inference_gate": {"occupancy": "occupied",
                                                   "reason": "person detected by Frigate"}},
                "kitchen": {"frame_age": 0.5, "health": "ok",
                            "inference_gate": {"occupancy": "vacant"}},
            },
            "latest": {
                "living_room": {"objects": {"valid": True, "total_people": 1, "people": [{}]},
                                "rtmw": {"valid": True, "people": []}},
                "kitchen": {"objects": {"valid": True, "total_people": 0, "people": []}},
            },
        }
        reading = parse_reading(payload, T0)
        self.assertEqual(reading.cameras_read, 2)
        self.assertTrue(reading.track_in("living_room"))
        self.assertEqual(reading.cameras["living_room"].people, 1)
        self.assertFalse(reading.track_in("kitchen"))
        self.assertEqual(reading.cameras["kitchen"].people, 0)

    def test_the_frigate_derived_inference_gate_is_never_read(self):
        """Corroboration has to come from something other than Frigate.

        ``inference_gate.occupancy`` is the one field that says "occupied" in
        words, and on this house its own reason is "person detected by
        Frigate". Reading it would make the observer agree with Frigate by
        construction, so a stuck Frigate zone would read as confirmed by vision
        and the night guard would rest on one sensor while appearing to rest on
        two.
        """
        payload = {
            "cameras": {"living_room": {"inference_gate": {"occupancy": "occupied",
                                                           "reason": "person detected by Frigate"}}},
            "latest": {"living_room": {"objects": {"valid": True, "total_people": 0, "people": []}}},
        }
        reading = parse_reading(payload, T0)
        self.assertFalse(reading.track_in("living_room"))
        self.assertEqual(reading.cameras["living_room"].people, 0)

    def test_a_worker_that_produced_no_result_is_passed_over(self):
        """valid false is "no answer", which is not "nobody there"."""
        payload = {"latest": {"living_room": {
            "objects": {"valid": False, "total_people": 0, "people": []},
            "rtmw": {"valid": True, "people": [{}, {}]}}}}
        reading = parse_reading(payload, T0)
        self.assertTrue(reading.track_in("living_room"))
        self.assertEqual(reading.cameras["living_room"].people, 2)

    def test_a_camera_with_no_usable_worker_has_no_reading(self):
        payload = {"latest": {"living_room": {"objects": {"valid": False}}}}
        reading = parse_reading(payload, T0)
        self.assertEqual(reading.cameras_read, 0)
        self.assertIsNone(reading.present("living_room"))

    def test_the_documented_flat_shapes_still_parse(self):
        """Another observer, or a fixture, may send presence directly."""
        for payload in ({"cameras": {"living_room": {"person_present": True}}},
                        {"cameras": {"living_room": 2}},
                        {"cameras": {"living_room": [{}, {}]}},
                        {"state": {"cameras": {"living_room": {"people": [{}]}}}}):
            with self.subTest(payload=payload):
                self.assertTrue(parse_reading(payload, T0).track_in("living_room"))

    def test_the_size_cap_fits_the_observers_whole_state(self):
        """The observer serves its entire state, images included.

        Measured at 717 KiB on 2026-09-18, against a 256 KiB cap that made
        every poll fail. There is no narrower endpoint and no query parameter
        that trims it, so the cap has to fit it.
        """
        from lighting_publisher.inputs.observer import MAX_BYTES
        self.assertGreaterEqual(MAX_BYTES, 1024 * 1024,
                                "a cap under a megabyte fails every poll against this observer")

    def test_url_validation_refuses_anything_but_http(self):
        for bad in ("", "file:///etc/passwd", "ftp://host/x", "not a url", "http:///nohost"):
            with self.assertRaises(ValueError):
                ObserverClient(bad, transport=FakeTransport())

    def test_default_path_is_added_once(self):
        client = ObserverClient("http://192.168.0.100:8767", transport=FakeTransport())
        self.assertEqual(client.url, "http://192.168.0.100:8767/api/state")
        explicit = ObserverClient("http://192.168.0.100:8767/api/other/", transport=FakeTransport())
        self.assertEqual(explicit.url, "http://192.168.0.100:8767/api/other")

    def test_a_reading_is_parsed_from_the_cameras_map(self):
        body = json.dumps({"cameras": {"living_room": {"person_present": True},
                                       "kitchen": {"person_count": 0},
                                       "dining_room": {}}}).encode("utf-8")
        client = ObserverClient("http://host:1/api/state", transport=FakeTransport(body))
        reading = client.poll(T0)
        self.assertIs(reading.present("living_room"), True)
        self.assertIs(reading.present("kitchen"), False)
        self.assertIsNone(reading.present("dining_room"))
        self.assertEqual(reading.cameras_read, 2)
        self.assertTrue(reading.track_in("living_room"))
        self.assertFalse(reading.track_in("dining_room"))

    def test_other_documented_shapes(self):
        reading = parse_reading({"state": {"cameras": {"kitchen": {"people": [1, 2]}}}}, T0)
        self.assertIs(reading.present("kitchen"), True)
        self.assertEqual(reading.cameras["kitchen"].people, 2)
        reading = parse_reading({"by_camera": {"kitchen": True, "sofa": 0}}, T0)
        self.assertIs(reading.present("kitchen"), True)
        self.assertIs(reading.present("sofa"), False)

    def test_an_unknown_shape_reads_as_no_reading_not_as_absence(self):
        reading = parse_reading({"cameras": {"kitchen": {"mood": "calm"}}}, T0)
        self.assertIsNone(reading.present("kitchen"))
        self.assertEqual(reading.cameras_read, 0)
        self.assertEqual(parse_reading([1, 2, 3], T0).cameras, {})

    def test_a_failed_poll_raises_and_is_counted(self):
        client = ObserverClient("http://host:1/api/state",
                                transport=FakeTransport(error=ObserverError("unreachable")))
        with self.assertRaises(ObserverError):
            client.poll(T0)
        self.assertEqual(client.failures, 1)
        self.assertIsNone(client.last)

    def test_a_body_that_is_not_json_is_a_failure(self):
        client = ObserverClient("http://host:1/api/state", transport=FakeTransport(b"<html>"))
        with self.assertRaises(ObserverError):
            client.poll(T0)
        self.assertEqual(client.failures, 1)

    def test_a_successful_poll_that_sees_nobody_is_still_a_poll(self):
        client = ObserverClient("http://host:1/api/state",
                                transport=FakeTransport(b'{"cameras": {}}'))
        reading = client.poll(T0)
        self.assertIsInstance(reading, ObserverReading)
        self.assertEqual(client.failures, 0)
        self.assertIs(client.last, reading)

    def test_the_urllib_transport_knows_only_http_and_refuses_redirects(self):
        transport = UrllibTransport()
        handlers = [type(h).__name__ for h in transport._opener.handlers]
        self.assertIn("RefuseRedirects", handlers)
        for forbidden in ("FileHandler", "FTPHandler", "DataHandler", "UnknownHandler"):
            self.assertNotIn(forbidden, handlers)
        self.assertEqual(sorted(transport._opener.handle_open), ["http", "https"])

    def test_no_proxy_is_registered_even_with_proxy_environment_variables(self):
        with mock.patch.dict("os.environ", {"http_proxy": "http://proxy:3128",
                                            "https_proxy": "http://proxy:3128"}):
            transport = UrllibTransport()
        openers = transport._opener.handle_open
        self.assertEqual(sorted(openers), ["http", "https"])
        self.assertEqual([type(h).__name__ for h in openers["http"]], ["HTTPHandler"])

    def test_the_redirect_handler_raises(self):
        from lighting_publisher.inputs.observer import RefuseRedirects
        import urllib.error

        handler = RefuseRedirects()
        request = mock.Mock(full_url="http://host/x")
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, "moved", {}, "http://elsewhere/")


if __name__ == "__main__":
    unittest.main()
