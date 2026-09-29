---
title: The repo's Home Assistant integration now matches LA Home Assistant
target: backend
type: changed
---

`ha-config/extended_openai_conversation/` is now byte-for-byte the integration
LA Home Assistant runs, including its manifest, config flow, AI task, profile
store and translations, so it can be deployed from the repo again. Nothing
changes on Home Assistant. The reviewed E4 identity cutover and action
containment design that LA does not run moves unchanged to
`ha-config/extended_openai_conversation_e4_reference/`, where its tests and
the E4 operator tooling still point. The Apartment engineered-fixture
validation, which was never deployed, is kept only in that reference copy for
now. The integration's own tests now run in CI.
