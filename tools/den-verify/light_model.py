"""The one adapter between rung 10 and rung 8's light model.

Every prediction rung 10 makes goes through `LightModel` here, and only
`_at_blocks` touches the model's code: rung 8 Stage 1's `predict(scene, model)`
in tools/den-light/predict.py (standard library only, no I/O). That folder goes
on the END of sys.path, because both folders hold a `synthetic.py` and rung
10's own must stay first; a model is refused when `import predict` finds no
predict.py, or finds one that is not den-light's. The report pins predict.py's
sha256. A model marked `fake: true` is labelled so, and verify.py refuses a
scored run on it: plumbing is never a gate.

`light-model.json` is the record `model.light_model` writes: `channels` lists
each channel's `channel_key` (`<fixture_id>.<channel_id>`) and its Home
Assistant `entity_id` (null for a virtual fixture, as schemas.json's F1);
`basis` is keyed `<channel_key>@<kelvin>K`, and `cct` and `dimming` are keyed
by channel_key. A file that is not one is refused in one sentence when it is
read (`check_shape`), not as a TypeError at the first prediction.

The state rung 10 holds is per channel {level 0-1, cct_k}, as in
`Decision.applied_scene[].readback`, which is predict()'s own scene shape. A
Decision may name a channel by its channel key (`F1.spot`, as schemas.json's
walkthrough does) or by its entity id (`light.den_front_left`, as den-core's
hue-den-4 Decisions do): `canon` gives the one name. A level of None is
unknown, never 0: every surface that channel can reach then has no prediction.
A level above full counts as full, as predict.scene_from_ha caps it. A channel
that is on with no cct_k is predicted only when the model captured a single
temperature for it, because predict() then gives the same light at any kelvin.
A state predict() refuses raises KeyError, which score.py reports as that
decision's reason for not being verified.

rung 8 v1's sample points are image blocks with no surface id. The map
{surface_id: [point_id, ...]} comes from --surfaces, which takes den-core's
surfaces.json, else from the model's own `surfaces`; with neither, the point
ids are the surfaces. A block the fit excluded predicts None, a surface is the
mean over its blocks that have a value, and a scene is the sum of its channels'
parts. That is den-core's adapter's rule, so a Decision's own
`predicted.surfaces` comes out the same.
"""
import hashlib
import sys
from pathlib import Path

from records import BadFile, is_number, read_json

DEN_LIGHT = Path(__file__).resolve().parent.parent / "den-light"
PREDICT_PATH = DEN_LIGHT / "predict.py"
if str(DEN_LIGHT) not in sys.path:
    sys.path.append(str(DEN_LIGHT))

_IMPORT_ERROR = None
_saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # leave no __pycache__ in den-light
try:
    import predict  # noqa: E402  rung 8 Stage 1's, from DEN_LIGHT
except Exception as e:  # whatever a broken file raises, a run stops on one sentence
    predict, _IMPORT_ERROR = None, f"{type(e).__name__}: {e}"
finally:
    sys.dont_write_bytecode = _saved

BACKEND = "predict.py (rung 8 Stage 1, tools/den-light)"
MARKED_FAKE = "predict.py (rung 8 Stage 1, tools/den-light), on a model marked fake: plumbing only, never a gate"


def _predict_module(where):
    """den-light's predict module and its sha256, or a sentence saying why there is none."""
    need = f"{where} needs rung 8's {PREDICT_PATH}"
    if not PREDICT_PATH.is_file():
        raise BadFile(f"{need}, and there is none: run on a tree with tools/den-light")
    if predict is None:
        raise BadFile(f"{need}, and it cannot be loaded: {_IMPORT_ERROR}" if _IMPORT_ERROR else
                      f"{need}, and `import predict` finds none")
    found = Path(getattr(predict, "__file__", "") or "")
    if not found.is_file() or found.resolve() != PREDICT_PATH.resolve():
        raise BadFile(f"{need}, and `import predict` finds {found}")
    if not (hasattr(predict, "predict") and hasattr(predict, "prepare")):
        raise BadFile(f"{need}, and it cannot be loaded: it needs predict() and prepare()")
    return predict, hashlib.sha256(found.read_bytes()).hexdigest()


