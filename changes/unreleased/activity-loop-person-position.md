---
title: Den activity loop publishes a quick person position for proximity lighting
target: backend
type: added
---

While the den is occupied, the always-on activity loop now publishes each
person's approximate room-frame position to the lighting brain as a
`person_position` event, so the brain can brighten a fixture as someone walks
toward it. The position is the person's chest point (between the shoulders),
taken from the existing approximate 3D placement. Every placed person is
included, not only the first. The events never wait on JEV and are not
affected by its back-off; each one is valid for 3 s. The placement updates
about once a second today, and the loop publishes at most five times a second.
A setting turns the events off, and the loop's status reports why no position
is available.
