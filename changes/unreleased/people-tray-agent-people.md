---
title: People tray shows Home Agent people instead of HTTP 404
target: web
type: fixed
---

When the People map falls back to the Home Agent's household, opening a person
now shows their name, pronouns and relationships, read-only, and notes that
photos, preferences and Frigate faces are not available from the Home Agent. It
used to show only "HTTP 404". The map no longer probes Home Assistant for
avatars these people cannot have. The banner now says the household is read from
the Home Agent and no longer calls it a verified legacy view.
