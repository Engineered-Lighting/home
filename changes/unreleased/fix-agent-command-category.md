---
title: Fix /agent slash command category
target: web
type: fixed
---

The /agent command declared a "navigation" category that does not exist in the
slash palette, so it rendered outside every labelled group. It now lives in the
existing "ask the agent" category.
