---
title: Apply per-arm history windows to live JEV experiments
target: backend
type: fixed
---

Carry each configured arm's history window through evidence collection, durable reasoning requests and JEV provenance instead of always using ten seconds. Zero history uses the latest observation timestamp within three seconds; historical comparisons retain their configured bounded window.
