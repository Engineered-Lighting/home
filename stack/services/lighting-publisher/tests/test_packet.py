"""Packet builder: allow-list, caps, anonymisation, fail-closed leak scan, signature stability."""
import json
import unittest
from pathlib import Path

from lighting_beliefs import packet as pk

FIXTURES = Path(__file__).parent / "fixtures"


def sample_cameras():
    return {
        "living_room": {
            "coverage_status": "fresh", "coverage_age_s": 4.7,
            "zones": {"sofa": "occupied", "front_left": "clear"},
            "people": [{"track_id": "1789697770.123456-abc123", "name": "Someone", "relationships": ["partner"],
                        "position": "sofa", "posture": "reclined", "activities": ["watching screen"]}],
            "semantic": {"posture": "reclined", "activities": ["watching screen"],
                         "summary": "One person reclined on the sofa facing the screen.",
                         "context": {"cognitive_account": {"summary": "Settled on the sofa for a while."}}},
            "person_adjacent": ["remote", "blanket"],
        }
    }


def plain_camera(**overrides):
    cam = {"coverage_status": "fresh", "coverage_age_s": 3, "zones": {"sofa": "occupied"}, "claims": ["one person on the sofa"]}
    cam.update(overrides)
    return cam


class PacketBuilderTest(unittest.TestCase):
    def test_builds_valid_packet_and_strips_identity(self):
        packet = pk.build_packet(sample_cameras(), [{"role": "tv", "state": "playing", "source_kind": "streaming", "age_s": 1240.9}], 45)
        pk.validate_packet(packet)
        person = packet["cameras"]["living_room"]["people"][0]
        self.assertEqual(person["track"], "p1")
        for field in pk.STRIPPED_PERSON_FIELDS:
            self.assertNotIn(field, person)
        self.assertNotIn("1789697770", json.dumps(packet))
        self.assertEqual(packet["cameras"]["living_room"]["coverage"]["age_s"], 4)
        self.assertEqual(packet["devices"]["media"][0]["age_s"], 1240)
        self.assertEqual(packet["quiet"]["credible_activity_age_s"], 45)
        self.assertEqual(packet["cameras"]["living_room"]["account"], "Settled on the sofa for a while.")

    def test_caps_text(self):
        cams = sample_cameras()
        cams["living_room"]["semantic"]["summary"] = "x" * 2400
        cams["living_room"]["claims"] = ["y " * 500]
        packet = pk.build_packet(cams, [], 10)
        for claim in packet["cameras"]["living_room"]["claims"]:
            self.assertLessEqual(len(claim), pk.TEXT_CAP)
        self.assertEqual(len(packet["cameras"]["living_room"]["claims"][0]), pk.TEXT_CAP)

    def test_ages_above_cap_raise_instead_of_clamping(self):
        for bad in (10 ** 12, 1789697770, "1789697770", pk.AGE_CAP_S + 1, float("inf"), float("nan"), -60):
            with self.subTest(age=bad):
                with self.assertRaises(pk.PacketError):
                    pk.build_packet(sample_cameras(), [], bad)
                with self.assertRaises(pk.PacketError):
                    pk.build_packet({"lr": plain_camera(coverage_age_s=bad)})
                with self.assertRaises(pk.PacketError):
                    pk.build_packet({"lr": plain_camera()}, [{"role": "tv", "age_s": bad}])
        self.assertEqual(pk.clamp_age(pk.AGE_CAP_S), pk.AGE_CAP_S)
        self.assertEqual(pk.clamp_age(-0.4), 0)
        self.assertIsNone(pk.clamp_age(None))
        self.assertIsNone(pk.clamp_age(True))
        self.assertIsNone(pk.clamp_age("unknown"))

    def test_rejects_disallowed_key(self):
        packet = pk.build_packet(sample_cameras(), [], 45)
        packet["cameras"]["living_room"]["entity_id"] = "camera.living_room"
        with self.assertRaises(pk.PacketError) as ctx:
            pk.validate_packet(packet)
        self.assertEqual(str(ctx.exception), "$.cameras.<key#0>.<key#6>: key not allowed")
        packet = pk.build_packet(sample_cameras(), [], 45)
        packet["clock"] = "23:59"
        with self.assertRaises(pk.PacketError):
            pk.validate_packet(packet)
        packet = pk.build_packet(sample_cameras(), [], 45)
        packet["cameras"]["living_room"]["people"][0]["name"] = "x"
        with self.assertRaises(pk.PacketError):
            pk.validate_packet(packet)

    def test_rejects_overlong_text_and_epoch_numbers(self):
        packet = pk.build_packet(sample_cameras(), [], 45)
        packet["cameras"]["living_room"]["claims"].append("z" * (pk.TEXT_CAP + 1))
        with self.assertRaises(pk.PacketError):
            pk.validate_packet(packet)
        packet = pk.build_packet(sample_cameras(), [], 45)
        packet["quiet"]["credible_activity_age_s"] = 1789697770
        with self.assertRaises(pk.PacketError):
            pk.validate_packet(packet)

    def test_builder_is_fail_closed_on_leak_patterns(self):
        cases = {
            "entity id camera key": dict(cameras={"camera.living_room": plain_camera()}),
            "entity id zone key": dict(cameras={"lr": plain_camera(zones={"binary_sensor.sofa_occ": "occupied"})}),
            "mixed case entity id in position": dict(cameras={"lr": plain_camera(people=[{"position": "Light.Sofa_Lamp"}])}),
            "entity id in media role": dict(cameras={"lr": plain_camera()}, media=[{"role": "media_player.living_room_tv"}]),
            "iso timestamp in claim": dict(cameras={"lr": plain_camera(claims=["seen at 2026-09-17T19:25:00"])}),
            "time of day in claim": dict(cameras={"lr": plain_camera(claims=["wall clock reads 19:25"])}),
            "numeric date in summary": dict(cameras={"lr": plain_camera(semantic={"summary": "overlay reads 09/17/2026"})}),
            "phone number in account": dict(cameras={"lr": plain_camera(semantic={"context": {"cognitive_account": "call 555-123-4567"}})}),
            "hostname in object": dict(cameras={"lr": plain_camera(person_adjacent=["nas.local"])}),
            "zero width split entity id": dict(cameras={"lr": plain_camera(claims=["light.\u200bsofa_lamp is on"])}),
            "homoglyph entity id": dict(cameras={"lr": plain_camera(claims=["l\u0456ght.sofa_lamp is on"])}),
            "accented text": dict(cameras={"lr": plain_camera(claims=["caf\u00e9 table"])}),
            "non ascii zone key": dict(cameras={"lr": plain_camera(zones={"sof\u00e1": "clear"})}),
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                with self.assertRaises(pk.PacketError) as ctx:
                    pk.build_packet(**kwargs)
                message = str(ctx.exception)
                self.assertIn("leak pattern", message)
                for secret in ("sofa_lamp", "living_room_tv", "sofa_occ", "2026", "19:25", "555", "nas.local", "caf"):
                    self.assertNotIn(secret, message, label)

    def test_builder_refuses_whitespace_split_entity_ids(self):
        cases = {
            "newline after dot in claim": dict(cameras={"lr": plain_camera(claims=["light.\nsofa_lamp is on"])}),
            "space after dot in claim": dict(cameras={"lr": plain_camera(claims=["light. sofa_lamp is on"])}),
            "space before dot in claim": dict(cameras={"lr": plain_camera(claims=["light .sofa_lamp is on"])}),
            "newline after dot in zone key": dict(cameras={"lr": plain_camera(zones={"light.\nsofa_lamp": "occupied"})}),
            "newline after dot in camera key": dict(cameras={"light.\nliving_room": plain_camera()}),
            "space after dot in media role": dict(cameras={"lr": plain_camera()}, media=[{"role": "media_player. living_room_tv"}]),
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                with self.assertRaises(pk.PacketError) as ctx:
                    pk.build_packet(**kwargs)
                message = str(ctx.exception)
                self.assertIn("leak pattern (ha_entity_id)", message)
                for secret in ("sofa_lamp", "living_room", "light"):
                    self.assertNotIn(secret, message, label)
        packet = pk.build_packet({"lr": plain_camera(claims=["Turned off the light. Sofa is empty."])})
        self.assertEqual(packet["cameras"]["lr"]["claims"][-1], "Turned off the light. Sofa is empty.")

    def test_builder_refuses_osd_dates_and_spoken_clocks(self):
        cases = {
            "ymd overlay": ["overlay 2026/09/17"], "dotted two digit year": ["overlay 17.9.26"],
            "dashed two digit year": ["17-9-26"], "month name": ["calendar shows 17 Sep 2026"],
            "month first": ["calendar shows September 17th"], "pm clock": ["clock shows 7pm"],
            "spaced pm clock": ["clock shows 7 pm"], "h clock": ["at 19h25"], "military clock": ["1925 hours"],
        }
        for label, claims in cases.items():
            with self.subTest(label):
                with self.assertRaises(pk.PacketError) as ctx:
                    pk.build_packet({"lr": plain_camera(claims=claims)})
                message = str(ctx.exception)
                self.assertIn("leak pattern", message)
                for secret in ("2026", "17", "Sep", "7", "19", "25"):
                    self.assertNotIn(secret, message, label)
        packet = pk.build_packet({"lr": plain_camera(claims=["1 may be asleep, 2 on the sofa"])})
        self.assertEqual(packet["cameras"]["lr"]["claims"][-1], "1 may be asleep, 2 on the sofa")

    def test_builder_refuses_extended_entity_domains(self):
        for text in ("group.living_room_lights", "sun.sun", "timer.evening", "remote.living_room",
                     "input_datetime.bedtime", "frigate.living_room", "assist_satellite.living_room"):
            with self.subTest(text=text):
                with self.assertRaises(pk.PacketError) as ctx:
                    pk.build_packet({"lr": plain_camera()}, [{"role": text}])
                self.assertIn("leak pattern", str(ctx.exception))
                self.assertNotIn("living_room", str(ctx.exception))

    def test_error_paths_never_echo_untrusted_keys(self):
        name = "Marisol Quintero"
        messages = []
        expectations = (
            (lambda: pk.build_packet({name: plain_camera(zones={"binary_sensor.sofa": "occupied"})}),
             "$.cameras.<key#0>.occupancy.zones.<key#0>: leak pattern (ha_entity_id)"),
            (lambda: pk.build_packet({name: plain_camera(claims=["seen at 19:25"])}),
             "$.cameras.<key#0>.claims[0]: leak pattern (time_of_day)"),
            (lambda: pk.build_packet({name + "x" * 30: plain_camera(), name + "x" * 40: plain_camera()}),
             "$.cameras: name collision after capping (entries 0 and 1)"),
            (lambda: pk.build_packet({"lr": plain_camera(zones={name + "x" * 30: "clear", name + "x" * 40: "occupied"})}),
             "$.cameras.<key#0>.occupancy.zones: name collision after capping (entries 0 and 1)"),
            (lambda: pk.build_packet({name: "not a mapping"}),
             "$.cameras.<key#0>: camera input must be a mapping"),
            (lambda: pk.validate_packet({"schema": pk.PACKET_SCHEMA, "cameras": {"lr": {(name + " ") * 40: "x"}}}),
             "$.cameras.<key#0>.<key#0>: key not allowed"),
            (lambda: pk.validate_packet({"schema": pk.PACKET_SCHEMA, "cameras": {name: {"coverage": {"status": {"x": 1}}}}}),
             "$.cameras.<key#0>.coverage.status: object where a scalar or list was expected"),
        )
        for index, (action, expected) in enumerate(expectations):
            with self.subTest(index=index):
                with self.assertRaises(pk.PacketError) as ctx:
                    action()
                self.assertEqual(str(ctx.exception), expected)
                messages.append(str(ctx.exception))
        for message in messages:
            self.assertNotIn("Marisol", message)
            self.assertNotIn("Quintero", message)
            self.assertLess(len(message), 100)

    def test_leaking_key_never_echoed_in_error(self):
        with self.assertRaises(pk.PacketError) as ctx:
            pk.build_packet({"camera.living_room": plain_camera()})
        self.assertEqual(str(ctx.exception), "$.cameras.<key#0>: leak pattern (ha_entity_id)")
        packet = pk.build_packet({"lr": plain_camera()})
        packet["cameras"]["lr"]["light.sofa_lamp"] = "on"
        with self.assertRaises(pk.PacketError) as ctx:
            pk.validate_packet(packet)
        self.assertNotIn("sofa_lamp", str(ctx.exception))
        self.assertIn("<key#", str(ctx.exception))

    def test_size_caps(self):
        cam = plain_camera()
        pk.build_packet({f"c{i}": cam for i in range(pk.MAX_CAMERAS)})
        with self.assertRaises(pk.PacketError):
            pk.build_packet({f"c{i}": cam for i in range(pk.MAX_CAMERAS + 1)})
        pk.build_packet({"lr": plain_camera(zones={f"z{i}": "clear" for i in range(pk.MAX_ZONES)})})
        with self.assertRaises(pk.PacketError):
            pk.build_packet({"lr": plain_camera(zones={f"z{i}": "clear" for i in range(pk.MAX_ZONES + 1)})})
        pk.build_packet({"lr": cam}, [{"role": "tv"}] * pk.MAX_MEDIA)
        with self.assertRaises(pk.PacketError):
            pk.build_packet({"lr": cam}, [{"role": "tv"}] * (pk.MAX_MEDIA + 1))
        crowd = pk.build_packet({"lr": plain_camera(people=[{"position": "sofa"}] * (pk.MAX_PEOPLE + 3))})
        self.assertEqual(len(crowd["cameras"]["lr"]["people"]), pk.MAX_PEOPLE)
        for path, cap in (("people", pk.MAX_PEOPLE), ("claims", pk.MAX_CLAIMS)):
            packet = pk.build_packet({"lr": cam})
            packet["cameras"]["lr"][path] = ([{"track": "p1"}] if path == "people" else ["c"]) * (cap + 1)
            with self.assertRaises(pk.PacketError, msg=path):
                pk.validate_packet(packet)
        packet = pk.build_packet({"lr": cam})
        packet["devices"]["media"] = [{"role": "tv"}] * (pk.MAX_MEDIA + 1)
        with self.assertRaises(pk.PacketError):
            pk.validate_packet(packet)

    def test_name_collision_after_capping_raises(self):
        with self.assertRaises(pk.PacketError) as ctx:
            pk.build_packet({"a" * 45: plain_camera(), "a" * 41: plain_camera()})
        self.assertIn("collision", str(ctx.exception))
        with self.assertRaises(pk.PacketError):
            pk.build_packet({"lr": plain_camera(zones={"z" * 45: "clear", "z" * 41: "occupied"})})

    def test_malformed_people_entries_are_skipped_not_truncating(self):
        packet = pk.build_packet({"lr": plain_camera(people=["junk", None, {"position": "sofa"}, 7, {"posture": "standing"}])})
        people = packet["cameras"]["lr"]["people"]
        self.assertEqual([p["track"] for p in people], ["p1", "p2"])
        self.assertEqual(people[0]["position"], "sofa")
        self.assertEqual(people[1]["posture"], "standing")

    def test_list_versus_mapping_strictness(self):
        with self.assertRaises(pk.PacketError):
            pk.validate_packet({"schema": pk.PACKET_SCHEMA, "cameras": [{"lr": {"claims": ["x"]}}]})
        packet = pk.build_packet({"lr": plain_camera()})
        packet["cameras"]["lr"]["people"] = {"track": "p1"}
        with self.assertRaises(pk.PacketError):
            pk.validate_packet(packet)
        packet = pk.build_packet({"lr": plain_camera()})
        packet["devices"]["media"] = {"role": "tv"}
        with self.assertRaises(pk.PacketError):
            pk.validate_packet(packet)
        packet = pk.build_packet({"lr": plain_camera()})
        packet["cameras"]["lr"]["coverage"] = [{"status": "fresh"}]
        with self.assertRaises(pk.PacketError):
            pk.validate_packet(packet)
        with self.assertRaises(pk.PacketError):
            pk.build_packet([plain_camera()])
        with self.assertRaises(pk.PacketError):
            pk.build_packet({"lr": plain_camera(zones=["sofa"])})

    def test_golden_fixtures_validate(self):
        for name in ("tv_evening.json", "quiet_night.json"):
            packet = json.loads((FIXTURES / name).read_text())
            pk.validate_packet(packet)

    def test_signature_stable_across_key_order_and_age_jitter(self):
        packet = pk.build_packet(sample_cameras(), [{"role": "tv", "state": "playing", "source_kind": "streaming", "age_s": 1240}], 45)
        reordered = json.loads(json.dumps(packet, sort_keys=True))
        shuffled = {k: reordered[k] for k in reversed(list(reordered))}
        self.assertEqual(pk.content_signature(packet), pk.content_signature(shuffled))
        jitter = json.loads(json.dumps(packet))
        jitter["cameras"]["living_room"]["coverage"]["age_s"] = 9
        jitter["quiet"]["credible_activity_age_s"] = 52
        jitter["devices"]["media"][0]["age_s"] = 1700
        self.assertEqual(pk.content_signature(packet), pk.content_signature(jitter))
        crossed = json.loads(json.dumps(packet))
        crossed["quiet"]["credible_activity_age_s"] = 1000
        self.assertNotEqual(pk.content_signature(packet), pk.content_signature(crossed))
        changed = json.loads(json.dumps(packet))
        changed["cameras"]["living_room"]["occupancy"]["zones"]["sofa"] = "clear"
        self.assertNotEqual(pk.content_signature(packet), pk.content_signature(changed))

    def test_signature_buckets_by_path_not_key_name(self):
        cam = plain_camera(zones={"age_s": "occupied", "credible_activity_age_s": "clear"})
        packet = pk.build_packet({"age_s": cam}, [], 5)
        self.assertIsInstance(pk.content_signature(packet), str)
        coarse = pk._coarsen(packet)
        self.assertEqual(coarse["cameras"]["age_s"]["occupancy"]["zones"]["age_s"], "occupied")
        self.assertEqual(coarse["cameras"]["age_s"]["coverage"]["age_s"], 0)
        self.assertEqual(coarse["quiet"]["credible_activity_age_s"], 0)


if __name__ == "__main__":
    unittest.main()
