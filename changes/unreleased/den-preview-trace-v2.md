---
title: The den spotlight preview's fixture trace names who each move was for, and replays through the simulator
target: deploy
type: changed
---

The den-live spotlight preview's offline fixture trace becomes `c12-preview-trace-2` (contract 5), before the sofa cues.
- **Every row names its room model:** the transform, equipment revision and equipment digest, with the scan.
- **Every MOVE names its subject:** the track, the camera epoch and the association revision, plus the reply it answers. A lit MOVE without a reply is refused by the replay.
- **A run header** opens each day file a run writes to. It holds the run's mono-and-wall anchor, the codec's and the host simulator's SHA-256, and the binding. A run that crosses UTC midnight leaves each file whole, and `c12_replay.py` takes both files in order.
- **The host simulator's build is recorded** (`preview_trace.SIMULATOR_SHA256`), and the replay refuses a trace that names another build.
- **A committed test** (`test_trace_v2.py`) feeds rows a real preview wrote through the pinned firmware codec and, when `C12_TEST_SIM` is set, the v2-a host simulator. It also brings in the C12 lane's replay tests and their golden fixture.

Nothing is sent to a fixture: the trace stays encode-only.
