"""The one adapter between den-core and rung 8's light model.

Every prediction den-core makes goes through LightModel here, both arms alike, and only `_predict` touches the
model's code. That is den-light's own predict.py, found as the sibling tools/den-light/predict.py and loaded by file
path under a private name, never through sys.path, where one tool's module can shadow another's. fake_light.py stands
in for it only on a model marked `fake: true`, and on a checkout with no tools/den-light. A run there still works, is
labelled fake, and score.py refuses to score it, naming why. No setting or environment variable moves the backend: only
a test can, by patching PREDICT_PATH. A log pins predict.py's hash in its light model entry, and check.py refuses when
it changes.

den-core speaks Hue scenes {on, brightness 0-255, kelvin}; rung 8 speaks {channel_key: {level 0..1, cct_k}}, with
level the command as a share of Hue's 254. A surface's value is the mean over its blocks: rung 8 v1's points are image
blocks with no surface id, so a surface map comes from the model's own `surfaces` or from `surfaces=` here. There is
always a surface 'room', for a person with no working region: the blocks a map names for it, else the whole view. A
block the fit excluded predicts None. A surface is the mean over its blocks that have a value, and None if none do,
whatever is on. Stage 1 excludes a block for every channel at once, so the channels' means add up to the scene's mean.

A bulb that is on with no brightness or no kelvin (HA's recorder keeps neither) has no prediction: its value is None,
and so is every total it is part of. Nothing guesses a level.
"""
import importlib.util
import sys
from pathlib import Path

import events
import fake_light

PREDICT_PATH = Path(__file__).resolve().parent.parent / "den-light" / "predict.py"
PREDICT_MODULE = "_den_core_den_light_predict"  # private, so it can never answer another tool's `import predict`
REAL_BACKEND = "den-light predict.py"
MARKED_FAKE = "fake_light: the model is marked fake"
NO_PREDICT = "fake_light: no tools/den-light/predict.py in this checkout"
HUE_FULL = 254
ROOM = "room"
_loaded = {}


def _real_predict():
    """(module, sha256) of den-light's predict.py, or (None, None) when this checkout has none."""
    path = Path(PREDICT_PATH)
    if not path.is_file():
        return None, None
    sha = events.sha256_file(path)
    if (path, sha) not in _loaded:
        spec = importlib.util.spec_from_file_location(PREDICT_MODULE, path)
        module = importlib.util.module_from_spec(spec)
        saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # leave no __pycache__ in den-light
        try:
            spec.loader.exec_module(module)
        except Exception as error:  # whatever a broken file raises, a run stops on one sentence
            raise ValueError("%s cannot be loaded: %s" % (path, error)) from None
        finally:
            sys.dont_write_bytecode = saved
        if not (hasattr(module, "predict") and hasattr(module, "prepare")):
            raise ValueError("%s cannot be loaded: it needs predict() and prepare()" % path)
        _loaded[(path, sha)] = module
    return _loaded[(path, sha)], sha


def backend_of(model):
    """(module, name, sha256 of the code or None) for a model dict. Anything but den-light's own code is fake."""
    if model.get("fake"):
        return fake_light, MARKED_FAKE, None
    module, sha = _real_predict()
    return (fake_light, NO_PREDICT, None) if module is None else (module, REAL_BACKEND, sha)


def _predict(scene, model, module):
    """The one call into a model's code: `model` as module.predict takes it."""
    return module.predict(scene, model)


def bad_surfaces(surfaces, points):
    """The surfaces of a map with no block, or with a block the model lacks, which its mean would leave out with no word."""
    known = set(points)
    return sorted(name for name, blocks in surfaces.items() if not blocks or not known.issuperset(blocks))


def is_surface_map(value):
    return isinstance(value, dict) and all(isinstance(blocks, list) and all(isinstance(p, str) for p in blocks) for blocks in value.values())


def _text(value):
    return isinstance(value, str) and bool(value)


def _numbers(values):
    return isinstance(values, list) and bool(values) and all(map(events.is_number, values))


