---
title: Perception Lab names an LA system-log scan that is still catching up
target: desktop
type: added
---

If a comparison can't start because the LA worker's kernel-fault scan ran out of time (`preflight_kernel_scan_incomplete`), the Perception Lab now says "a health check on the LA computer is still catching up on its system log". Before, it showed the raw reason code as words. A real fault still reads "flagged a recent system error".
