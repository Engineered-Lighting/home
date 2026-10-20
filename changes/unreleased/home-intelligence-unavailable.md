---
title: Show Intelligence service failures instead of empty history
target: web
type: fixed
---

Intelligence now reports an unavailable observation service and keeps its last successful view. Refresh retries the request instead of silently replacing history with an empty list.
