---
title: Lit lights fade and dim like the fixtures
target: web
type: changed
---

In the Apartment Lit view, lights now fade toward a new level over about half a second, matching the fixtures' own 160 ms fade, instead of jumping. Brightness follows an assumed perceptual dimming curve rather than a straight line, so half brightness gives a little under a fifth of full output. Colour temperature now sets only the colour, at constant luminance. All output is still assumed: the view keeps its "Simulated · not photometric · assumed output" label and SPEC's lumen ratings.
