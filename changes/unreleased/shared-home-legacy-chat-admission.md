---
title: Admit legacy LA chat through the shared inference queue
target: backend
type: added
---

Add optional bounded OpenAI chat and streaming compatibility using the shared
durable admission queue. Bind per-consumer request policies and deadlines into
the resource manifest, preserve streamed tool-call data, and retain reservations
when completion cannot be independently verified. Production routes remain
unchanged and require validated authorities and deployment gates.
