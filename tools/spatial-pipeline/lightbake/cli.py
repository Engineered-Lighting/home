"""Shared command line for steps 90 and 91 (workstation only; never on the servers)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import binding as BND

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]


def parser(step):
    p = argparse.ArgumentParser(description=f"L3 lighting layer, step {step}. Reads the accepted pack read-only "
                                            "and writes a private layer beside it (<pack>.lighting/).")
    site = Path(os.environ.get("HOME_CONTENT_PACKS_DIR", REPO / "app" / "data" / "home-content")) / "victoria"
    p.add_argument("--site-dir", type=Path, default=site, help="content-pack site folder holding ACTIVE")
    p.add_argument("--pack", type=Path, help="explicit pack folder (default: the ACTIVE pack)")
    p.add_argument("--out", type=Path, help="layer folder (default: <pack>.lighting beside the pack)")
    p.add_argument("--equipment", type=Path, required=True,
                   help="equipment registration JSON bound to the same pack (fixture mounts, walls)")
    p.add_argument("--device", default=None, help="warp device (default cuda:0, else cpu)")
    p.add_argument("--ray-eps", type=float, default=0.01, help="ray origin offset along the normal (m)")
    p.add_argument("--oidn-dir", default=None, help="folder holding an OpenImageDenoise 2.x library")
    if step == 90:
        p.add_argument("--capture-spp", type=int, default=256)
        p.add_argument("--capture-spp-mesh", type=int, default=128)
        p.add_argument("--mesh-albedo-size", type=int, default=2048)
        p.add_argument("--eps-rel", type=float, default=0.02, help="E_cap floor as a fraction of the median")
        p.add_argument("--rho-max", type=float, default=0.95, help="albedo clamp")
        p.add_argument("--surface-tol", type=float, default=0.05, help="receiver-to-mesh distance for on-surface (m)")
        p.add_argument("--window-cell", type=float, default=0.05, help="aperture grid cell (m)")
        # capture-state questions Q1-Q4: unanswered, conservative defaults
        p.add_argument("--room-lamps", type=Path, help="Q1: JSON list of {position, intensity_rgb, label} lamps that "
                                                       "were on at capture (default: none / unknown-off)")
        p.add_argument("--blinds", default="unknown", choices=["unknown", "open", "half", "closed"], help="Q1 (recorded)")
        p.add_argument("--sky", default="unknown", choices=["unknown", "overcast", "sun"], help="Q3 (recorded)")
        p.add_argument("--glass-door-wall", default="unknown", help="Q4 (recorded)")
        p.add_argument("--capture-time", default=None, help="Q2 (recorded), e.g. local time of capture")
        p.add_argument("--wall-bearings", default=None,
                       help="sensitivity only: 'wall-id=true-north outward bearing,...'; the first is the reference")
        p.add_argument("--sun", default=None,
                       help="sensitivity only: 'label=azimuth/elevation,...' in degrees (needs --wall-bearings)")
        p.add_argument("--window-gain", type=float, default=1.0, help="multiplier on exterior-splat window radiance")
    else:
        p.add_argument("--spp", type=int, default=64, help="paths per splat/point receiver")
        p.add_argument("--mesh-spp", type=int, default=128, help="paths per lightmap texel")
        p.add_argument("--bounces", type=int, default=5)
        p.add_argument("--lightmap-size", type=int, default=1024)
        p.add_argument("--emitter-drop", type=float, default=0.14, help="emitter below the mount anchor (m, assumed)")
        p.add_argument("--keep-work", action="store_true", help="keep <layer>/_work intermediates")
    return p


def parse(step, argv=None):
    a = parser(step).parse_args(argv)
    a.pack = a.pack or BND.resolve_active_pack(a.site_dir)
    return a
