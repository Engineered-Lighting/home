---
title: Home parser for explicit cross-home lighting commands
target: internal
type: added
---

Home gains a deterministic parser that turns commands such as "turn off the
kitchen light in Victoria" or "dim the lights to 30% in both homes" into a
typed lighting request, and asks which home or which lights instead of
guessing. It is not wired into chat yet.
