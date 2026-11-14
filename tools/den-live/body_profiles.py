"""Write the measured figure's two private profiles from one scored sofa session.

    python -B body_profiles.py <session> --candidate <the scored candidate> --out <private folder>

The session is sofa_qualify.py's, captured with a candidate profile and scored with the head and neck marked on the
exact depth frames. This refuses unless that score passed: every pose cue has a covered frame and no frame leaked.

It writes, never over an existing file:
- `den-body-clock.json` (`den-body-clock-profile-1`) for the camera, which the body-clock camera release reads with
  `--body-clock-profile`. Its device, SDK and calibration come from the session's camera calibration; its fps from
  the session's own RGB sequence rate; its acquisition margins are SDK_BOUNDS below.
- `den-live-body-rgb.json` for install_la.py: a byte copy of the candidate the passing score used (qualification.json
  names its sha256), with its clock evidence id; and beside it `den-live-body-rgb.qualification.json`, the counts
  and provenance install_la.py checks it against.

SDK_BOUNDS are bounds from the camera's own SDK semantics, not an optical measurement (the owner chose this on
2026-11-14; COMMISSIONING.md's strobe reference needs a rig that doesn't exist). DepthAI 3.10's ImgFrame timestamp is
the end of exposure (getTimestamp's default, END). So:
- RGB: exposure (at most 33.3 ms at 30 fps) before the stamp, and the rolling readout (at most one frame) after it.
- Depth: the two global-shutter mono exposures, hardware-synced with each other, end at their stamp.
- Each margin adds a few milliseconds for host synchronization and quantization.
The camera refuses a receipt for any frame whose reported exposure is longer than its margins, so a darker room
fails closed. The head cut's safety rests on the exact-frame coverage score, which this script requires.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from body_rgb import RgbBodyProfile

CLOCK_SCHEMA = "den-body-clock-profile-1"
CLOCK_FILE, RGB_FILE = "den-body-clock.json", "den-live-body-rgb.json"
SDK_BOUNDS = {"rgb": {"before_stamp_s": 0.040, "after_stamp_s": 0.040},
              "depth": {"before_stamp_s": 0.040, "after_stamp_s": 0.004}}
DRIFT_PPM = 200   # crystal oscillators and DepthAI's host sync, rounded well up; over a 1 s lease it is 0.2 ms
SDK = "3.10.0"


class Refused(Exception):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def session_fps(manifest_rows, frame_meta):
    """The RGB rate from the session's own bundles: sequence steps over SDK host-time steps, rounded."""
    stamps = sorted((meta["frames"]["rgb"]["sequence"], meta["frames"]["rgb"]["host_monotonic_timestamp_seconds"])
                    for meta in frame_meta)
    rates = [(b[0] - a[0]) / (b[1] - a[1]) for a, b in zip(stamps, stamps[1:]) if b[1] > a[1] and b[0] > a[0]]
    if len(rates) < 3:
        raise Refused("too few advancing frames to read the camera's fps")
    rates.sort()
    fps = round(rates[len(rates) // 2])
    if not 1 <= fps <= 30:
        raise Refused("the session's RGB rate (%d fps) is outside 1-30" % fps)
    return fps


def clock_profile(calibration, fps, evidence_id):
    correction = calibration.get("depth_correction") or {}
    device_id, digest = calibration.get("device_id"), correction.get("calibration_sha256")
    if calibration.get("depthai") != SDK:
        raise Refused("the camera runs DepthAI %r; the body clock is written for %s" % (calibration.get("depthai"), SDK))
    if correction.get("applied") is not True or not isinstance(digest, str) or len(digest) != 64:
        raise Refused("the camera doesn't report its applied depth correction")
    if not isinstance(device_id, str) or not 0 < len(device_id) <= 128:
        raise Refused("the camera calibration has no device id")
    return {"schema": CLOCK_SCHEMA, "evidence_id": evidence_id, "device_id": device_id, "depthai_version": SDK,
            "calibration_sha256": digest, "fps": fps, "clock_drift_ppm": DRIFT_PPM, "receiver_drift_ppm": DRIFT_PPM,
            "rgb": dict(SDK_BOUNDS["rgb"]), "depth": dict(SDK_BOUNDS["depth"])}


QUALIFICATION_FILE = "den-live-body-rgb.qualification.json"
PASSING_BASES = ("measured_separation", "zero_separation")   # never the old max-separation basis


def passed(qualification):
    mask = (qualification or {}).get("mask") or {}
    return mask.get("verdict") == "pass" and mask.get("basis") in PASSING_BASES


def qualification_record(qualification, candidate_bytes):
    """What install_la.py checks a profile against: counts and provenance only."""
    return {"schema": "den-live-body-rgb-qualification-1",
            "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "mask": qualification["mask"], "den_live_commit": qualification.get("den_live_commit"),
            "scope": qualification.get("scope")}


def build(session, *, frame_meta, candidate_path=None):
    session = Path(session)
    candidate_path = Path(candidate_path) if candidate_path is not None else session / "body-rgb-candidate.json"
    candidate = read_json(candidate_path)
    registration = read_json(session / "registration.json")
    RgbBodyProfile.from_config(candidate, registration)   # the candidate still binds to its registration
    qualification = read_json(session / "qualification.json")
    if not passed(qualification):
        raise Refused("the session's head-cut score didn't pass; no profile is written")
    scored = qualification.get("candidate_sha256")
    if scored is not None and scored != hashlib.sha256(candidate_path.read_bytes()).hexdigest():
        raise Refused("that candidate isn't the one the passing score used")
    fps = session_fps(read_json(session / "manifest.json")["frames"], frame_meta)
    clock = clock_profile(read_json(session / "camera-calibration.json"), fps, candidate["clock_evidence_id"])
    candidate_bytes = candidate_path.read_bytes()
    return clock, candidate, candidate_bytes, qualification_record(qualification, candidate_bytes)


def write_new(path, data):
    with open(path, "x", encoding="utf-8", newline="\n") as out:   # never over an existing profile
        out.write(json.dumps(data, indent=2) + "\n")


def main(argv=None):
    import numpy as np
    import io

    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("session", type=Path)
    args.add_argument("--candidate", type=Path, help="the candidate the score used, when not the capture's own")
    args.add_argument("--out", type=Path, required=True, help="the private folder for both files")
    args = args.parse_args(argv)
    manifest = read_json(args.session / "manifest.json")
    frame_meta = []
    for row in manifest["frames"]:
        with np.load(io.BytesIO((args.session / row["file"]).read_bytes()), allow_pickle=False) as arrays:
            frame_meta.append(json.loads(arrays["metadata_json"].tobytes()))
    try:
        clock, candidate, candidate_bytes, record = build(args.session, frame_meta=frame_meta,
                                                         candidate_path=args.candidate)
        args.out.mkdir(parents=True, exist_ok=True)
        write_new(args.out / CLOCK_FILE, clock)
        with open(args.out / RGB_FILE, "xb") as out:   # byte for byte: its sha256 is the scored candidate's
            out.write(candidate_bytes)
        write_new(args.out / QUALIFICATION_FILE, record)
    except (Refused, FileExistsError, KeyError, ValueError) as exc:
        print("No profiles written: %s" % (exc if not isinstance(exc, FileExistsError) else "a profile is already there"),
              file=sys.stderr)
        return 2
    print("Wrote %s and %s in %s (fps %d, evidence %s)." % (CLOCK_FILE, RGB_FILE, args.out, clock["fps"],
                                                           clock["evidence_id"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
