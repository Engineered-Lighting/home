---
title: Repair a malformed unreleased change fragment
target: internal
type: fixed
---

The fix-phase3-source-refresh fragment was missing its title and used a
legacy area key, which failed fragment parsing and blocked the change-notes
gate for every pull request.
