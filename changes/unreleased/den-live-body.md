---
title: Add the live body to the live den view, for signed-in Home
target: internal
type: added
---

den-live now makes the tracked person's body from the camera's depth, and Home draws it in the apartment view in the Recording look with its labels. Signed-in Home only: look-only tailnet devices still see markers only.
- `body_worker.py` runs `body.py` in the detector's venv, as a second process with no network that writes no file. It learns the empty den in memory, once nobody has been tracked for 8 s. It keeps what's nearer than the empty room (12 cm, or 4 cm inside a limb the pose places), near the person, and adds the back, guessed limbs and what the person holds. Each frame goes out as `den-live-body-1`.
- `body_link.py` feeds it from the loop without waiting, at most about 3 body frames a second (about 75 KB each for a reader). A busy body skips frames, and a body process that stops or won't start turns only the body off: the markers go on.
- The stream serves body frames on their own path, `/api/den-live/v1/homes/den/body`. The gateway passes it to signed-in Home and never to a look-only device.
- `home-den-live-view.mjs` mounts the body layer and its labels beside the markers, on the scan the camera is registered to. The local live page does the same.
- Tests: `tools/den-live/test_body.py` (synthetic depth, in the detector's venv), `test_body_link.py`, and additions to `test_live.py` and `run-web-gateway-look-only-tests.mjs`.
