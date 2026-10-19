---
title: Perception Lab PC input no longer counts as computer work without a camera cue
target: backend
type: fixed
---

In owner session `c88fe5b6`, JEV over-called `computer_work`. PC input recency read `under_60s` for almost the whole session, including while the owner read and for about a minute after they left the room. The corroboration block counted it, together with `computer_active`, as multiple independent support.

The HA derived fact `computer_input_recency` accepts an optional `"rule"` key. `"computer_active_v3"` gives the buckets `under_15s`, `15_to_60s`, `1_to_5_min`, `over_5_min` and `locked`, with the same 5 s clock-skew rule. Without the key the rule stays `computer_active_v2`, so existing HA source files keep their meaning.

Corroboration rule `corroboration-2`: PC input is independent support for `computer_work` only when it is `under_15s` and a camera cue places the observed person at the PC. The cues are Qwen `gaze_target` `screen`, `typing_visible` `yes` or `screen_facing_subject` `yes`, or a wrist near the keyboard or mouse. In every other case, `computer_work.pc_input` names why PC input does not count, for example `pc_input_recent_but_not_observed_at_pc`. A book or phone that Qwen sees in hand is listed under `computer_work.conflicts`. The activity question and the `computer_work_supported` question now say that PC input alone does not show computer work by the person in view.

Objects that stay still across an arm's recent frames, or that are seen while no person is present, are tagged `background_static` (rule `static-scene-1`). They move from `detected_objects` to `background_objects` and are never activity cues. This covers the den "tv" detection, which the owner says is not a TV. A detected tv is never a screen cue.

A wrist now counts as near a phone within one phone length of the phone box. A new `phone_near_face_in_image` fact fires when a phone is within one phone length of the nose, eyes or ears. Both carry a heuristic `basis` label.

The versions are now `structured-semantic-7`, `den-semantics-6`, `den-activity-7-semantic` and `den-activity-5`. To use the finer buckets at Victoria, add `"rule": "computer_active_v3"` to `derived.computer_input_recency` in the HA source file.
