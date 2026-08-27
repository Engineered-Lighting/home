---
title: Submit the Phase 3 Cutover Document in PostgreSQL's jsonb Form
target: backend
type: fixed
---

The Phase 3 semantic cutover could never complete: the E4 kernel binds the submitted cutover document to PostgreSQL's own `jsonb::text` rendering, while the activation runner submitted RFC-canonical JSON. The two encodings can never coincide — `jsonb` orders object keys by length and then by bytes and separates with `", "` and `": "`, where canonical JSON sorts lexicographically and omits whitespace — so every cutover attempt failed with `identity_cutover_document_not_canonical` after the one-time candidate and admission rows had already been written. The hosted gate did not catch it because its fixture generates the document from `jsonb::text` directly rather than through the runner. The runner now renders the wire form the kernel requires and hands identical bytes to both the admission and the execution, so the digest the admission records is the digest the kernel verifies; the ceremony receipt still commits to the canonical form.
