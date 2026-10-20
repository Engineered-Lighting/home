---
title: Keep Simulation visible throughout the Apartment interface
target: web
type: fixed
---

An active Simulation now has a persistent indicator in the Apartment top bar, including when Controls or Camera is selected. The indicator opens the existing Simulation controls and remains usable on phones.

Exiting Simulation resets its synthetic connection state before reconnecting, so live subscriptions cannot run against an unconnected client and blank the interface.

Scenario selection, reset, exit and close controls have 44-pixel minimum targets.