def _own_text(values):
    """Ids a dict can key, with no two the same."""
    return all(map(_text, values)) and len(set(values)) == len(values)


def _check_ids(model):
    """The blocks' and channels' ids, which key every table after them; [point_id, ...]."""
    points, channels = model["sample_points"], model["channels"]
    if not isinstance(points, list) or not points or not all(isinstance(point, dict) for point in points):
        raise TypeError("its sample_points must be a list of points")
    if not isinstance(channels, list) or not channels or not all(isinstance(channel, dict) for channel in channels):
        raise TypeError("its channels must be a list of channels")
    ids = [point["point_id"] for point in points]
    if not _own_text(ids):
        raise TypeError("each sample point needs its own point_id, as text")
    if not _own_text([channel["channel_key"] for channel in channels]):
        raise TypeError("each channel needs its own channel_key, as text")
    if not _own_text([channel["entity_id"] for channel in channels]):
        raise TypeError("each channel needs its own entity_id, as text")
    return ids


def _check_channel(key, model, ids):
    """What predict.py reads for one channel: a basis per temperature, each row over every block, and a dimming table it can
    interpolate. A repeated temperature or falling levels would give a wrong number and no word."""
    cct, dimming = model["cct"][key], model["dimming"][key]
    kelvin, basis_keys = cct["kelvin"], cct["basis_keys"]
    if (not _numbers(kelvin) or min(kelvin) <= 0 or len(set(kelvin)) != len(kelvin) or not isinstance(basis_keys, list)
            or len(basis_keys) != len(kelvin)):
        raise TypeError("channel %s needs one basis per temperature, each above 0 K and no two the same" % key)
    levels, g = dimming["levels"], dimming["g"]
    if not _numbers(levels) or not _numbers(g) or len(levels) != len(g) or not all(a < b for a, b in zip(levels, levels[1:])):
        raise TypeError("channel %s needs a dimming table of numbers, its levels rising" % key)
    for basis_key in basis_keys:
        row = model["basis"][basis_key]
        if not isinstance(row, dict) or not all(value is None or events.is_number(value) for value in row.values()):
            raise TypeError("its basis %s must be numbers or null" % basis_key)
        if not row.keys() >= set(ids):
            raise TypeError("its basis %s must name every sample point, with null for one the fit excluded" % basis_key)


def _check_glare(table):
    """Canon's per channel, per eye position: {G, sd, model_status}. A solve trips on a bad row only at the eye it names."""
    for key, rows in table.items():
        if not isinstance(rows, dict):
            raise TypeError("its glare for channel %s must be {eye: {G, sd, model_status}}" % key)
        for eye, row in rows.items():
            if not (isinstance(row, dict) and events.is_number(row.get("G")) and events.is_number(row.get("sd")) and _text(row.get("model_status"))):
                raise TypeError("its glare for channel %s at eye %s needs G and sd as numbers, and a model_status" % (key, eye))


def _check_spots(table):
    """{channel: {aim: {point_id: value}}}, which only fake_light reads: a real-path model with a spot_model stops in LightModel."""
    for key, aims in table.items():
        if not isinstance(aims, dict) or not all(
                isinstance(row, dict) and all(value is None or events.is_number(value) for value in row.values()) for row in aims.values()):
            raise TypeError("its spot_model for channel %s must be {aim: {point_id: number or null}}" % key)


