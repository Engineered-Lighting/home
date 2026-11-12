---
title: den-live keeps the den's labels and serves them to Home
target: internal
type: added
---

den-live now keeps the den's labels (the `den-room-labels-1` document of named boxes on the den's scan) and serves them beside the people stream. `GET` and `PUT /api/den-live/v1/homes/den/labels` read and save the whole document, as a compare-and-swap on its revision. On this PC they're kept in the private den-live folder; the LA service keeps them in its own state folder. `run_local.py --labels-only` starts only the labels and the local Home page, so the den can be labelled with no camera, detector or registration. `room_labels.line_for()` builds the line the Qwen ask will carry about a seated person, for example "Seated at the desk, beside the 3D printer.". It's pure, so a replay gives the same text as a live ask. Each track in the people stream (`den-live-people-1`) now also carries `near`: what's near it from the confirmed labels, for example "at the desk, beside the 3D printer". It's null with no labels, none near, or labels placed on another scan revision, and Home shows it under the marker. Tests: `tools/den-live/test_room_labels.py`, and the `near` case in `test_live.py`.
