---
title: Fix "Start account linking" failing for signed-in Home Agent users
target: backend
type: fixed
---

Starting account linking from the Home Agent panel always failed with "did not
return a verified result" because the fresh Home Assistant identity check was
called without a network client on the standard Echo session path. Linking now
re-checks the signed-in account and proceeds.
