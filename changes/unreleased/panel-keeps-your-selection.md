---
title: Stop the People Tab Discarding What You Selected
target: web
type: fixed
---

Choosing a partner in the People tab and then finding the button still greyed out was a bug, not a rule. Any background refresh that failed — a brief network blip is enough — reset every field on the page, including the person you had just chosen. The button then stayed disabled because nothing was selected, and nothing on screen explained why.

A failed refresh now clears only what the server told us, which the next successful refresh restores. What you typed or chose is kept. Signing out or switching to a different account still clears everything, so one person's half-finished entry can never appear under another's.
