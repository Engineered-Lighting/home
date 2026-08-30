---
title: Fix the People Tab Buttons Doing Nothing
target: web
type: fixed
---

Recording a partner or adding a person from the People tab never worked in a browser. Both handlers generate a ceremony identifier as their first step, using a helper that the page's other script keeps to itself — so the moment either button was pressed the code stopped, before any request was sent. No error appeared, nothing reached the server, and the button simply looked dead.

The helper is now shared the same way the rest of that script's interface already is. A new check loads the script the way a browser does and fails the build if anything it publishes is unreachable from the page, which no existing test could catch: the old arrangement worked correctly under the test runner and only failed in a real browser.
