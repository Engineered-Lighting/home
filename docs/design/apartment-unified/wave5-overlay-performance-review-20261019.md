# F01 matched desktop measurement results - fixed V2 acquisition

Outcome: the allocation-only production correction is structurally proved, but performance is not cleared. Six fixed source phases completed once (216 selected, nonoverlapping production windows). The preimage cycle requires review; the optimized cycle is inconclusive under the predeclared exact rules. No deadline, frame count, quality, scene visibility or assertion changed; no rerun was used to select a green result.

## Runtime and conditions

All six used bundled Playwright1.62.1, Edge155.0.4283.62, Node24.14.1, RTX4090/D3D11, desktop1366x768, actualcanvas1026x716, browser/rendererDPR1, maxFidelity/maximum, snapHome0, native virtual spotlights ON and default browser pacing. Owner's private preview remained open; its GPU/CPU idleness was not confirmed or claimed. The benchmark served authored fixture data only, with no household files, physical commands, camera/model sessions or writes. All source/readiness/fixture-state/fence/cleanup checks passed.

The first failed Chromium149 acquisition and single Edge setup preflight remain separate; the latter is capacity evidence, not a source improvement or historical166fps equivalence. Historical runtime mismatch was caused by choosing C:/Claude/home Playwright1.61.1 rather than the handoff's bundled runtime. No browser install or pacing flag change was used to correct it.

## Fixed source phases

| Phase | Duration ms | Selected windows | Result | Receipt SHA256 |
| --- | ---: | ---: | --- | --- |
| red-baseline-before | 63598 (last completed checkpoint) | 36 | passed; requires-review | 6abac9b3b7f3856305b2318bc26286d553d8e9b8566c09c9e4454387bd9a6cf7 |
| red-preimage | 62228 (last completed checkpoint) | 36 | passed; requires-review | 2522f3d35105c091d6cd4fb9a50d8ac532c5d027bbe788c0f27475480d19a366 |
| red-baseline-after | 63819 (last completed checkpoint) | 36 | passed; requires-review | 2c7932e6a467dcc715b616115c061d36ef57e2b28ee09708e5b87a0432dbafb3 |
| optimized-baseline-before | 61738 (last completed checkpoint) | 36 | passed; requires-review | 20b67118f27479898ea65a158a38142753d0c85ccb2ec9a7bc9081b5c0ff2e19 |
| optimized-candidate | 62139 (last completed checkpoint) | 36 | passed; requires-review | 511d2796c38a640aaf794a9cbf26383af2bd246080e0cb18cfd5126932d55f57 |
| optimized-baseline-after | 62572 (last completed checkpoint) | 36 | passed; requires-review | 8aefe6560d5905f0497c7d7ff57d0147adcbd5fede01c395d91bfb43e91a0d3f |

Receipt folders, in the same order:

- .tmp/apartment-overlay-matched-edge-browser/run-1792372374799-red-baseline-before/receipt.json
- .tmp/apartment-overlay-matched-edge-browser/run-1792372453437-red-preimage/receipt.json
- .tmp/apartment-overlay-matched-edge-browser/run-1792372523828-red-baseline-after/receipt.json
- .tmp/apartment-overlay-matched-edge-browser/run-1792372611995-optimized-baseline-before/receipt.json
- .tmp/apartment-overlay-matched-edge-browser/run-1792372704798-optimized-candidate/receipt.json
- .tmp/apartment-overlay-matched-edge-browser/run-1792372775822-optimized-baseline-after/receipt.json

Public baseline substitutes engine557164592f916cc4f93b79214f560d24d6f797b6 only. Preimage serves engine/leaf/picking from a6f057bba5333fab54ac2cd646a8bf45ecc50f36. Optimized serves only the allocation-corrected leafSHAf56bdc8d346923dd92a29892d71615ae0e429f6ffaf1e13c7aa9042c1f4d3c7e; engine/picking remain frozen. Every served file inventory/hash matches between source phases except declared engine/leaf substitutions. Both cycles have zero invariant failures.

## Exact comparison results

Preimage: requires-review; 4 exact range flags, all renderP95; all six frame-p95 comparisons are within their local empty ranges. No source-control drift flag. Optimized: inconclusive; 6 exact range flags (one frame-p95, five renderP95), one source-control drift flag. No epsilon or rounding removes any flag. Negative changes are retained as improvements requiring two-sided range review, not mislabeled regressions.

The optimized Photo4 frame flag is B1=4.30000000000291ms versus empty maximum4.299999999995634ms, B2=4.299999999995634ms. Its balanced contrast is +0.049999999999272404ms; the same candidate block's A/A difference is +0.09999999999126885ms. These exact facts do not establish clearance. The optimized Mesh renderP95 source-control drift is +0.0999999701998604ms versus frozen descriptive envelope0.09999997019622242ms. It remains a drift flag; after-controls do not widen the envelope.

