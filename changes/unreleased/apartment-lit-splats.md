---
title: Simulated relighting of Photo and Cloud in the Apartment
target: web
type: added
---

Simulated lighting now relights Photo (the splats) and Cloud (the point cloud), not just the mesh. It removes the scan's daylight using the de-lit lighting layer stored beside the content pack, then adds the fixtures' assumed output (spot 450, down 350, radial 120 lm). Dimming, colour temperature and spot aim follow live, with the night exposure and tone. The layer loads only when its SHA-256 binding matches the active pack, and pack bytes are never changed. The view is labelled simulated and is never measured.
