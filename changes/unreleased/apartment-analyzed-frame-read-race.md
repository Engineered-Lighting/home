---
title: Keep analyzed camera frames available through their original lifetime
target: backend
type: fixed
---

Keep each unexpired Apartment viewing frame in bounded RAM when a newer analysis completes, so its descriptor and image range reads cannot race with replacement. Preserve the original image expiry, session limit and eight-frame / 8 MiB bounds.
