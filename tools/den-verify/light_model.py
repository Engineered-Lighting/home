"""The one adapter between rung 10 and rung 8's light model.

Every prediction rung 10 makes goes through `LightModel` here, and only
`_backend_predict` touches the model's code: rung 8 Stage 1's
`predict(scene, model)` in tools/den-light/predict.py (standard library only,
no I/O). That folder goes on the END of sys.path, because both folders hold a
`synthetic.py` and rung 10's own must stay first.

`light-model.json` is the record `model.light_model` writes: `channels` lists
each channel's `channel_key` (`<fixture_id>.<channel_id>`) and its Home
Assistant `entity_id` (null for a virtual fixture, as schemas.json's F1);
`basis` is keyed `<channel_key>@<kelvin>K`, and `cct` and `dimming` are keyed
by channel_key. A file that is not one is refused in one sentence when it is
read (`check_shape`), not as a TypeError at the first prediction.

The state rung 10 holds is per channel {level 0-1, cct_k}, as in
`Decision.applied_scene[].readback`, which is predict()'s own scene shape. A
state is keyed as its Decision keys it: by channel key (`F1.spot`, as
schemas.json's walkthrough does) or by entity id (`light.den_front_left`, as
den-core's hue-den-4 Decisions do); an entity id is looked up in `channels`.
A level above full counts as full, as predict.scene_from_ha caps it. A channel
that is on with no cct_k is predicted only when the model captured a single
temperature for it, because predict() then gives the same light at any kelvin.
A state the model cannot predict raises KeyError, which score.py reports as
the decision's reason for not being verified.

predict() gives None at a point the fit excluded, in every scene. Such a point
(or one a basis lacks a number for) is no surface here. rung 8 v1's sample
points are image blocks with no surface id. A model may map surfaces to blocks
with `"surfaces": {surface_id: [point_id, ...]}`; a surface's prediction is
then the mean over its blocks. Without that map, each point is a surface.
"""
import sys
from pathlib import Path

from records import BadFile, is_number, read_json

DEN_LIGHT = Path(__file__).resolve().parent.parent / "den-light"
if str(DEN_LIGHT) not in sys.path:
    sys.path.append(str(DEN_LIGHT))

import predict  # noqa: E402  rung 8 Stage 1's, from DEN_LIGHT

BACKEND = "predict.py (rung 8 Stage 1, tools/den-light)"


def _backend_predict(scene, model):
    return predict.predict(scene, model)["surfaces"]


def _own_names(values):
    """Ids a dict can key, with no two the same."""
    return all(isinstance(v, str) and v for v in values) and len(set(values)) == len(values)


def _numbers(values):
    return isinstance(values, list) and bool(values) and all(map(is_number, values))


def is_surface_map(value):
    return isinstance(value, dict) and all(
        isinstance(blocks, list) and all(isinstance(p, str) for p in blocks) for blocks in value.values())


def _check_points(data):
    points = data["sample_points"]
    if not isinstance(points, list) or not points or not all(isinstance(p, dict) for p in points):
        raise TypeError("its sample_points must be a list of points")
    if not _own_names([p["point_id"] for p in points]):
        raise TypeError("each sample point needs its own point_id, as text")
    return [p["point_id"] for p in points]


def _check_channel(key, data, ids):
    """What predict.py reads for one channel: a basis per temperature, each row over every block, and a dimming
    table it can interpolate. A repeated temperature or falling levels would give a wrong number and no word."""
    cct, dimming = data["cct"][key], data["dimming"][key]
    kelvin, basis_keys = cct["kelvin"], cct["basis_keys"]
    if (not _numbers(kelvin) or min(kelvin) <= 0 or len(set(kelvin)) != len(kelvin)
            or not isinstance(basis_keys, list) or len(basis_keys) != len(kelvin)):
        raise TypeError(f"channel {key} needs one basis per temperature, each above 0 K and no two the same")
    levels, g = dimming["levels"], dimming["g"]
    if (not _numbers(levels) or not _numbers(g) or len(levels) != len(g)
            or not all(a < b for a, b in zip(levels, levels[1:]))):
        raise TypeError(f"channel {key} needs a dimming table of numbers, its levels rising")
    for basis_key in basis_keys:
        row = data["basis"][basis_key]
        if not isinstance(row, dict) or not all(v is None or is_number(v) for v in row.values()):
            raise TypeError(f"its basis {basis_key} must be numbers or null")
        if not row.keys() >= set(ids):
            raise TypeError(f"its basis {basis_key} must name every sample point, with null for one the fit excluded")


