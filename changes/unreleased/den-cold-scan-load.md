---
title: A slow first load of a room no longer ends in "could not be loaded"
target: web
type: fixed
---

Opening a room on a fresh device gave its point-cloud scan (11.4 MB for the Den) only 4 seconds to arrive, then showed "The interior could not be loaded" until Retry, so a slower link left the Den blank. The scan now keeps loading for as long as its bytes keep arriving, with its progress on screen. It gives up only after 20 seconds with nothing new, and then tries once more by itself before offering Retry.
