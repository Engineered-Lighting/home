---
title: Remove the default web gateway login screen
target: web
type: changed
---

The Tailscale web gateway now opens the Home app directly by default and only
enables the native gateway login when `HOME_WEB_AUTH_REQUIRED=1` is set.
