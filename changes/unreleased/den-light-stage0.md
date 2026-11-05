---
title: Den light model Stage 0 camera check (rung 8)
target: internal
type: added
---

`tools/den-light` adds rung 8's Stage 0: a written method (`METHOD-V1.md`), a
metering build of the den camera server that holds exposure, gain and white
balance through an authenticated `POST /meter` and installs beside the script
perception-lab pins (a systemd drop-in, never an overwrite), and a PC tool that
steps exposure for about a minute and checks that one inverse response fits the
whole view. Nothing is installed or run until the owner approves; frames stay
on the PC.
