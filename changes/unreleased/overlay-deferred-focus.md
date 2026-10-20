---
title: Keep keyboard focus on the chosen menu control
target: web
type: fixed
---

Deferred menu autofocus now preserves focus that has already moved to a control inside the panel. Fast keyboard actions after opening a menu therefore activate the chosen control rather than jumping to the first item. Opening Home's menu still moves focus from its Menu button to Setup, and closing it restores the opener.
