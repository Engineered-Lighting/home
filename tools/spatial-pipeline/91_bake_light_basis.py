#!/usr/bin/env python3
"""91_bake_light_basis.py - L3 step 2: path-trace the 29 unit bases and write the layer.

4 downlights + 24 radials + 1 window/ambient column, traced over the scan BVH
with the de-lit albedo (NVIDIA Warp on the RTX 4090), OIDN-denoised mesh
lightmaps, the 40 B/receiver encoding for splats and points, and
lighting-manifest.json bound by SHA-256 to the accepted pack.

  python 91_bake_light_basis.py --equipment <equipment-registration.json>

Private Den data: never commit, upload or paste the output.
"""
from lightbake import bake, cli

if __name__ == "__main__":
    bake.run(cli.parse(91))
