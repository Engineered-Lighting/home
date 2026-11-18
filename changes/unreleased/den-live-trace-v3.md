---
title: The den spotlight's fixture trace records what was sent, and the replay checks it per board
target: internal
type: changed
---

den-live's fixture trace becomes `c12-fixture-trace-3`, ready for the desk stage (plan 05, step 05-3).
- **Send rows:** after each call or drop, the FC13 sender's send row follows the encoded row. It holds the wire `seq` and bytes the `c12` integration used, the board's answer and the outcome.
- **The run header names the desk mapping:** the one fixture a run may send to, or `null` for a virtual-only run, which may send nothing.
- **`c12_replay.py` gains five send checks:** each send names an earlier encoded row; the wire bytes equal the encoded bytes except the `seq` and `lit_seq`; a keep-lit renews the latest accepted lit move; per board, across runs, every wire `seq` is new; and, with `--sim`, the frames give the board's own answers in the simulator.
- **A committed test** (`test_trace_v3.py`) runs it on two den-live runs with a restart between them, and shows each check refusing its own fault.

Encoded rows are unchanged, and still written before any send. The replay still reads trace-1 and trace-2.
