---
title: Add the live body to the live den view, for signed-in Home
target: internal
type: added
---

den-live now makes the tracked person's body from the camera's depth, and Home draws it in the apartment view in the Recording look with its labels. Signed-in Home only: look-only tailnet devices still see markers only.
- `body.py` runs in the detector's own process (`detect_worker.py`), on the frame it just detected, so a frame crosses the pipe once and numpy loads once. It writes no file. About 16 ms a frame here. It learns the empty den in memory, once nobody has been tracked for 8 s. It keeps what's nearer than the empty room (12 cm, or 4 cm inside a limb the pose places), near the person, and adds the back, guessed limbs and what the person holds. When the depth shows no body but the pose's torso is there (thin depth at night, or before the empty room is learned), it draws the figure from the joints alone, every point a guess. Each frame goes out as `den-live-body-1`.
- `body_link.py` asks for it after tracking without waiting, at most about 3 body frames a second (about 75 KB each for a reader), and never for a frame the detector is late on. A busy body skips frames, and a body that won't set up turns only the body off: the markers go on.
- The stream serves body frames on their own path, `/api/den-live/v1/homes/den/body`. The gateway passes it to signed-in Home and never to a look-only device.
- `home-den-live-view.mjs` mounts the body layer and its labels beside the markers, on the scan the camera is registered to. The local live page does the same.
- Tests: `tools/den-live/test_body.py` (synthetic depth, in the detector's venv), `test_body_link.py` (including the real perceiver and body step on the den simulation's renders), and additions to `test_live.py` and `run-web-gateway-look-only-tests.mjs`.
