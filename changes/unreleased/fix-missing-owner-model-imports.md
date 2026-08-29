---
title: Fix the Owner-Attested Route Imports
target: web
type: fixed
---

The core API would not have served after a deploy. `api.py` referenced the four models behind the owner-attested person and partner routes without importing any of them. The names sit inside the function that builds the router, so importing the module succeeds and nothing complains until the router is actually constructed — at which point the process fails and the API stops answering.

It survived because the same fault was patched by hand on the deployed machine during an earlier outage and never committed, so the running system was correct while the repository was not. A new check compares the names `api.py` uses against the models that exist and fails on any used without being imported, which a test that merely imported the module could never catch.
