---
title: Keep Den recording byte counts accurate on Windows
target: backend
type: fixed
---

Write Den causal recordings in binary mode so Windows preserves the original
JSONL delimiters. Recorded byte counts, durable prefix offsets and storage limits
now cover the actual file bytes; existing recording readers remain unchanged.
