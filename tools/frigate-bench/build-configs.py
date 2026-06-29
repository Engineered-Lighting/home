"""Build 5 Frigate config variants for the A/B/C/D/E bench (Addendum 25).

Reads config-baseline.yaml, replaces the face_recognition and (option E)
semantic_search blocks, writes config-<option>.yaml. Per-option SCP+restart
then runs the bench harness.
"""
from __future__ import annotations
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent
BASELINE = (BASE_DIR / "config-baseline.yaml").read_text(encoding="utf-8")

# Exact text of the existing face_recognition + semantic_search blocks.
# Locating by `^face_recognition:\n` and reading until next top-level key.
FACE_RE = re.compile(
    r"^face_recognition:\n(?:[ \t].*\n)+",
    flags=re.MULTILINE,
)
SEM_RE = re.compile(
    r"^semantic_search:\n(?:[ \t].*\n)+",
    flags=re.MULTILINE,
)

OPTIONS = {
    "A": {
        "label": "baseline (small / CPU)",
        "face": (
            "face_recognition:\n"
            "  enabled: true\n"
            "  model_size: small\n"
            "  detection_threshold: 0.7\n"
            "  recognition_threshold: 0.85\n"
            "  min_faces: 2\n"
        ),
        "sem": None,
    },
    "B": {
        "label": "large / CPU",
        "face": (
            "face_recognition:\n"
            "  enabled: true\n"
            "  model_size: large\n"
            "  detection_threshold: 0.7\n"
            "  recognition_threshold: 0.85\n"
            "  min_faces: 2\n"
        ),
        "sem": None,
    },
    "C": {
        "label": "small / GPU (Intel iGPU via OpenVINO)",
        "face": (
            "face_recognition:\n"
            "  enabled: true\n"
            "  model_size: small\n"
            "  device: GPU\n"
            "  detection_threshold: 0.7\n"
            "  recognition_threshold: 0.85\n"
            "  min_faces: 2\n"
        ),
        "sem": None,
    },
    "D": {
        "label": "large / GPU",
        "face": (
            "face_recognition:\n"
            "  enabled: true\n"
            "  model_size: large\n"
            "  device: GPU\n"
            "  detection_threshold: 0.7\n"
            "  recognition_threshold: 0.85\n"
            "  min_faces: 2\n"
        ),
        "sem": None,
    },
    "E": {
        "label": "large face / GPU + semantic_search / GPU",
        "face": (
            "face_recognition:\n"
            "  enabled: true\n"
            "  model_size: large\n"
            "  device: GPU\n"
            "  detection_threshold: 0.7\n"
            "  recognition_threshold: 0.85\n"
            "  min_faces: 2\n"
        ),
        "sem": (
            "semantic_search:\n"
            "  enabled: true\n"
            "  model_size: small\n"
            "  device: GPU\n"
        ),
    },
}


def build_config(option: str, cfg: dict) -> str:
    text = BASELINE
    text, n = FACE_RE.subn(cfg["face"], text, count=1)
    if n != 1:
        raise SystemExit(f"face_recognition block not found / replaced in baseline (n={n})")
    if cfg["sem"] is not None:
        text, n = SEM_RE.subn(cfg["sem"], text, count=1)
        if n != 1:
            raise SystemExit(f"semantic_search block not found / replaced (n={n})")
    return text


def main() -> int:
    for opt, cfg in OPTIONS.items():
        out = BASE_DIR / f"config-{opt}.yaml"
        text = build_config(opt, cfg)
        out.write_text(text, encoding="utf-8")
        print(f"[build] wrote {out.name} — {cfg['label']}")
    print(f"\n[build] All 5 configs ready under {BASE_DIR}")
    print(f"[build] Deploy any one via:")
    print(f"  scp -P 22222 -o StrictHostKeyChecking=no \\")
    print(f"    {BASE_DIR}/config-<X>.yaml \\")
    print(f"    root@homeassistant.local:/addon_configs/ccab4aaf_frigate-fa/config.yaml")
    print(f"  ssh -p 22222 -o StrictHostKeyChecking=no root@homeassistant.local \\")
    print(f"    'ha addons restart ccab4aaf_frigate-fa'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
