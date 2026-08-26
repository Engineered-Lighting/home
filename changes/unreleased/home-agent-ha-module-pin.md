---
title: Pin the Home Assistant host module verification
target: backend
type: changed
---

Advances the activation source pin to the commit that installs and verifies all
three modules step 20 executes on the Home Assistant host.

`phase3_activation_runner.py` is an activation path, so steps 25, 30 and 32
would refuse the checkout until the pin moves with it. Only the pin advances
here; no activation-path content changes, so the digests the signing bundle is
sealed against are untouched.
