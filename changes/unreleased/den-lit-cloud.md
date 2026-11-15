---
title: Lighting keeps the point cloud in Cloud view
target: web
type: fixed
---

On a desktop, turning Lighting on in Cloud view made the room's point cloud disappear, leaving only the surfaces, the fixtures and the live figure. Lighting draws the relit cloud in a pass of its own, which it marks on the cloud. Cloud's x-ray look reset that marking every frame while it stood aside, so the relit cloud was drawn in no pass at all. The x-ray look now leaves Lighting's marking alone, so the cloud shows relit by the virtual lights, and the look comes back when Lighting goes off. Phones, which don't use the x-ray look, were not affected.