The first red-cycle empty Mesh4 baseline A3 had production frame-p958.30000000000291ms against4.3ms other rows; every window's median RAF remained4.2ms. The isolated tail produced a2ms descriptive envelope and is explicitly not an acceptance tolerance. Nominal-empty Photo4 proxy tails likewise occurred before candidate.

| Leaf | Mode | Anchors | Frame ABBA delta ms | Frame exact range | Render proxy ABBA delta ms | Proxy exact range |
| --- | --- | ---: | ---: | --- | ---: | --- |
| preimage | points | 1 | -1.8189894035458565e-12 | within-observed-empty-range | 0.4499999999998181 | requires-review |
| preimage | points | 4 | 0 | within-observed-empty-range | 1.1919837561435997e-8 | within-observed-empty-range |
| preimage | splat | 1 | 0 | within-observed-empty-range | 1.788066583685577e-8 | requires-review |
| preimage | splat | 4 | 0 | within-observed-empty-range | -0.35000001192020136 | within-observed-empty-range |
| preimage | mesh | 1 | 0 | within-observed-empty-range | -0.04999999403662514 | requires-review |
| preimage | mesh | 4 | -3.637978807091713e-12 | within-observed-empty-range | 1.1499999821171514 | requires-review |
| optimized | points | 1 | 9.094947017729282e-13 | within-observed-empty-range | 0.0500000000001819 | requires-review |
| optimized | points | 4 | 0 | within-observed-empty-range | 0.05000001788175723 | requires-review |
| optimized | splat | 1 | 0 | within-observed-empty-range | -0.09999999999672582 | requires-review |
| optimized | splat | 4 | 0.049999999999272404 | requires-review | 0.04999998807761585 | within-observed-empty-range |
| optimized | mesh | 1 | 0 | within-observed-empty-range | 0.10000000000218279 | requires-review |
| optimized | mesh | 4 | 0 | within-observed-empty-range | 0.4499999999970896 | requires-review |

## Source-empty controls (distinct from active anchor cost)

| Cycle | Mode | Metric | Candidate empty delta vs paired public engine ms | Before/after source drift ms | Drift flag |
| --- | --- | --- | ---: | ---: | --- |
| preimage | points | p95 | 0 | 0 | false |
| preimage | points | renderP95 | 0.15000001788121153 | -2.9802322387695312e-8 | false |
| preimage | splat | p95 | 1.8189894035458565e-12 | 0 | false |
| preimage | splat | renderP95 | 0.5499999761595973 | 0.09999996423903212 | false |
| preimage | mesh | p95 | 0 | 0 | false |
| preimage | mesh | renderP95 | 0.05000000000291038 | 0.6999999642357579 | false |
| optimized | points | p95 | 0 | 0 | false |
| optimized | points | renderP95 | 6.556456355610862e-8 | -3.5763150663115084e-8 | false |
| optimized | splat | p95 | -3.637978807091713e-12 | -3.637978807091713e-12 | false |
| optimized | splat | renderP95 | 0.050000011922747944 | -0.4000000238411303 | false |
| optimized | mesh | p95 | 7.275957614183426e-12 | -7.275957614183426e-12 | false |
| optimized | mesh | renderP95 | 0.10000000000218279 | 0.0999999701998604 | true |

## Raw selected leaf-condition rows

Each array preserves slot order A0,A1,A2,B1,B2,A3 with no rounding. Full raw production-stat emissions and independent RAF timestamps/deltas remain in receipts. Production renderP95 is RAF-timestamp-to-render-completion elapsed, including entry/queue timing; it is not isolated CPU or GPU time.

