"""V-JEPA encoder backbone for the M4 embed job (frozen, encoder-only).

Weight-source truth (VERIFIED 2026-06-12): the facebookresearch/vjepa2 repo
ships hubconf with VJEPA_BASE_URL = "http://localhost:8300" ("for testing"),
so torch.hub.load(..., pretrained=True) can NEVER download real weights. The
real bucket is the commented-out https://dl.fbaipublicfiles.com/vjepa2 --
vjepa2_1_vitb_dist_vitG_384.pt answers HTTP 200 there (1.55 GB), while the
HF hub facebook/* org hosts only NON-2.1 vjepa2 checkpoints. Therefore:
  * build the architecture via torch.hub (pretrained=False, repo code cached
    under {DATA_DIR}/models/torchhub),
  * download the checkpoint OURSELVES, once, into {DATA_DIR}/models/backbone/
    (VJEPA_WEIGHTS_PATH points at an existing file instead;
    VJEPA_WEIGHTS_URL overrides the bucket url),
  * load the encoder state dict from the local file and DROP the predictor.

If the 2.1 download fails, the loader falls back LOUDLY to the non-2.1
vjepa2_vit_large at its native 256 px -- the smallest non-2.1 checkpoint
that exists (there is NO non-2.1 vit_base entrypoint or file). model_id_base
then reads 'vjepa2_vit_large_256', so substituted embeddings are never
silently mixed with 2.1 ones. An explicit VJEPA_WEIGHTS_PATH never falls
back -- it loads or the job fails.

All torch/numpy imports stay inside methods: the API process and the
Windows test venv never load them; tests inject a mock through
set_embedder_factory() (same pattern as vlm.client).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("videolabeler.embeddings.backbone")

HUB_REPO = "facebookresearch/vjepa2"
DOWNLOAD_TIMEOUT_S = 3600.0
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

# file names match the repo's ARCH_NAME_MAP so a manually scp'd official
# checkpoint lands on the same path the downloader would use
WEIGHT_SOURCES = {
    "vjepa2_1_vit_base_384": {
        "entrypoint": "vjepa2_1_vit_base_384",
        "file": "vjepa2_1_vitb_dist_vitG_384.pt",
        "url": "https://dl.fbaipublicfiles.com/vjepa2/vjepa2_1_vitb_dist_vitG_384.pt",
        "checkpoint_key": "ema_encoder",   # 2.1 checkpoints store the EMA encoder
        "img_size": 384,
        "strict": True,
    },
    "vjepa2_vit_large_256": {  # loud fallback ONLY -- see module docstring
        "entrypoint": "vjepa2_vit_large",
        "file": "vitl.pt",
        "url": "https://dl.fbaipublicfiles.com/vjepa2/vitl.pt",
        "checkpoint_key": "target_encoder",
        "img_size": 256,
        "strict": False,  # repo loads non-2.1 non-strict (pos_embed vs RoPE)
    },
}
FALLBACK_MODEL_NAME = "vjepa2_vit_large_256"


def _clean_backbone_key(state_dict: dict) -> dict:
    """Mirror of the repo's prefix strip (module. / backbone.)."""
    out = {}
    for key, val in state_dict.items():
        out[key.replace("module.", "").replace("backbone.", "")] = val
    return out


