---
title: Lighting renders one HDR frame with visible fixtures and camera glare
target: web
type: changed
---

With Lighting on, Photo, Cloud and Mesh now render into one high-dynamic-range frame, and the camera look (exposure for the highlights, white balance toward the fixtures, contrast) is applied once per pixel. Mesh now gets the camera look too. Each lit fixture shows its emitting face at the brightness its assumed output implies, and bright sources glow with an energy-conserving camera glare. A faint, still sensor grain and dither keep dark gradients from banding. Glare, the fixture faces and the grain are presentation only: they never change the modeled lux or the linear readback, and the view still says "Simulated · not photometric · assumed output". With Lighting off nothing changes.

Turning Lighting off and on preserves other render instrumentation. Failed HDR proofs and frame-cost measurements restore their caller's render target.

Adaptation diagnostics retain the renderer's original sample clock, so timing checks account for frames that take longer to render.
