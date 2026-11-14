"""Capture and score one private, room-light Den sofa qualification session."""

import argparse
import hashlib
import http.client
import io
import json
import sys
import time
from dataclasses import asdict, fields
from pathlib import Path

import sofa_check
import sofa_mask_score
from body_head import HeadProfile
from body_rgb import RGB_PAIR_S, RgbBodyProfile
from source import camera_client

from sofa_clock_score import reference_template, score_clock


SCHEMA = "den-sofa-rgb-session-1"
CUES = {
    "book": "Sit on the sofa and read an open book as you normally would.",
    "turn-left": "Turn your head to your left and back slowly. Repeat until the next cue.",
    "turn-right": "Turn your head to your right and back slowly. Repeat until the next cue.",
    "recline": "Lean back against the sofa with the book and change your head angle.",
    "reach": "Reach beside the sofa and return. Repeat at your normal speed.",
    "stand": "Stand up beside the sofa and look around the room. Keep turning your head.",
    "walk": "Walk slowly across the room and back, at your normal pace. Repeat until the next cue.",
    "empty": "Please leave the room now, and stay out until you hear that capture is complete.",
}
PER_CUE = 3
POLL_S = 0.5
SAMPLE_SPACING_S = 1.5
SETTLE_S = 5.0
SETTLE_EMPTY_S = 12.0   # time to leave the room before the empty frames
WINDOW_S = 6.0
SESSION_INPUTS = ("camera-calibration.json", "body-rgb-candidate.json", "registration.json", "room-model.jsonl")
CAMERA_INPUTS = SESSION_INPUTS[:2]
CAMERA_SCOPE = "camera_relative_trial"
ROOM_SCOPE = "room_registered"

# Initial settings for a local trial, taken from the generated RGB body test.
TRIAL_HEAD = HeadProfile(head_radius_m=0.13, neck_radius_m=0.055, max_head_shoulder_m=0.35,
                         alignment_px=1.0, localization_m=0.006, max_motion_m_s=0.50, max_separation_s=0.085)


def write_json(path, data):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    pending.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def frame_metadata(data):
    import numpy as np

    try:
        with np.load(io.BytesIO(data), allow_pickle=False) as arrays:
            metadata = json.loads(arrays["metadata_json"].tobytes())
        frames = metadata["frames"]
        key = (metadata["session"], frames["rgb"]["sequence"], frames["depth"]["sequence"])
        if (metadata["schema"] != 1 or not isinstance(key[0], str) or not key[0]
                or any(type(v) is not int or v < 0 for v in key[1:])):
            raise ValueError
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise sofa_check.Stop("a camera bundle has invalid metadata") from exc
    return metadata, key


def correction_matches(record, profile):
    correction = record.get("depth_correction") or {}
    return (correction.get("applied") is True and correction.get("factor") == profile.correction_factor
            and correction.get("calibration_sha256") == profile.calibration_sha256)


def camera_trial_profile(calibration):
    correction = calibration.get("depth_correction") or {}
    digest, factor = correction.get("calibration_sha256"), correction.get("factor")
    if (correction.get("applied") is not True or not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or type(factor) not in (int, float) or not 0.5 <= factor <= 1.5):
        raise sofa_check.Stop("the camera calibration does not report its applied depth correction")
    return RgbBodyProfile("sofa-mask-trial", "sofa-no-witness", digest, factor,
                          sofa_mask_score.CAMERA_TRIAL_TRANSFORM, CAMERA_SCOPE, 0.20, TRIAL_HEAD)


