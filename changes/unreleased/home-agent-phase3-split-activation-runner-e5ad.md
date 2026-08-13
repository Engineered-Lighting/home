---
title: Add resumable private identity activation runner
target: backend
type: added
---

Home Agent now has one bounded, restart-safe operator runner for the reviewed
backup, migration, privacy-cutover, authenticated identity-binding, and parent
confirmation stages. It pauses for private confirmations, verifies service
health after each staged restart, and contains failures without restoring the
legacy semantic authority.
