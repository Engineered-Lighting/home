"""Bind current RGB observations to measured-body input for one registered camera."""
import math
from dataclasses import dataclass, replace
from uuid import uuid4

from body import FAR_MM, MIN_TORSO_SEEDS, NEAR_MM
from stream import BODY_LEASE_S
from body_clock import capture_bounds, lease_clock
from body_head import HeadProfile, head_evidence
from body_input import BodyInput


RGB_PAIR_S = 0.067
TORSO = {5, 6, 11, 12}


def _finite(value):
    return type(value) in (int, float) and -1e300 <= value <= 1e300


@dataclass(frozen=True)
class RgbBodyProfile:
    qualification_id: str
    clock_evidence_id: str
    calibration_sha256: str
    correction_factor: float
    transform_id: str
    scan_revision: str
    min_valid_depth_fraction: float
    head: HeadProfile

    @classmethod
    def from_config(cls, block, registration):
        """Read the private, owner-qualified profile and bind it to step 2's receipt."""
        correction = registration["source_correction"]
        fit = registration["fit"]
        digest, = correction["calibrations"]
        factor, = correction["factors"]
        profile = cls(block["qualification_id"], block["clock_evidence_id"], digest, factor, block["transform_id"], block["scan_revision"],
                      block["min_valid_depth_fraction"], HeadProfile(**block["head"]))
        if (not all(isinstance(v, str) and 0 < len(v) <= 128
                    for v in (profile.qualification_id, profile.clock_evidence_id, profile.transform_id, profile.scan_revision))
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
                or not _finite(factor) or not 0.5 <= factor <= 1.5
                or not _finite(profile.min_valid_depth_fraction) or not 0 < profile.min_valid_depth_fraction <= 1
                or type(correction["frames"]) is not int or correction["frames"] <= 0
                or correction["applied"] != correction["frames"]
                or fit["result"] != "registered" or fit["transform_id"] != profile.transform_id
                or registration["scan_revision"] != profile.scan_revision):
            raise ValueError("RGB body profile does not match the registered depth source")
        return profile


class RgbBodyInput:
    def __init__(self, profile, camera, *, track_epoch, clock=lease_clock):
        self.profile, self.camera = profile, camera
        self.track_epoch, self.clock = track_epoch, clock
        self.run_id = uuid4().hex

    def __call__(self, bundle, meta, depth, *, frame_key, mode, observations, tracks, supported, association):
        profile = self.profile
        if mode != "rgb" or self.camera.transform_id != profile.transform_id:
            return None
        bounds = capture_bounds(bundle, meta, frame_key=frame_key)
        now_mono = self.clock()
        if (bounds is None or bounds.evidence_id != profile.clock_evidence_id
                or not bounds.earliest_mono <= now_mono < bounds.earliest_mono + BODY_LEASE_S):
            return None
        if not self._depth_qualified(bundle, meta, depth):
            return None

        common = (frame_key, self.run_id, self.track_epoch(), profile.scan_revision, profile.transform_id,
                  profile.qualification_id, bounds.earliest_mono, "rgb")
        people = [o for o in observations if o["kind"] in ("keypoints", "person_box")]
        if not people and not tracks:
            return BodyInput(*common, "empty")
        if len(people) > 1 or len(tracks) > 1:
            return BodyInput(*common, "ambiguous")
        if len(people) != 1 or len(tracks) != 1 or len(supported) != 1:
            return BodyInput(*common, "unknown")

        track, pose = tracks[0], people[0]
        status, _ = association.get(track["track_id"], (None, None))
        if status == "ambiguous":
            return BodyInput(*common, "ambiguous")
        if (track["state"] != "confirmed" or status != "confident" or pose["obs_id"] not in supported[0]
                or pose["kind"] != "keypoints" or pose["validity"] != "valid"
                or pose["transform_id"] != self.camera.transform_id
                or pose["freshness"]["status"] != "fresh" or pose["freshness"]["depth"] != "used"):
            return BodyInput(*common, "unknown")
        joints = [joint for joint in pose["joints_room"] if joint["status"] == "supported" and joint["lift"] == "stereo"]
        if len({j["j"] for j in joints} & TORSO) < MIN_TORSO_SEEDS:
            return BodyInput(*common, "unknown")
        value = BodyInput(*common, "person", {**track, "association": status}, {**pose, "joints_room": joints})
        head = head_evidence(pose, self.camera, frame_key=frame_key, binding=value.binding(),
                             profile=profile.head, separation_s=bounds.separation_s)
        return replace(value, head_evidence=head) if head is not None else BodyInput(*common, "unknown")

    def _depth_qualified(self, bundle, meta, depth):
        profile = self.profile
        correction = meta["raw"].get("depth_correction")
        if (meta.get("intrinsics") is None or not isinstance(correction, dict) or correction.get("applied") is not True
                or correction.get("calibration_sha256") != profile.calibration_sha256
                or correction.get("factor") != profile.correction_factor
                or (depth.width, depth.height) != self.camera.rig.depth_size
                or not _finite(meta.get("t_depth")) or depth.t_host != meta["t_depth"]
                or depth.sequence != meta["depth_seq"]
                or abs(bundle.t_host - depth.t_host) > RGB_PAIR_S + 1e-9):
            return False
        # Empty means an observed, usable view with corrected measured depth. A
        # black frame, a stale result, and a failed detector never reach this path.
        needed = math.ceil(profile.min_valid_depth_fraction * (depth.width * depth.height))
        valid = 0
        for distance in depth.mm:
            if NEAR_MM <= distance <= FAR_MM:
                valid += 1
                if valid >= needed:
                    return True
        return False