def check_shape(model, where):
    """A sentence for a file that is not a light model, here rather than as a TypeError in the first solve, or at the first eye
    or aim a bad table names, or as a wrong number with no word. A model is Stage-1-shaped when it has a `channels` list, and
    this reads what predict.py and this adapter read of it, and nothing they ignore."""
    try:
        if not isinstance(model, dict):
            raise TypeError("it must be a JSON object")
        for key in ("version", "basis_unit"):
            if not _text(model[key]):
                raise TypeError("its %s must be text" % key)
        if model.get("fake") is not None and type(model["fake"]) is not bool:
            raise TypeError("its fake must be true or false")
        ids = _check_ids(model)
        for key in ("cct", "dimming", "basis"):
            if not isinstance(model[key], dict):
                raise TypeError("its %s must be an object" % key)
        for channel in model["channels"]:
            _check_channel(channel["channel_key"], model, ids)
        if "surfaces" in model and not is_surface_map(model["surfaces"]):
            raise TypeError("its surfaces must be {surface_id: [point_id, ...]}")
        for key in ("error", "glare", "spot_model"):
            if model.get(key) is not None and not isinstance(model[key], dict):
                raise TypeError("its %s must be an object or null" % key)
        p90 = (model.get("error") or {}).get("p90_pct")
        if p90 is not None and not events.is_number(p90):
            raise TypeError("its error.p90_pct must be a number or null")
        _check_glare(model.get("glare") or {})
        _check_spots(model.get("spot_model") or {})
    except KeyError as error:
        raise events.BadFile("%s is not a light model: it has no %s" % (where, error)) from None
    except (TypeError, AttributeError) as error:
        raise events.BadFile("%s is not a light model: %s" % (where, error)) from None


def describe(spec):
    """(model dict, what to log) for a config's light_model spec: {source: fake, params} or {source: file, path}."""
    if spec["source"] == "fake":
        model = fake_light.build(**spec.get("params", {}))
        logged = {"source": "fake", "params": spec.get("params", {})}
    elif spec["source"] == "file":
        try:
            model = events.read_json(spec["path"])
        except OSError as error:
            raise events.BadFile("light model unreadable: %s" % error) from None
        check_shape(model, spec["path"])
        logged = {"source": "file", "path": str(spec["path"])}
    else:
        raise ValueError("light_model source must be fake or file: %r" % (spec["source"],))
    try:
        sha = events.sha256_json(model)
    except ValueError:  # json.loads reads NaN and Infinity, in a field nothing here reads too; a log can't hold them
        raise events.BadFile("%s is not a light model: it holds NaN or Infinity, which JSON has no form for" % spec.get("path", "fake")) from None
    if spec.get("sha256", sha) != sha:
        raise ValueError("light model changed: %s" % spec.get("path", "fake"))
    module, backend, code = backend_of(model)
    if spec.get("predict_sha256", code) != code:
        raise ValueError("code changed: den-light predict.py")
    logged.update(sha256=sha, version=model["version"], unit=model["basis_unit"], fake=module is fake_light,
                  backend=backend, predict_sha256=code)
    if spec.get("surfaces"):
        logged["surfaces"] = spec["surfaces"]
    return model, logged


