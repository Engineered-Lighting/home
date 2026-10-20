---
title: Accept vLLM 0.20.2 null response metadata for snapshots
target: backend
type: fixed
---

Fix snapshot response parsing against the installed vLLM server, which includes
null token, audio and annotation fields. Non-null unsupported content remains
rejected. A captured response from synthetic imagery covers the regression.
