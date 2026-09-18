# Night post-mortem: who wrote the asleep latch

The definition of done for story S says that while the estimator is live every
`input_boolean.living_lights_asleep` transition is written by the mirror
automation, by hand, or by the ungated hard backstop, and that none is written
on presence reconnect, at 09:00, or by the tick. Nothing measured that.

`tools/night-postmortem-join.py` now reads the latch, the writer helper and the
from-estimator toggle from the recorder, pairs each transition with the writer
value recorded within thirty seconds of it, and reports the families, the
clears by trigger, how many the publisher wrote, and how many fired while the
estimator was live with a writer the plan does not permit. That last count is
the acceptance bar and it is zero.

Attribution is evidence, not inference. A transition with no writer value in
the window is `unattributed` rather than assigned to whatever wrote last, and a
window that looked backwards would let the previous flip's writer claim this
one, which would make the report lie rather than merely say nothing.

A latch that goes unavailable across a Home Assistant restart and comes back
holding a different value is the restore, not a write; those are skipped and
counted, so the omission is never silent.

The HTTP client, the token read and the scrubber come from the TV evening
post-mortem rather than being written twice: GET only, redirects refused, no
proxy registered, the token never printed. `--no-latch` skips the recorder
query entirely.

Run today over the last week it finds five transitions, two latches and three
clears, all unattributed: the writer helper arrived with this morning's deploy
and the latch has not moved since. From tonight it will attribute.
