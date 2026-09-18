"""The generator holds no house.

Every name that belongs to one building lives in ``ha-config/house.json``, so
a different house is a different file rather than a different copy of the
generator. These tests hold two things: that extracting the vocabulary changed
no byte of the deployed package, and that a genuinely different house produces
a complete one.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
GENERATOR = REPO / "tools" / "build-living-lights-yaml.py"
LA_HOUSE = REPO / "ha-config" / "house.json"
OFFICE_HOUSE = REPO / "ha-config" / "house.victoria-office.example.json"
DEPLOYED = REPO / "ha-config" / "packages" / "living_lights_observability.yaml"

LA_NAMES = ("sofa", "island_left", "island_right", "dining_left", "dining_right",
            "front_left", "rear_left", "rear_right", "weights", "workshop", "e28")


def generate(house: Path, out: Path, mirror: Path | None = None) -> str:
    env = dict(os.environ, LIVING_LIGHTS_HOUSE=str(house))
    argv = [sys.executable, str(GENERATOR), "--output", str(out)]
    if mirror is not None:
        argv += ["--mirror-output", str(mirror)]
    subprocess.run(argv, check=True, capture_output=True, env=env, cwd=REPO)
    return out.read_text(encoding="utf-8")


class ExtractionChangedNothing(unittest.TestCase):
    def test_this_house_regenerates_byte_for_byte(self):
        """The whole point of the refactor: the deployed package is untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            produced = generate(LA_HOUSE, Path(tmp) / "obs.yaml")
        self.assertEqual(produced, DEPLOYED.read_text(encoding="utf-8"),
                         "extracting the vocabulary must not change the deployed package")


class ADifferentHouseGenerates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.text = generate(OFFICE_HOUSE, Path(self.tmp.name) / "obs.yaml",
                             Path(self.tmp.name) / "mirror.yaml")
        self.doc = yaml.safe_load(self.text)

    def test_it_is_a_complete_valid_package(self):
        self.assertIn("automation", self.doc)
        self.assertIn("template", self.doc)
        self.assertGreater(len(self.doc["automation"]), 5)

    def test_no_light_from_the_other_house_appears(self):
        """A light id that belongs to another building is the leak that matters:
        it names a real entity somewhere, so it actuates something."""
        office = json.loads(OFFICE_HOUSE.read_text())
        for light in json.loads(LA_HOUSE.read_text())["dimmable_lights"]:
            if light in office["dimmable_lights"]:
                continue
            with self.subTest(light=light):
                # Whole entity id: light.office is a prefix of light.office_desk,
                # which is a legitimate office light rather than a leak.
                found = re.search(re.escape(light) + r"(?![A-Za-z0-9_])", self.text)
                self.assertIsNone(found, f"{light} leaked into another house's package")

    def test_the_office_zones_are_the_ones_generated(self):
        office = json.loads(OFFICE_HOUSE.read_text())
        for zone in office["zones"]:
            with self.subTest(zone=zone):
                self.assertIn(zone, self.text)


class TheActuatorsFollowTheSameHouse(unittest.TestCase):
    """The zone map used to exist in both generators with nothing checking they
    agreed, which shows up as a zone that is generated, deployed, enabled and
    silently does nothing."""

    ACTUATORS = REPO / "tools" / "build-living-lights-actuators.py"

    def run_for(self, house: Path):
        env = dict(os.environ, LIVING_LIGHTS_HOUSE=str(house))
        return subprocess.run([sys.executable, str(self.ACTUATORS)],
                              capture_output=True, env=env, cwd=REPO, text=True)

    def test_this_house_builds_its_ten_actuators(self):
        done = self.run_for(LA_HOUSE)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("10 actuators built", done.stdout)

    def test_the_office_builds_its_own(self):
        done = self.run_for(OFFICE_HOUSE)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("2 actuators built", done.stdout)

    def test_a_house_without_actuators_says_so(self):
        doc = json.loads(LA_HOUSE.read_text())
        doc.pop("actuators")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "house.json"
            path.write_text(json.dumps(doc))
            done = self.run_for(path)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("actuators", done.stderr)


