---
title: Improve travel web startup reliability
target: web
type: fixed
---

The web app now serves React, ReactDOM, and Babel from the Home gateway and
prefetches boot-chain files in a small ordered window, reducing startup stalls
on slow travel connections.