def capture(session, camera, profile, *, cue, sleep=time.sleep, clock=time.monotonic,
            now_ns=time.time_ns):
    status, calibration = camera.get("/calibration", timeout=2.0)
    if status != 200:
        raise sofa_check.Stop("camera calibration returned HTTP %d" % status)
    camera_calibration = json.loads(calibration)
    scope = CAMERA_SCOPE if profile is None else ROOM_SCOPE
    if profile is None:
        profile = camera_trial_profile(camera_calibration)
        write_json(session / "body-rgb-candidate.json", asdict(profile))
    if not correction_matches(camera_calibration, profile):
        raise sofa_check.Stop("camera calibration does not match the candidate's registration")
    (session / "camera-calibration.json").write_bytes(calibration)
    rows = []
    manifest = {"schema": SCHEMA, "scope": scope, "complete": False, "frames": rows,
                "inputs": {name: hashlib.sha256((session / name).read_bytes()).hexdigest()
                           for name in (CAMERA_INPUTS if scope == CAMERA_SCOPE else SESSION_INPUTS)}}
    write_json(session / "manifest.json", manifest)
    expected_session = camera_calibration["session"]
    prior = None
    cue("Capture starts in ten seconds. Get on the sofa with your book.")
    sleep(10.0)
    for label, instruction in CUES.items():
        cue(instruction)
        sleep(SETTLE_EMPTY_S if label == "empty" else SETTLE_S)
        deadline = clock() + WINDOW_S
        count = 0
        while clock() < deadline and count < PER_CUE:
            requested = clock()
            status, data = camera.get("/frame.npz", timeout=2.0)
            received, wall = clock(), now_ns()
            if status in (401, 403):
                raise sofa_check.Stop("the camera refused the saved token")
            interval = POLL_S
            if status == 200:
                metadata, key = frame_metadata(data)
                if key[0] != expected_session:
                    raise sofa_check.Stop("the camera restarted during capture. Start a new session.")
                if not correction_matches(metadata, profile):
                    raise sofa_check.Stop("the camera's depth correction changed during capture")
                if prior != key:
                    if prior is not None and any(current <= old for current, old in zip(key[1:], prior[1:])):
                        raise sofa_check.Stop("camera frame sequences stopped advancing together")
                    name = "frames/%04d.npz" % (len(rows) + 1)
                    (session / name).write_bytes(data)
                    rows.append({"file": name, "cue": label, "key": list(key), "sha256": hashlib.sha256(data).hexdigest(),
                                 "observed_utc_ns": wall, "request_monotonic_ns": round(requested * 1e9),
                                 "receive_monotonic_ns": round(received * 1e9)})
                    write_json(session / "manifest.json", manifest)
                    prior, count = key, count + 1
                    interval = SAMPLE_SPACING_S
            if count < PER_CUE:
                sleep(max(0.0, requested + interval - clock()))
        if count != PER_CUE:
            raise sofa_check.Stop("too few distinct bundles for %s. Start a new session." % label)
    manifest["complete"] = True
    write_json(session / "manifest.json", manifest)
    cue("Capture complete. You can come back in.")
    return rows


