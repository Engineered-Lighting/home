# RESULTS: N2 scored runs

Append-only. `score.py --split held_out` prints one line on the owner's PC.
The owner pastes it to an agent, who appends it below unchanged and pushes. A
line holds the verdict, hashes and counts only: no labels, times or replies.
The EvalRecord it names stays in `C:\den-takes\n2\eval\`.

Line format:

```text
den-n2 held_out verdict=<feasible|not_feasible|insufficient> eval=<EvalRecord sha256> gate=<GATE-V1 sha256> labels=labels-vN/<sha256> scored=<date> sessions=<k>s/<days>d units=fine<n>/casual<n> onsets=<n>
```

No scored run yet.

## Log
