"""Render the FULL mesh footprint with grid + current crop boxes overlaid."""
import numpy as np
import trimesh
import cv2

from pipeline_util import OUT

mesh = trimesh.load(OUT / "apartment_mesh_debug.glb", force="mesh")
pts, _ = trimesh.sample.sample_surface(mesh, 600_000)
pts = pts[(pts[:, 2] > -0.3) & (pts[:, 2] < 2.6)]

PX = 80
mn = pts[:, :2].min(0) - 0.4
mx = pts[:, :2].max(0) + 0.4
size = np.ceil((mx - mn) * PX).astype(int)
img = np.zeros((size[1], size[0]), np.float32)
ij = ((pts[:, :2] - mn) * PX).astype(int)
np.add.at(img, (ij[:, 1], ij[:, 0]), 1.0)
img = np.clip(img / np.percentile(img[img > 0], 96), 0, 1)
out = cv2.cvtColor((np.flipud(img) * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)

def to_px(x, y):
    return (int((x - mn[0]) * PX), out.shape[0] - 1 - int((y - mn[1]) * PX))

# 1 m grid + labels
for gx in np.arange(np.ceil(mn[0]), mx[0], 1.0):
    p = to_px(gx, mn[1]); q = to_px(gx, mx[1])
    cv2.line(out, p, q, (50, 50, 50), 1)
    cv2.putText(out, f"{int(gx)}", (p[0] + 2, out.shape[0] - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)
for gy in np.arange(np.ceil(mn[1]), mx[1], 1.0):
    p = to_px(mn[0], gy); q = to_px(mx[0], gy)
    cv2.line(out, p, q, (50, 50, 50), 1)
    cv2.putText(out, f"{int(gy)}", (4, p[1] - 3),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)

# current crop boxes in red
BOXES = [((2.55, 0.40), (7.35, 6.60)), ((7.35, 0.50), (15.30, 6.35))]
for (x0, y0), (x1, y1) in BOXES:
    cv2.rectangle(out, to_px(x0, y0), to_px(x1, y1), (0, 0, 255), 2)

cv2.imwrite(str(OUT / "full_extent_map.png"), out)
print(f"extent: x {mn[0]:.2f}..{mx[0]:.2f}, y {mn[1]:.2f}..{mx[1]:.2f}")
print(f"wrote {OUT / 'full_extent_map.png'} ({out.shape[1]}x{out.shape[0]})")
