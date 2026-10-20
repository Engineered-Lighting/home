---
title: Camera views stay in the room, failed views recover in place, and every old screen is still one step away
target: web
type: changed
---

Viewing from an Echo camera now keeps the room toolbar and shows "Viewing from <camera>" with Return to room in one top-centre hint bar, instead of the old camera HUD with its second home picker. Victoria offers no camera view until Den supplies a calibrated camera pose, and About says why. Photo and Mesh show their loading percentage and, if they fail, say so with Retry and Back to Cloud. A saved view that cannot load, or a room that fails to render, offers Retry (and the other home) without switching homes on its own.

Leaving Perception Lab or Metrics now reads "Back", and the light inspector's list button reads "All lights", so "Return to room" only ever leaves a camera view. The Simulation menu item appears while simulation runs, as in the previous Home; `/simulation` and `?simulation=1` start it.
