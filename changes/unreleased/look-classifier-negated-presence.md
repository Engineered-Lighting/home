---
title: Stop "no one moving" waking the house
target: web
type: fixed
---

The person branch of `classifyLookFinding` matches motion words, and a camera
reporting that nothing is there often says so with one: "The living room is
quiet with no one moving.", "Nothing is moving.", "No motion detected." All of
them filed as `person` at importance 90 — the tier that wakes someone — on
frames where the model was explicitly saying nobody was present.

Adds a negation guard that blanks a negated presence clause before the person
test, and widens the quiet branch to accept "is/are/all quiet" as well as the
perception verbs, so a plain "The room is quiet." lands in `quiet` at
importance 0 instead of `activity` at 55.

**Both changes are needed; neither works alone.** With only the guard, the
caption clears the person branch and then falls through the narrow quiet branch
to `activity`/55. With only the wider quiet branch, the person branch matches
"moving" first and returns 90 before quiet is ever reached. Together the same
caption reaches `quiet`/0. That interaction is why they ship as one change
rather than two.

Measured on the frozen corpora. On the 25 owner-verified empty frames, false
presence drops 11 → 7 and importance-90 person findings drop 7 → 3 for the arm
that uses this phrasing; the incumbent's captions do not use it, so its numbers
are unchanged at 3/25 and 2/25. On the 50-frame daylight corpus nothing
regresses: people missed stays 1 of 19 and phantom alerts stay 0.

The negation window is capped at two filler words so a real subject is never
swallowed — "There is no dog but a man is walking." still files as `person`,
verified.

`tools/qwen38_gates.py` is updated in step, so the G4 gate keeps scoring what
production actually does rather than a stale copy of it. Both suites pass:
`run-look-tests.js` 83/0 and the gate-core tests.

Bare negations with no quiet word ("Nothing is moving.", "No motion detected.")
now land at `activity`/55 rather than `quiet`/0. That is deliberate: 55 does not
wake anyone, and pushing further would mean tuning the branch against invented
phrasings rather than measured ones.

The same fix is applied in the repo that actually serves the app
(`/home/marcelo-lima/code/home`, commit `2917d4d`) — see
`docs/AI-ARCHITECTURE-EXPERIMENTS.md` for why that distinction matters.
