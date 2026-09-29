# E4 reference copy of `extended_openai_conversation`

Home Assistant never loads this directory. It keeps the reviewed E4 identity
cutover and action-containment design that LA Home Assistant does not run
(owner decision, 2026-09-29), so it can be adopted deliberately later.

It is an overlay, not a complete integration. It holds only the files whose
reviewed version differs from `../extended_openai_conversation/`, which is a
byte-for-byte copy of what LA runs. Every other module a file here imports
(`helpers`, `entity`, `skills`, `functions/`, `cross_home_guard`, …) is the one
in the live directory. To assemble the reviewed design, lay this directory over
a copy of the live one.

When a file here becomes identical to the live one, delete it from here rather
than keeping two copies. `tools/run-home-security-tests.js` checks that every
relative import in this directory resolves in this directory or the live one.

The E4 operator modules (the freeze, its observer, `identity_store.py` and
`legacy_identity_fence.py`) and the tests for this design also live here; see
`docs/HOME-AGENT-RUNBOOK.md`.
