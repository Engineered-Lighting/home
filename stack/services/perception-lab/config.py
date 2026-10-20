"""Explicit configuration. Importing this module never starts a device or service."""
from dataclasses import dataclass, field
from pathlib import Path
import json


@dataclass(frozen=True)
class Settings:
    storage_root: Path
    site_id: str = "victoria"
    quota_bytes: int = 64 * 1024**3
    min_free_bytes: int = 2 * 1024**3
    max_session_seconds: int = 1800
    max_object_bytes: int = 16 * 1024**2
    max_read_bytes: int = 4 * 1024**2
    developer_fixture_mode: bool = False
    token_grants: tuple = field(default_factory=tuple)
    cameras: tuple = field(default_factory=tuple)
    devices: tuple = field(default_factory=tuple)
    sources: tuple = field(default_factory=tuple)

    def __post_init__(self):
        if self.site_id not in {"victoria", "echo"}:
            raise ValueError("site_id must be victoria or echo")
        if not 1 <= self.max_session_seconds <= 1800:
            raise ValueError("session duration must be at most 30 minutes")
        if self.quota_bytes <= 0 or self.min_free_bytes < 0:
            raise ValueError("invalid storage limits")
        if not 1 <= self.max_object_bytes <= 64 * 1024**2:
            raise ValueError("invalid object bound")
        if not 1 <= self.max_read_bytes <= 16 * 1024**2:
            raise ValueError("invalid read bound")

    @classmethod
    def from_file(cls, path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        raw["storage_root"] = Path(raw["storage_root"])
        for name in ("token_grants", "cameras", "devices", "sources"):
            if name in raw:
                raw[name] = tuple(raw[name])
        return cls(**raw)
