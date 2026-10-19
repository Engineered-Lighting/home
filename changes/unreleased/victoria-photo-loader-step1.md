---
title: Show Photo without waiting for the textured floor mesh
target: desktop
type: changed
---

The apartment Photo view now appears as soon as its splat decodes, with the point cloud backing gaps until the scan floor arrives, and the large Photo and mesh preloads wait for idle time instead of competing with the visible view. Mesh textures also use the GPU's full anisotropic filtering for sharper floors and walls.
