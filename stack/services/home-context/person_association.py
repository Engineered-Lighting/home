"""Conservative short-lived anonymous association; never appearance identity.

Boxes/poses stay in the separately authorized local geometry path. Tracking
continuity is experimental until real concurrent episodes qualify the producer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _box(value):
    if not isinstance(value, list) or len(value) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError("finite image box required")
    if not 0 <= value[0] < value[2] <= 1 or not 0 <= value[1] < value[3] <= 1:
        raise ValueError("normalized image box required")
    return tuple(value)


def _iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = lambda x: (x[2] - x[0]) * (x[3] - x[1])
    return intersection / (area(a) + area(b) - intersection) if intersection else 0


@dataclass
class Track:
    pid: int
    epoch: str
    box: tuple
    observed_at: float
    observation_root: str


class AnonymousAssociator:
    def __init__(self, scope, source_id, capture_epoch, max_gap_s=2, minimum_iou=0.3):
        required = {"site_id", "room_id", "session_id", "domain_epoch"}
        if set(scope) != required or not all(isinstance(v, str) and v for v in scope.values()):
            raise ValueError("exact room scope required")
        if not 0 < max_gap_s <= 3 or not 0 < minimum_iou <= 1:
            raise ValueError("bounded association policy required")
        self.scope, self.source_id, self.capture_epoch = dict(scope), source_id, capture_epoch
        self.max_gap_s, self.minimum_iou = max_gap_s, minimum_iou
        self.tracks, self.generation, self.last_observed_at = {}, 0, None
        self.last_root, self.last_result = None, None

    def reset(self, capture_epoch):
        self.capture_epoch = capture_epoch
        self.tracks = {}
        self.generation += 1
        self.last_root, self.last_result, self.last_observed_at = None, None, None

    def observe(self, *, observed_at, observation_root, detections, clock_status, capture_epoch):
        if clock_status != "commissioned":
            return {"tracks": [], "status": "unobservable", "reason": "clock_unverified"}
        if capture_epoch != self.capture_epoch:
            self.reset(capture_epoch)
        if observation_root == self.last_root:
            return self.last_result  # same frame never establishes new progression
        if type(observed_at) not in (float, int) or not math.isfinite(observed_at) or self.last_observed_at is not None and observed_at <= self.last_observed_at:
            raise ValueError("strictly progressing observation time required")
        if not isinstance(observation_root, str) or not observation_root or not isinstance(detections, list) or len(detections) > 8:
            raise ValueError("bounded source observation required")
        rows = []
        for d in detections:
            if set(d) != {"instance_ref", "pose_instance_ref", "box"} or not isinstance(d["instance_ref"], str) or d["pose_instance_ref"] != d["instance_ref"]:
                self.tracks = {}
                self.last_observed_at, self.last_root = observed_at, observation_root
                self.last_result = {"tracks": [], "status": "unresolved", "reason": "pose_box_association_missing"}
                return self.last_result
            rows.append({**d, "box": _box(d["box"])})
        if len({d["instance_ref"] for d in rows}) != len(rows):
            raise ValueError("unique within-frame instance refs required")
        existing = {p: tr for p, tr in self.tracks.items() if observed_at - tr.observed_at <= self.max_gap_s}
        candidates = {i: [p for p, tr in existing.items() if _iou(d["box"], tr.box) >= self.minimum_iou] for i, d in enumerate(rows)}
        # Any competing assignment or overlapping detections discards continuity.
        ambiguous = any(len(ps) > 1 for ps in candidates.values()) or any(sum(p in ps for ps in candidates.values()) > 1 for p in existing)
        ambiguous |= any(_iou(a["box"], b["box"]) >= self.minimum_iou for i, a in enumerate(rows) for b in rows[i + 1:])
        self.last_observed_at, self.last_root = observed_at, observation_root
        if ambiguous:
            self.tracks = {}
            self.generation += 1
            self.last_result = {"tracks": [], "status": "unresolved", "reason": "competing_person_association", "alternatives": [d["instance_ref"] for d in rows]}
            return self.last_result
        next_tracks, output = {}, []
        reserved = {p for values in candidates.values() for p in values}
        for i, d in enumerate(rows):
            if candidates[i]:
                tr = existing[candidates[i][0]]
                pid, epoch = tr.pid, tr.epoch
            else:
                pid = next(p for p in range(1, 9) if p not in next_tracks and p not in reserved)
                self.generation += 1
                epoch = f"{self.capture_epoch}:anonymous:{self.generation}"
            next_tracks[pid] = Track(pid, epoch, d["box"], observed_at, observation_root)
            output.append({"person_ref": {**self.scope, "track_epoch": epoch, "pid": pid}, "track_status": "tracked", "instance_ref": d["instance_ref"], "observation_root": observation_root, "source_id": self.source_id, "qualification": "experimental_shadow"})
        self.tracks = next_tracks
        self.last_result = {"tracks": output, "status": "supported", "qualification": "experimental_shadow", "observed_at": observed_at}
        return self.last_result


def qualify_surface_claim(track, claim, registration):
    """Require supplied geometry qualification, never infer a room ID from words."""
    required = {"person_ref", "surface_id", "geometry_revision", "registration_ref", "coverage_refs", "association_ref", "observed_at", "valid_until", "depth_status"}
    if not isinstance(claim, dict) or set(claim) != required or claim["person_ref"] != track["person_ref"]:
        return {"status": "unresolved", "reason": "person_surface_binding_missing"}
    if registration.get("status") != "verified" or registration.get("revision") != claim["geometry_revision"] or registration.get("registration_ref") != claim["registration_ref"]:
        return {"status": "unobservable", "reason": "geometry_revision_mismatch"}
    if claim["depth_status"] != "supplied" or not claim["coverage_refs"] or track["observation_root"] != claim["association_ref"]:
        return {"status": "unobservable", "reason": "depth_coverage_or_frame_pairing_missing"}
    return {"status": "supported", "qualification": "experimental_shadow", "person_ref": track["person_ref"], "surface_id": claim["surface_id"], "geometry_revision": claim["geometry_revision"], "evidence_refs": [claim["association_ref"], *claim["coverage_refs"]]}
