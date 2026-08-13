---
title: Add reviewed identity packet compiler
target: backend
type: added
---

Security: add the offline E5x reviewed-identity packet compiler. Every selected
legacy SQLite row now receives a keyed source commitment and either a typed
semantic projection or an explicit omission decision. The compiler emits exact
review-signing bytes, verifies the distinct review signature, and independently
re-verifies the assembled bundle before emitting the separate finalization
signing payload; it has no database, network, or authority path.