- preimage points 1 anchors: frame=[4.299999999999272,4.300000000001091,4.300000000001091,4.299999999999272,4.299999999999272,4.300000000001091]; renderProxy=[1.100000023841858,2.899999976158142,1.2000000119205652,1.100000023841858,3.5999999761588697,2.5999999880805262]; draws=[{"slot":"A0","calls":76,"tris":2508},{"slot":"A1","calls":76,"tris":2508},{"slot":"A2","calls":76,"tris":2508},{"slot":"B1","calls":76,"tris":2508},{"slot":"B2","calls":76,"tris":2508},{"slot":"A3","calls":76,"tris":2508}]
- preimage points 4 anchors: frame=[4.299999999999272,4.299999999999272,4.299999999999272,4.299999999999272,4.299999999999272,4.299999999999272]; renderProxy=[3.0999999761588697,1.0999999761588697,1.0999999880805262,1.0999999880805262,1.0999999999985448,1.0999999761588697]; draws=[{"slot":"A0","calls":76,"tris":2508},{"slot":"A1","calls":76,"tris":2508},{"slot":"A2","calls":76,"tris":2508},{"slot":"B1","calls":76,"tris":2508},{"slot":"B2","calls":76,"tris":2508},{"slot":"A3","calls":76,"tris":2508}]
- preimage splat 1 anchors: frame=[4.299999999999272,4.299999999999272,4.299999999999272,4.299999999999272,4.30000000000291,4.30000000000291]; renderProxy=[1.500000023843313,1.5000000119216566,1.5,1.500000023843313,1.5999999999985448,1.5999999880805262]; draws=[{"slot":"A0","calls":76,"tris":2508},{"slot":"A1","calls":76,"tris":2508},{"slot":"A2","calls":76,"tris":2508},{"slot":"B1","calls":76,"tris":2508},{"slot":"B2","calls":76,"tris":2508},{"slot":"A3","calls":76,"tris":2508}]
- preimage splat 4 anchors: frame=[4.299999999995634,4.30000000000291,4.30000000000291,4.30000000000291,4.30000000000291,4.30000000000291]; renderProxy=[1.5999999880805262,1.6999999761610525,1.6000000119238393,1.5999999880805262,1.7999999761595973,2.499999976156687]; draws=[{"slot":"A0","calls":76,"tris":2508},{"slot":"A1","calls":76,"tris":2508},{"slot":"A2","calls":76,"tris":2508},{"slot":"B1","calls":76,"tris":2508},{"slot":"B2","calls":76,"tris":2508},{"slot":"A3","calls":76,"tris":2508}]
- preimage mesh 1 anchors: frame=[4.30000000000291,4.299999999995634,4.30000000000291,4.30000000000291,4.30000000000291,4.30000000000291]; renderProxy=[1.199999988079071,1.1999999761610525,1.199999988079071,1.1000000119238393,1.0999999880805262,1.0999999999985448]; draws=[{"slot":"A0","calls":77,"tris":2514},{"slot":"A1","calls":77,"tris":2514},{"slot":"A2","calls":77,"tris":2514},{"slot":"B1","calls":77,"tris":2514},{"slot":"B2","calls":77,"tris":2514},{"slot":"A3","calls":77,"tris":2514}]
- preimage mesh 4 anchors: frame=[4.30000000000291,4.299999999995634,4.30000000000291,4.30000000000291,4.299999999995634,4.30000000000291]; renderProxy=[1.8000000238389475,1.0999999999985448,1.100000023841858,1.3000000238389475,3.0999999880805262,1.000000023843313]; draws=[{"slot":"A0","calls":77,"tris":2514},{"slot":"A1","calls":77,"tris":2514},{"slot":"A2","calls":77,"tris":2514},{"slot":"B1","calls":77,"tris":2514},{"slot":"B2","calls":77,"tris":2514},{"slot":"A3","calls":77,"tris":2514}]
- optimized points 1 anchors: frame=[4.299999999999272,4.300000000001091,4.300000000001091,4.300000000001091,4.300000000001091,4.299999999999272]; renderProxy=[1.0000000238414941,1.100000023841858,1.100000023841858,1.1000000357635145,1.0999999880787072,1]; draws=[{"slot":"A0","calls":76,"tris":2508},{"slot":"A1","calls":76,"tris":2508},{"slot":"A2","calls":76,"tris":2508},{"slot":"B1","calls":76,"tris":2508},{"slot":"B2","calls":76,"tris":2508},{"slot":"A3","calls":76,"tris":2508}]
- optimized points 4 anchors: frame=[4.299999999999272,4.299999999999272,4.299999999999272,4.299999999999272,4.299999999999272,4.299999999999272]; renderProxy=[1.000000023843313,1.0000000119216566,1.0999999880805262,1.1000000357635145,1.100000023841858,1.0000000357613317]; draws=[{"slot":"A0","calls":76,"tris":2508},{"slot":"A1","calls":76,"tris":2508},{"slot":"A2","calls":76,"tris":2508},{"slot":"B1","calls":76,"tris":2508},{"slot":"B2","calls":76,"tris":2508},{"slot":"A3","calls":76,"tris":2508}]
- optimized splat 1 anchors: frame=[4.299999999999272,4.299999999999272,4.299999999999272,4.299999999999272,4.299999999995634,4.299999999995634]; renderProxy=[1.5000000119216566,1.5000000119216566,1.5999999999985448,1.5000000119216566,1.4000000238447683,1.5000000357613317]; draws=[{"slot":"A0","calls":76,"tris":2508},{"slot":"A1","calls":76,"tris":2508},{"slot":"A2","calls":76,"tris":2508},{"slot":"B1","calls":76,"tris":2508},{"slot":"B2","calls":76,"tris":2508},{"slot":"A3","calls":76,"tris":2508}]
- optimized splat 4 anchors: frame=[4.200000000004366,4.299999999995634,4.200000000004366,4.30000000000291,4.299999999995634,4.299999999995634]; renderProxy=[1.500000023843313,1.500000023843313,1.500000023843313,1.5999999999985448,1.5999999999985448,1.5999999999985448]; draws=[{"slot":"A0","calls":76,"tris":2508},{"slot":"A1","calls":76,"tris":2508},{"slot":"A2","calls":76,"tris":2508},{"slot":"B1","calls":76,"tris":2508},{"slot":"B2","calls":76,"tris":2508},{"slot":"A3","calls":76,"tris":2508}]
- optimized mesh 1 anchors: frame=[4.299999999995634,4.30000000000291,4.30000000000291,4.299999999995634,4.30000000000291,4.299999999995634]; renderProxy=[1.100000023841858,1.0999999880805262,1.0000000119180186,1.0000000357613317,1.2000000119223841,1.0000000357613317]; draws=[{"slot":"A0","calls":77,"tris":2514},{"slot":"A1","calls":77,"tris":2514},{"slot":"A2","calls":77,"tris":2514},{"slot":"B1","calls":77,"tris":2514},{"slot":"B2","calls":77,"tris":2514},{"slot":"A3","calls":77,"tris":2514}]
- optimized mesh 4 anchors: frame=[4.30000000000291,4.299999999995634,4.30000000000291,4.30000000000291,4.30000000000291,4.30000000000291]; renderProxy=[1.1000000119238393,1.100000023841858,1.0999999999985448,1.1000000357598765,3.6999999999970896,2.800000035764242]; draws=[{"slot":"A0","calls":77,"tris":2514},{"slot":"A1","calls":77,"tris":2514},{"slot":"A2","calls":77,"tris":2514},{"slot":"B1","calls":77,"tris":2514},{"slot":"B2","calls":77,"tris":2514},{"slot":"A3","calls":77,"tris":2514}]