class VjepaEmbedder:
    """Frozen V-JEPA encoder: uint8 clips in, one mean-pooled vector per
    clip out. Loads eagerly in __init__ (the job has already passed the
    NVML free-VRAM gate by the time it constructs one)."""

    def __init__(self, cfg):
        self.cfg = cfg
        name = cfg.vjepa_model_name
        if name not in WEIGHT_SOURCES:
            raise RuntimeError(
                f"unknown VJEPA_MODEL_NAME '{name}'"
                f" (known: {', '.join(sorted(WEIGHT_SOURCES))})")
        try:
            weights = self._ensure_weights(name)
        except Exception as e:
            if cfg.vjepa_weights_path or name == FALLBACK_MODEL_NAME:
                raise
            log.error(
                "V-JEPA 2.1 weights UNREACHABLE (%s) -- FALLING BACK to the"
                " non-2.1 %s @256px (no non-2.1 vit_base exists)."
                " Embeddings will carry model_id_base '%s', NOT the 2.1 id.",
                e, FALLBACK_MODEL_NAME, FALLBACK_MODEL_NAME)
            name = FALLBACK_MODEL_NAME
            weights = self._ensure_weights(name)
        src = WEIGHT_SOURCES[name]
        self.model_id_base = name
        self.img_size = src["img_size"]
        self._load(name, weights)

    # ------------------------------------------------------------ weights

    def _ensure_weights(self, name: str) -> str:
        if self.cfg.vjepa_weights_path:
            p = self.cfg.vjepa_weights_path
            if not os.path.isfile(p):
                raise RuntimeError(f"VJEPA_WEIGHTS_PATH not found: {p}")
            return p
        src = WEIGHT_SOURCES[name]
        dest = os.path.join(self.cfg.backbone_dir, src["file"])
        if os.path.isfile(dest):
            return dest
        url = self.cfg.vjepa_weights_url or src["url"]
        log.info("downloading V-JEPA weights ONCE: %s -> %s", url, dest)
        import httpx
        os.makedirs(self.cfg.backbone_dir, exist_ok=True)
        part = dest + ".part"
        with httpx.stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S,
                          follow_redirects=True) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"weight download HTTP {resp.status_code}: {url}")
            with open(part, "wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
        os.replace(part, dest)
        log.info("downloaded %s (%.1f MB)", dest,
                 os.path.getsize(dest) / 2**20)
        return dest

    # -------------------------------------------------------------- model

    def _load(self, name: str, weights_path: str) -> None:
        import torch
        src = WEIGHT_SOURCES[name]
        hub_dir = os.path.join(self.cfg.models_dir, "torchhub")
        os.makedirs(hub_dir, exist_ok=True)
        torch.hub.set_dir(hub_dir)
        # pretrained=False: the repo's own loader points at localhost:8300
        encoder, _predictor = torch.hub.load(
            HUB_REPO, src["entrypoint"], pretrained=False, trust_repo=True)
        del _predictor
        try:
            state = torch.load(weights_path, map_location="cpu",
                               weights_only=True)
        except Exception:
            log.warning("weights_only load failed for %s -- retrying with"
                        " weights_only=False (official Meta checkpoint)",
                        weights_path)
            state = torch.load(weights_path, map_location="cpu",
                               weights_only=False)
        for key in (src["checkpoint_key"], "ema_encoder", "target_encoder",
                    "encoder"):
            if key in state:
                sd = _clean_backbone_key(state[key])
                break
        else:
            raise RuntimeError(
                f"no encoder key in checkpoint {weights_path}"
                f" (top-level keys: {sorted(state)[:8]})")
        encoder.load_state_dict(sd, strict=src["strict"])
        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        # Weights stay fp32; bf16 happens via AUTOCAST in the forward.
        # Hard-casting the module crashed live: V-JEPA 2.1's attention runs
        # its RoPE q/k path in fp32 while v keeps the module dtype, and
        # scaled_dot_product_attention rejects the q/k/v dtype mismatch.
        self.autocast_dtype = (torch.bfloat16 if self.device.startswith("cuda")
                               else None)
        encoder.eval().requires_grad_(False)
        self.encoder = encoder.to(self.device)
        self.dim = int(getattr(encoder, "embed_dim", 0)) or None
        log.info("V-JEPA encoder %s ready (%s, autocast=%s, dim=%s, img=%d)",
                 name, self.device, self.autocast_dtype, self.dim,
                 self.img_size)

    # -------------------------------------------------------------- embed

    def embed_clips(self, clips: list) -> list:
        """clips: list of [T, H, W, 3] uint8 ndarrays (H == W == img_size)
        -> one mean-pooled float vector per clip (encoder tokens averaged).
        Raises torch.cuda.OutOfMemoryError through to the caller -- the
        embed job owns the batch-halving policy."""
        import numpy as np
        import torch
        import contextlib
        x = np.stack(clips).astype("float32") / 255.0      # [B,T,H,W,3]
        t = torch.from_numpy(x).permute(0, 4, 1, 2, 3).contiguous()
        mean = torch.tensor(IMAGENET_MEAN).view(1, 3, 1, 1, 1)
        std = torch.tensor(IMAGENET_STD).view(1, 3, 1, 1, 1)
        t = ((t - mean) / std).to(self.device)             # fp32 input
        amp = (torch.autocast(device_type="cuda", dtype=self.autocast_dtype)
               if self.autocast_dtype is not None else contextlib.nullcontext())
        with torch.inference_mode(), amp:
            tokens = self.encoder(t)                       # [B, n_tokens, D]
            pooled = tokens.float().mean(dim=1).cpu()
        return [row.tolist() for row in pooled]


# Tests (and future alternates) swap the construction path here; production
# code never instantiates VjepaEmbedder directly.
_embedder_factory = None


def set_embedder_factory(factory) -> None:
    """factory: Config -> object with .model_id_base, .img_size, .dim and
    .embed_clips(clips). Pass None to restore the real encoder."""
    global _embedder_factory
    _embedder_factory = factory


def get_embedder(cfg):
    if _embedder_factory is not None:
        return _embedder_factory(cfg)
    return VjepaEmbedder(cfg)
