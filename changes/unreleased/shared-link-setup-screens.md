---
title: Add guided two-home account-linking screens
target: web
type: added
---

Add a browser-only Home setup flow and a separate Victoria sign-in/verification
page. A provisioned Victoria hostname, one-use connection code, fresh account
authentication and explicit account review precede linking. Unknown outcomes use
status checks, and session changes clear private form context. The screens remain
disabled until the linking services and distinct HTTPS origins are provisioned;
shared memory is not deployed by this change.
