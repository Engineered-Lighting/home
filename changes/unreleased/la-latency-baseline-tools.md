---
title: Workstation tools for the Victoria to LA latency baseline
target: internal
type: added
---

`tools/la-latency/harvest.py` turns exported LA pilot journal lines, vLLM metric dumps, a read-only Lab ledger snapshot, camera-chat browser marks, published captions and the `la-fact-log-1` log into one latency-sample format. `report.py` uses those samples and `slo.json` to write the baseline table and the S2.1 decision record.
