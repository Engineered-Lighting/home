# Frigate face-recognition bench — REPORT

Target identity: `marcelo` (only person at home during bench).

## Per-option summary

| Option | face p50 / p95 ms | CLIP p50 ms | emb CPU% | iGPU% | det fps | indoor reco | false-pos | unknown | mean conf |
|---|---|---|---|---|---|---|---|---|---|
| **A** |   49.4 /   50.9 |   95.0 |  67.5% |   4.6% |  134.8 |  42.9% (3/7) |  14.3% (1/7) |  42.9% (3/7) | — |
| **A2** |   65.3 /   69.7 |   90.8 |  79.7% |   3.0% |  129.4 |   0.0% (0/1) |   0.0% (0/1) | 100.0% (1/1) | — |
| **B** |   73.8 /   84.7 |   89.9 |  94.8% |   2.4% |  130.1 |  25.0% (2/8) |  25.0% (2/8) |  50.0% (4/8) | — |
| **C** |   52.5 /   57.5 |   85.2 |  69.1% |   4.3% |  126.2 |  31.8% (7/22) |  13.6% (3/22) |  54.5% (12/22) | — |
| **E** |    0.0 /    0.0 | 3525.5 | 144.2% |  51.0% |  130.2 |   0.0% (0/13) |   0.0% (0/13) | 100.0% (13/13) | — |

## Per-camera recognition rate (indoor cameras)

| Option | dining_room | kitchen | living_room | workshop | driveway |
|---|---|---|---|---|---|
| **A** |   0.0% (0/1) |   0.0% (0/1) | 100.0% (1/1) |  50.0% (2/4) |   0.0% (0/10) |
| **A2** | n=0 | n=0 |   0.0% (0/1) | n=0 |   0.0% (0/10) |
| **B** |  25.0% (1/4) |   0.0% (0/2) |  50.0% (1/2) | n=0 |   0.0% (0/5) |
| **C** |  30.8% (4/13) |   0.0% (0/4) |  50.0% (2/4) | 100.0% (1/1) |   0.0% (0/6) |
| **E** |   0.0% (0/5) |   0.0% (0/3) |   0.0% (0/5) | n=0 |   0.0% (0/9) |

## Environmental drift

A run #1 indoor reco: 42.9%
A run #2 indoor reco: 0.0%
Delta: **42.9 percentage points**

⚠ **WARNING** — drift > 10 pp. Lighting/environment shifted during the bench window. Re-run during a more stable window.