## Diagnosis and limits

1. Observation/timer/scheduling noise is demonstrated, but not a license to waive acceptance. Original overlapping stats windows and active-only projection/draw observers were removed. With identical observation and disjoint rings, the unchanged preimage no longer reproduces the old +0.075ms Mesh frame contrast; its Mesh1/4 contrasts are0/-3.637978807091713e-12ms. Exact tail and floating representation flags still occur in empty and active controls. Known open-preview workload prevents an exclusive-GPU attribution.
2. The actual source correction reduces deterministic transient Vector3 allocations from3to1 per anchor (4anchors12to4), while preserving fresh/stable world callback arguments, canonical transform-once semantics, all currentness/layout guard placements and engine/picking ABI. The focused author/independent QA suite reported34/36 before (two expected allocation reds),36/36 after. This is reduced work, not measured performance acceptance.
3. Three viewport/current admissions per nonempty update, duplicate camera projection via the required callback, and existing1/4 additional sphere draws remain. Their cost is not separated by this one fixed acquisition. Proxy contrasts have no consistent monotonic mode/count signature and share tail excursions with empty controls. No new renderer/guard architecture is justified by these timings alone.

The smallest bounded source correction is ready for source review, but F01 frame-cost acceptance remains qualified/partial. No production, installed-source, actual-room visual, physical or deployment claim follows. Root/independent review owns next scope; no repeated performance search or further guard/engine edit was attempted.

## Frozen artifacts

- .tmp/overlay-matched-edge-protocol.md: SHA2560a74571f6d2af195d6abe17b9e4b449dafdb5eb0e3b46b66c7956105024da541
- .tmp/test-apartment-overlay-matched-edge-browser.mjs: SHA256413711314f019a3c607117a61a338918fe378c79de0a174608f7d6daa0354edf
- .tmp/analyze-apartment-overlay-matched-edge.mjs: SHA2564e2c3ccdf1d421fae3e68d819a8ca6176a26fabba475bc619086942c3cb677ff
- .tmp/apartment-overlay-matched-edge-browser/run-1792372523828-red-baseline-after/red-matched-analysis.json: SHA2561faea99e05ab1c6af8fb8661246821a783633580ee56cab421af0403ee71ad51
- .tmp/apartment-overlay-matched-edge-browser/run-1792372775822-optimized-baseline-after/optimized-matched-analysis.json: SHA2564d63583649376384a46b2d9e2673940a57932f2d0472af8578191aa2cb131157
