#!/usr/bin/env python3
"""Rung 7's pure part: the card-and-meter math and the records. Standard library only; no network.

Reads METHOD-V1's `method-v1 photometry` block, measures card and surface boxes
in meter-norm/v1 (through tools/den-light/meter.py, imported, never copied),
fits the camera scale k_L, runs the known-card round trip, exposure
invariance, the held-out lux checks and the reflectance pass, each with its
uncertainty, and builds the CalibrationRecord, RoomModel and LightState
records with the field names of docs/architecture/live-v1/schemas.json.
"""
import copy
import datetime
import hashlib
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEN_LIGHT = HERE.parent / "den-light"
if str(DEN_LIGHT) not in sys.path:
    sys.path.insert(0, str(DEN_LIGHT))

import meter  # noqa: E402  rung 8's meter-norm/v1, imported from tools/den-light

METHOD_FILE = "METHOD-V1.md"
METHOD_INFO = "method-v1 photometry"
CONTRACT = "live-v1/B"
K_L_UNIT = "(cd/m²)/nc"
NUMBER_KEYS = (
    "meter_tolerance_lx", "meter_sigma_percent", "meter_drift_max_percent", "rho_card", "rho_card_sigma",
    "min_days", "invariance_ratio", "invariance_tolerance_percent", "clipped_frac_max", "box_code_low",
    "box_code_high", "min_box_pixels", "round_trip_C_card_nc", "round_trip_rho_card", "round_trip_E_meter_lx",
    "round_trip_k_L", "round_trip_k_L_tolerance", "round_trip_L_card_cd_m2", "round_trip_L_tolerance",
    "round_trip_E_back_tolerance_lx")
INTEGER_KEYS = ("min_days", "box_code_low", "box_code_high", "min_box_pixels")
WORD_KEYS = ("commissioning_option", "k_L_scope", "meter_class", "camera_id", "fit_point", "daylight_class")
LIST_KEYS = ("required_points", "reflectance_surfaces", "illuminant_classes")
METHOD_KEYS = NUMBER_KEYS + WORD_KEYS + LIST_KEYS
CHOICES = {"commissioning_option": ("card-and-meter", "meter-only", "other-sensor"),
           "k_L_scope": ("single", "per-illuminant-class")}
