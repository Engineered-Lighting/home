---
title: Add the live body's Recording look and labels to the apartment view
target: internal
type: added
---

Adds the layers that draw a live person's body in the apartment view, in the look the owner approved ("Recording"), with its labels. Nothing in Home mounts them yet: they wait for a body stream, and until then the local body prototype feeds them.
- `apartment-body-frame.mjs` is the frame contract (`den-live-body-1`): one person's points in the apartment frame, with each point's provenance (measured, back fill, guessed, remembered, held object) and the pose joints. It interpolates between frames by depth-pixel key.
- `apartment-body-layer.mjs` draws the Recording look: warm gold particles with bands, flicker and two trailing frames, a soft glow, motes and a faint floor pool. Guessed limbs are fainter, and the head is drawn thinner. What the person holds is steady points in Home's accent blue. Zoomed out, points grow at most 1.25x and the glow eases off, so the person stays crisp.
- `apartment-body-labels.mjs` and `apartment-body-annotation-model.mjs` draw the System labels in Home's type: a caption that says less zoomed out, a floor ring showing which way the head faces, a tag for a held object, and a glass card on tap. An unknown person is "Someone". Activity changes wait 2 s, the object tag holds 1.5 s, the caption keeps its side, and the head direction is steadied.
- On the WebGPU renderer (`?renderer=webgpu`), which can't run the layer's GLSL, `apartment-body-webgpu.mjs` draws the same particles and held object as instanced sprites built from TSL nodes. The glow, trails, motes and floor pool are the WebGL path's only.
- None of them has network, identity, lighting or model authority. Tests: `tools/test-apartment-body-frame.mjs`, `tools/test-apartment-body-annotation-model.mjs`, `tools/test-apartment-body-layer.mjs` and `tools/test-apartment-body-webgpu.mjs`; the WebGPU ports test lists the layer with its twin.
