---
title: Keep the Home Assistant bridge running through long lighting-brain outages
target: backend
type: fixed
---

The Home Assistant/Frigate bridge no longer crashes when the lighting brain
stays unreachable for hours. Its retry backoff overflowed after about 1,024
consecutive failures (roughly 8.5 hours at the 30 s cap), so the service exited
and restarted in a loop. It now keeps retrying every `retry_max_s` for as long
as the outage lasts and delivers the latest events once the brain is back.
Deploying the fix to the bridge host needs the owner's approval.
