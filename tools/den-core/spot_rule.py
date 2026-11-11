"""The coordinator's spot mode asks this file which fixtures may light a reader's spot: the lighting brain's own aim.py,
imported and not copied, on a PersonState.

coordinator.py loads it by file path when config["channels"] is set and pins it, with aim.py, settings.py and geometry.py,
in the logged config, so check.py refuses a log once the rule or one of its thresholds changes. The brain's package, at
stack/services/lighting-brain/lighting_brain, loads under a private name and never through sys.path. Standard library only.
"""
import importlib
import importlib.util
import math
import sys
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[2] / "stack" / "services" / "lighting-brain" / "lighting_brain"
PACKAGE = "_den_core_lighting_brain"
SHOULDER_ABOVE_SEAT_M, HEAD_ABOVE_SHOULDER_M = 0.5, 0.23  # the startup line's stated reader, as brain_run.py placed it


def _aim():
    if PACKAGE not in sys.modules:
        spec = importlib.util.spec_from_file_location(PACKAGE, BRAIN / "__init__.py", submodule_search_locations=[str(BRAIN)])
        sys.modules[PACKAGE] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[PACKAGE])
    return importlib.import_module(PACKAGE + ".aim")


aim = _aim()
SETTINGS = aim.Settings(timezone="UTC")  # the brain's default zone needs tzdata, a third-party package on Windows


def refusal(fixture, person, point):
    """None when this fixture may light `point` for this PersonState, else aim.py's reason: pan_tilt_infeasible:<detail>,
    too_far, beam_through_head or in_front_of_reader, and no_head when the PersonState has no head. fixture is the light
    model's {aim_origin_m, horizontal_world_reference, limits}. heading.deg is clockwise from +y; with none the reader faces
    nowhere, and the in-front test, the only one a facing changes, is skipped."""
    head = (person.get("attention") or {}).get("head_xyz")
    if not head:
        return "no_head"
    deg = (person.get("heading") or {}).get("deg")
    facing = (0.0, 0.0, 0.0) if deg is None else (math.sin(math.radians(deg)), math.cos(math.radians(deg)), 0.0)
    place = (person.get("position") or {}).get("xyz") or head
    reader = aim.Reader(tuple(place), facing, "heading", tuple(head), "pose", tuple(point))
    origin = fixture.get("aim_origin_m")
    candidate = aim.evaluate_fixture(aim.Fixture("fixture", origin and tuple(origin), tuple(fixture["horizontal_world_reference"]),
                                                 {**aim.DEFAULT_LIMITS, **(fixture.get("limits") or {})}), reader, SETTINGS)
    return None if candidate.ok or (deg is None and candidate.reason == "in_front_of_reader") else candidate.reason


def reader_notes(person):
    """What refusal() stood in for on this PersonState, in words for a Decision's why."""
    return (["has no heading: the in-front test is skipped"] if (person.get("heading") or {}).get("deg") is None else []) + (
        ["has no position: the head's place stands in for it"] if not (person.get("position") or {}).get("xyz") else [])


def words(reason):
    """A refusal in words: 'in front of reader', 'pan tilt infeasible', 'too far', 'beam through head', 'no head'."""
    return reason.split(":")[0].replace("_", " ")


def startup_line(fixtures, seat_m, facing_unit, spot_m):
    """Each fixture against a stated facing and the spot, through refusal(): ['F1 clear', 'F2 refused: in front of reader',
    ...], in key order. The reader sits at seat_m facing facing_unit [x, y], shoulders 0.5 m and head 0.73 m above the seat."""
    x, y, z = seat_m
    person = {"attention": {"head_xyz": [x, y, z + SHOULDER_ABOVE_SEAT_M + HEAD_ABOVE_SHOULDER_M]},
              "position": {"xyz": [x, y, z + SHOULDER_ABOVE_SEAT_M]},
              "heading": {"deg": math.degrees(math.atan2(facing_unit[0], facing_unit[1]))}}
    found = [(key, refusal(fixtures[key], person, spot_m)) for key in sorted(fixtures)]
    return ["%s clear" % key if why is None else "%s refused: %s" % (key, words(why)) for key, why in found]
