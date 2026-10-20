---
title: Victoria's Qwen transport keeps one mTLS connection to LA
target: backend
type: changed
---

The Lab controller's Qwen transport (live and saved analysis) now reuses one connection to LA, reads every response to its end before reuse (needed on the Jetson's Python 3.10), and reconnects when the connection has been idle more than 15 s, its socket is readable, or LA answered with `Connection: close`. Only a failed fresh connect still proves a request was not sent; a failure on a reused connection stays unproven and is recovered by identity lookup, and `keep_alive: false` in the transport file restores one connection per request.
