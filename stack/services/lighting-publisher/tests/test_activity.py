"""Activity sensors: cooking/eating only in occupied kitchen or dining zones, idle otherwise."""
import datetime as dt
import json
import pathlib
import tempfile
import unittest

from lighting_publisher import stories
from lighting_publisher.activity import (COOKING, EATING, IDLE, ActivityTracker, ZoneMap, activity_entities,
                                         load_zones)
from lighting_publisher.beliefs import Beliefs

T0 = dt.datetime(2026, 9, 17, 19, 30, tzinfo=dt.timezone.utc)


def s(seconds: int) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def belief(food_prep=(1.0, 0.0, 0.0), eating=0.0) -> Beliefs:
    return Beliefs(at=T0, request_id="r", p_attention=(1.0, 0.0, 0.0), p_eating=eating, p_food_prep=food_prep,
                   p_settling=(1.0, 0.0, 0.0), p_rest=(1.0, 0.0, 0.0))


class ZoneMapTest(unittest.TestCase):
    def test_default_file_loads_and_matches_the_harness(self):
        zones = load_zones()
        self.assertEqual(zones.cameras, ("dining_room", "kitchen", "living_room"))
        self.assertEqual(zones.camera_of("sofa"), "living_room")
        self.assertEqual(zones.camera_of("sink"), "kitchen")
        self.assertEqual(zones.camera_of("dining_left"), "dining_room")
        self.assertEqual(zones.dominates, {"sofa": ("front_left",)})
        self.assertEqual(set(zones.activity_zones()),
                         {"sink", "island_left", "island_right", "whole_kitchen", "dining_left", "dining_right",
                          "whole_dining_room"})
        self.assertEqual(zones.entity_object_id("island_left"), "kitchen_island_left_activity")
        self.assertNotIn("workshop_zone", zones.zones)

    def test_sofa_dominates_front_left(self):
        zones = load_zones()
        occ = zones.effective_occupancy({"sofa": True, "front_left": True, "office": True})
        self.assertTrue(occ["sofa"])
        self.assertFalse(occ["front_left"])
        self.assertTrue(occ["office"])
        occ = zones.effective_occupancy({"front_left": True})
        self.assertTrue(occ["front_left"])
        self.assertTrue(zones.living_room_occupied({"front_door": True}))
        self.assertFalse(zones.living_room_occupied({"sink": True}))

    def test_rejects_bad_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "zones.json"
            bad.write_text(json.dumps({"schema": "living-lights-zones/v1", "cameras": ["kitchen"],
                                       "zones": {"sofa": "living_room"}}))
            with self.assertRaises(ValueError):
                load_zones(bad)
            bad.write_text(json.dumps({"schema": "other", "cameras": [], "zones": {}}))
            with self.assertRaises(ValueError):
                load_zones(bad)

    def test_the_three_story_keys_must_name_things_that_exist(self):
        """The keys the stories rest on used to fall back to this house's names.

        A zone map from a different house that omits them, or names a room it
        does not have, loaded clean: health stayed ok, the heartbeat kept
        beating, and the living room, the sofa and the front door were simply
        never occupied. Story T would never reach WATCHING and the estimator
        would never see an arrival, with nothing anywhere saying why.
        """
        base = {"schema": "living-lights-zones/v1", "cameras": ["office"],
                "zones": {"desk": "office"}}
        cases = {
            "living_room_camera names a camera that is not there": {
                **base, "living_room_camera": "living_room", "sofa_zone": "desk",
                "front_door_zone": "desk"},
            "sofa_zone names a zone that is not there": {
                **base, "living_room_camera": "office", "sofa_zone": "sofa",
                "front_door_zone": "desk"},
            "front_door_zone names a zone that is not there": {
                **base, "living_room_camera": "office", "sofa_zone": "desk",
                "front_door_zone": "front_door"},
            "the sofa is on a different camera from the living room": {
                "schema": "living-lights-zones/v1", "cameras": ["office", "hall"],
                "zones": {"desk": "office", "mat": "hall"},
                "living_room_camera": "office", "sofa_zone": "mat",
                "front_door_zone": "desk"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "zones.json"
            for why, doc in cases.items():
                with self.subTest(why=why):
                    path.write_text(json.dumps(doc))
                    with self.assertRaises(ValueError):
                        load_zones(path)

    def test_a_single_room_map_is_accepted(self):
        """One camera and one zone is a legitimate map: an office, say.

        The validation must refuse names that do not exist, not refuse small
        houses.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "zones.json"
            path.write_text(json.dumps({
                "schema": "living-lights-zones/v1", "cameras": ["office"],
                "zones": {"desk": "office"}, "activity_cameras": [],
                "living_room_camera": "office", "sofa_zone": "desk",
                "front_door_zone": "desk"}))
            zones = load_zones(path)
        self.assertEqual(zones.living_room_camera, "office")
        self.assertEqual(zones.sofa_zone, "desk")

    def test_shadow_entity_ids(self):
        zones = load_zones()
        self.assertEqual(activity_entities(zones, shadow=True)["sink"], "kitchen_sink_activity_shadow")
        self.assertEqual(activity_entities(zones, shadow=False)["sink"], "kitchen_sink_activity")


class ActivityTrackerTest(unittest.TestCase):
    def setUp(self):
        self.zones = load_zones()
        self.tracker = ActivityTracker(self.zones)

    def test_idle_everywhere_without_beliefs(self):
        out = self.tracker.update(T0, None, {z: True for z in self.zones.zones})
        self.assertEqual(set(out.values()), {IDLE})
        self.assertEqual(len(out), len(self.zones.zones))

    def test_cooking_needs_thirty_seconds_and_an_occupied_kitchen_zone(self):
        b = belief(food_prep=(0.1, 0.2, 0.7))
        out = self.tracker.update(s(0), b, {"island_left": True})
        self.assertEqual(out["island_left"], IDLE)
        out = self.tracker.update(s(stories.ACTIVITY_SUSTAIN_S - 1), b, {"island_left": True})
        self.assertEqual(out["island_left"], IDLE)
        out = self.tracker.update(s(stories.ACTIVITY_SUSTAIN_S), b, {"island_left": True})
        self.assertEqual(out["island_left"], COOKING)
        self.assertEqual(out["sink"], IDLE, "vacant zones stay idle")
        self.assertEqual(out["dining_left"], IDLE)
        out = self.tracker.update(s(60), b, {"island_left": True, "sofa": True})
        self.assertEqual(out["sofa"], IDLE, "living-room zones are idle in this milestone")
        out = self.tracker.update(s(90), belief(food_prep=(0.6, 0.3, 0.1)), {"island_left": True})
        self.assertEqual(out["island_left"], IDLE)
        self.assertEqual([(e["zone"], e["to"]) for e in self.tracker.journal],
                         [("island_left", COOKING), ("island_left", IDLE)])

    def test_eating_in_dining_and_kitchen_cooking_wins(self):
        b = belief(food_prep=(0.1, 0.2, 0.7), eating=0.9)
        self.tracker.update(s(0), b, {"dining_left": True, "sink": True})
        out = self.tracker.update(s(30), b, {"dining_left": True, "sink": True})
        self.assertEqual(out["dining_left"], EATING)
        self.assertEqual(out["sink"], COOKING)
        out = self.tracker.update(s(60), belief(eating=0.61), {"dining_left": True, "sink": True})
        self.assertEqual(out["sink"], EATING)
        out = self.tracker.update(s(90), belief(eating=0.59), {"dining_left": True})
        self.assertEqual(out["dining_left"], IDLE)

    def test_belief_threshold_constants(self):
        self.assertEqual(stories.P_COOKING_FOOD_PREP_GE2, 0.60)
        self.assertEqual(stories.P_EATING, 0.60)
        self.assertEqual(stories.ACTIVITY_SUSTAIN_S, 30)
        self.assertEqual(set(self.tracker.unknown().values()), {"unknown"})


if __name__ == "__main__":
    unittest.main()