def _check_stage1(data):
    ids = _check_points(data)
    channels = data["channels"]
    if not isinstance(channels, list) or not channels or not all(isinstance(c, dict) for c in channels):
        raise TypeError("its channels must be a list of channels")
    if not _own_names([c["channel_key"] for c in channels]):
        raise TypeError("each channel needs its own channel_key, as text")
    if not _own_names([c["entity_id"] for c in channels if c["entity_id"] is not None]):
        raise TypeError("a channel's entity_id must be text or null, and no two the same")
    for key in ("cct", "dimming", "basis"):
        if not isinstance(data[key], dict):
            raise TypeError(f"its {key} must be an object")
    for c in channels:
        _check_channel(c["channel_key"], data, ids)
    if data.get("spot_model"):
        raise TypeError("it has a spot_model, and den-light's predict.py has no aim: it would ignore the aim")


def check_shape(data, where):
    """A sentence for a file that is not a light model, here rather than as a TypeError at the first prediction.
    This reads what predict.py and this adapter read of it, and nothing they ignore."""
    try:
        if not isinstance(data, dict):
            raise TypeError("it must be a JSON object")
        if not isinstance(data["version"], str) or not data["version"]:
            raise TypeError("its version must be text")
        if not isinstance(data["basis_unit"], str):
            raise TypeError("its basis_unit must be text")
        if data.get("fake") is not None and type(data["fake"]) is not bool:
            raise TypeError("its fake must be true or false")
        _check_stage1(data)
        if data.get("surfaces") is not None and not is_surface_map(data["surfaces"]):
            raise TypeError("its surfaces must be {surface_id: [point_id, ...]}")
    except KeyError as e:
        raise BadFile(f"{where} is not a light model: it has no {e}") from None
    except (TypeError, AttributeError) as e:
        raise BadFile(f"{where} is not a light model: {e}") from None


class LightModel:
    def __init__(self, data, where="the light model"):
        check_shape(data, where)
        self.data = data
        self.version = data["version"]
        self.basis_unit = data["basis_unit"]
        self.backend = BACKEND
        self.sample_points = {p["point_id"]: p for p in data["sample_points"]}
        rows = list(data["basis"].values())
        unknown = {pid for pid, p in self.sample_points.items()
                   if p.get("excluded") or any(row.get(pid) is None for row in rows)}
        given = data.get("surfaces") or {p: [p] for p in self.sample_points}
        known = {sid: [b for b in blocks if b not in unknown] for sid, blocks in given.items()}
        self.surfaces = {sid: blocks for sid, blocks in known.items() if blocks}
        self.channels = {c["channel_key"] for c in data["channels"]}
        self.keys = {c["entity_id"]: c["channel_key"] for c in data["channels"] if c.get("entity_id")}
        self.keys.update({key: key for key in self.channels})
        self.temperatures = {key: list(data["cct"][key]["kelvin"]) for key in self.channels}
        self._prepared = predict.prepare(data)

    @classmethod
    def load(cls, path):
        return cls(read_json(path), where=str(path))

    def has(self, surface_id):
        return surface_id in self.surfaces

    def surface_problem(self, surface_id):
        """Why this model has no prediction for a surface a Decision names."""
        return (f"{surface_id} is not a surface of light model {self.version}: "
                "give the surfaces.json of the run that made this decision with --surfaces")

    def kind(self, surface_id):
        blocks = self.surfaces.get(surface_id) or []
        return self.sample_points[blocks[0]].get("kind") if len(blocks) == 1 else None

    def xy(self, surface_id):
        blocks = self.surfaces.get(surface_id) or []
        xyz = self.sample_points[blocks[0]].get("xyz") if len(blocks) == 1 else None
        return (float(xyz[0]), float(xyz[1])) if xyz else None

    def _scene(self, state):
        scene = {}
        for key, s in state.items():
            level = float(s.get("level") or 0)
            if level <= 0:
                continue
            channel = self.keys.get(key, key)  # an unknown key reaches predict(), which names it
            cct_k = s.get("cct_k")
            if cct_k is None and len(self.temperatures.get(channel, ())) == 1:
                cct_k = self.temperatures[channel][0]
            scene[channel] = {"level": min(level, 1.0), "cct_k": cct_k}
        return scene

    def predict(self, state):
        """Electric light at every surface for a state {channel: {level, cct_k}}."""
        try:
            by_point = _backend_predict(self._scene(state), self._prepared)
        except ValueError as e:
            raise KeyError(f"light model {self.version}: {e}") from None
        return {sid: sum(by_point[b] for b in blocks) / len(blocks)
                for sid, blocks in self.surfaces.items()}

    def contributions(self, state):
        """Each channel's part of predict(state), surface by surface."""
        return {channel: self.predict({channel: s}) for channel, s in state.items()}
