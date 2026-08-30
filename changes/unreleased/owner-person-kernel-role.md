---
title: Make Adding a Person Work
target: web
type: fixed
---

Adding someone to the household has never worked. The step that records them reads the account binding to check who is asking, and the identity it ran under was forbidden from reading that record — deliberately, by a rule protecting a different part of the system. It was given that identity when it was written, rather than one of its own, so it wanted an access it was never allowed.

It now has an identity of its own, holding exactly the permissions the work requires and nothing more. The borrowed permission granted to the other identity has been taken back, since the reason for it is gone.

The tests that exercise this against a real database were switched off while it was broken. They are switched back on.
