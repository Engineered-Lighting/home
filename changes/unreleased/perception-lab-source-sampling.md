---
title: Apply experiment sampling rates to specialist inputs
target: backend
type: fixed
---

Apply each registered recipe's per-arm sampling rate using source timestamps. Persist sampling decisions across runner reconstruction and record cadence skips as dropped inputs, preserving offered-input coverage without invoking inference for skipped frames.
