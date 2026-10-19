---
title: Victoria den virtual fixtures follow the lighting brain
target: web
type: added
---

Apartment's Victoria view now follows the central den lighting brain through the web gateway (`/api/lighting-brain/v1/homes/victoria/`). The four virtual den fixtures take the brain's ambient and spotlight states, including fades, CCT and the spot's room-frame target. A compact "why" line, the brain's online/offline status, an expandable log of its last 20 decisions and an Auto/Paused marker for each fixture explain every change.

A manual change, aim, undo or redo pauses only that fixture and sends the brain an override. The fixture stays paused until the brain clears it, or until the owner chooses **Return to Auto**. If the brain stops answering, Home shows "brain offline" and keeps the last state without applying anything new. It reconnects with backoff.

The owner can mark the sofa once, with the same selector used for the desk target. The mark is saved privately beside the desk target, and `tools/den-room/brain-targets.mjs` writes the one private `lighting-brain-geometry-1` file the brain reads: the four fixtures from the equipment registration, the camera position, and the sofa and desk zones.

The fixed-desk aim preview in the Den perception panel is retired in favour of the brain. Virtual fixtures only; physical output stays disabled.