class LightModel:
    """One light-model.json, as a function of Hue scenes. It keeps each channel's prediction per setting, since Ê is a
    sum over channels and a solve asks for the same settings many times."""

    def __init__(self, model, surfaces=None):
        self.model = model
        self.version = model["version"]
        self.unit = model["basis_unit"]
        self.module, self.backend, self.predict_sha256 = backend_of(model)
        self.fake = self.module is fake_light
        if not self.fake and model.get("spot_model"):
            raise ValueError("this model has a spot_model, and den-light's predict.py has no aim: it would ignore the aim")
        self._prepared = model if self.fake else self.module.prepare(model)  # read once, not on every call
        self.points = [p["point_id"] for p in model["sample_points"]]
        self.excluded = frozenset(p["point_id"] for p in model["sample_points"] if p.get("excluded"))
        self.keys = {c["entity_id"]: c["channel_key"] for c in model["channels"]}
        self.surfaces = {ROOM: self.points, **(surfaces or model.get("surfaces") or {})}  # 'room' unmapped: the whole view
        index = {p: n for n, p in enumerate(self.points)}
        self.members = {name: frozenset(index[p] for p in blocks if p in index) for name, blocks in self.surfaces.items()}
        # What an off bulb adds: nothing, except at a surface with no block that has a value, which never has one.
        self._off = {name: 0.0 if any(p in index and p not in self.excluded for p in blocks) else None
                     for name, blocks in self.surfaces.items()}
        self.error = model.get("error") or {}
        self.glare_table = model.get("glare")
        self.spots = model.get("spot_model") or {}
        self._cache = {}

    def _point_values(self, entity_id, scene):
        """rung 8's answer for one channel: {point_id: value} (values may be None), or None without a level."""
        if not scene["on"]:
            return {}
        aim = scene.get("aim")
        if scene.get("brightness") is None or (aim is None and scene.get("kelvin") is None):
            return None
        tag = (entity_id, scene["brightness"], scene.get("kelvin"), aim)
        if tag not in self._cache:
            setting = {"level": min(HUE_FULL, scene["brightness"]) / HUE_FULL, "cct_k": scene.get("kelvin")}
            if aim is not None:
                setting["aim"] = aim
            answer = _predict({self.keys[entity_id]: setting}, self._prepared, self.module)["surfaces"]
            values = {p: answer[p] for p in self.points}
            self._cache[tag] = (values, {name: self._mean(values, blocks) for name, blocks in self.surfaces.items()},
                                [values[p] or 0.0 for p in self.points])
        return self._cache[tag][0]

    @staticmethod
    def _mean(values, blocks):
        known = [values[p] for p in blocks if values.get(p) is not None]
        return sum(known) / len(known) if known else None

    def contribution(self, entity_id, scene):
        """One channel's part at every surface, or None when the scene has no level."""
        if scene is None or entity_id not in self.keys:
            return None
        if not scene["on"]:
            return self._off
        if self._point_values(entity_id, scene) is None:
            return None
        return self._cache[(entity_id, scene["brightness"], scene.get("kelvin"), scene.get("aim"))][1]

    def row(self, entity_id, scene):
        """One channel's part at every block, in model order, a block the model excluded counting as no light; None when
        the scene has no level."""
        if scene is None or entity_id not in self.keys:
            return None
        if not scene["on"]:
            return [0.0] * len(self.points)
        if self._point_values(entity_id, scene) is None:
            return None
        return self._cache[(entity_id, scene["brightness"], scene.get("kelvin"), scene.get("aim"))][2]

    def mask(self, indexes):
        """Blocks written as hex bits over the model's sample_points, bit 0 the first: one length for any blocks, so a
        record can name any of the grid's blocks and stay small."""
        return "%0*x" % ((len(self.points) + 3) // 4, sum(1 << n for n in set(indexes)))

    def unmask(self, mask):
        """The point_ids of a mask."""
        bits = int(mask, 16)
        return [p for n, p in enumerate(self.points) if bits >> n & 1]

    def predict(self, scene, surfaces=None):
        """{surface: Ê} for a whole scene {entity_id: Hue scene}, at every surface or at the ones named, since a map can be
        wide and a solve asks often. A value is None while any bulb has no level, and at a surface with no block that has one."""
        total = {name: self._off[name] for name in (self.surfaces if surfaces is None else surfaces)}
        for entity_id, one in scene.items():
            part = self.contribution(entity_id, one)
            for name in total:
                total[name] = None if part is None or total[name] is None or part[name] is None else total[name] + part[name]
        return total

    def patch(self, scene, surface_id):
        """The per-block values of one surface for a whole scene, or None while any bulb has no level. A block the
        model excluded is None."""
        blocks = self.surfaces[surface_id]
        total = {p: None if p in self.excluded else 0.0 for p in blocks}
        for entity_id, one in scene.items():
            values = self._point_values(entity_id, one) if one is not None else None
            if values is None:
                return None
            for p in blocks:
                total[p] = None if total[p] is None or (one["on"] and values.get(p) is None) else total[p] + values.get(p, 0.0)
        return [total[p] for p in blocks]

    def aims(self, entity_id):
        """The aims a channel's spot model offers; [None] for a channel with no aim, which is every Hue bulb."""
        return sorted(self.spots.get(self.keys.get(entity_id), {})) or [None]

    def glare(self, entity_id, eye):
        """rung 8's glare row {G: at full output, sd, model_status} for a channel and eye, or None. Its plan writes
        `glare: null`: no eye positions yet, and comfort-policy is pending."""
        return ((self.glare_table or {}).get(self.keys.get(entity_id)) or {}).get(eye)
