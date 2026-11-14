---
title: Send what Qwen saw with the Den activity
target: backend
type: added
---

Each answered activity on the Den people stream now carries Qwen's own facts (held object, book state, gaze target and posture) and its visible-basis tokens. Home's caption reads them when they're present, for the held-object tag and the "Qwen saw" line. No confidence is added.
