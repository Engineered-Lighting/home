---
title: Home status says what each system is connected to, from one shared set of states
target: web
type: added
---

Home's status popover now shows one row per system for the home you're viewing: room, Home Assistant, lights, saved work and camera. For example, when Echo Park's Home Assistant is offline, it reads "Home Assistant offline · Last reported 20:58" and says that nothing is queued. Victoria never shows Echo's connection.

These rows, the Echo light inspector and Quick now read the same shared states. Den's lighting-brain, ops and activity-loop rows and its capability sections use Den's reason vocabulary, and an unknown code reads "Unavailable" with the raw code under Details. Den does not supply these reports yet, so those rows stay hidden until it does.
