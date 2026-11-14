---
title: The den coordinator learns from the owner's own changes and never fights them
target: backend
type: added
---

The den spotlight preview (`tools/den-live/preview.py`) now hears each change the owner makes to a den fixture in Home, holds that fixture for them, and learns from the change. All of it is virtual: nothing is sent to a fixture or to Home Assistant.
- **`POST /coordinator/manual`** takes one `den-manual-1` event per settled change. The event carries Home's own `before` and `after`, the snapshot Home was drawing, and an `event_id`. A retry with the same id gets the same reply. Each event is written to `coordinator/manual-events.jsonl` and fsynced before the reply says it's recorded. den-live reads the change's context, and the state it was made from, from its own record of the snapshots it published; Home's copy is only a cross-check. **`POST /coordinator/manual/resume {fixture_id}`** gives a fixture back.
- **Manual wins.** The parts a change touches (the spot, the glow zones, the downlight) are the owner's at once, and den-live sends them nothing.
  - If the held spot was the reading light, the reading counts as served: nothing else lights for it, and the room stays dimmed while the spot is on.
  - A hold ends at the next real reason to act. For the reading light, that's the reading ending, standing up, or someone else becoming the reader. For a light the owner put on for themselves, it's a reading starting. For glow zones and the downlight, it's the room glow's next cue.
  - Every hold also ends after 30 s with nobody seen, on Off or Stop, and on Resume. A reply that only renews the reading, or a new time of day, ends nothing.
  - The snapshot shows each hold as `fixtures[F].hand`, and the fixture's `reason` reads `hand`.
- **Learning** follows den-core's rules. A change counts only while the coordinator is On, exactly one person is in the den, the situation is known, and the snapshot is under 30 s old and matches what Home showed. Each change moves what's learned halfway toward what the owner set; warmth moves halfway in mireds; "no spot" needs two offs in a row. For each situation (for example reading in the evening), it learns:
  - how bright the reading spot is, as a scale on den-core's target lux, so it carries across fixtures and distances;
  - where the spot points, as an offset from the page of at most 30 cm;
  - the room glow's level and warmth.
- **When it's applied:** the spot's learned values are fixed when the next reading starts, so nothing moves while someone reads. The glow's are fixed at its next cue, and its warmth changes smoothly. Every safety check runs on the learned aim, and the page itself is used if no fixture can take it.
- **Shown and cleared:** `/coordinator` shows `learned_from_you`, the holds, and the situation den-live acts on. The snapshot carries `fixtures[F].learned` and `ambient.learned`. `POST /coordinator/learned/clear` keeps the old log as `manual-events-<stamp>.jsonl` and starts empty (it also clears the bulbs' learning, as before).
- **Time of day** comes from the machine's own timezone, with daylight saving, unless an offset is configured. If the timezone can't be read, the time of day is "any".
