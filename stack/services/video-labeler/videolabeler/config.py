"""ENV-driven configuration.

ENV (defaults): DATA_DIR=/data  PORT=8099  MIN_FREE_VRAM_GB=6 (reserved for
gpu-lane jobs — unused in M0)  INBOX_DIR={DATA_DIR}/inbox

Construction is side-effect free (no mkdir) so tests can build Config against
a tmp dir before it exists; callers run ensure_dirs() explicitly at startup.
"""
from __future__ import annotations

import os


class Config:
    def __init__(self, env=os.environ):
        self.data_dir = env.get("DATA_DIR", "/data")
        self.port = int(env.get("PORT", "8099"))
        self.min_free_vram_gb = float(env.get("MIN_FREE_VRAM_GB", "6"))
        self.inbox_dir = env.get("INBOX_DIR") or os.path.join(self.data_dir, "inbox")
        self.db_path = os.path.join(self.data_dir, "videolabeler.db")
        self.originals_dir = os.path.join(self.data_dir, "videos", "originals")
        self.proxies_dir = os.path.join(self.data_dir, "proxies")
        self.thumbs_dir = os.path.join(self.data_dir, "thumbs")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.inbox_dir, self.originals_dir,
                  self.proxies_dir, self.thumbs_dir):
            os.makedirs(d, exist_ok=True)

    def resolve(self, rel_path: str) -> str:
        """DATA_DIR-relative path (stored with forward slashes) -> absolute."""
        return os.path.join(self.data_dir, *rel_path.split("/"))
