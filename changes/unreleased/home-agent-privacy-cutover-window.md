---
title: Bound the privacy cutover evidence on the service stop, not a stopwatch
target: backend
type: fixed
---

The privacy cutover observation required the HA Edge privacy receipt to be
within five minutes of the writer freeze. That was unsatisfiable by
construction, and measurably so: on the live deployment the newest available
receipt was **twelve hours** old.

The edge records a receipt only when it successfully fetches the privacy policy
from Core. The agent services stop at step 12, an unbounded human confirmation
sits at step 17, and the freeze is taken at step 20 — so by the time the two are
compared, the edge has been unable to refresh for however long the operator
took. Both inputs are also create-once, so a missed window could never be
recovered.

The bound was standing in for something real: that nothing changed between the
edge's last verification and the freeze. While the agent services are down the
edge can neither refresh nor act on a different policy, and the policy itself
cannot change — so elapsed time carries no information, and the property the
bound approximated can be asserted exactly. The observation now requires the
agent services to be proven stopped, and the service state is passed in as a
value like every other live input the function validates.

The orderings that do carry meaning are unchanged: the edge receipt must
describe a moment at or before the freeze, and the freeze must precede the
observation and not by more than five minutes. That second bound stays tight
because both steps are automated and consecutive.

Considered and rejected: comparing the edge's `policy_digest` to the freeze's.
They are different digests — the edge records the digest of the privacy policy
document Core serves, the freeze records the deployment identity policy digest.
Confirmed against the live deployment before relying on it.

Residual risk, unchanged by this commit and worth a separate look: the freeze is
create-once, so if more than five minutes elapse between the freeze and the
observation the activation cannot proceed and cannot retake the freeze. The step
timeouts permit considerably more than five minutes.
