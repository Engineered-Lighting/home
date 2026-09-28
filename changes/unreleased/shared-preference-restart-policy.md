---
title: Shared-preference services restart automatically
target: backend
type: changed
---

The five live shared-preference and account-linking services now restart
automatically after a crash or host reboot (`unless-stopped`) instead of
staying down until an operator starts them. The unused Victoria preference
service stays staged.
