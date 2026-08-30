---
title: The People Tab Reads and Writes the Household Again
target: backend
type: fixed
---

The People tab had gone blank, and adding someone from it did nothing. Its store was deliberately frozen during the identity cutover and will not serve or accept writes again, and the tab had no other source.

It now reads the household from the system's current record, and adding a person writes there too. The tab keeps the same connection to Home Assistant it always used; only what sits behind that connection changed.

Two notes on how this is wired. Home Assistant reaches the household over the same mutually-authenticated link it already uses to send events, so no new door was opened. And the credential that identifies the request to the core service is added on the server side rather than stored on the Home Assistant box, because that credential can act as any user.
