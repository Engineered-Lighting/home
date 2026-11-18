---
title: Den accepts intent hints bound to the registered room transform
target: backend
type: fixed
---

Den's intent-hint intake and its walking evidence projection now accept a `transform_id` in the `<camera>@<version>` form of the live registration (for example `den-oak@2026-11-13.1`), so a hint for one present person is no longer refused as `invalid_hint_dependencies` or reported as `malformed_intent_hint`. The hint must still match the current frame's transform, scan and calibration exactly; every other binding and the request, clip and session identities still refuse `@`, and `/`, whitespace, empty and over-128-character values are still refused.
