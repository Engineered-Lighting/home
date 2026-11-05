# Live architecture contracts, v1

These files are the contracts for building the live den architecture rung by
rung, in separate sessions that never share code. They were copied unchanged on
2026-11-04 from the private design artifact (data revision B, after the
2026-10-29 corrections pass):

| File | What it holds |
|---|---|
| `schemas.json` | The records: field names, types, versions, producers, consumers and one illustrative evening (A at the counter, B in the armchair). **Field names here are canon.** |
| `ladder.json` | The build ladder: rungs R, 0, N1, N2 and 1-12, each with its inputs, outputs, test, gate and loop check, plus the later items. |
| `decisions.json` | The decision register. A `{{dec:name}}` in the ladder points here. |
| `notes.json` | The repo facts R1-R19 that the ladder cites as `[[Rn]]`. |
| `experiments.json` | Experiments E1-E19 that the ladder names. |
| `owner-choices.md` | The owner's answers so far, with the numbers each gate uses. |

Nothing here is real data. The design sheets the records cite (A2-A14) are not
copied. Where a builder needs one of their numbers, its brief states it.

## Who builds what

| Rung | Piece | Writes | Reads | Folder | Branch |
|---|---|---|---|---|---|
| 1 | Camera registration from measured control points | `room_model` camera entry, `calibration_record` | `room_model` | `tools/den-register` | `claude/rung-1-register` |
| 2 | Observations in the room frame | `observation` | `room_model`, detector outputs, R's takes | `tools/den-observe` | `claude/rung-2-observe` |
| 3 | Geometry-only tracker, and E2's scorer | `track` | `observation` | `tools/den-track` | `claude/rung-3-track` |
| 5 | Person bundles and router v0 | `person_bundle` | `track`, `observation` | `tools/den-bundle` | `claude/rung-5-bundle` |
| 7 | Measure light (E8: card, meter, `k_L`) | `calibration_record`, `light_state`, `room_model` photometric fields | `room_model`, meter-norm/v1 | `tools/den-photometry` | `claude/rung-7-photometry` |
| 8 | Light model, Stage 0 then Stage 1 (E7) | `light_model` | `fixture`, `light_state` | `tools/den-light` | `claude/rung-8-stage0`, `claude/rung-8-stage1` |
| 9 | Coordinator v0, scored on replay | `decision` | `person_state`, `light_model`, `look`, `preference`, `correction` | `tools/den-core` | `claude/den-core-live` |
| 10 | Verify | `decision.verify`, `light_state` residual | `decision`, `light_state`, `light_model` | `tools/den-verify` | `claude/rung-10-verify` |

The next wave, once its inputs exist: rung 4 (two-person gold, extending
`tools/den-label`), rung 6 (person need, extending den-core's need), N2's
need v1 and ask path, rung 11 (the canary through den_lighting) and rung 12
(correction attribution). The later items stay parked.

## Rules for every builder

- **Records travel as JSON Lines**: one record per line, UTF-8, LF, with the
  record's field names and its version. A tool that reads another rung's
  records tests against fixtures it writes from `schemas.json`, never against
  the other tool's code, so the sessions stay independent.
- **A field or number the contracts leave open** goes into the tool's own
  method or gate file as an empty slot, marked `open`, with a proposed value.
  The tool refuses a scored run while a slot is open.
- **A pending decision** (`{{dec:name}}`) is built as named options. As the
  ladder says, a case whose expected result depends on it is not admitted as
  pass or fail until the owner decides.
- **Building needs no go-ahead; running does.** Camera access, LA GPU time,
  light output, Home Assistant changes and any deploy each need the owner's
  go-ahead at run time. Tests use synthetic data and fakes bound to 127.0.0.1.
- **No real data in git or the cloud**: no frames, takes, labels, logs or
  exports. Anything shown blurs faces. People are A and B, never names.
- The owner runs Windows with PowerShell 5.1. Every command the owner types
  must work there. Python tools use the standard library unless their plan
  says otherwise.
