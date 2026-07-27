---
category: Security
---

- Put the legacy Home UI's grounded-camera request compatibility surface behind
  an explicit operator flag and constrain it to reviewed methods and paths.
- Keep authenticated read-only annotated camera results on a separate narrow
  route so generic Vision APIs remain unavailable.
