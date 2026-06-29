"""Pydantic request/response schemas (API contract is frozen — the frontend
is built against these shapes; return them via model_dump(mode="json"))."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


def _row_kwargs(cls, row, **overrides):
    keys = set(row.keys())
    kw = {k: row[k] for k in cls.model_fields if k in keys}
    kw.update(overrides)
    return kw


class Job(BaseModel):
    id: str
    type: str
    state: str
    lane: str
    progress: float = 0.0
    progress_msg: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "Job":
        return cls(**_row_kwargs(cls, row))


class Video(BaseModel):
    id: str
    filename: str
    duration_s: Optional[float] = None
    fps: Optional[float] = None
    codec: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    rotation_deg: int = 0
    size_bytes: Optional[int] = None
    source_batch: Optional[str] = None
    is_holdout: bool = False
    import_status: str = "imported"
    has_proxy: bool = False
    has_sprite: bool = False
    created_at: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "Video":
        return cls(**_row_kwargs(
            cls, row,
            is_holdout=bool(row["is_holdout"]),
            has_proxy=bool(row["has_proxy"]) if "has_proxy" in row.keys() else False,
            has_sprite=bool(row["has_sprite"]) if "has_sprite" in row.keys() else False,
        ))


class VideoDetail(Video):
    # local_path/proxy_path are DATA_DIR-relative (forward slashes)
    local_path: Optional[str] = None
    proxy_path: Optional[str] = None
    checksum: Optional[str] = None
    drive_file_id: Optional[str] = None
    mime: Optional[str] = None
    error: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row, proxy_path: Optional[str] = None) -> "VideoDetail":
        return cls(**_row_kwargs(
            cls, row,
            is_holdout=bool(row["is_holdout"]),
            has_proxy=bool(row["has_proxy"]) if "has_proxy" in row.keys() else False,
            has_sprite=bool(row["has_sprite"]) if "has_sprite" in row.keys() else False,
            proxy_path=proxy_path,
        ))


class SpriteManifest(BaseModel):
    tile_w: int
    tile_h: int
    cols: int
    interval_s: float
    count: int
    sheets: list[str]  # URL paths, /api/video-labeler/videos/{id}/sprite/{n}


class ImportManualRequest(BaseModel):
    batch_name: Optional[str] = None


# --- M1 labels surface. Segment dicts stay loosely typed here on purpose:
# the rich per-axis validation (values, non-overlap, aux keys) lives in
# videolabeler/labels.py so 400s can carry every problem in one round trip.

class LabelsPutRequest(BaseModel):
    revision: int
    axes: dict[str, list[dict]] = {}


class ReviewRequest(BaseModel):
    action: str


# --- M2 prelabel trigger: explicit ids OR everything still pending.

class PrelabelRequest(BaseModel):
    video_ids: Optional[list[str]] = None
    all_pending: bool = False


# --- M4 embed/cluster triggers. model is a variant name
# (personcrop_v1 | wholeframe_v1) or a full '<backbone>@<variant>' id.

class EmbedRequest(BaseModel):
    video_ids: Optional[list[str]] = None
    all_pending: bool = False
    model: Optional[str] = None


class ClusterRequest(BaseModel):
    model: Optional[str] = None


class CustomLabelCreate(BaseModel):
    axis: str
    name: str
    description: Optional[str] = None
    color: Optional[str] = None


class CustomLabelPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None


class CustomLabelMerge(BaseModel):
    into_slug: str


class CustomLabelPromote(BaseModel):
    canonical_value: str
