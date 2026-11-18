---
title: Keep the Den figure's stream fresh in Home
target: web
type: fixed
---

Home now drops a backlogged body stream and reconnects when frames keep arriving past their 2 s lease, backing off up to once a minute. Every 5 minutes it also refreshes the stream's clock on a second connection without blanking the figure, so a long-open tab no longer shortens the lease. The 2 s lease and the instant clear are unchanged, and the live view's status now counts accepted, rejected and cleared body frames, reconnects and draw timing.
