---
title: Lit view reads naturally with the lights off (WebGPU preview)
target: web
type: changed
---

With the WebGPU renderer preview (`?renderer=webgpu`), the Apartment Lit view now renders the lit room scene-referred and applies a fixed design-night exposure with partial night adaptation, a mesopic blue shift in dim areas and an AgX tone curve. Turning lights down or off now looks like a room at night instead of going black: a single dim spot keeps its warm pool, and a moonlit room stays legible. The dollhouse cut also works in Lit there. The undisclosed night fill is replaced by a disclosed, assumed night ambient of about 0.2 lx (moonlight or street light). The tooltip says that night adaptation is presentation, not lux. The default WebGL renderer keeps its current Lit look. The label still reads "Simulated · not photometric · assumed output".
