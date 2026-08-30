---
title: The People Tab Keeps Working After a Restart
target: backend
type: fixed
---

The People tab reads the household through a component that runs outside the container network, so it can only reach the core service by address. That address was assigned automatically and changes whenever the service is recreated, which would have left the tab blank after an ordinary restart, with nothing to indicate why.

The core service now keeps a fixed address, chosen so it can never be handed out to anything else.
