---
title: Preserve tracking updates delivered together
target: web
type: fixed
---

Home now applies tracking event size limits to individual events instead of discarding valid updates delivered together in a larger network chunk. Oversized and malformed events remain refused, with bounded diagnostic reasons that exclude event contents.
