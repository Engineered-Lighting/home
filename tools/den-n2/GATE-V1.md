# GATE-V1: the N2 gate

This is N2's gate, version 1, for the offline need test on one person (plan:
`docs/plans/n2-need-test/PLAN.md`). Like R's CHECK-V1 it is committed first
with every slot empty, before any estimator output exists. `score.py` reads
every value it uses from the one fenced `gate-v1` block below.

- `score.py --split development` runs with slots empty and reports what it
  can.
- `score.py --split held_out` refuses while any slot is `null`.
- Slots are filled on the development split only, then this file is committed
  and pushed before the first held-out ask line. Its commit time shows the
  order. After that it never changes. A changed gate is a `GATE-V2.md`.
- Owner decisions stay empty until the owner gives them: `scope`, every
  `floor.*` and every `cap.*` slot. An agent never fills them.

## Fixed values

These are copied in now from the plan and the owner's answers of 2026-11-02.
They are never slots:

- τ_on 3 s and τ_off 60 s (A13, and N1's `tau_off_s`), and the 6 s grace for
  one person;
- the cut points: fine at a level of 0.6 or more, casual from 0.4 to under 0.6,
  none under 0.4;
- false-fine at most 0.033, onset median at most 3 s, ECE at most 0.1 over 10
  equal-width bins;
- the sample gates: at least 30 units per band, and 30 timed onsets, from at
  least 3 sessions over at least 2 days; every per-second metric and every case
  needs data in at least 3 held-out sessions over at least 2 days;
- the interval rules: two-sided 95%, exact binomial (Clopper-Pearson) for
  per-unit proportions, a session bootstrap of 10,000 rounds with percentile
  intervals for the rest, seeded from the labels hash;
- A7's starting values for need v1: `theta_on` 0.6, `theta_off` 0.4,
  `entry_dwell_s` 5, `exit_negative_s` 20, `demand_rise_s` 1, `demand_fall_s`
  10, and the levels fine 0.6, casual 0.4 (the cut), none 0.2;
- the request schedule P = 4 s, qwen_scene's 10 s validity, den-core's 2.0 s
  tick, and the field assignment (both fields read one row per reply).

## Slots

| Slot | Filled by, before held-out |
|---|---|
| `scope` (`"stand_in"` for option 1, `"qwen_only"` for option 2) | the owner, at plan review |
| `prompt` (`{"version", "sha256"}`, prompt-1 or prompt-2) | development step |
| `pacing_s`, `onset_mode` (`"gated"` or `"report_only"`) | development step, from D_dev (plan section 1(a)) |
| `facts_map`, `confidence_table` | fitted on development |
| `rule_changes` (`{}` when A7's values stand) | development step |
| `floor.competence.fine`, `.casual`, `.none` | the owner, on development |
| `floor.fine_recall`, `floor.coverage` | the owner, on development |
| `cap.early_off`, `cap.false_on` | the owner, on development |
| `detector` (option 1 only; may stay `null` under option 2) | development step |
| `stand_ins` (`{"protocol_sha256", "pad": {"fraction", "min_px"}, "need": {"code_sha256", "config_sha256"}}`), `s_dev`, `d_dev`, `look_test` (the look-test tool's JPEG and gate settings) | mechanical |

`stand_ins` pins the protocol, the pad and need v1's code and config, not a
labels or fixture version: after an `insufficient` verdict the enlarged
held-out set arrives as a new labels version and is scored again with this
gate unchanged.

## The block

One `name = value` per line, the value in JSON (it may run on over the next
lines). Lines starting with `#` are comments.

```gate-v1
# ---- fixed: copied in, never slots ----
tau_on_s = 3
tau_off_s = 60
grace_s = 6
cut.fine = 0.6
cut.casual = 0.4
cap.false_fine = 0.033
cap.onset_s = 3
cap.ece = 0.1
ece_bins = 10
sample.units = 30
sample.onsets = 30
sample.sessions = 3
sample.days = 2
interval.level = 0.95
interval.rounds = 10000
a7.theta_on = 0.6
a7.theta_off = 0.4
a7.entry_dwell_s = 5
a7.exit_negative_s = 20
a7.demand_rise_s = 1
a7.demand_fall_s = 10
a7.levels = {"fine": 0.6, "casual": 0.4, "none": 0.2}
schedule_s = 4
max_age_s = 10
tick_s = 2.0
field_assignment = {"engagement": "row", "demand": "row", "row": ["activity_hypothesis", "held_object"]}
# ---- slots: null until filled on development, before held-out ----
scope = null
prompt = null
pacing_s = null
onset_mode = null
facts_map = null
confidence_table = null
rule_changes = null
floor.competence.fine = null
floor.competence.casual = null
floor.competence.none = null
floor.fine_recall = null
floor.coverage = null
cap.early_off = null
cap.false_on = null
detector = null
stand_ins = null
s_dev = null
d_dev = null
look_test = null
```
