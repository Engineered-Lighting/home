---
title: The den spotlight preview lights on one confident answer and rides out one bad frame
target: deploy
type: changed
---

The den-live spotlight preview (`tools/den-live/preview.py`) gets step 2's coordinator rules, before the sofa cues.
- **One confident yes lights.** A single valid, bound "reading or close-up task" answer turns the virtual spot on; it no longer waits for a second answer.
  - A later answer confirms it. A phone, a screen or a closed book starts the patience, which ends the light. None or several people in the stills end it at once.
  - The existing guards stay: stills from before the seated episode are refused, and every answer is delivered.
- **One bad frame darkens instead of starting over.** A stale snapshot or an unsafe, poseless or crowded frame turns the light off but keeps the person's episode, its onset, the answers and the requests. An answer already on its way still counts, though the ask names no eligible person on that frame. The next good frame lights again with no new answer. Only a change of the person binding (run, camera epoch, track or association revision) starts over.
- **A light needs a current, posed, seated frame.** An answer to an ask sent as the person stood up is taken but lights nothing.
- **The subject is named.** `den-virtual-spots-1` and the Decision carry `subject {track_id, camera_epoch, association_revision, request_id}`.
- **The controls are saved as memory holds them.**
  - On, Off and Stop always apply and answer 200: no more 409 when two arrive together.
  - Resume checks that it's still the newest control, writes `stopped: false`, then un-stops, in one step. A failed write leaves it stopped.
  - `control_revision` is persisted and survives a restart.
- **Facing jitter can't flicker a lit fixture.** The fixture lit for someone stays theirs through a facing refusal until it has lasted 1 second.
