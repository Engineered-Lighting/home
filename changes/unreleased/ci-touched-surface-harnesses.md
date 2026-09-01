---
title: Run per-surface test harnesses in CI for the files a PR touches
target: internal
type: added
---

Pull requests now run the tools/run-*.js harnesses that guard the specific
app/src and web-gateway files changed in the diff (tools/run-touched-harnesses.mjs),
plus the offline JSX parse check. Previously only four checks gated PRs and
stale harness pins could merge silently. check-jsx now prefers the vendored
Babel build so it works offline and in CI.