BUILT_OPTION = "card-and-meter"
WORD = re.compile(r"[A-Za-z0-9._-]+")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```", re.M | re.S)
SINGLE_GROUP = "all"


class MethodError(ValueError):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def lf_text(raw):
    """Text with a BOM dropped and CRLF or CR read as LF, so either checkout reads the same."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


# ---- METHOD-V1 -------------------------------------------------------------------

@dataclass(frozen=True)
class Method:
    values: dict
    open: tuple = ()
    file_sha256: str = ""
    block_sha256: str = ""

    def __getitem__(self, name):
        return self.values[name]

    def replace(self, **changes):
        """A copy with some values changed (tests use it); open marks are kept."""
        unknown = set(changes) - set(self.values)
        if unknown:
            raise KeyError(sorted(unknown))
        return Method({**self.values, **changes}, self.open, self.file_sha256, self.block_sha256)

    def closed(self, **changes):
        """A copy with the open marks removed (tests use it)."""
        return Method({**self.values, **changes}, (), self.file_sha256, self.block_sha256)


def _value(name, raw, line):
    if not raw:
        return None
    if name in NUMBER_KEYS:
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
        if type(value) not in (int, float) or not math.isfinite(value) or (
                name in INTEGER_KEYS and type(value) is not int):
            raise MethodError(f"bad method line (a number belongs here): {line.strip()!r}")
        return value
    words = raw.split()
    if not words or not all(WORD.fullmatch(w) for w in words) or (name in WORD_KEYS and len(words) != 1):
        raise MethodError(f"bad method line (words of letters, digits, '.', '_' or '-'): {line.strip()!r}")
    return words[0] if name in WORD_KEYS else tuple(words)


def parse_method(text):
    """The values and open names of the one `method-v1 photometry` block in text."""
    text = lf_text(text)
    blocks = [body for info, body in FENCE.findall(text) if info.strip() == METHOD_INFO]
    if len(blocks) != 1:
        raise MethodError(f"the method file must hold exactly one `{METHOD_INFO}` block")
    values, marked = {}, []
    for line in blocks[0].splitlines():
        content, _, comment = line.partition("#")
        content = content.strip()
        if not content:
            continue
        name, sep, raw = (part.strip() for part in content.partition("="))
        if not sep or name not in METHOD_KEYS or name in values:
            raise MethodError(f"bad method line: {line.strip()!r}")
        is_open = comment.strip().lower().startswith("open")
        value = _value(name, raw, line)
        if value is None and not is_open:
            raise MethodError(f"only an open line may be empty: {line.strip()!r}")
        values[name] = value
        if is_open:
            marked.append(name)
    if set(values) != set(METHOD_KEYS):
        missing, extra = sorted(set(METHOD_KEYS) - set(values)), sorted(set(values) - set(METHOD_KEYS))
        raise MethodError(f"method keys differ from card.py's list (missing {missing}, extra {extra})")
    return values, tuple(marked), blocks[0]


def load_method(path=None):
    text = lf_text(Path(path or HERE / METHOD_FILE).read_bytes())
    values, marked, block = parse_method(text)
    return Method(values, marked, sha256_bytes(text.encode("utf-8")), sha256_bytes(block.encode("utf-8")))


def ready_problems(method):
    """Why a scored step may not run under this method: its open lines, then any value it cannot use."""
    if method.open:
        return ["METHOD-V1 still has lines the owner has not confirmed: " + ", ".join(method.open)]
    v, problems = method.values, []
    for name, choices in CHOICES.items():
        if v[name] not in choices:
            problems.append(f"{name} must be one of {', '.join(choices)}")
    if v["commissioning_option"] != BUILT_OPTION:
        problems.append(f"commissioning_option is {v['commissioning_option']}: this tool builds {BUILT_OPTION} only")
    positive = ("meter_tolerance_lx", "rho_card", "invariance_ratio", "invariance_tolerance_percent", "min_days",
                "min_box_pixels")
    problems += [f"{name} must be above 0" for name in positive if not v[name] > 0]
    problems += [f"{name} must not be below 0" for name in (
        "meter_sigma_percent", "meter_drift_max_percent", "rho_card_sigma", "clipped_frac_max") if v[name] < 0]
    if not v["rho_card"] <= 1:
        problems.append("rho_card must be at most 1")
    if not v["invariance_ratio"] < 1:
        problems.append("invariance_ratio must be below 1 (hold 2 is the shorter one)")
    if not 0 <= v["box_code_low"] < v["box_code_high"] <= 255:
        problems.append("box_code_low and box_code_high must satisfy 0 <= low < high <= 255")
    if v["daylight_class"] not in v["illuminant_classes"]:
        problems.append("daylight_class must be one of illuminant_classes")
    names = [v["camera_id"], v["fit_point"], *v["required_points"], *v["reflectance_surfaces"],
             *v["illuminant_classes"]]
    problems += [f"{n!r} is not a name (lower case letters, digits and '-')" for n in names if not NAME.fullmatch(n)]
    return problems


# ---- K21 and K11: the camera scale and the production path ------------------------

def fit_k_L(C_card_nc, rho_card, E_meter_lx):
    """K21: k_L = ρ_card·E_meter / (π·C_card), in (cd/m²)/nc."""
    if not C_card_nc > 0:
        raise ValueError("C_card must be above 0")
    return rho_card * E_meter_lx / (math.pi * C_card_nc)


def luminance(k_L, C_nc):
    """K11: L = k_L·C, in cd/m²."""
    return k_L * C_nc


def illuminance(k_L, C_nc, rho):
    """K11: Ê = π·k_L·C/ρ for a matte surface of reflectance ρ, in lux."""
    return math.pi * luminance(k_L, C_nc) / rho


def round_trip(method, frame_versions=(meter.METER_NORM_VERSION,), k_L_version=meter.METER_NORM_VERSION):
    """The known-card round trip through fit_k_L, then luminance and illuminance (the gate's precondition)."""
    v = method.values
    C, rho, E = v["round_trip_C_card_nc"], v["round_trip_rho_card"], v["round_trip_E_meter_lx"]
    k = fit_k_L(C, rho, E)
    L = luminance(k, C)
    E_back = illuminance(k, C, rho)
    versions = sorted(set(frame_versions))
    matches = bool(versions) and versions == [k_L_version] and k_L_version == meter.METER_NORM_VERSION
    ok = (abs(k - v["round_trip_k_L"]) <= v["round_trip_k_L_tolerance"]
          and abs(L - v["round_trip_L_card_cd_m2"]) <= v["round_trip_L_tolerance"]
          and abs(E_back - E) <= v["round_trip_E_back_tolerance_lx"] and matches)
    return {"C_card_nc": C, "rho_card": rho, "E_meter_lx": E, "k_L": round(k, 9),
            "L_card_cd_m2": round(L, 9), "E_back_lx": round(E_back, 9),
            "expected": {"k_L": v["round_trip_k_L"], "L_card_cd_m2": v["round_trip_L_card_cd_m2"],
                         "E_back_lx": E},
            "normalisation_version": k_L_version, "frame_normalisation_versions": versions,
            "normalisation_version_matches": matches, "result": "pass" if ok else "fail"}


# ---- boxes in frames ----------------------------------------------------------------

def parse_box(text):
    """'x0 y0 x1 y1' (inclusive, inside 640x360) as a tuple, or None when blank."""
    text = (text or "").replace(",", " ").strip()
    if not text:
        return None
    parts = text.split()
    try:
        x0, y0, x1, y1 = (int(p) for p in parts)
    except ValueError:
        raise ValueError(f"a box is four whole numbers 'x0 y0 x1 y1', not {text!r}") from None
    if not (0 <= x0 < x1 < meter.WIDTH and 0 <= y0 < y1 < meter.HEIGHT):
        raise ValueError(f"box {text!r} must have 0 <= x0 < x1 <= 639 and 0 <= y0 < y1 <= 359")
    return x0, y0, x1, y1


def box_indices(box):
    x0, y0, x1, y1 = box
    return [y * meter.WIDTH + x for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def box_hold(frames, box, parameters):
    """One box over one hold's frames: [{report, rgb}] with rgb the full-frame codes (meter.decode, step 1)."""
    index = box_indices(box)
    values, flagged, total = [], 0, 0
    code_sums = [0, 0, 0]
    for frame in frames:
        tg = meter.exposure_ms_gain(frame["report"])
        rs, gs, bs = frame["rgb"]
        frame_sum, frame_n = 0.0, 0
        for k in index:
            r, g, b = rs[k], gs[k], bs[k]
            code_sums[0] += r
            code_sums[1] += g
            code_sums[2] += b
            total += 1
            value = meter.meter_norm_value(r, g, b, *tg, parameters) if tg else None
            if value is None:
                flagged += 1
                continue
            frame_sum += value
            frame_n += 1
        if frame_n:
            values.append(frame_sum / frame_n)
    n = len(values)
    return {"C_nc": statistics.fmean(values) if n else None,
            "sem_nc": statistics.stdev(values) / math.sqrt(n) if n > 1 else 0.0,
            "frames": len(frames), "frames_with_value": n, "pixels": len(index), "flagged": flagged,
            "pixel_frames": total,
            "mean_codes": [s / total for s in code_sums] if total else None}


def box_reading(hold1, hold2, gains_pass, method):
    """A box's reading from holds 1 and 2: C, u_C, exposure invariance and whether it is usable."""
    v = method.values
    flagged, total = hold1["flagged"] + hold2["flagged"], hold1["pixel_frames"] + hold2["pixel_frames"]
    clipped = flagged / total if total else 1.0
    problem = None
    if hold1["pixels"] < v["min_box_pixels"]:
        problem = f"box has {hold1['pixels']} pixels, fewer than {v['min_box_pixels']}"
    elif not gains_pass:
        problem = "a kept frame is off its hold (rung 8's gain agreement)"
    elif clipped > v["clipped_frac_max"]:
        problem = f"clipped fraction {clipped:.4f} is above {v['clipped_frac_max']}"
    elif any(not v["box_code_low"] <= code <= v["box_code_high"]
             for hold in (hold1, hold2) for code in hold["mean_codes"]):
        problem = f"a hold's mean code is outside {v['box_code_low']}..{v['box_code_high']}"
    elif hold1["C_nc"] is None or hold2["C_nc"] is None:
        problem = "no unflagged pixel in a hold"
    if problem:
        return {"usable": False, "problem": problem, "clipped_frac": clipped, "hold1": hold1, "hold2": hold2}
    c1, c2 = hold1["C_nc"], hold2["C_nc"]
    C = (c1 + c2) / 2
    if not C > 0:
        return {"usable": False, "problem": "the box reads 0 nc", "clipped_frac": clipped, "hold1": hold1,
                "hold2": hold2}
    s1, s2 = hold1["sem_nc"], hold2["sem_nc"]
    u_C = math.sqrt((s1 * s1 + s2 * s2) / 4 + (c1 - c2) ** 2 / 4)
    d = 100 * abs(c1 - c2) / C
    u_d = 100 * math.sqrt(s1 * s1 + s2 * s2) / C
    return {"usable": True, "problem": None, "C_nc": C, "u_C_nc": u_C, "clipped_frac": clipped,
            "invariance_pct": d, "u_invariance_pct": u_d,
            "invariance_pass": d <= v["invariance_tolerance_percent"] + 1e-12, "hold1": hold1, "hold2": hold2}


def meter_reading(before, after, method):
    """The point's reading: the mean of before and after, its relative uncertainty, and whether it is used."""
    if before is None and after is None:
        return None
    v = method.values
    if before is None or after is None:
        return {"used": False, "problem": "give both E_before_lx and E_after_lx", "E_before_lx": before,
                "E_after_lx": after}
    if not (before > 0 and after > 0):
        return {"used": False, "problem": "a reading must be above 0 lx", "E_before_lx": before, "E_after_lx": after}
    E = (before + after) / 2
    drift = 100 * abs(before - after) / E
    u_rel = math.sqrt((v["meter_sigma_percent"] / 100) ** 2 + ((before - after) / (2 * E)) ** 2)
    out = {"used": drift <= v["meter_drift_max_percent"] + 1e-12, "problem": None, "E_before_lx": before,
           "E_after_lx": after, "E_lx": E, "u_rel": u_rel, "drift_pct": drift}
    if not out["used"]:
        out["problem"] = f"before and after differ by {drift:.1f}%, above {v['meter_drift_max_percent']}%: the light changed"
    return out


def k_L_fit(card, reading, method):
    """k_L from one card reading and one meter reading, with its relative 1-sigma uncertainty."""
    v = method.values
    k = fit_k_L(card["C_nc"], v["rho_card"], reading["E_lx"])
    rel = math.sqrt((v["rho_card_sigma"] / v["rho_card"]) ** 2 + reading["u_rel"] ** 2
                    + (card["u_C_nc"] / card["C_nc"]) ** 2)
    return {"k_L": k, "u_rel": rel}


def held_out_check(fit_card, fit_reading, k_L, card, reading, method):
    """Ê at a held-out card against its meter reading, in lux, with the error's 1-sigma uncertainty."""
    v = method.values
    E_hat = illuminance(k_L, card["C_nc"], v["rho_card"])
    rel = math.sqrt(fit_reading["u_rel"] ** 2 + (fit_card["u_C_nc"] / fit_card["C_nc"]) ** 2
                    + (card["u_C_nc"] / card["C_nc"]) ** 2)
    u_E = reading["E_lx"] * reading["u_rel"]
    error = E_hat - reading["E_lx"]
    tolerance = v["meter_tolerance_lx"]
    return {"E_meter_lx": reading["E_lx"], "u_E_meter_lx": u_E, "E_hat_lx": E_hat,
            "error_lx": error, "u_error_lx": math.sqrt((E_hat * rel) ** 2 + u_E ** 2),
            "pass": None if tolerance is None else abs(error) <= tolerance + 1e-9}


def reflectance(pairs, method):
    """ρ_s = ρ_card·q̄ from [(surface box reading, card box reading)] of one surface, with its uncertainty."""
    v = method.values
    qs, us = [], []
    for surface, card in pairs:
        q = surface["C_nc"] / card["C_nc"]
        qs.append(q)
        us.append(q * math.sqrt((surface["u_C_nc"] / surface["C_nc"]) ** 2 + (card["u_C_nc"] / card["C_nc"]) ** 2))
    if all(u > 0 for u in us):
        weights = [1 / (u * u) for u in us]
        q_bar = sum(w * q for w, q in zip(weights, qs)) / sum(weights)
        u_q = 1 / math.sqrt(sum(weights))
    else:
        q_bar = statistics.fmean(qs)
        u_q = statistics.stdev(qs) / math.sqrt(len(qs)) if len(qs) > 1 else 0.0
    rho = v["rho_card"] * q_bar
    rel = math.sqrt((v["rho_card_sigma"] / v["rho_card"]) ** 2 + (u_q / q_bar) ** 2)
    return {"rho": rho, "u_rel": rel, "q": q_bar, "u_q": u_q, "n_captures": len(pairs)}


# ---- which captures count -------------------------------------------------------------

def group_of(capture, method):
    return SINGLE_GROUP if method["k_L_scope"] == "single" else capture["illuminant"]


def select(captures, method):
    """Groups with their fit and held-out captures, the reflectance captures, and every capture not used.

    captures: [{id, day, order, point, illuminant, card, surface, reading}] where card and
    surface are box_reading results (or None) and reading a meter_reading result (or None).
    """
    v = method.values
    unused, reflect, metered, days = [], {}, {}, {}
    for cap in sorted(captures, key=lambda c: c["order"]):
        key = group_of(cap, method)
        days[key] = min(days.get(key, cap["day"]), cap["day"])
        card, surface, reading = cap.get("card"), cap.get("surface"), cap.get("reading")
        if card is None and surface is None and reading is None:
            unused.append((cap, "nothing typed"))
            continue
        if card is None or not card["usable"]:
            unused.append((cap, "card box: " + (card["problem"] if card else "missing")))
            continue
        if surface is None and reading is None:
            unused.append((cap, "a card box only: no reading and no surface box"))
            continue
        if surface is not None:
            if surface["usable"]:
                reflect.setdefault(cap["point"], []).append(cap)
            else:
                unused.append((cap, "surface box: " + surface["problem"]))
        if reading is not None:
            if reading["used"]:
                metered.setdefault(key, []).append(cap)
            else:
                unused.append((cap, "reading: " + reading["problem"]))
    groups = {}
    for key, caps in metered.items():
        first_day = days[key]  # the earliest day holding any capture of the group (METHOD-V1, The fit)
        candidates = [c for c in caps if c["day"] == first_day and c["point"] == v["fit_point"]]
        fit = candidates[0] if candidates else None
        held = []
        for cap in caps:
            if cap is fit:
                continue
            if fit is not None and cap["day"] == fit["day"] and cap["point"] == v["fit_point"]:
                unused.append((cap, "repeats the fit (same point and day)"))
                continue
            held.append(cap)
        groups[key] = {"first_day": first_day, "fit": fit, "held_out": held}
    return {"groups": groups, "reflectance": reflect, "unused": unused}


def coverage(selection, method):
    """What the held-out checks and the reflectance pass still miss (empty lists when complete)."""
    v = method.values
    held = [c for g in selection["groups"].values() for c in g["held_out"]]
    missing = []
    days = {c["day"] for g in selection["groups"].values() for c in g["held_out"] + ([g["fit"]] if g["fit"] else [])}
    if len(days) < v["min_days"]:
        missing.append(f"{len(days)} day(s) with fit or held-out readings, fewer than {v['min_days']}")
    points = {c["point"] for c in held}
    missing += [f"no held-out reading at {p}" for p in v["required_points"] if p not in points]
    if not any(c["illuminant"] == v["daylight_class"] for c in held):
        missing.append(f"no held-out reading logged under {v['daylight_class']}")
    missing += [f"group {k} has no fit capture at {v['fit_point']} on its first day ({g['first_day']})"
                for k, g in sorted(selection["groups"].items()) if g["fit"] is None]
    if not selection["groups"]:
        missing.append("no capture with a used reading")
    reflect_missing = [f"no usable reflectance capture of {s}" for s in v["reflectance_surfaces"]
                       if s not in selection["reflectance"]]
    return {"held_out": missing, "reflectance": reflect_missing}


# ---- the score ------------------------------------------------------------------------------

def score(captures, method, frame_versions):
    """Every check and verdict for a set of measured captures (see METHOD-V1, Verdicts)."""
    v = method.values
    selection = select(captures, method)
    cover = coverage(selection, method)
    trip = round_trip(method, frame_versions)
    used = {id(c): c for g in selection["groups"].values() for c in g["held_out"] + ([g["fit"]] if g["fit"] else [])}
    for caps in selection["reflectance"].values():
        used.update({id(c): c for c in caps})
    boxes = []
    for cap in sorted(used.values(), key=lambda c: c["order"]):
        for name in ("card", "surface"):
            box = cap.get(name)
            if box is not None and box["usable"]:
                boxes.append({"capture": cap["id"], "box": name, "d_pct": box["invariance_pct"],
                              "u_pct": box["u_invariance_pct"], "pass": box["invariance_pass"]})
    invariance = {"result": "pass" if boxes and all(b["pass"] for b in boxes) else "fail",
                  "status": "scored", "tolerance_pct": v["invariance_tolerance_percent"],
                  "worst_pct": max((b["d_pct"] for b in boxes), default=None), "n_boxes": len(boxes),
                  "boxes": boxes}
    groups = {}
    for key, group in sorted(selection["groups"].items()):
        fit = group["fit"]
        entry = {"first_day": group["first_day"], "fit": fit["id"] if fit else None, "k_L": None,
                 "u_rel": None, "points": []}
        if fit is not None:
            entry.update(k_L_fit(fit["card"], fit["reading"], method))
            for cap in group["held_out"]:
                check = held_out_check(fit["card"], fit["reading"], entry["k_L"], cap["card"], cap["reading"], method)
                entry["points"].append({"capture": cap["id"], "day": cap["day"], "point": cap["point"],
                                        "illuminant": cap["illuminant"], **check})
        groups[key] = entry
    errors = [p["error_lx"] for g in groups.values() for p in g["points"]]
    if trip["result"] != "pass" or invariance["result"] != "pass":
        held_result = "not scored"
    elif cover["held_out"]:
        held_result = "incomplete"
    else:
        held_result = "pass" if all(p["pass"] for g in groups.values() for p in g["points"]) else "fail"
    held_out = {"tolerance_lx": v["meter_tolerance_lx"], "tolerance_status": "fixed before day 1",
                "result": held_result, "status": "scored",
                "residual_lx_rms": math.sqrt(statistics.fmean(e * e for e in errors)) if errors else None,
                "n_points": len(errors), "missing": cover["held_out"]}
    reflect = {}
    if invariance["result"] == "pass":
        for surface, caps in sorted(selection["reflectance"].items()):
            reflect[surface] = dict(reflectance([(c["surface"], c["card"]) for c in caps], method),
                                    captures=[c["id"] for c in caps])
    anchored = trip["result"] == "pass" and invariance["result"] == "pass" and held_result == "pass"
    gate = "pass" if anchored and not cover["reflectance"] else (
        "fail" if "fail" in (trip["result"], invariance["result"], held_result) else "incomplete")
    return {"round_trip": trip, "exposure_invariance": invariance, "held_out": held_out, "groups": groups,
            "reflectance": reflect, "reflectance_missing": cover["reflectance"], "anchored": anchored,
            "gate": gate, "selection": selection}


# ---- the sun --------------------------------------------------------------------------------

def sun_position(unix_s, latitude, longitude):
    """{azimuth, elevation} in degrees (azimuth clockwise from true north), NOAA's general solar position."""
    t = datetime.datetime.fromtimestamp(unix_s, datetime.timezone.utc)
    hours = t.hour + t.minute / 60 + (t.second + t.microsecond / 1e6) / 3600
    days = 366 if (t.year % 4 == 0 and (t.year % 100 or t.year % 400 == 0)) else 365
    g = 2 * math.pi / days * (t.timetuple().tm_yday - 1 + (hours - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    true_solar_min = hours * 60 + eqtime + 4 * longitude
    hour_angle = math.radians(true_solar_min / 4 - 180)
    lat = math.radians(latitude)
    cos_zen = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    zen = math.acos(max(-1.0, min(1.0, cos_zen)))
    denominator = math.cos(lat) * math.sin(zen)
    if abs(denominator) < 1e-12:
        azimuth = 180.0
    else:
        x = (math.sin(lat) * math.cos(zen) - math.sin(decl)) / denominator
        a = math.degrees(math.acos(max(-1.0, min(1.0, x))))
        azimuth = (a + 180) % 360 if math.sin(hour_angle) > 0 else (540 - a) % 360
    return {"azimuth": round(azimuth, 2), "elevation": round(90 - math.degrees(zen), 2)}


# ---- the records --------------------------------------------------------------------------

def envelope(record_id, body):
    return {"record": record_id, "contract": CONTRACT, **body}


def exposure_of(capture):
    return {"time_ms": capture["exposure"]["time_ms"], "gain": capture["exposure"]["gain"], "black_level": None}


def k_L_records(result, captures_by_id, method, normalisation, score_n, previous=None):
    """One CalibrationRecord per group (a single k_L, or one per illuminant class)."""
    v = method.values
    out = []
    for key, group in sorted(result["groups"].items()):
        fit = captures_by_id.get(group["fit"]) if group["fit"] else None
        version = f"cal-{v['camera_id']}-kL-{score_n}" + ("" if key == SINGLE_GROUP else f"-{key}")
        points = [{k: (round(x, 6) if isinstance(x, float) else x) for k, x in p.items()} for p in group["points"]]
        held = dict(result["held_out"], points=points)
        if v["k_L_scope"] != "single":
            held["group"] = key
        record = {
            "parameter": f"RoomModel.cameras[{v['camera_id']}].photometric.k_L",
            "value": round(group["k_L"], 9) if group["k_L"] is not None else None,
            "unit": K_L_UNIT, "method": "meter",
            "confidence_pct_1sigma": round(100 * group["u_rel"], 4) if group["u_rel"] is not None else None,
            "inputs": {
                "normalisation_version": normalisation["version"],
                "normalisation_parameters_sha256": normalisation["sha256"],
                "rho_card": v["rho_card"],
                "E_meter_lx": round(fit["reading"]["E_lx"], 6) if fit else None,
                "C_card_nc": round(fit["card"]["C_nc"], 9) if fit else None,
                "exposure": exposure_of(fit) if fit else None,
                "clipped_frac": round(fit["card"]["clipped_frac"], 6) if fit else None,
                "illuminant": {"class": fit["illuminant"], "light": fit["light"], "blinds": fit["blinds"]}
                if fit else None},
            "checks": {"round_trip": result["round_trip"], "held_out": held,
                       "exposure_invariance": result["exposure_invariance"]},
            "ts": fit["ts"] if fit else None, "version": version}
        if previous and previous.get(key):
            record.update(supersedes=previous[key]["version"], supersedes_method=previous[key]["method"],
                          supersedes_value=previous[key]["value"])
        out.append(envelope("calibration_record", record))
    return out


def reflectance_records(result, captures_by_id, method, normalisation, score_n):
    v = method.values
    out = []
    for surface, rho in sorted(result["reflectance"].items()):
        caps = [captures_by_id[c] for c in rho["captures"]]
        out.append(envelope("calibration_record", {
            "parameter": f"RoomModel.surfaces[{surface}].reflectance.value",
            "value": round(rho["rho"], 6), "unit": "fraction", "method": "meter",
            "confidence_pct_1sigma": round(100 * rho["u_rel"], 4),
            "inputs": {"normalisation_version": normalisation["version"],
                       "normalisation_parameters_sha256": normalisation["sha256"],
                       "rho_card": v["rho_card"], "C_card_nc": [round(c["card"]["C_nc"], 9) for c in caps],
                       "C_surface_nc": [round(c["surface"]["C_nc"], 9) for c in caps],
                       "clipped_frac": max(max(c["card"]["clipped_frac"], c["surface"]["clipped_frac"]) for c in caps),
                       "illuminant": sorted({c["illuminant"] for c in caps})},
            "ts": max(c["ts"] for c in caps), "version": f"cal-{v['camera_id']}-rho-{surface}-{score_n}",
            "note": "rho_s = rho_card * C_s / C_card, the card beside s in the same frames; no camera scale"}))
    return out


def room_model_record(room, result, k_records, rho_records, method, normalisation, score_n):
    """The input RoomModel with a new version, the camera's photometric entry and the measured reflectances."""
    v = method.values
    room = copy.deepcopy(room)
    room.pop("record", None)
    room.pop("contract", None)
    old = room.get("version")
    room["version"] = f"{old}-p{score_n}"
    cameras = [c for c in room.get("cameras", []) if c.get("camera_id") == v["camera_id"]]
    if len(cameras) != 1:
        raise ValueError(f"the RoomModel must hold exactly one camera {v['camera_id']}")
    photometric = {"k_L": None, "unit": K_L_UNIT, "normalisation_version": normalisation["version"],
                   "calibration_id": None, "status": "uncalibrated"}
    if v["k_L_scope"] == "single" and k_records:
        record = k_records[0]
        photometric["calibration_id"] = record["version"]
        if result["anchored"]:
            photometric.update(k_L=record["value"], status="measured")
    elif k_records:  # per illuminant class: an owner question, the contract has room for one k_L
        keys = sorted(result["groups"])
        photometric["k_L_by_illuminant"] = ({k: r["value"] for k, r in zip(keys, k_records)}
                                            if result["anchored"] else None)
        photometric["calibration_ids_by_illuminant"] = {k: r["version"] for k, r in zip(keys, k_records)}
        if result["anchored"]:
            photometric["status"] = "measured"
    cameras[0]["photometric"] = photometric
    surfaces = {s.get("surface_id"): s for s in room.get("surfaces", [])}
    for record in rho_records:
        surface_id = record["parameter"][len("RoomModel.surfaces["):-len("].reflectance.value")]
        surfaces[surface_id]["reflectance"] = {"value": record["value"], "status": "measured",
                                               "calibration_id": record["version"]}
    return envelope("room_model", room)


def light_state_record(capture, k_L, anchored, method, normalisation, room_version, location=None):
    """One LightState from one capture's card; lux only when anchored."""
    v = method.values
    card = capture["card"]
    if anchored:
        value, unit = illuminance(k_L, card["C_nc"], v["rho_card"]), "lx"
    else:
        value, unit = math.pi * card["C_nc"] / v["rho_card"], "relative"
    sun = sun_position(capture["ts"], *location) if location else None
    return envelope("light_state", {
        "ts": capture["ts"], "camera_id": v["camera_id"], "room_version": room_version,
        "exposure": {**exposure_of(capture), "wb_locked": True, "linear": True,
                     "normalisation_version": normalisation["version"]},
        "anchored": anchored,
        "surface_estimates": [{"surface_id": capture["point"], "value": round(value, 9), "unit": unit,
                               "clipped_frac": round(card["clipped_frac"], 6), "rho_status": "measured",
                               "basis": "measured"}],
        "window_daylight": [], "sun": sun, "blinds": capture["blinds"], "residual": []})
