#!/usr/bin/env python3
"""Rung 8 Stage 1's model: the schedule, the reduction, the fit, the score. Standard library only.

    python -B tools\\den-light\\model.py --session C:\\den-light\\<session-id> --stage0 C:\\den-light\\stage0-<date>\\sweep-<n>

reduces the session's frames to readings.json, fits, scores its hold-outs and
writes light-model.json and score.json into the session folder. Importing this
module has no side effects: it reads nothing until a function is called.
Every number comes from METHOD-V1's `method-v1 stage1` block (and Stage 0's
block for meter-norm/v1's clipping limits and the noise tolerance).
"""
import argparse
import hashlib
import json
import math
import random
import re
import statistics
import sys
import time
from pathlib import Path

import guard
import meter
import predict as predict_module

HERE = Path(__file__).resolve().parent
STAGE1_INFO = "method-v1 stage1"
STAGE1_KEYS = (
    "block_px", "reduce_pixel_step", "min_unmasked_share",
    "exposure_percentile", "exposure_search_steps", "confirm_ratios",
    "dimming_levels", "full_level", "dark_level_max", "dark_run_limit_s", "step_estimate_s", "bracket_every",
    "holdouts", "random_holdouts", "holdout_on_probability", "holdout_level_low", "holdout_level_high",
    "near_full_holdouts", "near_full_share", "gaming_level", "schedule_seed",
    "frames_per_capture", "step_dwell_s", "settle_limit_s", "frame_timeout_s",
    "signal_rich_fraction", "shared_curve_percent", "shared_curve_from_level", "bracket_tolerance_multiple",
    "noise_floor_multiple", "gate_median_percent", "gate_p90_percent",
    "poll_s", "vacancy_s", "start_wait_limit_s", "hold_brightness_tolerance", "hold_mired_tolerance",
    "hold_check_wait_s",
    "unavailable_wait_s", "walkthrough_limit_s", "off_check_s", "resume_check_s", "ha_timeout_s")
