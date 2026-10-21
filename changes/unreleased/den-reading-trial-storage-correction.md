---
title: Correct Den reading diagnostic trial storage admission
target: internal
type: fixed
---

The private bounded Den reading trial now gives its disposable RAM store an
explicit free-space reserve and verifies that its entire recording quota fits
before activation. Deployment verification waits briefly for the existing
service startup without retrying a restart. Normal recording storage and host
admission limits are unchanged; live reading acceptance remains outstanding.
