---
title: Home Assistant endpoint for confirmed cross-home light control
target: backend
type: added
---

The Home Agent Edge component can expose a light-only endpoint for the
upcoming cross-home lighting feature. It accepts signed, short-lived requests
for allowlisted lights only (on, off or brightness), records each operation
before acting, and never sends the same request twice. It stays off unless a
home configures it.
