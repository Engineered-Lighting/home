# What was run, with counts

This is the detail behind REPORT.md for the 2026-11-05 pipeline review. Times are UTC. The review started at 05:07 and the report was written from 07:15.

## Validation

**Suites, twice each in a clean checkout of the reviewed commit.** All pass both times except where noted: den-take 72 (rung-r) and 82 (den-take-script); den-label 50; den-n2 40; den-core 113 (1 skip); correction-capture 22; den-register 44; den-observe 50; den-track 58; den-bundle 81; den-photometry 58; den-light 86 (Stage 0) and 139 (Stage 1); den-verify 30. Two exceptions:
- den-core-live's den-core suite (146 tests) failed the same 2 tests on both runs: rung 9's "a full-size light model stays under 100 ms" took 0.14-0.21 s. The container was heavily loaded (load average about 19 on 4 CPUs), so this is a wall-clock test that depends on the machine. Re-timed at load 5 it passed 2 of 3 runs and failed once at 0.102 s, so it sits on the bound (see findings).
- den_lighting's tests need Home Assistant: 17 policy tests pass without it. With Home Assistant 2026.2.3 installed, 50 of 51 pass; one fails on a test-harness call (`DeviceRegistry.async_setup`) that this Home Assistant version no longer has.

No flaky test was found. Several suites (den-register, den-n2, den-photometry) fail one or two tests when run outside a git checkout, by design.

**Do the tests catch a broken gate?** Reviewers mutated the gate and verdict logic of every tool: thresholds, `<=` against `<`, denominators, empty input, open-slot refusals. 161 of 396 targeted mutants survived the suites. The weakest were den-n2 (15 of 22 survived, including every gate-logic mutant but two controls), den-core-live's rung 9 scorer (18 of 27), den-observe (17 of 27) and den-bundle (15 of 37). Surviving mutants that matter are in `findings.json` as test-coverage findings.

**Merge rehearsal.** All 18 branches merge onto claude/live-records with no conflict (scratch head f479e537). No file in the merged tree differs from every branch's version, so the merge creates no untested hybrid. All suites were then run twice in the merged tree. Every suite passed both times except three. den-photometry ran 27 tests with 25 errors on both runs, because its tests import den-light's `synthetic.py` (finding `merge-den-photometry-synthetic-shadowed`); alone it passes 59 of 59. den-core's rung 9 timing test failed 2 times, then 1 time. den_lighting kept its single harness error.

**Statistics recomputed by hand** on small constructed cases, by independent code:
- k_L round trip: C_card 100 nc, ρ 0.18, E 174.533 lx give k_L 0.1000000 (cd/m²)/nc and 174.533 lx back. den-photometry matches. Stage 0 and 1 rightly have no k_L.
- IDF1 per window and the one-sided bounds (0.1391 at 20, 0.0950 at 30; Clopper-Pearson for k > 0) match. den-track's *gate* IDF1 does not: it maps each window separately and drops observations outside windows (0.933 against a standard 0.600).
- Session bootstrap (10,000 rounds, session unit, 95% percentile), ECE (10 bins), false-fine and the onset median match in den-n2. ECE puts a unit whose mean confidence sits exactly on a decile edge into the bin below (0.175 against 0.475 in one constructed case). den-n2's fine recall is per second, where the ladder says per episode (0.455 against 0.667).
- Hit rate at ±15%: den-verify's boundaries (15% inclusive, relative to the prediction) and the V11 residual (−2.99%) match. Its hit rate does not, once a bulb is held (1.0 reported against 0.50 true).
- Corrections per hour and φ (Pearson χ²/(n−p), log-hours offset) match on ordinary cases. φ differs when a session has zero hours, when exports leave gaps, and across stratum blocks. The planning-hours output drops to 0 h when φ̂ < 1.

Merge order used, onto claude/live-records 6772ed39: rung-r, den-take-script, plan-n1, den-label, plan-n2, den-n2, den-core, correction-capture, den-core-live, rung-1-register, rung-2-observe, rung-3-track, rung-5-bundle, rung-7-photometry, plan-rung-8, rung-8-stage0, rung-8-stage1, rung-10-verify.

## Suites per branch (run twice each, clean checkout of the reviewed commit)

| Branch @ commit | Suite | Run 1 | Run 2 |
|---|---|---|---|
| rung-r @ 8b1c945c | tools/den-take | 72 OK | 72 OK |
| den-take-script @ 23cdde50 | tools/den-take | 82 OK | 82 OK |
| den-label @ a28130a4 | tools/den-label · its den-take copy | 50 OK · 71 OK | 50 OK · 71 OK |
| den-n2 @ a39f02f5 | tools/den-n2 · its den-label copy | 40 OK · 50 OK | 40 OK · 50 OK |
| den-core @ 022aa9d5 | tools/den-core | 113 OK (1 skip) | 113 OK (1 skip) |
| den-core @ 022aa9d5 | den_lighting tests (Home Assistant 2026.2.3 installed) | 51, 1 harness error | 51, 1 harness error |
| correction-capture @ 37e47b20 | tools/correction-capture · tools/den-core | 22 OK · 113 OK | 22 OK · 113 OK |
| den-core-live @ 64bd23ca | tools/den-core · tools/correction-capture | 146, 2 timing failures · 22 OK | 146, 2 timing failures · 22 OK |
| rung-1-register @ ce55b0a9 | tools/den-register | 44 OK | 44 OK |
| rung-2-observe @ ab491a88 | tools/den-observe | 50 OK | 50 OK |
| rung-3-track @ 49ff481d | tools/den-track | 58 OK | 58 OK |
| rung-5-bundle @ cf659ba8 | tools/den-bundle | 81 OK | 81 OK |
| rung-7-photometry @ 1931c831 / 9a5023e3 | tools/den-photometry | 58 OK / 59 OK | 58 OK / 59 OK |
| rung-7-photometry @ 1931c831 | tools/den-light (its copy) | 86 OK | 86 OK |
| rung-8-stage0 @ 58aa1742 | tools/den-light | 86 OK | 86 OK |
| rung-8-stage1 @ 028981bc | tools/den-light | 139 OK | 139 OK |
| rung-10-verify @ b8ac320e | tools/den-verify | 30 OK | 30 OK |

At load 5, den-core-live's timing test passed 2 of 3 times and failed once at 0.102 s. The suites took several times longer than their READMEs say because the container was heavily loaded.

## Mutation testing by tool

Mutants were aimed at gate and verdict logic, so the survival rate is high by design. Each survivor that matters is a test-coverage finding in `findings.json`. The runners are `probes/<unit>/mutate_*.py`.

| Tool | Caught | Survived |
|---|---|---|
| den-take (R) | 23 | 7 |
| den-label (N1) | 14 | 8 |
| den-n2 (N2) | 7 | 15 |
| den-core (0) | 35 | 7 |
| correction-capture (0) | 22 | 12 |
| den-core-live (0, 9) | 9 | 18 |
| den-register (1) | 17 | 14 |
| den-observe (2) | 10 | 17 |
| den-track (3) | 13 | 11 |
| den-bundle (5) | 22 | 15 |
| den-photometry (7) | 15 | 6 |
| den-light Stage 0 (8) | 13 | 10 |
| den-light Stage 1 (8) | 13 | 8 |
| den-verify (10) | 13 | 12 |
