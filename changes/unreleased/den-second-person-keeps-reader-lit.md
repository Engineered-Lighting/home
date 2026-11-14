---
title: A second person in the den no longer darkens the reader's spot
target: deploy
type: changed
---

The den spotlight preview (`tools/den-live/preview.py`) keeps the reader's light while someone else is in the room. Before, any frame with more than one person darkened it.
- **The light's person** is the one its binding names. Among several people only that one keeps it; a person who walks in never takes it over, and with no binding nobody is lit.
- **Everyone else's head stays out of every beam,** at the same clearance as the reader's own. A fixture whose beam would cross another head is refused (`beam_through_head`); a lit spot someone walks into lets go, and lights again once the way is clear. A lit glide that would sweep across a head goes dark for the move.
- The bulbs' reading scene isn't played while someone else is there.
