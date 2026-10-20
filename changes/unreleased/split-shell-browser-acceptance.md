---
title: Split the authored shell browser acceptance across two CI workers
target: internal
type: changed
---

The authored Apartment shell browser acceptance now runs as three files on two CI workers. Part 1 stays in the chat and full-shell job, and parts 2 and 3 run in a new job that the required check also needs. Every scene, viewport, native input and assertion is kept, and each file keeps its 145 s browser and 180 s file deadlines, so no part runs near its limit on hosted runners. Nothing a user sees changes.
