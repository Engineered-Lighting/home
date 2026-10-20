# Den reason vocabulary

**Status:** proposed, 2026-10-20. The machine-readable list is
[reason-vocabulary.json](reason-vocabulary.json) (`den-reason-vocabulary-1`).

Den producers report a state or refusal as a short code. The dock and status
rows show the code's `text`, with the raw code in Details. They never invent
their own wording for a listed code.

## How to show a code

1. Look the code up in its group: the group that matches the field it came
   from. The same code can appear in two groups with different meanings. For
   example, `degraded_ambient` is both a mode and a rule.
2. If the code isn't listed by name, try the group's `pattern` entries:
   - `{n}` matches an integer, as in `state_http_503`;
   - `{state}` matches a word, as in `deep_stack_failed`. Where a pattern lists
     `states`, those are the values producers emit today.
3. Still unknown: show **Unavailable**, with the raw code in Details. Never
   drop a code silently.
4. `severity` is a hint for the row's tone:
   - absent: neutral;
   - `warn`: amber;
   - `alert`: red.

   Severity never decides whether a control is enabled; the producer's own
   check does that.
5. Codes marked `"legacy": true` belong to the retired fixed-desk preview.
   They may still appear in recorded sessions and old receipts. Show them,
   suffixed "(retired preview)", and never offer actions for them.

## Groups

| Group | Field | Producer |
|---|---|---|
| `brain.mode` | `mode` in `activity-lighting-state-1` | lighting brain (AL-1) |
| `brain.rule` | `decisions[].rule` | lighting brain |
| `brain.status` | Home brain client `status` and `reason`: online, connecting, offline, stopped, and why it is offline | Home |
| `brain.refusal` | Home brain client `refused_reason`: a state document Home ignored | Home |
| `brain.aim` | the aim planner's refusals, shown inside a decision's `why` | lighting brain |
| `fixture.pause` | per-fixture pause reason: Home's local pause, or the brain's `pause_reason` | Home and brain |
| `den_ops.level` | `level` and `step` in `den-ops-banner-1`, plus "monitor silent" | always-on monitor (AL-5) |
| `activity_loop.health` | `health.detail` per component: `jev`, `deep_stack`, `camera` | activity loop (AL-3) |
| `activity.label` | `activity.label` | activity loop |
| `perception.age` | the perception view's `age.status` and `age.reason` | Home perception view |
| `perception.placement` | room, diagnostic and approximate placement reasons | Home perception view |
| `legacy.desk_preview` | the retired fixed-desk preview | Home (retired) |

## Notes

- **The JEV release code is `jev_resuming`.** It is emitted after an abandoned
  request with an unknown outcome stops blocking JEV egress. Planning notes
  called it `jev_released_resuming`. The JSON lists that name as an alias.
  Producers emit only `jev_resuming`.
- **"Monitor silent" has no level of its own.** The monitor reports level 0
  with a `warn` banner reading "Ops monitor silent on <host>". It is listed as
  `monitor_silent` so that rows can refer to it. A status row shows the
  banner's own text.
- **Brain refusals are not outages.** A refused state document keeps the last
  good state. Show it in Details, not as Offline.
- **Aim refusals arrive inside a decision's `why` text.** The brain does not
  send them as separate fields today. The codes are listed so that a later
  structured field can reuse them.

## Fixtures

The following files use only codes from this vocabulary:

- [fixtures/brain-state-reading.json](fixtures/brain-state-reading.json)
- [fixtures/den-ops-banner-silent.json](fixtures/den-ops-banner-silent.json)
- [fixtures/den-ops-banner-alert.json](fixtures/den-ops-banner-alert.json)
- [fixtures/activity-health-events.json](fixtures/activity-health-events.json)
- [fixtures/reason-rows.json](fixtures/reason-rows.json): one example status
  row per group, as a dock would render it.
