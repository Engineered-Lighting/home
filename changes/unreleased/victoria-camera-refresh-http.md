---
title: Add a disabled camera refresh HTTP boundary
target: backend
type: added
---

Add explicit Victoria den refresh and status routes requiring a dedicated gateway
subject, camera_refresh capability and server-derived authentication namespace.
Bound request bodies, concurrency and polling; reuse runtime idempotency receipts.
Routes remain unavailable without an explicitly injected commissioned backend.
The Home web gateway allowlists these routes under a separate camera_refresh
grant, derives opaque request ownership from authenticated server context, and
rejects mismatched receipts or revoked context before delivering results.
Add typed browser refresh/status operations and a bounded mailbox that executes
HTTP requests on the existing Victoria runtime thread. Cancelled unsent requests
do not dispatch; closing the runtime rejects pending ingress.
Home camera chat now submits one refresh, polls that request, and accepts only
its matching fresh caption. Failed or uncertain requests do not fall back to a
previously saved observation or automatically submit another inference request.
Publish admitted runtime observations atomically in the existing Home caption
format, preserving request ID, observation time and absolute expiry; reject
wrong-source, wrong-model and late results before replacing the saved caption.
Add an explicit-request backend using the existing capture, gateway and submission
journal contracts without requiring occupancy/MQTT. It allows one active request,
retains uncertain submissions, and never re-enqueues duplicate request IDs.
Add explicitly provisioned interactive capability to the gateway client and
drain refresh work during Lab shutdown before releasing service storage.
Recover uncertain requests by querying their original gateway ID; terminal
recovery closes accounting without replaying inference or republishing old views.
Create and close refresh journal resources on the ASGI lifespan thread to avoid
cross-thread SQLite access during deployed requests and shutdown.
Provide an explicit refresh application factory preserving Lab capture/preview
behavior, with fixed mutual-TLS gateway credentials and private journal/caption
paths. It does not enable inference without the provisioned gateway service.
Preserve the site-wide refresh cooldown in the durable journal so restarting the
service cannot bypass request limits.
