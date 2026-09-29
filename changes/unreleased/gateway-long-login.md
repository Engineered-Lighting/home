---
title: Stay signed in to Home and connect to Home Assistant without a token prompt
target: web
type: changed
---

The Home web login now lasts 400 days instead of ending when the browser
closes. With `HOME_WEB_HA_TOKEN_FILE` set, the gateway holds the owner's
long-lived Home Assistant token and Home connects without asking for it; the
browser only ever sees a placeholder. A plain gateway restart no longer reloads
open tabs, because the asset version now follows the shell files' content.
