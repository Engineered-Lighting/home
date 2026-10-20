---
title: Prepare authenticated Den observations for the Apartment view
target: backend
type: added
---

A bounded gateway adapter connects existing Victoria Lab observation reads to
an authenticated Home session using a private read-only grant and verified TLS.
It refuses recording/model writes, arbitrary upstreams, stale session access,
redirects and oversized responses. The browser distinguishes read-only access
from upstream recording readiness. Live activation still requires reviewed
private configuration and deployment verification.
