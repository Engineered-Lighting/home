"""Private handoff from the calibrated perception pipeline to the body worker."""
import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BodyInput:
    frame_key: object
    run_id: str
    track_epoch: int | str
    content_revision: str
    transform_id: str
    depth_profile: str
    capture_earliest_mono: float
    mode: str
    observation: str                         # person, empty, ambiguous, or unknown
    track: dict | None = None                # current track_id, state, association, and xy_m
    pose: dict | None = None                 # current calibrated den-observe keypoints observation
    head_evidence: dict | None = None        # full head and upper-neck bounds in depth pixels

    def binding(self):
        return {key: getattr(self, key) for key in
                ("run_id", "track_epoch", "content_revision", "transform_id", "depth_profile")}

    def scope(self, home):
        return dict(home=home, run_id=self.run_id, track_epoch=self.track_epoch,
                    content_revision=self.content_revision, transform_id=self.transform_id, mode=self.mode)


def bundle_frame_key(bundle, meta):
    """Bind the detector and provider to the same parsed RGB and depth bundle."""
    session, rgb_seq, depth_seq = (meta.get(name) for name in ("session", "rgb_seq", "depth_seq"))
    if (not isinstance(session, str) or not session or session != bundle.session
            or type(rgb_seq) is not int or rgb_seq < 0 or rgb_seq != bundle.rgb_seq
            or type(depth_seq) is not int or depth_seq < 0):
        return None
    return [session, rgb_seq, depth_seq]


def qualified(value, scope):
    """The pipeline supplies only healthy measured depth profiles and positively observed empty frames."""
    if not isinstance(value, BodyInput) or value.frame_key is None or value.mode not in ("rgb", "infrared"):
        return False
    if json.loads(json.dumps(value.frame_key, allow_nan=False)) != value.frame_key:
        return False
    if any(not isinstance(key, str) or not 0 < len(key) <= 128 for key in
           (value.run_id, value.content_revision, value.transform_id, value.depth_profile)):
        return False
    if any(getattr(value, key) != scope[key] for key in ("content_revision", "transform_id")):
        return False
    epoch = value.track_epoch
    if not ((type(epoch) is int and 0 <= epoch < 2**53) or (isinstance(epoch, str) and 0 < len(epoch) <= 128)):
        return False
    stamp = value.capture_earliest_mono
    return type(stamp) in (int, float) and math.isfinite(stamp)
