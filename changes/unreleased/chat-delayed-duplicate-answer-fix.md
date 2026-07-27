---
title: Chat Removes Delayed Duplicate Answers
target: web
type: fixed
---

Assistant responses that arrive twice from separate Home Assistant and sidecar event sources are now deduped across the same turn, while repeated answers after a new user question still render normally.
