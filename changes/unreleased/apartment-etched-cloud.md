---
title: Apartment Cloud view drawn as an etching
target: desktop
type: changed
---

The Apartment's Cloud view now draws the scan the way the engineered.lighting site does: fine points lit by
edges and a steep contrast curve on black, so furniture, frames and seams stand out and plain walls fall away,
and near walls hide the rooms behind them. It applies on desktop WebGL once the scan has loaded; phones and the
WebGPU renderer keep the classic cloud. Set localStorage `apartment3d.pointLook` to `classic` to keep the
classic cloud in a browser.
