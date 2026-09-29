---
title: The Los Angeles assistant's Victoria guard is part of the integration LA runs
target: backend
type: fixed
---

The cross-home guard that answers "turn off the kitchen light in Victoria" and
other actions on Victoria or both homes with a fixed reply pointing to the Home
chat was only in the reviewed reference copy, so LA Home Assistant never ran
it. It is now hooked into the live conversation agent, ahead of the camera
preroute and the model, and a test checks that order. It takes effect on LA
after the next reviewed deploy and restart.
