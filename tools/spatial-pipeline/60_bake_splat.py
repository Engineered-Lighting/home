#!/usr/bin/env python3
"""60_bake_splat.py — bake T_splat into the splat file itself.

Spark ignores SplatMesh object transforms for already-accumulated splats (the
render is byte-identical with and without them — verified), so the runtime
transform path is a dead end. Instead: apply the scan->apartment registration
with the SplatTransform CLI (which also rotates spherical harmonics) and ship
the BAKED apartment.spz.

SplatTransform's Euler convention is undocumented; this script determines it
EMPIRICALLY: it runs each candidate convention on a decimated copy, reads the
output positions, and compares against apply_T(T_splat, means). The matching
convention is then used for the full bake. frame.json gets splat_baked: true
so the app skips applyRegistration.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from pipeline_util import APP_DATA, MODEL, OUT, RAW, apply_T, read_3dgs_ply

HERE = Path(__file__).resolve().parent
ST = HERE / "node_modules" / ".bin" / ("splat-transform.cmd" if sys.platform == "win32" else "splat-transform")

reg = json.loads((MODEL / "registration.json").read_text())
T = np.array(reg["T_splat"])
R, t = T[:3, :3], T[:3, 3]

src = RAW / "scan_splat.ply"
pos_raw, _, alpha = read_3dgs_ply(src)
sample_idx = np.random.default_rng(0).choice(len(pos_raw), 2000, replace=False)
expected = apply_T(T, pos_raw[sample_idx])

# small decimated probe file for convention detection
probe = OUT / "_probe.ply"
subprocess.run([str(ST), "-w", str(src), "-F", "8000", str(probe)],
               check=True, capture_output=True)
probe_pos, _, _ = read_3dgs_ply(probe)
probe_expected = apply_T(T, probe_pos)

# SplatTransform works in an X/Y-negated frame (empirically: -t (a,b,c) moves
# by (-a,-b,c); -r flips X/Y rotation signs). Conjugate by F = diag(-1,-1,1).
F = np.diag([-1.0, -1.0, 1.0])
R_cli = F @ R @ F
t_cli = F @ t

best = None
for i, conv in enumerate(["xyz", "XYZ", "zyx", "ZYX", "yxz", "YXZ", "zxy", "ZXY"]):
    e = Rotation.from_matrix(R_cli).as_euler(conv, degrees=True)
    out = OUT / f"_probe_c{i}.ply"   # unique names: Windows is case-insensitive
    r = subprocess.run(
        [str(ST), "-w", str(probe),
         "-r", f"{e[0]},{e[1]},{e[2]}", "-t", f"{t_cli[0]},{t_cli[1]},{t_cli[2]}",
         str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  {conv}: CLI failed: {r.stderr[-120:]}")
        continue
    got, _, _ = read_3dgs_ply(out)
    if len(got) != len(probe_pos):
        print(f"  {conv}: count mismatch")
        continue
    err = float(np.median(np.linalg.norm(got - probe_expected, axis=1)))
    print(f"  convention {conv}: median err {err*100:.2f} cm")
    if best is None or err < best[1]:
        best = (conv, err)

conv, err = best
if err > 0.02:
    sys.exit(f"NO convention matched (best {conv} at {err*100:.1f} cm) — aborting")
print(f"convention {conv} matched ({err*1000:.1f} mm) — baking full splat")

e = Rotation.from_matrix(R_cli).as_euler(conv, degrees=True)
out_spz = APP_DATA / "apartment.spz"
r = subprocess.run(
    [str(ST), "-w", str(src),
     "-r", f"{e[0]},{e[1]},{e[2]}", "-t", f"{t_cli[0]},{t_cli[1]},{t_cli[2]}",
     "--filter-floaters", str(out_spz)],
    capture_output=True, text=True)
if r.returncode != 0:
    # floaters filter may not be valid here; retry without it
    r = subprocess.run(
        [str(ST), "-w", str(src),
         "-r", f"{e[0]},{e[1]},{e[2]}", "-t", f"{t_cli[0]},{t_cli[1]},{t_cli[2]}",
         str(out_spz)], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"bake failed: {r.stderr[-300:]}")

reg["splat_baked"] = True
(MODEL / "registration.json").write_text(json.dumps(reg, indent=2))
for d in (APP_DATA, HERE.parent.parent / "app" / "src" / "assets" / "apartment"):
    f = d / "frame.json"
    if f.parent.exists():
        fr = json.loads(f.read_text()) if f.is_file() else {}
        fr.update(reg)
        f.write_text(json.dumps(fr, indent=2))
print(f"baked -> {out_spz} ({out_spz.stat().st_size/1e6:.1f} MB), frame.json marked splat_baked")
