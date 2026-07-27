---
title: Natural visual questions use grounded look
target: web
type: added
---

Broad visual questions like "what do you see in my apartment" now run the
grounded look pipeline automatically instead of falling back to cached chat.
The routing is covered by adversarial scenario tests for false positives,
fallbacks, and duplicate-free chat output.
