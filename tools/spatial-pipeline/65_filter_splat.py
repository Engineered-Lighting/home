#!/usr/bin/env python3
"""65_filter_splat.py â€” post-filter the baked apartment splat PLY.

Removes the user-circled photo-mode overflow:
- keeps only splats inside the command-center room-union (same CORE_BOXES as
  30_make_pointcloud.py) + z in [-0.25, 2.05]
- drops GIANT gaussians (exp(max scale) > 0.30 m) â€” the blurry halo beyond
  the walls is made of huge low-detail blobs at scan boundaries

Operates on binary_little_endian f4 PLYs written by SplatTransform.
"""
from __future__ import annotations

import numpy as np

from pipeline_util import APP_DATA

CORE_BOXES = [
    {"x": (2.70, 6.85), "y": (0.40, 6.55)},    # kitchen + dining (west nub trimmed)
    {"x": (6.85, 7.35), "y": (0.40, 6.15)},    # hallway strip (chimney blob cut)
    {"x": (7.35, 14.45), "y": (0.50, 6.35)},   # living room + east alcove
    {"x": (10.85, 13.95), "y": (6.35, 7.95)},  # OFFICE (northeast) - kept
]
SCALE_MAX_M = 0.30
OPACITY_MIN = 0.30   # the fuzzy halo is low-alpha mist, not giant gaussians

path = APP_DATA / "apartment.ply"
with open(path, "rb") as f:
    header = b""
    while not header.endswith(b"end_header\n"):
        header += f.readline()
    lines = header.decode().splitlines()
    n = next(int(l.split()[-1]) for l in lines if l.startswith("element vertex"))
    props = [l.split()[-1] for l in lines if l.startswith("property")]
    body = np.fromfile(f, dtype="<f4", count=n * len(props)).reshape(n, len(props))

i = {p: k for k, p in enumerate(props)}
x, y, z = body[:, i["x"]], body[:, i["y"]], body[:, i["z"]]
scales = np.exp(body[:, [i["scale_0"], i["scale_1"], i["scale_2"]]]).max(axis=1)

m = np.zeros(n, bool)
for b in CORE_BOXES:
    m |= ((x >= b["x"][0]) & (x <= b["x"][1]) & (y >= b["y"][0]) & (y <= b["y"][1]))
m &= (z >= -0.25) & (z <= 2.05)
big = scales > SCALE_MAX_M
opacity = 1.0 / (1.0 + np.exp(-body[:, i["opacity"]]))
mist = opacity < OPACITY_MIN
print(f"in-core: {int(m.sum()):,}/{n:,} | giants: {int((m & big).sum()):,} "
      f"| mist (<{OPACITY_MIN} alpha): {int((m & mist).sum()):,}")
m &= ~big & ~mist

out = body[m]
new_header = header.decode().replace(f"element vertex {n}", f"element vertex {len(out)}")
with open(path, "wb") as f:
    f.write(new_header.encode())
    out.astype("<f4").tofile(f)
print(f"filtered splat: {n:,} -> {len(out):,} -> {path} "
      f"({path.stat().st_size/1e6:.1f} MB)")

# verification scatter (top-down) for visual diff against the user's circles
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(12, 7), facecolor="black")
ax.set_facecolor("black")
sel = np.random.default_rng(0).choice(len(out), min(250_000, len(out)), replace=False)
ax.scatter(out[sel, i["x"]], out[sel, i["y"]], s=0.05, c="white", alpha=0.35, linewidths=0)
ax.set_aspect("equal"); ax.axis("off")
fig.savefig(APP_DATA.parent / "splat_topdown_check.png", dpi=110, bbox_inches="tight")
print(f"check render -> {APP_DATA.parent / 'splat_topdown_check.png'}")
