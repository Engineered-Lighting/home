---
title: Amend the latency budget so the gate can pass a healthy model
target: internal
type: changed
---

The latency gate was measured against the current model and failed it: a
routine multi-tool voice query runs 6.1 seconds at the 95th percentile
against a four-second budget, reproducibly. A gate the current system cannot
pass cannot judge a replacement — it would either be waived under pressure
or reject a candidate for a fault it inherited.

The owner chose to re-scope. The three budgets the current model already
meets are unchanged. The voice clause becomes a median budget of 2.5
seconds plus a paired tail cap: the candidate's 95th percentile may not
exceed 1.25 times the current model's, measured in the same session. That is
deliberately relative, because the absolute tail is polluted by a defect the
migration did not cause, while an open-ended allowance would let a six
second tail become fifteen unnoticed.

The tail itself is now tracked as a named pre-existing defect rather than
folded into a pass, with attribution pointing away from the model: the same
engine serves ambient captions at 0.18 seconds and first-token at 0.05.

Also corrects an instrument error. The budget named Home Assistant's
conversation endpoint as the source for time-to-first-token, but that
endpoint returns a finished response and can never expose a first token.
Time-to-first-token is now measured by streaming at the sidecar, and says so.
