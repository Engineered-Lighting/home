---
title: Find the root cause of the reasoning leak and verify a fix
target: internal
type: fixed
---

A diagnostic window using the new reproducer isolated the fault that rolled
back two cutover attempts, and confirmed a configuration that does not leak.

The trigger is streaming together with the tool catalogue. The long system
prompt alone never leaked; adding the twenty-three tools took it to fifteen
percent, and it fires on the first turn, while the model decides whether to
call a tool.

The cause is that the two settings in use were contradictory. Suppressing
thinking makes the template pre-fill an already-closed thinking block in the
prompt, so the model's output contains no opening tag, the reasoning parser
never engages, and the stray closing tag the model emits anyway lands in the
text handed to speech synthesis. Suppression and parsing were fighting each
other, and the previous attempts used the half that loses.

Measured across three configurations at the same sample size: thinking
suppressed with no parser leaks three in twenty; suppressed with a parser
still leaks two in twenty and the reasoning field stays empty; thinking
enabled with a parser leaks none in forty and the reasoning field is finally
populated. The runbook now carries that configuration, and the parser has
moved from the do-not-add list to required.

One consequence is flagged rather than buried: thinking is genuinely on now,
so the model generates reasoning tokens it previously did not. Latency needs
re-measuring before any go-live decision, on a model already slower at
decode.
