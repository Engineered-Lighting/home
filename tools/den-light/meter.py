#!/usr/bin/env python3
"""Stage 0's pure part for rung 8. Standard library only; no network.

Reads bundles (zipfile and a small .npy header parser: no numpy, no pickle,
uint8 and uint16 only), decodes NV12 as BT.601 limited range, applies
meter-norm/v1, fits the inverse response per channel and runs the global
check. Every number comes from METHOD-V1's `method-v1 stage0` block.
"""
import bisect
import hashlib
import io
import json
import math
import re
import struct
import zipfile
import zlib
from array import array
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
METHOD_FILE = "METHOD-V1.md"
METHOD_INFO = "method-v1 stage0"
METER_NORM_VERSION = "meter-norm/v1"
METHOD_KEYS = (
    "white_balance_kelvin", "clip_limit_r", "clip_limit_g", "clip_limit_b", "normalise_code",
    "weight_r", "weight_g", "weight_b",
    "pixel_step", "grid_cols", "grid_rows",
    "noise_tolerance_codes",
    "countdown_s", "still_span_s", "still_wait_limit_s", "run_limit_s", "hold_ttl_s",
    "renew_every_s", "poll_s", "settle_limit_s", "frames_per_step",
    "step_ratio_low", "step_ratio_high", "step_count", "max_exposure_us", "iso_max",
    "exposure_quantum_us",
    "fit_iterations", "fit_smoothing", "residual_max_codes", "region_code_low", "region_code_high",
    "region_tolerance_percent", "region_min_code_samples")
INTEGER_KEYS = {"white_balance_kelvin", "clip_limit_r", "clip_limit_g", "clip_limit_b",
                "normalise_code", "pixel_step", "grid_cols", "grid_rows", "frames_per_step",
                "step_count", "max_exposure_us", "iso_max", "fit_iterations",
                "region_code_low", "region_code_high", "region_min_code_samples"}
WIDTH, HEIGHT = 640, 360
NV12_BYTES = WIDTH * HEIGHT * 3 // 2
NV12_SHAPE = (540, 640)
CHANNELS = ("r", "g", "b")
NPY_ITEMSIZE = {"|u1": 1, "<u2": 2}
NPY_HEADER = re.compile(r"\{'descr': *'([^']*)', *'fortran_order': *(True|False), *"
                        r"'shape': *\(([0-9, ]*)\),? *\}")
FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```", re.M | re.S)


class BundleError(ValueError):
    pass


class Degenerate(ValueError):
    """The fit has no scale: f(normalise_code) is not above 0."""


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    return sha256_bytes(Path(path).read_bytes())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


# ---- METHOD-V1 --------------------------------------------------------------

@dataclass(frozen=True)
class Method:
    values: dict
    open: tuple = ()
    file_sha256: str = ""
    block_sha256: str = ""

    def __getitem__(self, name):
        return self.values[name]

    def replace(self, **changes):
        """A copy with some numbers changed (tests use it); open marks are kept."""
        unknown = set(changes) - set(self.values)
        if unknown:
            raise KeyError(sorted(unknown))
        return Method({**self.values, **changes}, self.open, self.file_sha256, self.block_sha256)


def parse_method(text):
    """The numbers and open names of the one `method-v1 stage0` block in text."""
    blocks = [body for info, body in FENCE.findall(text) if info.strip() == METHOD_INFO]
    if len(blocks) != 1:
        raise ValueError(f"the method file must hold exactly one `{METHOD_INFO}` block")
    values, marked = {}, []
    for line in blocks[0].splitlines():
        content, _, comment = line.partition("#")
        content = content.strip()
        if not content:
            continue
        name, sep, raw = (part.strip() for part in content.partition("="))
        try:
            value = json.loads(raw) if sep else None
        except ValueError:
            value = None
        if (not sep or name in values or type(value) not in (int, float) or not math.isfinite(value)
                or (name in INTEGER_KEYS and type(value) is not int)):
            raise ValueError(f"bad method line: {line.strip()!r}")
        values[name] = value
        if comment.strip().lower().startswith("open"):
            marked.append(name)
    if set(values) != set(METHOD_KEYS):
        missing, extra = sorted(set(METHOD_KEYS) - set(values)), sorted(set(values) - set(METHOD_KEYS))
        raise ValueError(f"method keys differ from meter.py's list (missing {missing}, extra {extra})")
    return values, tuple(marked), blocks[0]


def load_method(path=None):
    path = Path(path or HERE / METHOD_FILE)
    raw = path.read_bytes()
    values, marked, block = parse_method(raw.decode("utf-8"))
    return Method(values, marked, sha256_bytes(raw), sha256_bytes(block.encode("utf-8")))


# ---- bundles ----------------------------------------------------------------

def _npy(raw, name):
    if raw[:6] != b"\x93NUMPY" or len(raw) < 12 or raw[6] not in (1, 2, 3):
        raise BundleError(f"{name}: not a .npy member")
    size, start = (struct.unpack("<H", raw[8:10])[0], 10) if raw[6] == 1 else (
        struct.unpack("<I", raw[8:12])[0], 12)
    match = NPY_HEADER.fullmatch(raw[start:start + size].decode("latin-1").strip())
    if not match:
        raise BundleError(f"{name}: unreadable .npy header")
    descr, fortran, shape_text = match.groups()
    if descr not in NPY_ITEMSIZE or fortran != "False":
        raise BundleError(f"{name}: dtype {descr} refused (only uint8 and uint16, C order)")
    shape = tuple(int(part) for part in shape_text.replace(" ", "").split(",") if part)
    body = raw[start + size:]
    if len(body) != math.prod(shape) * NPY_ITEMSIZE[descr]:
        raise BundleError(f"{name}: size does not match its header")
    return descr, shape, body


def parse_metadata(raw):
    def refuse_constant(_):
        raise BundleError("metadata holds a non-finite number")
    try:
        meta = json.loads(raw.decode("utf-8"), parse_constant=refuse_constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BundleError("metadata is not JSON") from None
    if type(meta) is not dict or type(meta.get("schema")) is not int or meta["schema"] != 1:
        raise BundleError("metadata schema must be 1")
    return meta


def read_bundle(data):
    """(metadata, rgb_nv12 bytes) from one /frame.npz bundle."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            def member(name, limit):
                if archive.getinfo(name).file_size > limit:
                    raise BundleError(f"{name} is too large")
                return _npy(archive.read(name), name)
            descr, shape, body = member("metadata_json.npy", 1 << 20)
            if descr != "|u1" or len(shape) != 1:
                raise BundleError("metadata_json must be 1-D uint8")
            meta = parse_metadata(body)
            descr, shape, nv12 = member("rgb_nv12.npy", 1 << 21)
            if descr != "|u1" or shape != NV12_SHAPE:
                raise BundleError("rgb_nv12 must be 540x640 uint8 (640x360 NV12)")
    except (zipfile.BadZipFile, KeyError, EOFError, zlib.error) as exc:
        raise BundleError(f"bad bundle archive: {type(exc).__name__}") from None
    return meta, nv12


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _int_or_none(value):
    return value if type(value) is int else None


def rgb_report(meta):
    """What Stage 0 reads from a bundle's RGB frame; unknown values stay None."""
    frames = meta.get("frames")
    rgb = frames.get("rgb") if type(frames) is dict else None
    session = meta.get("session")
    if type(rgb) is not dict or type(session) is not str or not session:
        raise BundleError("metadata needs a session and an rgb frame")
    sequence, t_host = rgb.get("sequence"), rgb.get("host_monotonic_timestamp_seconds")
    if type(sequence) is not int or sequence < 0 or not _finite(t_host):
        raise BundleError("rgb sequence or timestamp missing")
    exposure = rgb.get("exposure_duration_seconds")
    meter = rgb.get("meter")
    held = generation = None
    if type(meter) is dict:
        held = meter.get("held") if type(meter.get("held")) is bool else None
        generation = _int_or_none(meter.get("generation"))
    return {"session": session, "sequence": sequence, "t_host": float(t_host),
            "exposure_us": exposure * 1e6 if _finite(exposure) and exposure > 0 else None,
            "iso": _int_or_none(rgb.get("sensitivity_iso")),
            "kelvin": _int_or_none(rgb.get("color_temperature_kelvin")),
            "lens_position": _int_or_none(rgb.get("lens_position")),
            "meter_keys": all(key in rgb for key in (
                "sensitivity_iso", "color_temperature_kelvin", "lens_position", "meter")),
            "held": held, "generation": generation}


# ---- pixels -------------------------------------------------------------------

def sample_points(step):
    return [(x, y) for y in range(0, HEIGHT, step) for x in range(0, WIDTH, step)]


def sample_regions(step, cols, rows):
    return [(y * rows // HEIGHT) * cols + (x * cols // WIDTH) for x, y in sample_points(step)]


def decode(nv12, step):
    """BT.601 limited-range codes (R, G, B lists) of the sampled pixels, as the server's JPEG."""
    if len(nv12) != NV12_BYTES:
        raise BundleError("NV12 plane must be 640x360")
    rs, gs, bs = [], [], []
    plane = WIDTH * HEIGHT
    for y in range(0, HEIGHT, step):
        row, chroma = y * WIDTH, plane + (y // 2) * WIDTH
        for x in range(0, WIDTH, step):
            yy = 298 * (nv12[row + x] - 16)
            at = chroma + (x // 2) * 2
            u, v = nv12[at] - 128, nv12[at + 1] - 128
            rs.append(min(255, max(0, (yy + 409 * v + 128) >> 8)))
            gs.append(min(255, max(0, (yy - 100 * u - 208 * v + 128) >> 8)))
            bs.append(min(255, max(0, (yy + 516 * u + 128) >> 8)))
    return rs, gs, bs


def region_means(rgb, regions, count):
    sums = [[0, 0, 0] for _ in range(count)]
    sizes = [0] * count
    for k, region in enumerate(regions):
        s = sums[region]
        s[0] += rgb[0][k]
        s[1] += rgb[1][k]
        s[2] += rgb[2][k]
        sizes[region] += 1
    return [tuple(value / sizes[i] for value in sums[i]) if sizes[i] else (0.0, 0.0, 0.0)
            for i in range(count)]


def agree(means_a, means_b, tolerance):
    """Two frames agree when every region mean is within tolerance in every channel."""
    return all(abs(a - b) <= tolerance for ra, rb in zip(means_a, means_b) for a, b in zip(ra, rb))


def clip_limits(method):
    return (method["clip_limit_r"], method["clip_limit_g"], method["clip_limit_b"])


def flags(rgb, limits):
    """True where meter-norm/v1 flags the pixel: any channel at or above its limit."""
    lr, lg, lb = limits
    return [r >= lr or g >= lg or b >= lb for r, g, b in zip(*rgb)]


def exposure_ms_gain(report):
    """(t in ms, G = ISO/100) from a frame's own report, or None."""
    if report.get("exposure_us") is None or report.get("iso") is None:
        return None
    return report["exposure_us"] / 1000.0, report["iso"] / 100.0


# ---- meter-norm/v1 ------------------------------------------------------------

def meter_norm_value(r, g, b, exposure_ms, gain, parameters):
    """One pixel's meter-norm/v1 value, or None when the pixel is flagged."""
    limits = parameters["clip_limits"]
    if r >= limits[0] or g >= limits[1] or b >= limits[2]:
        return None
    fr, fg, fb = (parameters["curves"][c] for c in CHANNELS)
    wr, wg, wb = parameters["weights"]
    return (wr * fr[r] + wg * fg[g] + wb * fb[b]) / (exposure_ms * gain)


def meter_norm_parameters(method, curves):
    """meter-norm/v1's parameters and the sha256 of their canonical JSON."""
    parameters = {"version": METER_NORM_VERSION,
                  "white_balance_kelvin": method["white_balance_kelvin"],
                  "clip_limits": list(clip_limits(method)),
                  "weights": [method["weight_r"], method["weight_g"], method["weight_b"]],
                  "normalise_code": method["normalise_code"],
                  "decode": "nv12_bt601_limited_range_server_integer",
                  "curves": {c: [round(v, 9) for v in curves[c]] for c in CHANNELS}}
    return parameters, sha256_bytes(canonical(parameters).encode())


# ---- the response fit -----------------------------------------------------------

def pav(values, weights):
    """Weighted pool-adjacent-violators: the closest non-decreasing sequence."""
    blocks = []
    for v, w in zip(values, weights):
        blocks.append([v * w, w, 1])
        while len(blocks) > 1 and blocks[-2][0] * blocks[-1][1] > blocks[-1][0] * blocks[-2][1]:
            s, w2, n = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += w2
            blocks[-1][2] += n
    out = []
    for s, w, n in blocks:
        out.extend([s / w] * n)
    return out


def smooth(means, weights, penalty):
    """argmin Σ w·(f − m)² + penalty·Σ (f[z−1] − 2f[z] + f[z+1])² (a Whittaker smoother).

    The normal equations are pentadiagonal; banded elimination solves them. Codes
    with weight 0 are filled by the penalty: interpolated inside, linear outside.
    """
    n = len(means)
    band = [[0.0] * 5 for _ in range(n)]  # band[i][k] is entry (i, i + k − 2)
    rhs = [w * m for w, m in zip(weights, means)]
    for i, w in enumerate(weights):
        band[i][2] += w
    for j in range(1, n - 1):
        rows = (j - 1, j, j + 1)
        for a, ca in zip(rows, (1.0, -2.0, 1.0)):
            for c, cc in zip(rows, (1.0, -2.0, 1.0)):
                band[a][c - a + 2] += penalty * ca * cc
    for i in range(n):
        pivot = band[i][2]
        if not pivot > 0:
            raise Degenerate("the smoothing system is singular")
        for r in range(i + 1, min(n, i + 3)):
            factor = band[r][i - r + 2] / pivot
            if factor:
                for c in range(i, min(n, i + 3)):
                    band[r][c - r + 2] -= factor * band[i][c - i + 2]
                rhs[r] -= factor * rhs[i]
    out = [0.0] * n
    for i in range(n - 1, -1, -1):
        total = rhs[i]
        for c in range(i + 1, min(n, i + 3)):
            total -= band[i][c - i + 2] * out[c]
        out[i] = total / band[i][2]
    return out


def finish_curve(sums, counts, normalise_code, smoothing):
    """Knot means, smoothed, made monotone, kept at or above 0, scaled to f(normalise_code) = 1."""
    known = [z for z in range(256) if counts[z]]
    if len(known) < 2:
        raise Degenerate("fewer than two codes have samples")
    penalty = smoothing * sum(counts[z] for z in known) / len(known)
    means = [sums[z] / counts[z] if counts[z] else 0.0 for z in range(256)]
    curve = smooth(means, [float(c) for c in counts], penalty) if penalty > 0 else None
    if curve is None:  # no smoothing: fill empty codes by linear interpolation
        curve, values = list(means), [means[z] for z in known]
        for z in range(256):
            if not counts[z]:
                k = bisect.bisect_left(known, z)
                if k == 0 or k == len(known):
                    curve[z] = values[0 if k == 0 else -1]
                else:
                    z0, z1 = known[k - 1], known[k]
                    curve[z] = values[k - 1] + (values[k] - values[k - 1]) * (z - z0) / (z1 - z0)
    curve = [max(0.0, v) for v in pav(curve, [counts[z] or 1e-9 for z in range(256)])]
    scale = curve[normalise_code]
    if not scale > 0:
        raise Degenerate(f"f({normalise_code}) is not above 0")
    return [v / scale for v in curve]


def inverse(curve, y):
    """F(y): the code where the curve reaches y, interpolated; 0 and 255 at the ends."""
    if y <= curve[0]:
        return 0.0
    if y >= curve[255]:
        return 255.0
    k = bisect.bisect_left(curve, y)
    lo, hi = curve[k - 1], curve[k]
    return k - 1 + (y - lo) / (hi - lo)


@dataclass
class Samples:
    """Unflagged samples: shared pixel and X arrays, one code array per channel."""
    pixels: int
    pix: array = field(default_factory=lambda: array("i"))
    xs: array = field(default_factory=lambda: array("d"))
    zs: tuple = field(default_factory=lambda: (array("B"), array("B"), array("B")))
    flagged: int = 0

    def add_frame(self, rgb, x, limits):
        lr, lg, lb = limits
        zr, zg, zb = self.zs
        for k, (r, g, b) in enumerate(zip(*rgb)):
            if r >= lr or g >= lg or b >= lb:
                self.flagged += 1
                continue
            self.pix.append(k)
            self.xs.append(x)
            zr.append(r)
            zg.append(g)
            zb.append(b)

    def split(self, owner, count):
        """One Samples per region, by each pixel's region in owner."""
        parts = [Samples(self.pixels) for _ in range(count)]
        zr, zg, zb = self.zs
        for k, p in enumerate(self.pix):
            part = parts[owner[p]]
            part.pix.append(p)
            part.xs.append(self.xs[k])
            part.zs[0].append(zr[k])
            part.zs[1].append(zg[k])
            part.zs[2].append(zb[k])
        return parts

    def __len__(self):
        return len(self.pix)


HAT = [min(z, 255 - z) for z in range(256)]  # the E-step's weight per code


def irradiances(curve, samples, channel):
    """E_i = Σ w·f(z)·X / Σ w·X² per pixel, w the hat weight of the sample's code."""
    num, den = [0.0] * samples.pixels, [0.0] * samples.pixels
    hat = HAT
    for p, x, z in zip(samples.pix, samples.xs, samples.zs[channel]):
        w = hat[z] * x
        num[p] += w * curve[z]
        den[p] += w * x
    return [a / b if b else 0.0 for a, b in zip(num, den)]


@dataclass
class Fit:
    curve: list
    irradiance: list
    counts: list


def fit_response(samples, channel, method):
    """Alternating least squares on a per-code piecewise-linear curve (METHOD-V1, The response fit)."""
    zs = samples.zs[channel]
    if not len(zs):
        raise Degenerate("no samples")
    normalise_code, smoothing = method["normalise_code"], method["fit_smoothing"]
    counts = [0] * 256
    for z in zs:
        counts[z] += 1
    curve = [z / normalise_code for z in range(256)]
    for _ in range(method["fit_iterations"]):
        energy = irradiances(curve, samples, channel)
        sums = [0.0] * 256
        for p, x, z in zip(samples.pix, samples.xs, zs):
            sums[z] += x * energy[p]
        curve = finish_curve(sums, counts, normalise_code, smoothing)
    return Fit(curve, irradiances(curve, samples, channel), counts)


def residual_rms(fit, samples, channel):
    """RMS of F(X·E) − z over the samples, in code values."""
    curve, energy = fit.curve, fit.irradiance
    total, n = 0.0, 0
    for p, x, z in zip(samples.pix, samples.xs, samples.zs[channel]):
        d = inverse(curve, x * energy[p]) - z
        total += d * d
        n += 1
    return math.sqrt(total / n)


def compare_region(region_fit, global_curve, method):
    """Scale a region curve to the global one (least squares on the ratio) and measure it.

    Returns (supported codes, scale, largest |s·q − 1|), or None when untested.
    """
    low, high, need = method["region_code_low"], method["region_code_high"], method["region_min_code_samples"]
    ratios = [region_fit.curve[z] / global_curve[z] for z in range(low, high + 1)
              if region_fit.counts[z] >= need and global_curve[z] > 0]
    square = sum(q * q for q in ratios)
    if not ratios or not square > 0:
        return None
    scale = sum(ratios) / square
    return len(ratios), scale, max(abs(scale * q - 1) for q in ratios)


# ---- gain agreement ---------------------------------------------------------------

def gain_agreement(report, hold, quantum_us):
    """(passes, reason) for one held frame against its step's hold."""
    if report.get("exposure_us") is None:
        return False, "no exposure reported"
    if report.get("iso") is None:
        return False, "no ISO reported"
    if report.get("kelvin") is None:
        return False, "no white balance reported"
    if abs(report["exposure_us"] - hold["exposure_us"]) > quantum_us + 1e-6:
        return False, "exposure off the hold"
    if report["iso"] != hold["iso"]:
        return False, "ISO off the hold"
    if report["kelvin"] != hold["white_balance_kelvin"]:
        return False, "white balance off the hold"
    return True, None


# ---- the global check ---------------------------------------------------------------

def collect(steps, method):
    """Samples from the kept frames of every step but the repeat (the last step)."""
    step_px = method["pixel_step"]
    limits = clip_limits(method)
    samples = Samples(len(sample_points(step_px)))
    frames = 0
    for step in steps[:-1]:
        for frame in step["frames"]:
            tg = exposure_ms_gain(frame["report"])
            if tg is None:
                continue
            samples.add_frame(decode(frame["nv12"], step_px), tg[0] * tg[1], limits)
            frames += 1
    return samples, frames


def check(steps, method):
    """Stage 0's global check on the kept frames. steps: [{hold, frames: [{n, report, nv12}]}], repeat last.

    Returns the result dict with the verdict 'pass' or 'fail' and every statistic.
    """
    reasons = []
    gains = []
    for index, step in enumerate(steps, 1):
        for frame in step["frames"]:
            ok, why = gain_agreement(frame["report"], step["hold"], method["exposure_quantum_us"])
            gains.append({"n": frame["n"], "step": index, "pass": ok, "reason": why})
    gain_pass = bool(gains) and all(g["pass"] for g in gains)
    if not gain_pass:
        reasons.append("gain agreement")
    samples, frames = collect(steps, method)
    result = {"samples": {"pixels": samples.pixels, "frames": frames, "used": len(samples),
                          "flagged": samples.flagged},
              "gain_agreement": {"pass": gain_pass, "quantum_us": method["exposure_quantum_us"],
                                 "frames": gains}}
    try:
        fits = {c: fit_response(samples, i, method) for i, c in enumerate(CHANNELS)}
    except Degenerate as exc:
        result.update(verdict="fail", reasons=reasons + [f"degenerate global fit: {exc}"],
                      residual=None, region_test=None, curves=None)
        return result
    residual = {c: residual_rms(fits[c], samples, i) for i, c in enumerate(CHANNELS)}
    residual_pass = all(value <= method["residual_max_codes"] for value in residual.values())
    if not residual_pass:
        reasons.append("residual")
    regions = region_test(samples, fits, method)
    if not regions["pass"]:
        reasons.append("region test")
    coverage = {}
    for c in CHANNELS:
        supported = [z for z in range(256) if fits[c].counts[z] >= method["region_min_code_samples"]]
        coverage[c] = [supported[0], supported[-1]] if supported else None
    result.update(verdict="fail" if reasons else "pass", reasons=reasons,
                  residual={"codes": residual, "max_codes": method["residual_max_codes"],
                            "pass": residual_pass},
                  region_test=regions, coverage=coverage,
                  curves={c: fits[c].curve for c in CHANNELS})
    return result


def region_test(samples, global_fits, method):
    cols, rows = method["grid_cols"], method["grid_rows"]
    parts = samples.split(sample_regions(method["pixel_step"], cols, rows), cols * rows)
    tolerance = method["region_tolerance_percent"] / 100.0
    out = []
    for region, part in enumerate(parts):
        for i, c in enumerate(CHANNELS):
            entry = {"region": region, "channel": c, "tested": False, "codes": 0,
                     "scale": None, "deviation_percent": None, "pass": True}
            try:
                fit = fit_response(part, i, method)
            except Degenerate:
                out.append(entry)
                continue
            compared = compare_region(fit, global_fits[c].curve, method)
            if compared is not None:
                codes, scale, deviation = compared
                entry.update(tested=True, codes=codes, scale=scale,
                             deviation_percent=100 * deviation, **{"pass": deviation <= tolerance + 1e-12})
            out.append(entry)
    return {"pass": all(e["pass"] for e in out), "tolerance_percent": method["region_tolerance_percent"],
            "untested": sum(not e["tested"] for e in out), "regions": out}
