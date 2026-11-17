"""In-process evidence shared by perception, the ask, and the coordinator, and contract 1's registration receipt."""
import re
from dataclasses import dataclass, field
from types import MappingProxyType
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
class BodyFacing:
    """Contract 2's body_facing: deg clockwise from +y, the circular median of the samples over the last second;
    sd_deg, their median sd; as_of_mono, the newest sample's capture time; n, how many. person_fields.Facing makes it."""
    deg: float
    sd_deg: float
    as_of_mono: float
    n: int


@dataclass(frozen=True)
class HeadPoint:
    """Contract 2's head_point_m: xyz, the median of the stereo-supported face joints in room metres; n, how many."""
    xyz: tuple[float, float, float]
    n: int


@dataclass(frozen=True)
class Orientation3D:
    """Current landmark geometry in room axes, with its measured or fitted basis. This describes anatomical direction.

    Yaw is clockwise from +y and pitch is positive towards +z. angular_sd_deg is a conservative propagated
    covariance bound combined with a geometric-model floor, not a calibrated probability. No temporal carry.
    The containing TrackingSnapshot supplies the exact frame and track/association binding.
    """
    forward: tuple[float, float, float]
    up: tuple[float, float, float]
    yaw_deg: float
    pitch_deg: float
    angular_sd_deg: float
    as_of_mono: float
    source_joint_ids: tuple[int, ...]
    basis: str


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
    body_facing: BodyFacing | None = None   # None until a sample, and once it expires
    head_point_m: HeadPoint | None = None   # None with fewer than two face joints
    wrists_m: Mapping[str, tuple[float, float, float]] = field(default_factory=lambda: MappingProxyType({}))
    posture: str | None = None   # person_fields.POSTURES: sitting, standing, walking or unclear
    head_orientation: Orientation3D | None = None
    torso_orientation: Orientation3D | None = None


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
