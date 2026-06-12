"""ENV-driven configuration.

ENV (defaults): DATA_DIR=/data  PORT=8099  MIN_FREE_VRAM_GB=6 (reserved for
gpu-lane jobs — unused in M0)  INBOX_DIR={DATA_DIR}/inbox

M2 VLM client ENV (OpenAI-compatible; defaults target the already-pulled
Ollama qwen2.5vl:32b so swapping to a vLLM serve later is config-only):
  VLM_BASE_URL=http://127.0.0.1:11434/v1  VLM_MODEL=qwen2.5vl:32b
  VLM_API_KEY= (empty)  VLM_TIMEOUT_S=180  VLM_KEEP_ALIVE=10m (passed through
  to Ollama as the keep_alive extra-body param when the base url is Ollama)

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
        # M2 perception/prelabel artifacts
        self.models_dir = os.path.join(self.data_dir, "models")
        self.keyframes_dir = os.path.join(self.data_dir, "keyframes")
        self.prelabels_dir = os.path.join(self.data_dir, "prelabels")
        # M4 embeddings: V-JEPA backbone weights land ONCE in backbone_dir;
        # fp16 .npy shards live under embeddings_dir/<model_id slug>/.
        self.embeddings_dir = os.path.join(self.data_dir, "embeddings")
        self.backbone_dir = os.path.join(self.models_dir, "backbone")
        self.vjepa_model_name = env.get("VJEPA_MODEL_NAME",
                                        "vjepa2_1_vit_base_384")
        # explicit local checkpoint (no download, no fallback when set)
        self.vjepa_weights_path = env.get("VJEPA_WEIGHTS_PATH", "")
        # overrides the verified dl.fbaipublicfiles.com bucket url
        self.vjepa_weights_url = env.get("VJEPA_WEIGHTS_URL", "")
        # M2 VLM client (OpenAI-compatible chat completions)
        self.vlm_base_url = env.get("VLM_BASE_URL", "http://127.0.0.1:11434/v1")
        self.vlm_model = env.get("VLM_MODEL", "qwen2.5vl:32b")
        self.vlm_api_key = env.get("VLM_API_KEY", "")
        self.vlm_timeout_s = float(env.get("VLM_TIMEOUT_S", "180"))
        self.vlm_keep_alive = env.get("VLM_KEEP_ALIVE", "10m")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.inbox_dir, self.originals_dir,
                  self.proxies_dir, self.thumbs_dir, self.models_dir,
                  self.keyframes_dir, self.prelabels_dir,
                  self.embeddings_dir, self.backbone_dir):
            os.makedirs(d, exist_ok=True)

    def resolve(self, rel_path: str) -> str:
        """DATA_DIR-relative path (stored with forward slashes) -> absolute."""
        return os.path.join(self.data_dir, *rel_path.split("/"))
