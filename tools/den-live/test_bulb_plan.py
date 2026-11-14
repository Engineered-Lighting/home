"""The bulb plan: the reading scene, its caps, the other situations, and den_lighting 1.2's payload. Pure."""

import tempfile
import unittest
from pathlib import Path

import bulb_plan
import coordinator_live
import sofa_check

LIGHTS = ("light.a", "light.b", "light.c")
WARMEST = {"light.a": 2000, "light.b": 2202, "light.c": 3000}
EVENING = 20 * 3600.0   # 20:00 UTC on 1970-01-01, with a 0 h offset


class BulbPlanTest(unittest.TestCase):
    def test_reading_dims_each_lit_bulb_warm_and_never_brightens_one(self):
        house = {"light.a": {"on": True, "brightness": 200, "kelvin": 3500},
                 "light.b": {"on": True, "brightness": 40, "kelvin": 3000},
                 "light.c": {"on": False, "brightness": None, "kelvin": None}}
        plan = bulb_plan.desired("task/evening", people=1, lights=LIGHTS, warmest=WARMEST, house=house)
        self.assertEqual(plan["reason"], "reading")
        self.assertFalse(plan["release"])
        self.assertEqual(plan["transition_s"], 4.0)
        self.assertEqual(plan["scenes"], {"light.a": {"on": True, "brightness": 77, "kelvin": 2700},
                                          "light.b": {"on": True, "brightness": 40, "kelvin": 2700}})

    def test_reading_never_asks_a_bulb_for_a_white_warmer_than_it_has(self):
        plan = bulb_plan.desired("task/evening", people=1, lights=LIGHTS, warmest=WARMEST)
        self.assertEqual(plan["scenes"]["light.c"]["kelvin"], 3000)
        self.assertEqual(plan["scenes"]["light.a"]["kelvin"], 2700)

    def test_without_den_lightings_list_one_scene_stands_for_every_lit_bulb(self):
        plan = bulb_plan.desired("task/evening", people=1)
        self.assertEqual(plan["scenes"], {bulb_plan.ALL: {"on": True, "brightness": 77, "kelvin": 2700}})
        with self.assertRaises(ValueError):
            bulb_plan.payload(plan, epoch="e", publisher="p", generation=1)

    def test_a_learned_scene_wins_light_by_light(self):
        learned = {"light.a": {"on": True, "brightness": 120, "kelvin": 2500}, "light.z": {"on": True}}
        plan = bulb_plan.desired("task/evening", people=1, lights=LIGHTS, warmest=WARMEST, learned=learned)
        self.assertEqual(plan["scenes"]["light.a"], {"on": True, "brightness": 120, "kelvin": 2500})
        self.assertEqual(plan["scenes"]["light.b"]["brightness"], 77)
        self.assertNotIn("light.z", plan["scenes"])   # not one of den_lighting's lights

    def test_several_people_get_no_reading_scene(self):
        plan = bulb_plan.desired("task/evening", people=2, lights=LIGHTS, warmest=WARMEST)
        self.assertEqual((plan["release"], plan["scenes"], plan["reason"]), (True, {}, "several_people"))

    def test_vacant_unknown_and_nothing_learned_release_to_den_lightings_rules(self):
        for key, reason in (("vacant/night", "vacant"), ("unknown/day", "unknown"),
                            ("sitting/evening", "den_lighting_rules"), ("present/day", "den_lighting_rules")):
            plan = bulb_plan.desired(key, people=1, lights=LIGHTS, warmest=WARMEST)
            self.assertEqual((plan["release"], plan["scenes"], plan["reason"]), (True, {}, reason), key)

    def test_sitting_plays_only_what_was_learned(self):
        learned = {"light.b": {"on": False, "brightness": None, "kelvin": None}}
        plan = bulb_plan.desired("sitting/evening", people=1, lights=LIGHTS, learned=learned)
        self.assertEqual((plan["release"], plan["scenes"], plan["reason"]), (False, learned, "learned"))

    def test_gaming_is_den_lightings_own_scene_plus_what_was_learned(self):
        learned = {"light.a": {"on": True, "brightness": 90, "kelvin": None}}
        plan = bulb_plan.desired("gaming/night", people=1, lights=LIGHTS, warmest=WARMEST, learned=learned)
        self.assertEqual(plan["scenes"]["light.a"], {"on": True, "brightness": 90, "kelvin": 2000})
        self.assertEqual(plan["scenes"]["light.b"], {"on": True, "brightness": 38, "kelvin": 2202})

    def test_the_ambient_records_goal_sets_the_reading_scene(self):
        ambient = {"level": .6, "scale": .8, "cct_k": 2900, "goal_level": .2, "goal_cct_k": 2800, "ramp_s": 6.0}
        plan = bulb_plan.desired("task/evening", people=1, lights=("light.a",), ambient=ambient)
        self.assertEqual(plan["scenes"]["light.a"], {"on": True, "brightness": 51, "kelvin": 2800})
        self.assertEqual(plan["transition_s"], 6.0)
        sampled_only = {"level": .5, "scale": .7, "cct_k": 2900}   # no goal yet: READING's numbers
        self.assertEqual(bulb_plan.desired("task/evening", people=1, lights=("light.a",), ambient=sampled_only)
                         ["scenes"]["light.a"]["brightness"], 77)

    def test_payload_speaks_den_lighting_1_2(self):
        plan = bulb_plan.desired("task/evening", people=1, lights=("light.a",))
        data = bulb_plan.payload(plan, epoch="e", publisher="p", generation=7)
        self.assertEqual(data, {"epoch": "e", "publisher": "p", "plan_id": "plan-7", "generation": 7,
                                "valid_for_s": 60, "transition_s": 4.0, "situation": "task/evening",
                                "scenes": plan["scenes"]})
        released = bulb_plan.payload(bulb_plan.release("vacant/night", "vacant"), epoch="e", publisher="p",
                                     generation=8)
        self.assertEqual((released["release"], released["scenes"]), (True, {}))
        long_key = bulb_plan.release("x" * 40, "unknown")
        self.assertEqual(len(bulb_plan.payload(long_key, epoch="e", publisher="p", generation=9)["situation"]), 32)


class SituationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tree.cleanup)
        cls.core, _learn = coordinator_live.load_core(sofa_check.archive(Path(cls.tree.name), "coordinator"))

    def context(self, people=1, task=True, seated=True, known=True):
        return {"people": people, "task": task, "seated": seated, "activity_known": known, "since_mono": 0.0}

    def test_den_lightings_status_decides_and_may_be_learned_from(self):
        attrs = {"occupancy": "on", "gaming_signal": "idle"}
        self.assertEqual(bulb_plan.situation(self.core, self.context(), attrs, EVENING),
                         ("task/evening", True, "home_assistant"))
        self.assertEqual(bulb_plan.situation(self.core, self.context(), {**attrs, "gaming_signal": "playing"},
                                             EVENING)[0], "gaming/evening")
        self.assertEqual(bulb_plan.situation(self.core, self.context(), {**attrs, "occupancy": "off"}, EVENING)[:2],
                         ("vacant/evening", False))

    def test_without_home_assistant_den_live_decides_and_nothing_is_learned(self):
        self.assertEqual(bulb_plan.situation(self.core, self.context(), None, EVENING),
                         ("task/evening", False, "den_live"))
        self.assertEqual(bulb_plan.situation(self.core, self.context(people=0, task=False, seated=False), None,
                                             EVENING)[0], "unknown/evening")


if __name__ == "__main__":
    unittest.main()
