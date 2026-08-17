# Goal: design the house's next AI architecture, by experiment

An overnight deep dive. Not a planning document — a loop of *download a
model, run it, measure it, revisit an assumption, plan the next
experiment*. Keep going until told to stop or out of budget. Prefer one
more measurement over one more paragraph.

**Companion docs (read before your first experiment, not all of them
every invocation):** `docs/QWEN38-MIGRATION.md` (measured baselines,
gates G1–G7, the trap index), `docs/QWEN38-CAPABILITY-ROADMAP.md`
(story groups A–F, group G video buffer), `docs/ARCHITECTURE_DECISIONS.md`,
`docs/QWEN38-CUTOVER-COMPRESSED.md`, `docs/UBUNTU-AI-HOST-STABILITY-REVIEW-2026-08-13.md`.

Branch: `claude/qwen3.8-integration-roadmap-0241ok` or a new
`claude/ai-architecture-*`. Every meaningful change needs a
`changes/unreleased/*.md` fragment per AGENTS.md.

---

## The question to answer

The house runs ONE model for everything: voice, tool calls, ambient
captions, grounded looks, video labelling. That is a compromise in both
directions — too slow for captions, too small for reasoning.

**The hardware allows better.** RTX PRO 6000 Blackwell, 97,887 MiB total,
of which ~11.6 GiB is already spoken for by non-LLM tenants (chatterbox
5.3, parakeet 3.4, kokoro 1.4, host ~0.7). **≈ 84 GiB is available to
models.**

Design and *prove* an architecture along these lines, adjusting as the
measurements demand:

- **a FAST vision-capable model** — ambient captions (~1,400/day), grounded
  looks, first-pass voice. Budget: ambient p95 ≤ 1.5 s.
- **a STRONGER model** — tool-heavy voice, nightly video distillation
  (roadmap group G), memory/understanding work. Latency-tolerant when
  batched, budget-bound when interactive.
- **headroom for a world model** (V-JEPA 2.1 or successor) for spatial /
  temporal understanding. The owner guesses ~26 GiB — **verify that
  number, do not inherit it.**

Whether that is two vLLM instances, one instance with two served models,
or something else is *yours to determine by measurement*. So is whether
Qwen3.8-27B belongs in it at all — the owner is interested, not committed.

---

## Every invocation

1. **Detect state.** `git log`; which experiments have run
   (`/srv/data/eval/arch/`); what the last invocation concluded and what it
   said to try next. Read your own previous findings before re-deriving them.
2. **Sanity-check the host.** `curl -s localhost:8092/healthz`;
   `ssh -p 22222 root@homeassistant.local echo ok`; working tree clean.
   Run the 5-step admission check
   (`tools/qwen38-phase3-preflight.py` covers it) and **abort-no-retry on
   failure**.
3. **Run the next experiment**, not the next plan. Record the result even
   when it is boring or negative — especially then.
4. **Record and push.** Update `docs/AI-ARCHITECTURE-EXPERIMENTS.md` (create
   it) with: what you tried, what you measured, what it killed, what it
   opened, and the single next experiment. Commit + push.

---

## Research first, and do not trust your own knowledge of the field

**Your training cutoff predates today.** Model releases, vLLM versions, and
benchmark results from the last months are things you do not know. Before
choosing candidates, actually search: what shipped since mid-2026 in
open-weight VLMs, small fast VLMs, world models, and speculative decoding.
Read release notes and model cards, not recollection. Record what you find
with dates and links.

Specific things to establish rather than assume:
- V-JEPA 2.1 (or its current successor): real VRAM footprint at the
  precision you would run, throughput, what it actually gives a home —
  and whether it needs a GPU resident *continuously* or can be batched
  nightly. **The 26 GiB figure is the owner's guess.**
- Whether a small VLM exists that holds ambient captioning quality at a
  fraction of the cost. Already cached locally:
  `Qwen3-VL-4B-Instruct-FP8` (5.7 G), `Qwen3.6-27B-FP8` (29 G),
  `Qwen3.8-27B-FP8` (29 G), `Qwen2.5-VL-32B-AWQ` (20 G),
  `Qwen3-VL-30B-A3B-FP8` (31 G, incumbent).
- Whether vLLM 0.20.2 can serve two models in one process, and what
  two instances cost in contention. **The image digest is pinned; a
  version bump is a separate, deliberate decision, never bundled.**

---

## Hard guardrails (violating any is failure)

- **The house must work in the morning.** Every experiment ends with the
  incumbent restored and voice verified by an actual query returning actual
  text. Not "containers are up" — a real answer.
- **Read-only toward the live stack by default.** Model swaps, compose
  edits, container recreates: allowed for experiments, but flag →
  quiesce → stash → change → measure → **restore** → verify → clear flag.
  `docs/QWEN38-CUTOVER-COMPRESSED.md` is the pattern.
- ⚠ **NEVER write `/config/.storage/*` on a running Home Assistant.** This
  caused a real voice outage on 2026-08-16: the file content was correct
  (sha256-verified) but HA holds it in memory, and a `reload_config_entry`
  is NOT equivalent to a restart. Use HA's config-flow/WebSocket API, or
  restart HA Core as part of the procedure, or don't touch it.
- **No host image builds** (D9 + the permanent quarantine). Off-host build
  + `docker save/load`, or bind-mount a patched file for experiments.
- **Never deploy repo copies** of metrics-sidecar, the EOC component,
  vision-sidecar, or intelligence. Live is truth; live sources are archived
  at `/srv/data/eval/migration/phase0/live-sources/`.
