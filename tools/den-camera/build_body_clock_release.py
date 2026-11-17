#!/usr/bin/env python3
"""Build an optional measured-body clock release from the exact #425 camera source."""

import argparse
import hashlib
from pathlib import Path


BASE_SHA = "15430138f9dffdead4a7d2833cb57514a0162a037d0328931f1b134cee1374cc"
CAMERA_CODE = '''
@dataclass(frozen=True)
class BodyClockProfile:
    """Commissioned bounds for this SDK, device, calibration and pipeline cadence.

    Each plane's margins enclose its whole acquisition around getTimestamp(),
    including the SDK frame-to-host synchronization and rolling readout.
    clock_drift_ppm bounds SDK-to-camera-monotonic relative rate. The receiver
    rate allowance bounds camera elapsed time expressed in real or LA seconds.
    """
    schema: str
    evidence_id: str
    device_id: str
    depthai_version: str
    calibration_sha256: str
    fps: int
    clock_drift_ppm: int
    receiver_drift_ppm: int
    rgb: dict
    depth: dict

    def __post_init__(self):
        if (self.schema != 'den-body-clock-profile-1' or not _identity(self.evidence_id)
                or not _identity(self.device_id) or self.depthai_version != '3.10.0'
                or not isinstance(self.calibration_sha256, str)
                or re.fullmatch(r'[0-9a-f]{64}', self.calibration_sha256) is None
                or type(self.fps) is not int or not 1 <= self.fps <= 30
                or any(type(rate) is not int or not 0 <= rate <= 1000
                       for rate in (self.clock_drift_ppm, self.receiver_drift_ppm))):
            raise ValueError('Invalid measured-body clock commissioning')
        for plane in (self.rgb, self.depth):
            if (not isinstance(plane, dict) or set(plane) != {'before_stamp_s', 'after_stamp_s'}
                    or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
                           for value in plane.values())
                    or plane['before_stamp_s'] <= 0):
                raise ValueError('Both planes need commissioned acquisition bounds')

    def matches(self, *, sdk, device_id, fps, correction):
        return (self.depthai_version == sdk and self.device_id == device_id and self.fps == fps
                and correction.get('applied') is True
                and correction.get('calibration_sha256') == self.calibration_sha256)


def _ceil_ratio(numerator, denominator):
    return (numerator + denominator - 1) // denominator


def _body_offset(sample):
    sdk, quantum = sample['source_ns'], sample['source_quantization_ns']
    return (sdk - quantum - sample['source_after_mono_ns'],
            sdk + quantum - sample['source_before_mono_ns'])


def _body_interval(frame, sample, margins, ppm):
    timestamp = frame.get('host_monotonic_timestamp_seconds')
    if type(timestamp) not in (float, int) or not math.isfinite(timestamp) or not 0 <= timestamp < 1e9:
        raise ValueError('Invalid SDK frame timestamp')
    stamp = round(timestamp * 1e9)
    # The bundle timestamp is microsecond-quantized before its JSON float.
    quantization = 1000 + math.ceil(math.ulp(timestamp) * 1e9) + 1
    delta = abs(stamp - sample['source_ns']) + quantization + sample['source_quantization_ns']
    drift = _ceil_ratio(delta * ppm, 1_000_000 - ppm)
    offset_low, offset_high = _body_offset(sample)
    return (stamp - quantization - offset_high - drift - math.ceil(margins['before_stamp_s'] * 1e9),
            stamp + quantization - offset_low + drift + math.ceil(margins['after_stamp_s'] * 1e9))


def _exposure_fits(frame, margins):
    # A commissioned interval cannot enclose an acquisition longer than
    # itself. Frame metadata may omit exposure on derived stereo output.
    duration = frame.get('exposure_duration_seconds')
    return (duration is None or type(duration) in (int, float) and math.isfinite(duration)
            and 0 < duration <= margins['before_stamp_s'] + margins['after_stamp_s'])


class BodyClockState(State):
    """Attach an acquisition bound only to the exact immutable bundle sampled."""

    def __init__(self, *args, body_clock=None, **kwargs):
        self.body_clock = body_clock
        super().__init__(*args, **kwargs)

    def reset(self):
        super().reset()
        with self.lock:
            self.body_clock_matches = False
            self.body_clock_sample = None
            self.body_clock_bundle = None

    def bind_body_clock(self, *, sdk, device_id, fps, correction):
        if self.body_clock is not None:
            self.body_clock_matches = self.body_clock.matches(sdk=sdk, device_id=device_id, fps=fps,
                                                              correction=correction)

    def publish_bundle(self, bundle, rgb, nv12, received, *, metadata=None, source_now=None):
        super().publish_bundle(bundle, rgb, nv12, received)
        if not self.body_clock_matches or metadata is None or source_now is None:
            return
        try:
            before = self.causal_point()
            sample = sample_clock_pair(source_now)
            after = self.causal_point()
            check_no_suspend(before, after)
            key = [metadata['session'], rgb['sequence'], metadata['frames']['depth']['sequence']]
            if any(type(seq) is not int or seq < 0 for seq in key[1:]):
                raise ValueError('Invalid paired frame identity')
            with self.lock:
                generation = self.clock_sample_generation
                previous = self.body_clock_sample
                self.body_clock_sample = (key, generation, sample, before)
                self.body_clock_bundle = None
                if previous is None or self.session != key[0]:
                    return
                old_key, old_generation, old_sample, old_before = previous
                if (old_key[0] != key[0] or old_generation != generation
                        or any(current <= prior for current, prior in zip(key[1:], old_key[1:]))
                        or sample['source_ns'] <= old_sample['source_ns']):
                    return
                check_no_suspend(old_before, before, after)
                old_low, old_high = _body_offset(old_sample)
                low, high = _body_offset(sample)
                elapsed = sample['source_after_mono_ns'] - old_sample['source_before_mono_ns']
                drift = _ceil_ratio(elapsed * self.body_clock.clock_drift_ppm, 1_000_000)
                if low > old_high + drift or old_low > high + drift:
                    return
                if (rgb.get('exposure_duration_seconds') is None
                        or not _exposure_fits(rgb, self.body_clock.rgb)
                        or any(not _exposure_fits(metadata['frames'][plane], self.body_clock.depth)
                               for plane in ('left', 'right', 'depth'))):
                    return
                rgb_bounds = _body_interval(rgb, sample, self.body_clock.rgb, self.body_clock.clock_drift_ppm)
                depth_bounds = _body_interval(metadata['frames']['depth'], sample, self.body_clock.depth,
                                              self.body_clock.clock_drift_ppm)
                # The oldest possible exposure must fall within the checked no-suspend interval.
                if (min(rgb_bounds[0], depth_bounds[0]) < old_before.before_ns
                        or max(rgb_bounds[0], depth_bounds[0]) > before.after_ns
                        or self.bundle is not bundle):
                    return
                self.body_clock_bundle = (bundle, key, generation, rgb_bounds, depth_bounds, old_before, before, after)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            with self.lock:
                self.body_clock_sample = None
                self.body_clock_bundle = None

    def body_capture_age(self, bundle):
        """Compute age inside the request, before writing the selected bundle."""
        try:
            with self.lock:
                candidate = self.body_clock_bundle
                if candidate is None or candidate[0] is not bundle or self.bundle is not bundle:
                    return None
                _, key, generation, rgb, depth, old_before, before, after = candidate
                if self.session != key[0] or self.clock_sample_generation != generation:
                    return None
                response = self.causal_point()
                check_no_suspend(old_before, before, after, response)
                oldest = min(rgb[0], depth[0])
                # A figure can never use a pair already a second old on the source.
                if not 0 <= response.after_ns - oldest < 1_000_000_000:
                    return None
                def seconds_upper(ns):
                    ppm = self.body_clock.receiver_drift_ppm
                    allowance = _ceil_ratio(ns * ppm, 1_000_000 - ppm)
                    return math.nextafter((ns + allowance) / 1e9, math.inf)
                return json.dumps({
                    'schema': 'den-body-pair-age-1', 'frame_key': key,
                    'evidence_id': self.body_clock.evidence_id,
                    'rgb_age_upper_s': seconds_upper(response.after_ns - rgb[0]),
                    'depth_age_upper_s': seconds_upper(response.after_ns - depth[0]),
                    'separation_upper_s': seconds_upper(max(abs(rgb[0] - depth[1]), abs(rgb[1] - depth[0]))),
                }, separators=(',', ':'), allow_nan=False)
        except (OSError, ValueError):
            return None


'''


