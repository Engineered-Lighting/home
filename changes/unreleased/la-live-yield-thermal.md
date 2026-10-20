---
title: LA live scene yields to a busy vLLM and sheds on heat before writing anything
target: backend
type: added
---

The LA live-scene consumer now refuses a request with 429 queue_busy while vLLM has a waiting request or at least 3 running, with the new typed 503 thermal_shed at GPU 77 °C or Tctl 88 °C (all live work stops at 79 °C), and with 503 admission_closed while the health or engine check fails, in every case before any ledger write; a live worker start too slow for one request is kept for the next one, and camera chat keeps its 80 °C guard. Expired live rows are now pruned in bounded batches, live-scene.json accepts the 200,000-row and 2-day sizing, and `python3 -m home_shared.lab_live_prune` prunes a backlog offline while the pilot is stopped.
