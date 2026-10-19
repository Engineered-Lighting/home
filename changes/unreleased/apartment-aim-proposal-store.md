---
title: Add an in-memory store for pending light aim proposals
target: internal
type: added
---

Adds an unwired, in-memory store that holds pending aim proposals for Apartment lights until a person Sets or Discards them, fenced to the exact fixture and equipment revision they were made for. Nothing uses it yet, so nothing a user sees changes; it never moves an aim, sends a command or saves anything.
