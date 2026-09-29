---
title: Home works out which home a light command is for
target: web
type: added
---

Light commands in the Home chat that name no home now go to the home you are
in. Home checks the network your device is on, then the zone your phone is in
(from the Home Assistant app), and otherwise uses the last home a light
command changed. It never asks. When it picks Victoria, the reply says why.
The Home selector next to the chat now offers Auto (the default), Los Angeles
or Victoria, and shows where Home thinks you are. Naming a home still always
wins.

Deploy: set `HOME_WEB_SITE_LANS`, `HOME_WEB_LOCATION_PERSON` and
`HOME_WEB_SITE_ZONES` in the gateway env, add a `Victoria` zone in LA Home
Assistant, restart the gateway, then add `--proxy-protocol=1` to the port 443
Tailscale Serve handler (see `docs/TAILSCALE-WEB.md`).
