# FREEZES: N1 freeze lines

Append-only. `label.py freeze fixture` and `label.py freeze labels` each print
one line on the owner's PC. The owner pastes it to an agent, who appends it
below unchanged and pushes, so the commit time shows the freeze came before any
estimator run. A line holds versions, hashes and counts only: no frames, boxes,
labels or times beyond the freeze date. `label.py verify` checks every frozen
folder against its manifest and each manifest hash against these lines.

Line formats:

```text
den-n1 fixture-vN sha256=<fixture.json> protocol=<PROTOCOL sha256> frozen=<date> sessions=<k> frames=<rows> pad=<fraction>/<px>px
den-n1 labels-vN sha256=<labels.json> protocol=<PROTOCOL sha256> split=<split.json> remark=<remark.json> frozen=<date> development=<sessions>s/<days>d/fine<n>/casual<n> held_out=...
```

No frozen version yet.

## Log
