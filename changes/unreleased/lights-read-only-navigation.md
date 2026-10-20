---
title: Viewing Lights preserves manual overrides and reports unconfirmed Travel Mode honestly
target: web
type: fixed
---

Opening Lights no longer clears Home Assistant manual-override cooldowns. The periodic tuning maintenance starts after an explicit tuning edit and stops when the drawer closes.

Travel Mode requires matching Home Assistant readback before reporting confirmation. Until then, its local safety lock stays distinct from the upstream state. The primary Travel Mode control has a 44-pixel minimum target.
