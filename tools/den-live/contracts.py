"""In-process evidence shared by perception, the ask, and the coordinator, and contract 1's registration receipt."""
import re
from dataclasses import dataclass
from typing import Mapping

# Contract 1, the room model: step 2's receipt, which register_now.py writes and every den-live start requires.
REGISTER_SCHEMA = "den-live-register-now-2"
DEPTH_SCALE_RANGE = (0.90, 1.10)   # den-live's own depth scale; 1.0 is none
_TAKE_SCALE = re.compile(r"-x([0-9]+(?:\.[0-9]+)?)(?:-m)?$")


def take_scale(name):
    """The depth scale register_now.py's copy of a take was made at, from the copy's name (<take>-x0.971-m), or 1.0 for
    a take that isn't such a copy (a rendered take, say), whose depth nothing scaled."""
    match = _TAKE_SCALE.search(str(name or ""))
    return float(match.group(1)) if match else 1.0


PERCEPTION_S = 1.5
FIGURE_S = 1.0
INFERENCE_S = 10.0
RGB_PAIR_S = 0.067
IR_PAIR_S = 0.020
FrameKey = tuple[str, int, int]


@dataclass(frozen=True)
class PlaneRef:
    seq: int
    device_s: float | None
    host_s: float
    width: int
    height: int


@dataclass(frozen=True)
class FrameRef:
    service_run_id: str
    camera_epoch: int
    frame_id: int
    camera_session: str
    mode: str
    rgb: PlaneRef
    right: PlaneRef | None
    depth: PlaneRef | None
    depth_calibration_sha256: str | None   # the camera's EEPROM hash, depth_correction's calibration_sha256
    transform_id: str
    scan_revision: str | None
    capture_mono: float
    arrival_mono: float
    capture_ts: float

    @property
    def key(self) -> FrameKey:
        return self.service_run_id, self.camera_epoch, self.frame_id


@dataclass(frozen=True)
class SeatedState:
    """Contract 2's seated: state True while seated; since_mono, the capture time of the first sample of the hold that
    qualified (None while not seated); basis, hips or head (None while not seated). seated.Published makes it."""
    state: bool
    since_mono: float | None
    basis: str | None


@dataclass(frozen=True)
class TrackEvidence:
    track_id: str
    state: str
    xy_m: tuple[float, float]
    sigma_m: float
    position_basis: str
    current_obs_ids: tuple[str, ...]
    association_status: str
    association_revision: int
    association_since_mono: float | None
    supported_pose: Mapping[str, tuple[tuple[float, float, float], str]]
    near: str | None = None
    seated: SeatedState | None = None   # perception's one answer; consumers read it and never work it out again


@dataclass(frozen=True)
class TrackingSnapshot:
    service_run_id: str
    camera_epoch: int
    seq: int
    frame: FrameRef | None
    status: str
    reason: str | None
    presence: str
    tracks: tuple[TrackEvidence, ...]
    eligible_track_id: str | None = None
    eligibility_revision: int = 0


@dataclass(frozen=True)
class InferenceReply:
    request_id: str
    request_body_sha256: str
    service_run_id: str
    camera_epoch: int
    track_id: str
    association_revision: int
    eligibility_revision: int
    frames: tuple[FrameRef, ...]
    observation: Mapping
    association: Mapping
    gate: Mapping
    model: Mapping
    observed_mono: float
    available_mono: float
    valid_until_mono: float
    timings_ms: Mapping
