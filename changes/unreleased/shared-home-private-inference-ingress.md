---
title: Add private certificate-authenticated inference ingress
target: backend
type: added
---

Add a bounded private TLS listener with registered client-certificate identities
and absolute upload deadlines. Add an explicit dispatch service loop for accepted
snapshot work, preserving the shared queue, admission gates and outstanding
reservations during shutdown. No listener or workload is enabled automatically.
