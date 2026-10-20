---
title: Preserve finalized encoded fragments at the recording duration limit
target: backend
type: fixed
---

Allow the recorder to commit bounded finalized MP4 fragments after confirmed process termination, including during stopping. Ordinary capture writes remain blocked at the duration limit; quota and object-size limits still apply.
