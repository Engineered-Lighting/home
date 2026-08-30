---
title: Open the bench commissioning console from the header
target: web
type: added
---

A gimbal chip in the header opens the bench commissioning console when one is
running on the machine you are looking at. It is a link out, not an embed: the
console binds 127.0.0.1 by design, because full control of a machine that can
move belongs to whoever is standing at its port, and an embedded copy would be
a live hub client whenever Home is running — permanently satisfying the
dead-man that exists to disarm a board nobody is watching.

The chip polls the console's `/healthz` every 15 s and checks the service name
rather than settling for a 200, so something else holding that port cannot
light it. Over HTTPS on the tailnet the browser blocks that probe as mixed
content before it leaves the page, which is not the same fact as "no console
here" — in that one case the chip still appears and says in its tooltip that
it could not be verified.
