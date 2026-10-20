---
title: Opt-in simulated relighting of Photo and Cloud in the Apartment Lit view
target: web
type: added
---

With the opt-in `?lit-splats=1` page flag, Lit can relight Photo (the splats) and Cloud (the point cloud) instead of switching to Mesh. The view removes the scan's daylight with the de-lit lighting layer stored beside the content pack, then adds the fixtures' assumed output (spot 450, down 350, radial 120 lm). Dimming, colour temperature and spot aim all follow live, and the view uses the night exposure and tone. Lit loads the layer only when its SHA-256 binding matches the active pack, and it never changes pack bytes. The view is labelled simulated and is never measured. Without the flag, Lit stays Mesh-only and unchanged.
