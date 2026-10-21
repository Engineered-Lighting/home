---
title: Show one message for a failed Chat request
target: web
type: fixed
---

Chat now reports a failed assistant request once when Home Assistant supplies both an error event and a failed completion. Requests rejected without an error event still show their failure, and the next message can recover normally.
