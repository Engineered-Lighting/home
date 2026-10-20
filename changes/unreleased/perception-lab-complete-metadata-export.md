---
title: Export complete revision-bound Perception Lab metadata
target: backend
type: fixed
---

Replace first-page-only session exports with bounded snapshot pages containing
all events, jobs, media references and annotation history. Browser and typed
native transport collect a complete metadata manifest, reject changed snapshots,
and distinguish reviewed human labels from unverified/model labels. Video bytes
are not included in the metadata download.