- **Never touch** ACTIVATION_PATHS. Run `tools/check-activation-paths.py`
  before any merge — it is installed as a pre-commit hook. Honour PIN
  FREEZE notices from the Home Agent session, and pre-announce any window
  that affects HA or the conversation path.
- **Nothing under `/srv/home-agent/**`.** Experiment artefacts go in
  `/srv/data/eval/arch/`.
- **The model never writes Home Agent memory** (roadmap group D). Any
  understanding a model produces is a non-authoritative observation.
- No cloud egress of camera imagery. ntfy is public `ntfy.sh` — text only.
- The s2s profile never starts.

---

## What is already measured — do not spend budget rediscovering it

Incumbent (Qwen3-VL-30B-A3B-FP8, vLLM 0.20.2, util 0.70), all at the
metrics-sidecar with cache state stated:

| | |
|---|---|
| KV pool | 366,848 tokens |
| prefix caching | **works, worth 5.23×** (99.9% hit warm, 0.1% busted) |
| warm / shared-prefix / busted p50 | 0.068 / 0.070 / 0.356 s |
| TTFT warm / busted | 0.037 / 0.332 s |
| ambient caption p95 | 0.176 s (budget 1.5 s) |
| 4-frame clip p95 | 0.463 s (budget 6 s) |
| voice e2e p50 / p95 | 1.84 s / 6.12 s busy, 3.73 s quiet |
| G4 false presence | 5/35 on verified-empty frames |
| grounded box extraction | **0/13** under the deployed prompt |

Candidate (Qwen3.8-27B-FP8): KV pool 497,097 (64 KiB/token geometry
confirmed — only 16 of 64 layers hold paged KV); grounded boxes **3/3**;
no zero-arg tool hang; **`preserve_thinking: false` causes the reasoning
leak** (26/80 with it, 1/80 without — the plan's own R5 recommendation was
the bug); thinking ON + `--reasoning-parser qwen3` = 0/40 leaks but ~10×
slower and 31.6 s voice.

Harnesses that already exist — **reuse, do not rewrite**:
`qwen38_capture.py` (latency instrument; refuses an unlabelled number),
`qwen38_gates.py` (production parsers + exact statistics),
`qwen38-matrix-driver.py` (cells, canary, drift control),
`qwen38-leak-repro.py` (ablation), `gate-g1-captions.py`,
`gate-g2-labeler-replay.py`, `gate-g4-negatives.py`,
`gate-g5-leak-grep.py`, `qwen38-cutover-smoke.py`,
`qwen38-phase3-preflight.py`, `check-activation-paths.py`.
Frozen corpora: G1 50 daylight frames + incumbent captions, G4 35
owner-verified negatives, 683 labeler keyframe windows.

---

## Method — these were learned expensively

1. **Verify every data source before building on it.** Three for three,
   the sources this project assumed were empty: `/trace` had no producer,
   the labeler had no request log, and a gate's baseline was zero. Open the
   file before writing the harness.
2. **One variable at a time.** Two cutover attempts were wasted on a flag
   added "while we're here". If you change two things, you have measured
   nothing.
3. **Build the reproducer before the third attempt, not after.** A
   4-minute ablation found in one run what two 10-minute cutover cycles
   could not.
4. **Sample enough to see the fault.** A 2-sample check against a 15%
   intermittent failure passes ~72% of the time. State n, and state the
   miss probability when you claim something is fixed.
5. **Every latency number carries its measurement point and cache state.**
   `qwen38_capture` enforces this; do not route around it.
6. **A negative result is a result.** Record what a config *cannot* do and
   move on. Do not iterate on wording.
7. **Say when you were wrong, plainly, and correct the record.**

---

## Experiment agenda (a starting order, not a script)

Re-plan after every result.

- **E1 — VRAM budget, measured.** Actual free VRAM with the live stack up.
  What does a second vLLM instance cost in overhead? Does 0.20.2 support
  multiple served models in one process? Establish the real envelope
  before designing against it.
- **E2 — the fast model.** Bring up `Qwen3-VL-4B-Instruct-FP8` (already
  cached) on a second port. Score it on the FROZEN G1 corpus and G4
  negatives against the incumbent's captured arms — the comparison is
  already paid for. Is small-and-fast good enough for ambient?
- **E3 — two instances, contention.** Both up, ambient hammering the small
  one while voice hits the large one. Does either budget survive? This is
  where `--max-num-seqs 4` and time-slicing bite.
- **E4 — Qwen3.8 revisited.** With `preserve_thinking` UNSET (the fix
  found on 2026-08-16), measure voice latency thinking-OFF — the number
  that was never obtained. If it lands, the leak is 1.25% and downstream
  recovery (`thinking_routing.recover_leaked_content`, 26/26 on real
  leaks) closes it.
- **E5 — MTP.** Never tried. The checkpoint ships an MTP head (84.8%
  official acceptance). Constraint set in R3 of the migration doc. This is
  the untested lever with the largest headroom on decode.
- **E6 — world model.** Establish V-JEPA 2.1's real footprint and whether
  it earns residency or runs nightly. Only then reserve VRAM for it.
- **E7 — routing.** Which surface goes to which model, enforced where?
  `tools/.../thinking_routing.py` already proves the metrics-sidecar proxy
  is a viable single choke point.
- **E8 — the architecture memo.** Only once E1–E7 have produced numbers:
  what the house should run, what it costs, what it gives up, and the
  migration path from here.

---

## Priorities when several things are possible

Measurement over design. A number that kills an option is worth more than a
paragraph defending one. When an experiment needs the live stack, batch it
with others into one window rather than taking three. If you find yourself
about to write a third document without having run anything, run something
instead.
