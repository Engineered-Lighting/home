#!/usr/bin/env python3
"""90_delight_layer.py - L3 step 1: de-light the accepted pack into an additive layer.

Workstation (RTX 4090) only, never on the servers. Reads the active Den pack
read-only (SHA-256 of every pack file before and after), derives mesh normals,
projects them to splats and points, builds window emitters from the exterior
splats, gathers the capture-state bounce and recovers clamped, flagged albedo.
Output: <pack>.lighting/_work (consumed by 91_bake_light_basis.py).

  python 90_delight_layer.py --equipment <equipment-registration.json>

Private Den data: never commit, upload or paste the output.
"""
from lightbake import cli, delight

if __name__ == "__main__":
    delight.run(cli.parse(90))
