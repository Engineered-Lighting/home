# Shadow report: refuse a week nobody was home

A latch scores `explained` when no person was near it. That is the right answer
for a house someone has gone to sleep in, and it is equally the right answer
for a house nobody is in. The report could not tell them apart, so a week in
which the household was away produced zero unexplained latches, printed "the
acceptance bar is zero over seven nights", and exited 0.

The refusal added for an empty journal directory cannot catch this: an empty
house journals a record every tick, so the directory is full.

The report now counts the days its journal covers and, of those, how many had
anybody seen in the house at all. It names the empty days, says plainly that
they do not count towards the seven, and refuses the range outright when no day
had anybody. `--allow-empty-house` reads it anyway, which is worth doing as a
negative control for the away path and for the plumbing, but never towards the
acceptance count.

Found by auditing what a house move does to the experiment, with the household
travelling the day after the shadow run began. It is the same defect shape this
plan has caught four times already, and this time the calendar was about to
walk into it rather than a bug.
