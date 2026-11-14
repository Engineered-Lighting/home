---
title: den-live asks Qwen what a seated person in the den is doing
target: backend
type: added
---

den-live on the LA AI host can ask the local Qwen what someone in the den is doing. It asks only while Frigate's den occupancy sensor reads on and den-live sees the person seated, at most every 2 s and 30 a minute. Each answer goes on den-live's stream for Home, into a words-only log with every hop's time, and to the coordinator's side. The ask is set up by den-live's install from the den home's Home Assistant token and address, and is off without them. `ask_check.py` checks it from the workstation.