def build(source):
    if hashlib.sha256(source).hexdigest() != BASE_SHA:
        raise ValueError("expected byte-exact pilot-20261111-depth capture.py")
    text = source.decode("utf-8")
    replacements = (
        ("def serve(state, bind, port, token):", CAMERA_CODE.lstrip("\n") + "def serve(state, bind, port, token):"),
        ("                status, content_type, body = state.response(self.path)\n",
         "                status, content_type, body = state.response(self.path)\n"
         "                if self.path == '/frame.npz' and status == 200 and token:\n"
         "                    age = state.body_capture_age(body)\n"
         "                    if age is not None:\n"
         "                        headers['X-Den-Body-Capture-Age'] = age\n"),
        ("            depth_correction = depth_correction_for(eeprom)\n",
         "            depth_correction = depth_correction_for(eeprom)\n"
         "            state.bind_body_clock(sdk=dai.__version__, device_id=device.getDeviceId(),\n"
         "                                  fps=args.fps, correction=depth_correction)\n"),
        ("                                             arrays['rgb_nv12'].tobytes(), now)\n",
         "                                             arrays['rgb_nv12'].tobytes(), now,\n"
         "                                             metadata=metadata, source_now=dai.Clock.now)\n"),
        ("    args = parser.parse_args()\n",
         "    parser.add_argument('--body-clock-profile', help='Protected commissioned RGB/depth capture bounds')\n"
         "    parser.add_argument('--body-clock-profile-uid', type=int, help='Linux owner UID for the body clock profile')\n"
         "    args = parser.parse_args()\n"
         "    if (args.body_clock_profile is None) != (args.body_clock_profile_uid is None):\n"
         "        parser.error('Body clock profile and its owner UID must be given together')\n"
         "    body_clock = None\n"
         "    if args.body_clock_profile is not None:\n"
         "        try:\n"
         "            body_clock = BodyClockProfile(**read_protected_json(args.body_clock_profile,\n"
         "                                                            args.body_clock_profile_uid))\n"
         "        except (OSError, TypeError, ValueError):\n"
         "            parser.error('Protected commissioned body clock profile required')\n"),
        ("    state = State(args.stale_seconds, args.jpeg_clock_map, causal_config=causal_config,\n"
         "                  host_clock_path=args.jpeg_host_clock, evidence_uid=args.jpeg_evidence_uid)\n",
         "    state = BodyClockState(args.stale_seconds, args.jpeg_clock_map, causal_config=causal_config,\n"
         "                           host_clock_path=args.jpeg_host_clock, evidence_uid=args.jpeg_evidence_uid,\n"
         "                           body_clock=body_clock)\n"),
    )
    for old, new in replacements:
        if text.count(old) != 1:
            raise ValueError("camera release layout differs")
        text = text.replace(old, new, 1)
    return text.encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        raw = build(args.base.read_bytes())
        with args.output.open("xb") as handle:
            handle.write(raw)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    main()
