---
title: Preserve Den model lifecycle and admission facts
target: web
type: added
---

Home can distinguish reported model lifecycle states, bounded trials and production admission while keeping unknown configuration and loading facts explicit. Model metadata is filtered for privacy, and freshness uses conservative producer-clock bounds rather than assuming a received report is current.