class TheHouseFileIsValidated(unittest.TestCase):
    """Every mistake a house file can carry is silent downstream.

    A zone that is not a Frigate zone renders "off" in the template, the
    classifier reads the room as vacant for ever, and the light never
    responds. Nothing raises and the YAML is valid, so the check has to happen
    here.
    """

    def bad(self, **changes):
        doc = json.loads(LA_HOUSE.read_text())
        for key, value in changes.items():
            if "." in key:
                outer, inner = key.split(".", 1)
                doc[outer] = dict(doc[outer]); doc[outer][inner] = value
            else:
                doc[key] = value
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "house.json"
            path.write_text(json.dumps(doc))
            env = dict(os.environ, LIVING_LIGHTS_HOUSE=str(path))
            done = subprocess.run(
                [sys.executable, str(GENERATOR), "--output", str(Path(tmp) / "o.yaml")],
                capture_output=True, env=env, cwd=REPO, text=True)
        return done

    def test_a_living_room_camera_that_no_zone_names_is_refused(self):
        done = self.bad(**{"rooms.living_room_camera": "conservatory"})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("living_room_camera", done.stderr)

    def test_a_sofa_zone_that_is_not_a_zone_is_refused(self):
        done = self.bad(**{"rooms.sofa_zone": "chaise"})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("sofa_zone", done.stderr)

    def test_a_sofa_on_another_camera_is_refused(self):
        done = self.bad(**{"rooms.sofa_zone": "sink"})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("same room", done.stderr)

    def test_a_zone_set_naming_an_unknown_zone_is_refused(self):
        doc = json.loads(LA_HOUSE.read_text())
        sets = dict(doc["zone_sets"]); sets["gaming_dim"] = ["nowhere"]
        done = self.bad(zone_sets=sets)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("gaming_dim", done.stderr)

    def test_a_house_with_no_zones_is_refused(self):
        done = self.bad(zones={})
        self.assertNotEqual(done.returncode, 0)

    def test_a_wrong_schema_is_refused(self):
        done = self.bad(schema="something-else/v9")
        self.assertNotEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()


class NoToolKeepsItsOwnCopy(unittest.TestCase):
    """Five files used to hold the same zone map, hand-synchronised.

    Nothing checked they agreed, and a zone that drifted between them did not
    fail: it produced a report about a house that does not exist. They all read
    ``tools/house.py`` now, and this test is what stops a copy coming back.
    """

    CONSUMERS = (
        "tools/lighting-sim/harness.py",
        "tools/lighting-sim/analyze_evening.py",
        "tools/lighting-sim/replay.py",
        "tools/tv-evening-postmortem.py",
        "tools/night-postmortem-join.py",
        "tools/build-living-lights-actuators.py",
    )

    def test_none_of_them_names_a_zone_of_this_house(self):
        zones = json.loads(LA_HOUSE.read_text())["zones"]
        for name in self.CONSUMERS:
            text = (REPO / name).read_text(encoding="utf-8")
            for zone in zones:
                with self.subTest(tool=name, zone=zone):
                    self.assertNotRegex(
                        text, r'^\s+[\'"]' + re.escape(zone) + r'[\'"]\s*:',
                        f"{name} has its own copy of the zone map again")

    def test_they_all_read_the_shared_house(self):
        """Either directly through tools/house.py, or through the generator
        that loads the same file. What matters is that none of them holds the
        building itself."""
        for name in self.CONSUMERS:
            text = (REPO / name).read_text(encoding="utf-8")
            with self.subTest(tool=name):
                self.assertTrue(
                    "house.py" in text or "build-living-lights-yaml.py" in text,
                    f"{name} does not read the shared house from anywhere")


class TheDrawingMustMatchTheCameras(unittest.TestCase):
    """The gradient divides Frigate box pixels by the camera's detect size.

    Draw the model at one size and run the camera at another and every centroid
    is scaled by the wrong factor. The result is still a coordinate between
    zero and one, so nothing looks wrong: the sofa gradient simply lights the
    wrong bulb. Nothing reconciled the two files until now.
    """

    GRADIENT = REPO / "tools" / "build-gradient-lighting.py"
    SPATIAL = REPO / "ha-config" / "spatial_model.json"

    def test_the_house_records_what_frigate_runs(self):
        house = json.loads(LA_HOUSE.read_text())
        self.assertIn("camera_detect", house)
        for camera, size in house["camera_detect"].items():
            with self.subTest(camera=camera):
                self.assertGreater(size["detect_w"], 0)
                self.assertGreater(size["detect_h"], 0)

    def test_the_spatial_model_agrees_with_it_today(self):
        house = json.loads(LA_HOUSE.read_text())["camera_detect"]
        model = json.loads(self.SPATIAL.read_text())["cameras"]
        for camera, drawn in model.items():
            if camera not in house:
                continue
            with self.subTest(camera=camera):
                self.assertEqual((drawn["detect_w"], drawn["detect_h"]),
                                 (house[camera]["detect_w"], house[camera]["detect_h"]))

    def test_a_mismatched_drawing_is_refused(self):
        house = json.loads(LA_HOUSE.read_text())
        camera = "living_room"
        house["camera_detect"][camera] = {"detect_w": 960, "detect_h": 540}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "house.json"
            path.write_text(json.dumps(house))
            env = dict(os.environ, LIVING_LIGHTS_HOUSE=str(path))
            done = subprocess.run([sys.executable, str(self.GRADIENT)],
                                  capture_output=True, env=env, cwd=REPO, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("wrong bulb", done.stderr)
