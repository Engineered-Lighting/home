---
title: A stuck den recording no longer blocks every later one
target: backend
type: fixed
---

The perception Lab no longer leaves a den recording stuck in `indeterminate`.
Before, a stuck row made the Lab refuse every new den recording (`conflict`),
which stopped live pose, posture and the body's limb fill until someone
stopped the row by hand. This happened twice, on 2026-11-11 and 2026-11-12.

- If one worker is slow to stop, the Lab now retries the cleanup with backoff
  until both workers are confirmed stopped. Before, it gave up after a fixed
  10 s wait, which is shorter than LA's start checks can take.
- After a Lab restart, and every minute after that, the Lab checks each
  `indeterminate` recording it no longer runs. It asks each worker host about
  that recording's own worker request, stopping it if it is still running. The
  row moves to `stopped` (reason `worker_cleanup_reconciled`) only when every
  worker is confirmed stopped, or was never reserved. The evidence is recorded
  as a `workers.reconciled` event, and the recording's data is kept.
- The den activity loop now retries after worker failures (for example
  `worker_busy`, or a timed-out preparation) instead of treating them as an
  operator Stop and shutting itself off.