def _own_names(values):
    """Ids a dict can key, with no two the same."""
    return all(isinstance(v, str) and v for v in values) and len(set(values)) == len(values)


def _numbers(values):
    return isinstance(values, list) and bool(values) and all(map(is_number, values))


def is_surface_map(value):
    return isinstance(value, dict) and all(
        isinstance(blocks, list) and all(isinstance(p, str) for p in blocks) for blocks in value.values())


def read_surface_map(path):
    surfaces = read_json(path)
    if not is_surface_map(surfaces):
        raise BadFile(f"{path} is not a surface map: it must be {{surface_id: [point_id, ...]}}")
    return surfaces


def _check_points(data):
    points = data["sample_points"]
    if not isinstance(points, list) or not points or not all(isinstance(p, dict) for p in points):
        raise TypeError("its sample_points must be a list of points")
    if not _own_names([p["point_id"] for p in points]):
        raise TypeError("each sample point needs its own point_id, as text")
    for p in points:
        xyz = p.get("xyz")
        if xyz is not None and not (_numbers(xyz) and len(xyz) >= 2):
            raise TypeError(f"sample point {p['point_id']}: its xyz must be numbers or null")
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
    # Every model here is rung 8 Stage 1's shape: score.py compares its predictions with a Decision's own.
    stage1 = True

    def __init__(self, data, surfaces=None, where="the light model"):
        check_shape(data, where)
        self.module, self.predict_sha256 = _predict_module(where)
        self.data = data
        self.version = data["version"]
        self.basis_unit = data["basis_unit"]
        self.sample_points = {p["point_id"]: p for p in data["sample_points"]}
        try:
            self._prepared = self.module.prepare(data)  # read once, not on every call
        except (LookupError, TypeError, ValueError, AttributeError, ArithmeticError) as e:
            raise BadFile(f"{where} is not a light model: {PREDICT_PATH.name} could not read it: {type(e).__name__}: {e}") from None
        self.fake = data.get("fake") is True
        self.backend = MARKED_FAKE if self.fake else BACKEND
        self.channels = {c["channel_key"] for c in data["channels"]}
        self.keys = {c["entity_id"]: c["channel_key"] for c in data["channels"] if c.get("entity_id")}
        self.keys.update({key: key for key in self.channels})
        self.temperatures = {key: list(data["cct"][key]["kelvin"]) for key in self.channels}
        self._set_surfaces(surfaces)
        excluded = {p for p, row in self.sample_points.items() if row.get("excluded")}
        # What an off bulb adds: nothing, except at a surface with no block that has a value, which never has a prediction.
        self._off = {sid: 0.0 if any(p not in excluded for p in blocks) else None for sid, blocks in self.surfaces.items()}
        self._parts, self._reaches = {}, {}

    def _set_surfaces(self, given):
        whole = given if given is not None else self.data.get("surfaces") or {p: [p] for p in self.sample_points}
        self.surfaces_source = ("--surfaces" if given is not None else
                                "the model's own surfaces" if self.data.get("surfaces") else "the model's point ids")
        wrong = [sid for sid, blocks in whole.items() if not blocks or not self.sample_points.keys() >= set(blocks)]
        self.surfaces = {sid: blocks for sid, blocks in whole.items() if sid not in wrong}
        self.wrong_surfaces = sorted(wrong)

    @classmethod
    def load(cls, path, surfaces_path=None):
        surfaces = None if surfaces_path is None else read_surface_map(surfaces_path)
        return cls(read_json(path), surfaces=surfaces, where=str(path))

    def describe(self):
        """What the report says this run stood on."""
        return {"version": self.version, "basis_unit": self.basis_unit, "backend": self.backend, "fake": self.fake,
                "predict_sha256": self.predict_sha256,
                "surfaces": {"source": self.surfaces_source, "count": len(self.surfaces), "wrong": self.wrong_surfaces}}

    def has(self, surface_id):
        return surface_id in self.surfaces

    def canon(self, channel):
        """One name for a channel, whichever the Decision used. A name the model lacks stays as it is, for its error to name."""
        return self.keys.get(channel, channel)

    def surface_problem(self, surface_id):
        """Why this model has no prediction for a surface a Decision names."""
        if surface_id in self.wrong_surfaces:
            return (f"{surface_id} in {self.surfaces_source} has no block, or a block light model {self.version} does not have: "
                    "check --surfaces")
        if self.surfaces_source == "--surfaces":
            return f"{surface_id} is not in the map --surfaces gave: check that it is the surfaces.json of the run that made this decision"
        return (f"{surface_id} is not a surface of light model {self.version}, which has {self.surfaces_source}: "
                "give the surfaces.json of the run that made this decision with --surfaces")

    def kind(self, surface_id):
        blocks = self.surfaces.get(surface_id) or []
        return self.sample_points[blocks[0]].get("kind") if len(blocks) == 1 else None

    def xy(self, surface_id):
        blocks = self.surfaces.get(surface_id) or []
        xyz = self.sample_points[blocks[0]].get("xyz") if len(blocks) == 1 else None
        return (float(xyz[0]), float(xyz[1])) if xyz else None

    def _at_blocks(self, key, level, cct_k):
        """The one call into the model's code: one channel's value at every block."""
        if cct_k is None and len(self.temperatures.get(key, ())) == 1:
            cct_k = self.temperatures[key][0]  # one captured temperature: predict() gives the same at any kelvin
        try:
            return self.module.predict({key: {"level": min(level, 1.0), "cct_k": cct_k}}, self._prepared)["surfaces"]
        except ValueError as e:
            raise KeyError(f"light model {self.version}: {e}") from None

    def reaches(self, channel, surface_id):
        """Whether a channel gives this surface any light, at any level: what a bulb of unknown level leaves unknown."""
        key = self.canon(channel)
        if key not in self._reaches:
            if key not in self.temperatures:
                raise KeyError(f"light model {self.version}: the light model has no channel {key!r}")
            lit = {sid: False for sid in self.surfaces}
            for kelvin in self.temperatures[key]:
                values = self._at_blocks(key, 1.0, kelvin)
                for sid, blocks in self.surfaces.items():
                    lit[sid] = lit[sid] or any(values.get(p) for p in blocks)
            self._reaches[key] = lit
        return self._reaches[key][surface_id]

    def _part(self, channel, s):
        """One channel's part at every surface, {surface: value or None}. Kept per setting, since a run asks for the same ones."""
        level, cct_k, key = s.get("level"), s.get("cct_k"), self.canon(channel)
        if level is None:
            return {sid: None if self.reaches(key, sid) else off for sid, off in self._off.items()}
        if level <= 0:
            return self._off
        tag = (key, level, cct_k)
        if tag not in self._parts:
            values = self._at_blocks(key, level, cct_k)
            part = {}
            for sid, blocks in self.surfaces.items():
                known = [values[p] for p in blocks if values.get(p) is not None]
                part[sid] = sum(known) / len(known) if known else None
            self._parts[tag] = part
        return self._parts[tag]

    def predict(self, state):
        """Electric light at every surface for a state {channel: {level, cct_k}}, None where it cannot be known."""
        total = dict(self._off)
        for channel, s in {self.canon(c): s for c, s in state.items()}.items():
            part = self._part(channel, s)
            for sid in total:
                total[sid] = None if total[sid] is None or part[sid] is None else total[sid] + part[sid]
        return total

    def contributions(self, state):
        """Each channel's part of predict(state), surface by surface."""
        return {channel: self._part(channel, s) for channel, s in state.items()}
