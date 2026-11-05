"""The camera's own geometry for the RoomModel entry. Standard library only.

schemas.json: a camera entry's `intrinsics` names its source, "the EEPROM values
today", and every Observation's `transform_id` resolves to that entry. So the
entry holds what rung 2 needs to go from an RGB pixel to the room, in the form
den-observe's METHOD.md names den-observe-camera-1. Two sources feed it:

- a bundle's metadata: the depth and RGB frames' own intrinsics, RGB distortion
  and sizes (scene.rgb_geometry);
- the camera's /calibration reply, which no take saves: the EEPROM's stereo
  baseline and its RGB-from-rectified-left chain. The owner saves that reply
  once as camera-calibration.json in the working folder.

The chain follows perception-lab's depth_association.eeprom_rgb_from_rectified_left
without numpy: cameraData[socket].extrinsics maps that camera into toCameraSocket
(translation in centimetres); stereoRectificationData.rectifiedRotationLeft maps
raw left into rectified left.
"""
import json
import math
from pathlib import Path

import rigid
import scene
from scene import Refused

SOURCE = "EEPROM: the registration frame's own intrinsics and distortion, and the camera's /calibration extrinsics"
RGB_SOCKET = 0
CM = 0.01
BASELINE_RANGE_M = (0.01, 1.0)  # metres; outside it the units are wrong


def _bad(detail):
    raise Refused("camera_calibration_invalid", detail)


def _invert(rotation, translation):
    inverse = [[rotation[j][i] for j in range(3)] for i in range(3)]
    return inverse, [-sum(inverse[i][j] * translation[j] for j in range(3)) for i in range(3)]


def _compose(a, b):
    """The transform that applies b, then a."""
    rotation = [[sum(a[0][i][k] * b[0][k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return rotation, [sum(a[0][i][k] * b[1][k] for k in range(3)) + a[1][i] for i in range(3)]


def _rotation(value, what):
    if (type(value) is not list or len(value) != 3 or any(type(row) is not list or len(row) != 3 for row in value)
            or not all(type(v) in (int, float) and math.isfinite(v) for row in value for v in row)
            or not rigid.is_rotation(value, tolerance=1e-4)):
        _bad(f"{what} is not a rotation")
    return value


def eeprom_geometry(eeprom):
    """{rgb_from_rectified_left: {rotation, translation_m}, stereo_baseline_m} from the EEPROM JSON."""
    try:
        cameras = {int(socket): data for socket, data in eeprom["cameraData"]}
        stereo = eeprom["stereoRectificationData"]
        left, right = int(stereo["leftCameraSocket"]), int(stereo["rightCameraSocket"])
        unrectify = _invert(_rotation(stereo["rectifiedRotationLeft"], "rectifiedRotationLeft"), [0.0, 0.0, 0.0])
        edges = {}
        for socket, data in cameras.items():
            extrinsics = data.get("extrinsics") or {}
            target = extrinsics.get("toCameraSocket")
            if target is None or int(target) < 0 or int(target) == socket:
                continue
            step = (_rotation(extrinsics["rotationMatrix"], f"socket {socket}'s rotationMatrix"),
                    [float(extrinsics["translation"][axis]) * CM for axis in "xyz"])
            edges.setdefault(socket, {})[int(target)] = step
            edges.setdefault(int(target), {})[socket] = _invert(*step)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        _bad(f"the EEPROM JSON has no usable cameraData or stereoRectificationData ({exc})")
    chain, frontier = {left: ([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], [0.0, 0.0, 0.0])}, [left]
    while frontier:
        socket = frontier.pop(0)
        for target, step in edges.get(socket, {}).items():
            if target not in chain:
                chain[target] = _compose(step, chain[socket])
                frontier.append(target)
    if RGB_SOCKET not in chain or right not in chain:
        _bad("no EEPROM extrinsic chain from the left camera to the right and RGB cameras")
    baseline = math.sqrt(sum(v * v for v in chain[right][1]))
    if not BASELINE_RANGE_M[0] <= baseline <= BASELINE_RANGE_M[1]:
        _bad(f"the stereo baseline comes out at {baseline} m")
    rotation, translation = _compose(chain[RGB_SOCKET], unrectify)
    return {"rgb_from_rectified_left": {"rotation": rotation, "translation_m": translation},
            "stereo_baseline_m": baseline}


def read_calibration(path):
    """The EEPROM geometry from a saved /calibration reply, or a named refusal."""
    path = Path(path)
    if not path.exists():
        raise Refused("camera_calibration_missing", f"no {path.name}; save the camera's /calibration reply there "
                                                    "(README, step 4b)")
    try:
        reply = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _bad(f"{path.name} does not read as JSON; save the /calibration reply again")
    if type(reply) is not dict or type(reply.get("eeprom")) is not dict:
        _bad(f"{path.name} has no eeprom object; save the /calibration reply again")
    return eeprom_geometry(reply["eeprom"])


def entry_intrinsics(bundle, eeprom):
    """The camera entry's `intrinsics`: den-observe-camera-1, with its source named."""
    return {"source": SOURCE, "rectified_left": dict(bundle["intrinsics"]), **scene.rgb_geometry(bundle), **eeprom,
            "depth_size": [scene.WIDTH, scene.HEIGHT]}
