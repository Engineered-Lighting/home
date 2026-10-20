---
title: LA camera pilot keeps mutual-TLS connections open, and camera chat connects while it captures
target: backend
type: changed
---

The LA camera pilot's private ingress now keeps each mutual-TLS connection open over HTTP/1.1 for up to 100 requests or 20 s idle, with 8 connection slots, TCP_NODELAY and no TLS session resumption; ingress_requests_per_connection 1 in private/pilot-runtime.json restores one request per connection. Jetson camera chat opens its TCP and TLS connection while the frame is captured and still sends exactly one request on it, closing it unused if the capture fails.
