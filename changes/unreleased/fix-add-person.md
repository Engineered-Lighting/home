---
title: Adding a Person to Your Household Works Again
target: backend
type: fixed
---

Adding someone to your household failed every time it was attempted. The request was rejected before it ever reached the database, so nothing was recorded and nothing explained why.

The cause was two stray values carried over from the code that records a partnership, which do not belong to the code that creates a person. Removing them is the whole fix; no stored record changes.

A new check now verifies that every value each of these operations reads actually exists on the request it reads it from, so the same slip cannot pass unnoticed in the others.