PROPOSED = re.compile(r"\bproposed\s+(\[[^\]]*\]|-?[0-9]+(?:\.[0-9]+)?)")
FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```", re.M | re.S)
SCHEMAS = {"readings": "den-light-readings-1", "score": "den-light-score-1"}
FITTED_KINDS = ("all_off", "drift", "basis", "dimming", "holdout")


class Refused(Exception):
    """Nothing was written."""


def sha256_lf(text):
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


# ---- METHOD-V1, Stage 1 part ---------------------------------------------------------

def _number_or_list(value):
    if type(value) in (int, float) and math.isfinite(value):
        return True
    return type(value) is list and value and all(type(v) in (int, float) and math.isfinite(v) for v in value)


class Method1:
    """The Stage 1 numbers and the open slots (name -> proposed value)."""

    def __init__(self, values, open_slots, file_sha256="", block_sha256=""):
        self.values, self.open = dict(values), dict(open_slots)
        self.file_sha256, self.block_sha256 = file_sha256, block_sha256

    def __getitem__(self, name):
        """The closed value, or the proposed one for an open slot (synthetic runs only)."""
        return self.values[name] if name in self.values else self.open[name]

    def closed(self, **values):
        """A copy with these slots closed (tests, or the owner's answers before the commit)."""
        unknown = set(values) - set(STAGE1_KEYS)
        if unknown:
            raise KeyError(sorted(unknown))
        return Method1({**self.values, **values}, {k: v for k, v in self.open.items() if k not in values},
                       self.file_sha256, self.block_sha256)

    def proposed(self):
        """Every open slot closed at its proposed value: a synthetic run's numbers."""
        return self.closed(**self.open)

    def describe_open(self):
        return [f"{name} (proposed {json.dumps(value)})" for name, value in sorted(self.open.items())]

    def refuse_if_open(self, what):
        if self.open:
            raise Refused(f"{what} needs every METHOD-V1 Stage 1 slot closed; open: " + ", ".join(self.describe_open()))


def parse_stage1(text):
    text = text.replace("\r\n", "\n")
    blocks = [body for info, body in FENCE.findall(text) if info.strip() == STAGE1_INFO]
    if len(blocks) != 1:
        raise ValueError(f"the method file must hold exactly one `{STAGE1_INFO}` block")
    values, open_slots = {}, {}
    for raw_line in blocks[0].splitlines():
        line, _, note = raw_line.partition("#")
        line = line.strip()
        if not line:
            continue
        name, sep, raw = (part.strip() for part in line.partition("="))
        if not sep or name in values or name in open_slots:
            raise ValueError(f"bad method line: {raw_line!r}")
        if raw == "open":
            match = PROPOSED.search(note)
            if not match or not note.strip().lower().startswith("open"):
                raise ValueError(f"open slot {name} needs '# open, proposed <value>'")
            proposed = json.loads(match[1])
            if not _number_or_list(proposed):
                raise ValueError(f"bad proposed value for {name}")
            open_slots[name] = proposed
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            raise ValueError(f"bad method line: {raw_line!r}") from None
        if not _number_or_list(value):
            raise ValueError(f"bad method line: {raw_line!r}")
        values[name] = value
    if set(values) | set(open_slots) != set(STAGE1_KEYS):
        keys = set(values) | set(open_slots)
        raise ValueError(f"Stage 1 keys differ from model.py's list (missing {sorted(set(STAGE1_KEYS) - keys)}, "
                         f"extra {sorted(keys - set(STAGE1_KEYS))})")
    return Method1(values, open_slots, sha256_lf(text), sha256_lf(blocks[0]))


def load_stage1(path=None):
    return parse_stage1(Path(path or HERE / meter.METHOD_FILE).read_bytes().decode("utf-8"))


# ---- the grid ----------------------------------------------------------------------------

def grid(method1):
    bp = int(method1["block_px"])
    if bp <= 0 or 640 % bp or 360 % bp:
        raise ValueError("block_px must divide 640 and 360")
    return {"block_px": bp, "cols": 640 // bp, "rows": 360 // bp, "blocks": (640 // bp) * (360 // bp)}


def block_rects(method1):
    g = grid(method1)
    bp = g["block_px"]
    return [{"x": (b % g["cols"]) * bp, "y": (b // g["cols"]) * bp, "w": bp, "h": bp} for b in range(g["blocks"])]


def pixel_blocks(method1):
    """The block of each sampled pixel, in meter.decode()'s order at reduce_pixel_step."""
    g, step = grid(method1), int(method1["reduce_pixel_step"])
    return [(y // g["block_px"]) * g["cols"] + x // g["block_px"] for x, y in meter.sample_points(step)]


# ---- the schedule ------------------------------------------------------------------------

def mired(kelvin):
    return 1e6 / kelvin


def temperatures(ranges):
    """(warm, middle in mired, cool) kelvin over the range all four share: ranges {entity: (min K, max K)}."""
    warm = max(low for low, _ in ranges.values())
    cool = min(high for _, high in ranges.values())
    if warm >= cool:
        raise ValueError("the four bulbs share no colour temperature range")
    return warm, round(1e6 / ((mired(warm) + mired(cool)) / 2)), cool


def off_scene():
    return {e: guard.scene(False) for e in guard.LIGHTS}


def one_on(e, level, kelvin):
    scene = off_scene()
    scene[e] = guard.scene(True, level, kelvin)
    return scene


def all_on(level, kelvins):
    return {e: guard.scene(True, level, kelvins[e] if isinstance(kelvins, dict) else kelvins) for e in guard.LIGHTS}


def is_dark(step, method1):
    lit = [s for s in step["scene"].values() if s["on"]]
    return not lit or all(s["brightness"] <= method1["dark_level_max"] for s in lit)


def has_full(step, method1):
    return any(s["on"] and s["brightness"] >= method1["full_level"] for s in step["scene"].values())


def holdout_scenes(method1, snapshot, temps, warmest, rng):
    warm, mid, cool = temps
    full = method1["full_level"]
    fitted = set(method1["dimming_levels"]) | {full}
    captured = {warm, mid, cool}
    scenes = [
        ("normal", {e: guard.scene(True, min(full, s["brightness"]), s["kelvin"]) for e, s in snapshot.items()}),
        ("gaming", {e: guard.scene(True, method1["gaming_level"], warmest[e]) for e in guard.LIGHTS}),
        ("all_full_middle", all_on(full, mid)),
        ("all_full_split", {e: guard.scene(True, full, warm if e in guard.PAIRS[0] else cool) for e in guard.LIGHTS}),
    ]
    low, high = method1["holdout_level_low"], method1["holdout_level_high"]

    def level(lo):
        while True:
            u = round(math.exp(rng.uniform(math.log(lo), math.log(high))))
            if u not in fitted and low <= u <= high:
                return u

    def kelvin():
        while True:
            k = round(1e6 / rng.uniform(mired(cool), mired(warm)))
            if k not in captured and warm <= k <= cool:
                return k

    near = method1["near_full_share"] * full
    for n in range(method1["random_holdouts"]):
        if n == 0:  # near full combined output
            scene = {e: guard.scene(True, level(near), kelvin()) for e in guard.LIGHTS}
        else:
            while True:
                scene = {e: (guard.scene(True, level(low), kelvin()) if rng.random() < method1["holdout_on_probability"]
                             else guard.scene(False)) for e in guard.LIGHTS}
                if sum(s["on"] for s in scene.values()) >= 2:
                    break
        scenes.append((f"random_{n + 1}", scene))
    return scenes


def schedule(method1, snapshot, ranges, warmest):
    """Every step of a session, in order. Pure: the same inputs and seed give the same list."""
    warm, mid, cool = temps = temperatures(ranges)
    full, rng = method1["full_level"], random.Random(method1["schedule_seed"])
    steps = []

    def add(kind, scene, **extra):
        steps.append({"index": len(steps) + 1, "kind": kind, "scene": scene, **extra})

    def lit(kind, scene, since, *, bracket=True, **extra):
        """A step that counts toward bracket_every; an all-off goes in first when due (never among dark steps)."""
        if bracket and since[0] >= method1["bracket_every"]:
            add("all_off", off_scene())
            since[0] = 0
        add(kind, scene, **extra)
        since[0] += 1

    add("all_off", off_scene())
    add("drift", all_on(full, mid), label="start")
    for ratio in method1["confirm_ratios"]:
        add("confirm", one_on(guard.LIGHTS[0], full, mid), ratio=ratio)
    since = [1]  # the drift capture counts
    bright = sorted((u for u in method1["dimming_levels"] if u > method1["dark_level_max"]), reverse=True)
    dark = sorted((u for u in method1["dimming_levels"] if u <= method1["dark_level_max"]), reverse=True)
    for e in guard.LIGHTS:
        lit("basis", one_on(e, full, mid), since, bulb=e, kelvin=mid)
        for u in bright:
            lit("dimming", one_on(e, u, mid), since, bulb=e, level=u)
        lit("basis", one_on(e, full, warm), since, bulb=e, kelvin=warm)
        add("all_off", off_scene())
        since[0] = 0
        for u in dark:
            lit("dimming", one_on(e, u, mid), since, bracket=False, bulb=e, level=u)
        lit("basis", one_on(e, full, cool), since, bulb=e, kelvin=cool)
    add("all_off", off_scene())
    since[0] = 0
    for name, scene in holdout_scenes(method1, snapshot, temps, warmest, rng)[:method1["holdouts"]]:
        lit("holdout", scene, since, label=name)
    lit("drift", all_on(full, mid), since, label="end")
    add("all_off", off_scene())
    check_schedule(steps, method1)
    return {"temperatures": {"warm": warm, "middle": mid, "cool": cool}, "steps": steps}


def check_schedule(steps, method1):
    """The dark-run rule and the bracket spacing; raises ValueError if either fails."""
    longest = int(method1["dark_run_limit_s"] // method1["step_estimate_s"])
    run, since = 0, 0
    for step in steps:
        if step["kind"] == "all_off":
            since = 0
        elif step["kind"] != "confirm":
            since += 1
            if since > method1["bracket_every"]:
                raise ValueError(f"step {step['index']} is more than bracket_every lit steps from an all-off")
        if has_full(step, method1):
            run = 0
        elif is_dark(step, method1):
            run += 1
            if run > longest:
                raise ValueError(f"step {step['index']} makes a dark run longer than {method1['dark_run_limit_s']} s")
        else:
            run = 0
    return True


# ---- the reduction ----------------------------------------------------------------------------

def frame_values(nv12, report, parameters, step):
    """(values, flagged) per sampled pixel: meter-norm/v1 of one frame (value None where flagged)."""
    tg = meter.exposure_ms_gain(report)
    if tg is None:
        raise ValueError("a frame reported no exposure or ISO")
    rs, gs, bs = meter.decode(nv12, step)
    values = [meter.meter_norm_value(r, g, b, tg[0], tg[1], parameters) for r, g, b in zip(rs, gs, bs)]
    return values, [v is None for v in values]


def reduce_captures(captures, method1, owner):
    """readings from captures [{step: {index, kind, scene, ...}, t, frames: [(values, flagged)]}].

    owner: the block of each sampled pixel. The session mask is the union of flagged pixels over
    the fitted and scored captures, fixed before any reading."""
    blocks = max(owner) + 1
    pixels = len(owner)
    mask = [False] * pixels
    for capture in captures:
        if capture["step"]["kind"] in FITTED_KINDS:
            for _, flagged in capture["frames"]:
                mask = [m or f for m, f in zip(mask, flagged)]
    sizes, kept = [0] * blocks, [0] * blocks
    for p, b in enumerate(owner):
        sizes[b] += 1
        kept[b] += not mask[p]
    share = [kept[b] / sizes[b] if sizes[b] else 0.0 for b in range(blocks)]
    steps = []
    for capture in captures:
        per_frame = []
        for values, flagged in capture["frames"]:
            sums, counts = [0.0] * blocks, [0] * blocks
            for p, b in enumerate(owner):
                if not mask[p] and values[p] is not None:
                    sums[b] += values[p]
                    counts[b] += 1
            per_frame.append([sums[b] / counts[b] if counts[b] == kept[b] and kept[b] else None
                              for b in range(blocks)])
        reading, spread = [], []
        for b in range(blocks):
            column = [f[b] for f in per_frame]
            if not column or any(v is None for v in column):
                reading.append(None)
                spread.append(None)
            else:
                reading.append(statistics.median(column))
                spread.append(statistics.pstdev(column) if len(column) > 1 else 0.0)
        steps.append({**capture["step"], "t": capture["t"], "reading": reading, "spread": spread})
    return {"schema": SCHEMAS["readings"], "blocks": blocks, "mask": {"masked_pixels": sum(mask),
            "pixels": pixels, "unmasked_share": share}, "steps": steps}


def noise_and_flags(readings, method1):
    """Each block's all-off noise, and the (step index, block) pairs flagged by disagreeing brackets."""
    blocks = readings["blocks"]
    offs = [s for s in readings["steps"] if s["kind"] == "all_off"]
    noise = []
    for b in range(blocks):
        spreads = [s["spread"][b] for s in offs if s["spread"][b] is not None]
        noise.append(math.sqrt(sum(v * v for v in spreads) / len(spreads)) if spreads else None)
    flagged = set()
    order = [s["index"] for s in readings["steps"]]
    for a, c in zip(offs, offs[1:]):
        between = [i for i in order if a["index"] < i < c["index"]]
        for b in range(blocks):
            va, vc = a["reading"][b], c["reading"][b]
            if va is None or vc is None or noise[b] is None:
                continue
            if abs(va - vc) > method1["bracket_tolerance_multiple"] * max(noise[b], 1e-12):
                flagged.update((i, b) for i in between)
    return noise, flagged


def daylight(readings):
    """D(b, t): the all-off readings, linear in time between brackets, flat outside."""
    offs = sorted((s["t"], s["reading"]) for s in readings["steps"] if s["kind"] == "all_off")
    if not offs:
        raise ValueError("no all-off capture")

    def at(b, t):
        points = [(tt, r[b]) for tt, r in offs if r[b] is not None]
        if not points:
            return None
        if t <= points[0][0]:
            return points[0][1]
        for (t0, v0), (t1, v1) in zip(points, points[1:]):
            if t <= t1:
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0) if t1 > t0 else v1
        return points[-1][1]
    return at


# ---- the fit ---------------------------------------------------------------------------------------

def pav(values):
    blocks = []
    for v in values:
        blocks.append([v, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, n = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += n
    out = []
    for s, n in blocks:
        out.extend([s / n] * n)
    return out


def fit(readings, temps, method1):
    """The light model from readings: bases, dimming tables, the shared-curve result, exclusions."""
    blocks = readings["blocks"]
    full = method1["full_level"]
    warm, mid, cool = temps["warm"], temps["middle"], temps["cool"]
    noise, flagged = noise_and_flags(readings, method1)
    day = daylight(readings)

    def signal(step, b):
        v = step["reading"][b]
        d = day(b, step["t"])
        if v is None or d is None or (step["index"], b) in flagged:
            return None
        return v - d

    share = readings["mask"]["unmasked_share"]
    excluded = {b: "mask" for b in range(blocks) if share[b] < method1["min_unmasked_share"]}
    basis = {e: {} for e in guard.LIGHTS}
    for step in readings["steps"]:
        if step["kind"] == "basis":
            basis[step["bulb"]][step["kelvin"]] = [signal(step, b) for b in range(blocks)]
    for e in guard.LIGHTS:
        if set(basis[e]) != {warm, mid, cool}:
            raise ValueError(f"{e} is missing a basis capture")
        for k, values in basis[e].items():
            for b, v in enumerate(values):
                if v is None and b not in excluded:
                    excluded[b] = "basis"
    dimming = {}
    for e in guard.LIGHTS:
        ref = basis[e][mid]
        usable = [b for b in range(blocks) if b not in excluded and ref[b] is not None]
        top = max((ref[b] for b in usable), default=0.0)
        rich = [b for b in usable if ref[b] >= method1["signal_rich_fraction"] * top and ref[b] > 0]
        table = {0: 0.0, full: 1.0}
        for step in readings["steps"]:
            if step["kind"] == "dimming" and step["bulb"] == e:
                ratios = [signal(step, b) / ref[b] for b in rich if signal(step, b) is not None]
                if ratios:
                    table[step["level"]] = statistics.median(ratios)
        levels = sorted(table)
        values = pav([table[u] for u in levels])
        values = [min(1.0, max(0.0, v)) for v in values]
        values[0], values[-1] = 0.0, 1.0
        dimming[e] = {"levels": levels, "values": values}
    shared = {}
    for a, c in guard.PAIRS:
        ta, tc = dimming[a], dimming[c]
        same = ta["levels"] == tc["levels"] and all(
            abs(x - y) <= method1["shared_curve_percent"] / 100 * (x + y) / 2
            for u, x, y in zip(ta["levels"], ta["values"], tc["values"]) if u >= method1["shared_curve_from_level"])
        shared["+".join((a, c))] = same
        if same:
            mean = pav([(x + y) / 2 for x, y in zip(ta["values"], tc["values"])])
            dimming[a] = dimming[c] = {"levels": list(ta["levels"]), "values": mean}
    return {"blocks": blocks, "temperatures": {"warm": warm, "middle": mid, "cool": cool},
            "basis": {e: {str(k): v for k, v in basis[e].items()} for e in guard.LIGHTS},
            "dimming": dimming, "shared_curve": shared, "excluded": excluded, "noise": noise,
            "flagged": sorted(flagged), "full_level": full}


def internal_predict(scene, fitted):
    """Ê per block for an HA scene {entity: {on, brightness, kelvin}}: predict.py's arithmetic on a fit."""
    channels = {e: {"temperatures": [int(k) for k in fitted["basis"][e]],
                    "basis": {int(k): v for k, v in fitted["basis"][e].items()},
                    "levels": fitted["dimming"][e]["levels"], "values": fitted["dimming"][e]["values"]}
                for e in guard.LIGHTS}
    settings = {e: {"u": min(fitted["full_level"], s["brightness"]), "kelvin": s["kelvin"]}
                for e, s in scene.items() if s.get("on") and s.get("brightness")}
    return predict_module.electric(settings, channels, fitted["blocks"])


# ---- the score -------------------------------------------------------------------------------------

def percentile(values, q):
    """Linear interpolation between closest ranks, as den-take's CHECK-V1 (numpy's default)."""
    ordered = sorted(values)
    if not ordered:
        return None
    pos = (len(ordered) - 1) * q / 100
    lo = math.floor(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def score(readings, fitted, method1):
    """score.json's body: the pooled median and p90 over every hold-out pair, and the verdict."""
    day = daylight(readings)
    noise, flagged = fitted["noise"], set(map(tuple, fitted["flagged"]))
    temps = fitted["temperatures"]
    errors, low, by_band, per_step = [], 0, {"warm": [], "middle": [], "cool": []}, []
    for step in readings["steps"]:
        if step["kind"] != "holdout":
            continue
        predicted = internal_predict(step["scene"], fitted)
        step_errors, snr = [], []
        for b in range(fitted["blocks"]):
            if b in fitted["excluded"] or (step["index"], b) in flagged:
                continue
            v, d = step["reading"][b], day(b, step["t"])
            if v is None or d is None or noise[b] is None:
                continue
            e = v - d
            if e < method1["noise_floor_multiple"] * noise[b] or e <= 0:
                low += 1
                continue
            step_errors.append(abs(predicted[b] - e) / e)
            snr.append(e / noise[b] if noise[b] > 0 else float("inf"))
        errors += step_errors
        on = [s["kelvin"] for s in step["scene"].values() if s["on"]]
        m = statistics.mean(mired(k) for k in on) if on else None
        third = (mired(temps["warm"]) - mired(temps["cool"])) / 3
        band = None if m is None else ("cool" if m < mired(temps["cool"]) + third else
                                       "warm" if m > mired(temps["warm"]) - third else "middle")
        if band:
            by_band[band] += step_errors
        per_step.append({"index": step["index"], "label": step.get("label"), "pairs": len(step_errors),
                         "median_error": statistics.median(step_errors) if step_errors else None,
                         "median_snr": statistics.median(snr) if snr else None})
    median, p90 = percentile(errors, 50), percentile(errors, 90)
    passes = (median is not None and median * 100 <= method1["gate_median_percent"]
              and p90 * 100 <= method1["gate_p90_percent"])
    excluded_mask = sum(1 for why in fitted["excluded"].values() if why == "mask")
    excluded_basis = sum(1 for why in fitted["excluded"].values() if why == "basis")
    mid = fitted["temperatures"]["middle"]
    return {"error": {"median": median, "p90": p90, "count": len(errors), "excluded_count": low,
                      "verdict": "pass" if passes else "fail",
                      "gate": {"median_max": method1["gate_median_percent"] / 100,
                               "p90_max": method1["gate_p90_percent"] / 100}},
            "blocks_excluded": {"mask_minimum": excluded_mask, "missing_basis": excluded_basis},
            "absolute": "not run, waits on photometry-commissioning",
            "camera_scale": ("the basis and the scores come from the same camera, so its scale and the surfaces' "
                             "reflectance cancel; the score cannot detect a camera-scale error"),
            "day_to_day_stability": ("not tested at rung 8 (one session); left to rung 10 Verify and K21 "
                                     "drift checks"),
            "information_only": {
                "error_by_temperature_band": {k: (statistics.median(v) if v else None) for k, v in by_band.items()},
                "steps": per_step, "shared_curve": fitted["shared_curve"],
                "middle_temperature_error": middle_error(fitted, mid),
                "drift": drift(readings, fitted),
                "response_confirmation": confirmation(readings)}}


def middle_error(fitted, mid):
    """Predicting the middle-temperature basis from the two ends: A9's linear mixing, tested."""
    warm, cool = fitted["temperatures"]["warm"], fitted["temperatures"]["cool"]
    w = (mired(warm) - mired(mid)) / (mired(warm) - mired(cool))
    out = []
    for e in guard.LIGHTS:
        bw, bm, bc = (fitted["basis"][e][str(k)] for k in (warm, mid, cool))
        for b in range(fitted["blocks"]):
            if b in fitted["excluded"] or None in (bw[b], bm[b], bc[b]) or bm[b] <= 0:
                continue
            out.append(abs(bw[b] + (bc[b] - bw[b]) * w - bm[b]) / bm[b])
    return statistics.median(out) if out else None


def drift(readings, fitted):
    ends = [s for s in readings["steps"] if s["kind"] == "drift"]
    if len(ends) < 2:
        return None
    ratios = [b2 / b1 for b1, b2 in zip(ends[0]["reading"], ends[-1]["reading"])
              if b1 is not None and b2 is not None and b1 > 0]
    return statistics.median(ratios) - 1 if ratios else None


def confirmation(readings):
    """The confirm captures' medians relative to the held one: near 1 when the Stage 0 curve holds."""
    caps = [s for s in readings["steps"] if s["kind"] == "confirm"]
    ref = next((s for s in caps if s.get("ratio") == 1), None)
    if ref is None:
        return None
    out = {}
    for s in caps:
        ratios = [a / r for a, r in zip(s["reading"], ref["reading"]) if a is not None and r is not None and r > 0]
        out[str(s["ratio"])] = statistics.median(ratios) if ratios else None
    return out


# ---- a session folder: reduce, fit, score, write -------------------------------------------

def read_jsonl(path):
    return [json.loads(raw) for raw in Path(path).read_text(encoding="utf-8").splitlines() if raw.strip()]


def session_captures(folder, parameters, method1):
    """The captures of a finished session, decoded to per-pixel meter-norm/v1 values."""
    lines = read_jsonl(folder / "probes.jsonl")
    steps = {l["index"]: l for l in lines if l.get("kind") == "step"}
    captures = []
    for line in lines:
        if line.get("kind") != "capture" or line["step"] not in steps:
            continue
        step = steps[line["step"]]
        frames = []
        for f in line["frames"]:
            nv12 = (folder / f["file"]).read_bytes()
            if meter.sha256_bytes(nv12) != f["sha256"]:
                raise Refused(f"{f['file']} does not hash what probes.jsonl recorded")
            frames.append(frame_values(nv12, f["report"], parameters, int(method1["reduce_pixel_step"])))
        info = {"index": step["index"], "kind": step["step_kind"], "scene": step["scene"]}
        for key in ("label", "ratio", "bulb", "level", "kelvin"):
            if step.get(key) is not None:
                info[key] = step[key]
        captures.append({"step": info, "t": step["sent_pc"], "frames": frames})
    return captures


def channel_key(fixture, channel):
    return f"{fixture['fixture_id']}.{channel['channel_id']}"


def point_id(b, cols):
    return f"r{b // cols:02d}c{b % cols:02d}"


ANCHOR_OPTIONS = {
    "decision": "photometry-commissioning", "status": "pending", "chosen": None,
    "options": {"card-and-meter": "k_L from a grey card and a meter reading; basis in lx under an anchored room version",
                "meter-only": "per-surface meter factors; no reflectance for bounce or unmetered surfaces",
                "other-sensor": "a separately approved sensor"},
    "until_chosen": "basis_unit stays relative; no absolute lux claim"}


def light_model(fitted, fixtures, method1, *, session_id, readings_sha256, meter_norm_sha256, score_error):
    """light-model.json: the live-v1 light_model record's fields, names unchanged, plus the plan's extras."""
    g = grid(method1)
    rects = block_rects(method1)
    points = [point_id(b, g["cols"]) for b in range(g["blocks"])]
    by_entity = {c["entity_id"]: (f, c) for f in fixtures for c in f["channels"]}
    channels, basis, cct, dimming = [], {}, {}, {}
    temps = [fitted["temperatures"][k] for k in ("warm", "middle", "cool")]
    pair_of = {a: c for a, c in guard.PAIRS} | {c: a for a, c in guard.PAIRS}
    for e in guard.LIGHTS:
        f, c = by_entity[e]
        key = channel_key(f, c)
        channels.append({"channel_key": key, "fixture_id": f["fixture_id"], "channel_id": c["channel_id"],
                         "entity_id": e, "kind": c["kind"], "cct_range_k": c["cct_range_k"]})
        keys = []
        for k in temps:
            bk = f"{key}@{k}K"
            keys.append(bk)
            row = fitted["basis"][e][str(k)]
            basis[bk] = {pid: (None if b in fitted["excluded"] or row[b] is None else round(row[b], 9))
                         for b, pid in enumerate(points)}
        cct[key] = {"kelvin": temps, "basis_keys": keys, "entity_id": e,
                    "interpolation": "linear in mired between the captured temperatures, clamped outside them"}
        partner = pair_of[e]
        shared = fitted["shared_curve"]["+".join(sorted((e, partner), key=guard.LIGHTS.index))]
        dimming[key] = {"levels": fitted["dimming"][e]["levels"],
                        "g": [round(v, 9) for v in fitted["dimming"][e]["values"]],
                        "shared_with": channel_key(*by_entity[partner]) if shared else None,
                        "command_unit": f"Hue brightness 0 to {method1['full_level']}",
                        "monotone": "pool-adjacent-violators"}
    room_version = f"den-oak-view/blocks{g['block_px']}@{method1.block_sha256[:12]}"
    return {"record": "light_model", "contract": "live-v1",
            "version": f"lm-den-{session_id}", "room_version": room_version,
            "room_version_note": ("no RoomModel version exists yet; the camera's view and METHOD-V1's block grid "
                                  "stand in for it, named by the Stage 1 block's sha256"),
            "room_view": {"camera_id": "den-oak", "width": 640, "height": 360, "grid": g,
                          "method_stage1_block_sha256": method1.block_sha256},
            "meter_norm": {"version": meter.METER_NORM_VERSION, "parameters_sha256": meter_norm_sha256},
            "sample_points": [{"point_id": pid, "kind": None, "xyz": None, "normal": None, "rect": rects[b],
                               "excluded": fitted["excluded"].get(b)} for b, pid in enumerate(points)],
            "channels": channels, "basis": basis, "basis_unit": "relative", "spot_model": None,
            "dimming": dimming, "shared_curve": fitted["shared_curve"], "cct": cct, "bounce": "measured",
            "glare": None, "glare_note": "not predicted at rung 8: no eye positions, and comfort-policy is pending",
            "error": {"median_pct": None if score_error["median"] is None else round(100 * score_error["median"], 4),
                      "p90_pct": None if score_error["p90"] is None else round(100 * score_error["p90"], 4),
                      "count": score_error["count"], "excluded_count": score_error["excluded_count"],
                      "verdict": score_error["verdict"], "scored_at": "camera blocks (v1 points have no kind)"},
            "anchor": ANCHOR_OPTIONS,
            "sessions": [{"session_id": session_id, "readings_sha256": readings_sha256}],
            "method_sha256": method1.file_sha256, "method_stage1_block_sha256": method1.block_sha256}


def write_new(path, value):
    with open(path, "x", encoding="utf-8", newline="\n") as out:
        json.dump(value, out, indent=1, sort_keys=True, allow_nan=False)
        out.write("\n")


def run_session(folder, meter_norm, method1, *, scored=True):
    """Reduce, fit, score and write readings.json, light-model.json and score.json for one session."""
    folder = Path(folder)
    if scored:
        method1.refuse_if_open("A scored light model")
    record = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    if record.get("outcome") != "complete":
        raise Refused(f"the session's outcome is {record.get('outcome')}: v1 fits and scores only complete sessions")
    for name in ("readings.json", "light-model.json", "score.json"):
        if (folder / name).exists():
            raise Refused(f"{name} already exists")
    recorded = (record.get("stage0") or {}).get("meter_norm_sha256")
    if recorded and recorded != meter_norm["parameters_sha256"]:
        raise Refused("--stage0 is not the Stage 0 sweep this session recorded")
    fixtures = read_jsonl(folder / "fixtures.jsonl")
    captures = session_captures(folder, meter_norm["parameters"], method1)
    readings = reduce_captures(captures, method1, pixel_blocks(method1))
    readings.update(session=record["id"], meter_norm_sha256=meter_norm["parameters_sha256"],
                    method_sha256=method1.file_sha256, method_stage1_block_sha256=method1.block_sha256,
                    temperatures=record["temperatures"], grid=grid(method1))
    write_new(folder / "readings.json", readings)
    return fit_and_score(folder, readings, fixtures, method1, meter_norm["parameters_sha256"])


def fit_and_score(folder, readings, fixtures, method1, meter_norm_sha256):
    """From readings.json alone (written or in memory): the fit, light-model.json and score.json."""
    folder = Path(folder)
    readings_sha256 = hashlib.sha256((folder / "readings.json").read_bytes()).hexdigest()
    fitted = fit(readings, readings["temperatures"], method1)
    scored = score(readings, fitted, method1)
    lm = light_model(fitted, fixtures, method1, session_id=readings["session"], readings_sha256=readings_sha256,
                     meter_norm_sha256=meter_norm_sha256, score_error=scored["error"])
    scored.update(schema=SCHEMAS["score"], session=readings["session"], light_model_version=lm["version"],
                  readings_sha256=readings_sha256, method_sha256=method1.file_sha256,
                  method_stage1_block_sha256=method1.block_sha256, written_pc_unix=time.time())
    write_new(folder / "light-model.json", lm)
    write_new(folder / "score.json", scored)
    return lm, scored


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", required=True, metavar="SESSION_FOLDER")
    parser.add_argument("--stage0", required=True, metavar="STAGE0_SWEEP_FOLDER")
    args = parser.parse_args(argv)
    import probe  # the Stage 0 reader lives with the session tools
    try:
        meter_norm, _ = probe.stage0_meter_norm(args.stage0)
        lm, scored = run_session(Path(args.session), meter_norm, load_stage1())
    except (Refused, probe.sweep.Refused, OSError, ValueError) as exc:
        print(f"Refused: {exc}. Nothing was written.", file=sys.stderr)
        return 2
    e = scored["error"]
    print(f"{e['verdict'].upper()}: median {100 * (e['median'] or 0):.1f}%, p90 {100 * (e['p90'] or 0):.1f}% "
          f"over {e['count']} pairs ({e['excluded_count']} below the noise floor).")
    print(f"Wrote readings.json, light-model.json ({lm['version']}) and score.json in {args.session}")
    return 0 if e["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