def load_session(session):
    manifest = read_json(session / "manifest.json")
    if manifest["schema"] != SCHEMA or manifest["complete"] is not True:
        raise sofa_check.Stop("capture is incomplete")
    scope = manifest.get("scope", ROOM_SCOPE)
    if scope not in (CAMERA_SCOPE, ROOM_SCOPE):
        raise sofa_check.Stop("unknown capture scope")
    for name in (CAMERA_INPUTS if scope == CAMERA_SCOPE else SESSION_INPUTS):
        if hashlib.sha256((session / name).read_bytes()).hexdigest() != manifest["inputs"][name]:
            raise sofa_check.Stop("a frozen session input changed: %s" % name)
    rows = manifest["frames"]
    if [row["cue"] for row in rows] != [cue for cue in CUES for _ in range(PER_CUE)]:
        raise sofa_check.Stop("the session is missing capture cues")
    samples = []
    for i, row in enumerate(rows, 1):
        name = "frames/%04d.npz" % i
        if row["file"] != name:
            raise sofa_check.Stop("unexpected frame filename in the manifest")
        data = (session / name).read_bytes()
        meta, key = frame_metadata(data)
        if list(key) != row["key"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise sofa_check.Stop("a saved frame no longer matches its capture manifest")
        samples.append(meta)
    return rows, samples, scope


def read_candidate(session, path=None):
    """The candidate this capture is scored with: its own, or a newer one named on the command line (recorded)."""
    path = Path(path) if path is not None else session / "body-rgb-candidate.json"
    block = read_json(path)
    registration = read_json(session / "registration.json")
    return RgbBodyProfile.from_config(block, registration), registration, hashlib.sha256(path.read_bytes()).hexdigest()


def clock_for_mask(clock_score, rows, profile):
    if clock_score["status"] != "measured":
        return None, clock_score["status"]
    if clock_score["candidate"]["evidence_id"] != profile.clock_evidence_id:
        return None, "evidence_id_mismatch"
    if clock_score["max_rgb_depth_stamp_delta_s"] > RGB_PAIR_S + 1e-9:
        return None, "pairing_limit_exceeded"
    separations = {tuple(row["frame_key"]): row["separation_s"] for row in clock_score["candidate_separations"]}
    if set(separations) != {tuple(row["key"]) for row in rows}:
        return None, "missing_exact_pair"
    if clock_score["max_candidate_separation_s"] > profile.head.max_separation_s:
        return separations, "head_separation_exceeded"
    return separations, "measured"


def den_live_commit():
    """The commit this scorer ran from, and whether its tools folder had changes: provenance for the profile."""
    import subprocess

    here = str(Path(__file__).resolve().parent)
    head = subprocess.run(["git", "-C", here, "rev-parse", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", here, "status", "--porcelain", "--", "."], capture_output=True, text=True)
    if head.returncode:
        return None
    return head.stdout.strip() + ("+changes" if dirty.stdout.strip() else "")


def pose_digest(frames):
    """sha256 over each scored frame's key, status and pose, as scored: the poses a qualification stands on."""
    rows = [[list(frame.key), frame.status, frame.pose] for frame in frames]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def report(session, clock_score, clock_check, mask_score, *, scope=ROOM_SCOPE, candidate_sha256=None,
           annotations_sha256=None, poses_sha256=None):
    passed = (clock_check == "measured" and mask_score["verdict"] == "pass"
              and mask_score["basis"] in ("measured_separation", "zero_separation"))
    pair_delta = clock_score.get("max_rgb_depth_stamp_delta_s")
    pairing = "unavailable" if pair_delta is None else "pass" if pair_delta <= RGB_PAIR_S + 1e-9 else "fail"
    verdict = "sampled_session_pass" if passed else "open"
    if scope == CAMERA_SCOPE:
        passed = pairing == "pass" and mask_score["verdict"] == "pass"
        verdict = "camera_trial_pass" if passed else "camera_trial_open"
    summary = {"verdict": verdict, "scope": scope, "rgb_pairing": pairing, "candidate_sha256": candidate_sha256,
               "den_live_commit": den_live_commit(), "annotations_sha256": annotations_sha256,
               "pose_digest": poses_sha256,
               "clock_check": clock_check,
               "clock": {key: value for key, value in clock_score.items() if key != "candidate_separations"},
               "mask": mask_score}
    write_json(session / "qualification.json", summary)
    print("Sofa session %s | clock %s | mask %s | qualification.json" %
          (summary["verdict"], clock_check, mask_score["verdict"]))
    return 0 if passed else 1


def private_session(take, path):
    path = path.resolve()
    if take.inside_checkout(path) or take.in_onedrive(path):
        raise sofa_check.Stop("put the session outside Git and OneDrive")
    return path


def local_registration(root, observe):
    from run_local import CAMERA_ID, registering_receipt

    sys.path.insert(0, str(observe / "tools" / "den-observe"))
    import roommodel

    registered = root / "private" / "register"
    room_model = registered / "room-model.jsonl"
    try:
        transform_id = roommodel.current_transform_id(roommodel.load(room_model), CAMERA_ID)
    except (OSError, roommodel.RegistrationRefused) as exc:
        raise sofa_check.Stop("the workstation has no current Den camera registration") from exc
    receipt = registering_receipt(registered, transform_id)
    if receipt is None:
        raise sofa_check.Stop("the current Den camera registration has no matching receipt")
    return room_model, read_json(receipt), transform_id


def candidate_template(registration, transform_id):
    return {"qualification_id": None, "clock_evidence_id": None,
            "transform_id": transform_id, "scan_revision": registration["scan_revision"],
            "min_valid_depth_fraction": None, "head": {item.name: None for item in fields(HeadProfile)}}


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    sub = command.add_subparsers(dest="action", required=True)
    init = sub.add_parser("profile-template", help="write a private template for candidate mask settings")
    init.add_argument("output", type=Path, help="new local JSON file")
    record = sub.add_parser("capture", help="record the five cues from the existing RGBD endpoint")
    record.add_argument("session", type=Path, help="new local directory")
    mode = record.add_mutually_exclusive_group(required=True)
    mode.add_argument("--profile", type=Path, help="candidate body_rgb JSON to test with room registration")
    mode.add_argument("--camera-relative", action="store_true", help="local mask trial with camera calibration")
    record.add_argument("--camera", help="override the existing den-take camera address")
    record.add_argument("--token-file", type=Path, help="override the existing den-take token file")
    prepare = sub.add_parser("prepare", help="write the blind marking page and timing witness template")
    prepare.add_argument("session", type=Path)
    analyse = sub.add_parser("score", help="score the saved session offline using the detector venv")
    analyse.add_argument("session", type=Path)
    analyse.add_argument("--annotations", type=Path, required=True)
    analyse.add_argument("--witness", type=Path, help="independent full-exposure timing bounds JSON")
    analyse.add_argument("--candidate", type=Path, help="score with this candidate instead of the capture's own "
                         "(it must bind to the same registration; its sha256 goes in qualification.json)")
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    root = sofa_check.data_root()
    tools_root = root / "tools"
    observe = sofa_check.archive(tools_root, "observe")
    sys.path.insert(0, str(observe / "tools" / "den-take"))
    import take

    if args.action == "profile-template":
        output = private_session(take, args.output)
        _, registration, transform_id = local_registration(root, observe)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as out:
            json.dump(candidate_template(registration, transform_id), out, indent=2)
            out.write("\n")
        return 0

    session = private_session(take, args.session)
    if args.action == "capture":
        if not sofa_check.has_console():
            raise sofa_check.Stop("run capture from an interactive PowerShell")
        profile = None
        if not args.camera_relative:
            block = read_json(args.profile)
            room_model_path, registration, _ = local_registration(root, observe)
            profile = RgbBodyProfile.from_config(block, registration)
            room_model = room_model_path.read_bytes()
        try:
            token = take.read_token(args.token_file or take.DEFAULT_TOKEN_FILE)
            camera = camera_client(take, args.camera or take.DEFAULT_CAMERA, token)
        except take.Refused as exc:
            raise sofa_check.Stop(str(exc)) from None
        session.mkdir(parents=True, exist_ok=False)
        (session / "frames").mkdir()
        if profile is not None:
            write_json(session / "body-rgb-candidate.json", block)
            write_json(session / "registration.json", registration)
            (session / "room-model.jsonl").write_bytes(room_model)
        capture(session, camera, profile, cue=take.say)
        return 0

    rows, samples, scope = load_session(session)
    if args.action == "prepare":
        template = session / "clock-witness-template.json"
        if scope == ROOM_SCOPE and not template.exists():
            try:
                clock_template = reference_template(samples)
            except (KeyError, TypeError, ValueError):
                print("Clock metadata is invalid. The mask can still be reviewed.", file=sys.stderr)
            else:
                write_json(template, clock_template)
        page = sofa_mask_score.annotation_html(session, rows, tools_root=tools_root)
        (session / "mark-head-and-neck.html").write_text(page, encoding="utf-8")
        print("Open mark-head-and-neck.html and save the annotation JSON inside the session.")
        return 0

    room_model = registration = candidate_sha256 = None
    if scope == CAMERA_SCOPE:
        if args.witness is not None:
            raise sofa_check.Stop("the camera-relative trial scores timestamp pairing and the candidate mask")
        if args.candidate is not None:
            raise sofa_check.Stop("the camera-relative trial scores its own fixed settings")
        profile = camera_trial_profile(read_json(session / "camera-calibration.json"))
        if asdict(profile) != read_json(session / "body-rgb-candidate.json"):
            raise sofa_check.Stop("the saved trial settings do not match this tool")
    else:
        profile, registration, candidate_sha256 = read_candidate(session, args.candidate)
        room_model = session / "room-model.jsonl"
    witness = read_json(args.witness) if args.witness is not None else None
    annotations = read_json(args.annotations)
    annotations_sha256 = hashlib.sha256(Path(args.annotations).read_bytes()).hexdigest()
    clock_score = score_clock(samples, witness)
    separations, clock_check = clock_for_mask(clock_score, rows, profile)
    frames, camera = sofa_mask_score.prepare_session(session, rows, tools_root=tools_root,
                                                     room_model=room_model,
                                                     registration=registration, profile=profile)
    mask_score = sofa_mask_score.score(frames, annotations, camera, profile=profile.head, separations=separations)
    return report(session, clock_score, clock_check, mask_score, scope=scope, candidate_sha256=candidate_sha256,
                  annotations_sha256=annotations_sha256, poses_sha256=pose_digest(frames))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Capture or scoring interrupted. Saved session files remain local.", file=sys.stderr)
        sys.exit(130)
    except (sofa_check.Stop, sofa_check.detect.Refused) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError) as error:
        print("Sofa qualification stopped (%s). Check the local inputs and camera connection." % type(error).__name__,
              file=sys.stderr)
        sys.exit(2)
