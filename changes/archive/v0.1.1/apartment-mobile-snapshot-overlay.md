---
title: Stabilize Apartment mobile camera overlays
target: web
type: fixed
---

Apartment camera snap views on mobile now use a cache-busted Frigate snapshot
refresh path for calibrated overlays, preload each snapshot before swapping it
onscreen, and start reachable camera media before slow mesh pose loading can
leave the view black. The feed stays mapped into the same projection frame as
the mesh when calibrated pose data is available.

Apartment mesh mode also avoids the neon normal-material debug fallback on
phones. If the full textured mesh cannot load and the app falls back to the
coarse collision mesh, it now renders as a neutral clay fallback and reports the
mesh source for diagnostics. Mobile screenshot audits now fail if mesh mode
silently uses the coarse fallback during validation.
