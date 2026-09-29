---
title: People tab works on the Home Agent household again
target: web
type: added
---

When the People tab reads the household from the Home Agent, it again shows
headshots, subroles and notes from Home Assistant's profile store, and each
person's tray can edit their relationship type, subrole, notes and headshot.
The tray lists the person's relationships, their recent Frigate sightings
(newest first, up to 200) and their enrolled reference photos, and both open a
full-screen capture browser. Frigate images are loaded only through Home
Assistant's typed Frigate proxy routes, never from a Frigate address. People and
relationships can be added from the tab. These writes go through the Home Agent
People bridge, which accepts only those two requests, checks each one against
the Home Agent's request rules and sends the session's CSRF token. People are
now placed on the map only by their relationships to you. Needs the typed
Frigate proxy and the profile route update deployed to Home Assistant.
