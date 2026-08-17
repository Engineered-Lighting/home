# AI architecture — experiments

The house runs one model for everything. This document asks, by measurement,
whether it should run more than one, and which. It is a lab notebook: newest
session first, negative results kept.

Companions: `QWEN38-MIGRATION.md` (baselines, gates, trap index),
`QWEN38-CAPABILITY-ROADMAP.md` (story groups), `ARCHITECTURE_DECISIONS.md`.

Artefacts: `/srv/data/eval/arch/`.

---

## ⚠ OPEN — voice is down, and it is not the LLM (found 2026-08-16 23:06 PDT)

**This predates tonight's experiments and survived them. It needs one command
from the owner.**

`conversation.extended_openai_conversation` — the agent the "Local AI (Qwen +
Parakeet + Chatterbox)" assist pipeline uses — returns
`response_type: action_done` with **empty speech in 0.01–0.03 s**, for every
utterance including ones that cannot be tool calls ("Tell me a joke about
cats"). Zero LLM traffic: the sidecar's `chat/completions` count does not move
when the agent is invoked (before=730, after=730).

What is *not* wrong:

| checked | result |
|---|---|
| vLLM engine + sidecar path | **healthy** — `What is the capital of France?` → `"The capital of France is Paris."` in 0.09 s |
| EOC on-disk config | **intact** — subentry `conversation`: `chat_model: qwen3-vl-30b`, `max_tokens: 2000`, prompt 33,760 chars, functions 21,671 chars |
| EOC component on disk | present, `/config/custom_components/extended_openai_conversation` |
| `/run/ha-maintenance` | cleared; only feeds `/usr/local/sbin/ha-reachable` (paging), not the conversation path |
| `homeassistant.reload_config_entry` | HTTP 200, **did not fix it** |

Last successful HA → LLM call: **2026-08-17 05:19:22 UTC = 2026-08-16 22:19
PDT**, ~30 min before this session began (22:50 PDT). The incumbent vLLM
container had itself restarted at ~22:16 PDT, by something other than this
session.

This is exactly the failure mode the goal brief documents: *"the file content
was correct (sha256-verified) but HA holds it in memory, and a
`reload_config_entry` is NOT equivalent to a restart."* Tonight's reload
reproduced that finding on the nose.

**Remedy (owner):** restart HA Core.

```bash
ssh -p 22222 root@homeassistant.local 'ha core restart'
# then verify with a real answer, not a health check:
python3 - <<'EOF'
import json,pathlib,urllib.request
tok=[l.split('=',1)[1].strip().strip('"\'') for l in
     pathlib.Path('/opt/home-ai-voice/.env').read_text(errors='replace').splitlines()
     if l.startswith('HA_TOKEN=')][0]
b=json.dumps({'text':'Tell me a one sentence joke about cats.','language':'en',
              'agent_id':'conversation.extended_openai_conversation'}).encode()
r=urllib.request.Request('http://192.168.0.125:8123/api/conversation/process',
    data=b,headers={'Authorization':f'Bearer {tok}','Content-Type':'application/json'})
print(json.load(urllib.request.urlopen(r,timeout=120))['response']['speech']['plain']['speech'])
EOF
```

**Why this session did not run it.** The brief requires pre-announcing any
window that affects HA or the conversation path, and the owner was asleep. An
HA Core restart is outward-facing — it drops lights, climate and automations
for the duration, and a restart that does not come back is a strictly worse
outcome than a voice agent that is already silent. The scoped remedy
(reloading only the broken integration) was tried first and failed. Escalating
to a full restart unannounced is the owner's call, not this session's.

Everything this session *did* touch was restored and verified: see E1.

---

## Session 1 — 2026-08-16 (22:50–23:30 PDT)

Admission check GO (22 mechanical checks). Flag set, additive experiment,
flag cleared, incumbent byte-identical afterwards.

### Headline

**The premise that the house needs a *fast* model is not supported.** The
incumbent's ambient captioning is already 8.5× inside its budget, and the
cached small VLM is not faster — it is only worse. What the house may need is
a *stronger* model, and the VRAM to run one alongside the incumbent exists
once the incumbent's oversized KV pool is right-sized.

### E1 — the VRAM envelope, measured

vLLM **0.20.2** confirmed by `vllm.__version__` (torch 2.11.0+cu130, CUDA
13.0). The image digest is unchanged and was not bumped.

Non-LLM tenants, mapped to processes:

| tenant | MiB |
|---|---|
| chatterbox (`server.py`) | 5,282 |
| parakeet (`wyoming_vad_asr_server.py`) | 3,392 |
| kokoro (`uvicorn api.src.main:app`) | 1,422 |
| comfyui (`/srv/comfyui`) | 662 |
| driver/context overhead | ~1,175 |
| **total non-LLM** | **~11.9 GiB** |

GPU total 97,887 MiB → **≈ 84 GiB available to models. The owner's figure is
confirmed.**

**The incumbent's KV pool is ~2.8× oversized.** At `--gpu-memory-utilization
0.70` it holds 68,702 MiB, of which **33.59 GiB is KV = 366,880 tokens**.
vLLM reports `Maximum concurrency for 32,768 tokens per request: 11.20x` —
but `--max-num-seqs 4` means the scheduler will never run more than 4. The
active ceiling is 4 × 32,768 = 131,072 tokens = **12.0 GiB**. The remaining
~21.6 GiB serves only prefix-cache retention.

**Verified KV geometry** (read from the checkpoints' own `config.json`, not
assumed):

| model | layers | kv heads | head_dim | full-attn layers | **KV/token bf16** |
|---|---|---|---|---|---|
| Qwen3-VL-4B-Instruct-FP8 (dense) | 36 | 8 | 128 | 36 | **144 KiB** |
| Qwen3-VL-30B-A3B-FP8 (MoE, incumbent) | 48 | 4 | 128 | 48 | **96 KiB** |
| Qwen3.8-27B-FP8 (hybrid) | 64 | 4 | 256 | **16** | **64 KiB** |

The 4B figure is confirmed against behaviour, not just arithmetic: vLLM
refused to start at `--max-model-len 32768` with *"4.5 GiB KV cache is
needed"* — 4.5 GiB / 32,768 = exactly 144 KiB/token. The Qwen3.8 row confirms
migration-doc R1.

> **The smallest model has the largest KV footprint.** The 4B needs 1.5× the
> incumbent's KV per token and 2.25× the Qwen3.8 candidate's. "Small model"
> does not mean "small memory" — it means small *weights*. Any two-model
> arithmetic must use the measured per-token figure, not parameter count.

**The prefix-cache working set is 1.24 GiB — 3.7% of the pool it lives in.**
This is the number that decides whether the pool can be cut. The 5.23×
prefix-caching win comes from voice turns re-sending one large shared prefix:
the EOC subentry's prompt (33,760 chars) plus its function schema (21,671
chars). Tokenised with the incumbent's own tokenizer, that prefix is
**13,552 tokens**:

| | |
|---|---|
| EOC shared prefix | 13,552 tokens |
| its KV at 96 KiB/token | **1.24 GiB** |
| as a fraction of the 366,880-token pool | **3.69%** |

The retained set is bounded by the number of distinct system prefixes, not by
traffic volume — ambient captions share only a ~50-token system prompt and
each image is unique, so they contribute almost nothing to retention. Active
KV (4 × 32,768 @ 96 KiB) is 12.0 GiB; the whole reused prefix set is another
1.24 GiB. **~13.3 GiB does the work that 33.59 GiB is currently reserved
for.**

**A second vLLM instance costs ~1.0 GiB beyond weights + KV.** Measured with
both engines resident: the 4B process held 13,344 MiB = 6.14 GiB weights +
5.92 GiB KV + **0.97 GiB fixed overhead** (CUDA context, graphs, activations).

**vLLM 0.20.2 cannot serve two models in one process.** Confirmed by source
inspection, not recollection: `vllm serve [model_tag]` is singular, and
`--models`, `--model-config-list` and `multi_model` do not exist in
`vllm/entrypoints/openai/cli_args.py` at this tag. Two models means two
processes, at ~1.0 GiB each.

*Restoration:* the 4B container was removed; VRAM returned to 80,652 MiB used
/ 16,595 MiB free — identical to the pre-experiment snapshot — and the
incumbent stayed `healthy` throughout, never restarted.

### E2 — the fast model: **killed**

`Qwen3-VL-4B-Instruct-FP8` brought up on port 8001 alongside the untouched
incumbent (`--max-model-len 8192`, `--gpu-memory-utilization 0.15`; KV pool
5.92 GiB = 43,072 tokens). Scored on the **frozen** G1 and G4 corpora against
the incumbent's already-captured arms.

**It is not faster.**

| | ambient caption p95 | measurement point |
|---|---|---|
| Qwen3-VL-4B | 0.17 s (n=50) | engine, cache cold |
| incumbent (published) | 0.176 s | sidecar |
| incumbent (re-measured tonight) | 0.224 s (n=12) | sidecar, 4B idle |
| **budget** | **1.5 s** | |

The incumbent is already **8.5× inside the ambient budget**. There is no
latency problem for a fast model to solve. Note the arms differ by
measurement point (engine vs sidecar); the sidecar adds overhead, so the true
engine-to-engine gap is smaller still — which only strengthens the finding.

**And it hallucinates far more.** G4, paired on 35 owner-verified negatives:

```
incumbent false presence:  5/35     (3/25 on true negatives)
candidate false presence: 13/35    (11/25 on true negatives)
McNemar: ties=21 discordant=14, candidate_wins=3 incumbent_wins=11
p=0.96021  ->  G4 VERDICT: FAIL
```

G5 rode along free: **0 reasoning leaks** across 50 captions and 35 negatives.
The 4B is clean on leakage — it is simply wrong more often.

**Verdict: the "fast small VLM" leg of the proposed architecture is not
justified by measurement.** It buys no latency the house needs and costs 3.7×
the hallucination rate. Do not spend VRAM on it. This does not rule out a
*different* small VLM, but it raises the bar: any candidate must beat 0.176 s
*and* 5/35, and the first of those is already free.

### E3 — two instances under contention: **viable**

Incumbent measured at the sidecar (production path) while the 4B was idle,
then while the 4B was saturated with two concurrent caption streams.

| | p50 | p95 | max |
|---|---|---|---|
| incumbent, 4B idle | 0.177 s | 0.224 s | 0.224 s |
| incumbent, 4B saturated | 0.255 s | 0.356 s | 0.359 s |
| **degradation** | **1.44×** | **1.59×** | |
| 4B itself under load (n=121) | 0.137 s | 0.277 s | |

**Ambient budget 1.5 s: held, with 4.2× margin.** Contention is real but
modest. Co-residency is not what kills the two-model idea — the 4B's quality
is. The contention envelope is now known for any future second model.

The idle arm also cross-validates the published baseline: p50 0.177 s tonight
vs 0.176 s p95 published.

### Incidental — two defects in the look classifier

Found while reading G4's scoring, per the "verify every data source" rule.
`classifyLookFinding` decides what the app surfaces, so it decides what G4
measures.

1. **Negated motion reads as a person.** The `person` branch matches
   `moving|motion|walking|walks` with no negation guard, so:

   | caption | category | importance |
   |---|---|---|
   | `The living room is quiet with no one moving.` | **person** | **90** |
   | `Nothing is moving.` | **person** | **90** |
   | `No motion detected.` | **person** | **90** |

   Importance 90 is the highest non-hazard tier — the one that wakes someone.

2. **The `quiet` branch is too narrow.** It requires
   `(looks|seems|appears) (normal|quiet|empty|clear)` or the literal
   `no obvious activity`, so a plain `The room is quiet.` falls through to
   **`activity`, importance 55** — which G4 counts as false presence.

Present at `app/src/home-natural-look.js:254` and faithfully ported in
`tools/qwen38_gates.py`. **I could not locate a deployed copy** — it is not in
`/config/www/engineered-lighting-card.js` (28 KB, Mar 27, no match) nor in
`hav-intelligence`, `hav-vision-sidecar`, `home-agent-bff` or
`home-agent-origin`. So I am **not** claiming a live outage; I am claiming a
defect in the repo source and in the gate's scoring, and asking where it ships.

**Effect on E2's verdict: none.** Re-scoring G4 with a negation guard moves
those four frames from `person` (90) to `activity` (55) — both are false
presence, so the counts are unchanged at 11/25 vs 3/25. The defect inflates
*severity*, not the ranking. E2's kill stands on its own.

### Research (searched tonight; training cutoff predates it)

**V-JEPA 2.1 — the 26 GiB guess is wrong by roughly an order of magnitude.**
Released **2026-03-16**. Four variants, all at 384 px:

| variant | params |
|---|---|
| ViT-B/16 | 80 M |
| ViT-L/16 | 300 M |
| ViT-g/16 | 1 B |
| **ViT-G/16 (largest)** | **2 B** |

2 B params ≈ 4–5 GiB at bf16. The owner's ~26 GiB estimate is far above the
whole model family.

**But footprint is the wrong question.** V-JEPA 2 outputs
`last_hidden_state` — **embeddings, not text**. There is no captioning head.
Actionable output requires either the SSv2 classification head (174 generic
action classes such as "putting something on something") or a probe trained
on the house's own data. Practical prediction horizon is **≤ 16 s**. So the
cost of adopting it is a *training* project, not a serving project, and the
VRAM reservation question is close to moot. It should not hold resident VRAM
until something is proven to consume its features.

**vLLM multi-model:** not supported in one process/port; the ecosystem
pattern is N instances behind a proxy — which matches E1's source inspection.

**Official Qwen3.8-27B serving recipe** (recipes.vllm.ai): tool parser
`qwen3_coder` (the house runs `hermes`, inherited from the incumbent —
consistent with migration-doc R6, which already flagged this), reasoning
parser `qwen3`, MTP via
`--speculative-config '{"method":"mtp","num_speculative_tokens":3}'`, min
vLLM 0.17.0. `mtp_num_hidden_layers = 1` confirmed present in the cached
checkpoint's `config.json`.

### What this session killed, and what it opened

**Killed**
- The 4B as an ambient captioner (E2): no latency win, 3.7× the false presence.
- "Ambient captions are too slow" as a motivation for any second model — the
  incumbent runs at 12% of budget.
- The idea that a smaller model necessarily frees KV memory (E1 geometry).
- The 26 GiB world-model reservation, and — more usefully — the assumption
  that V-JEPA is a drop-in understanding component at all.

**Opened**
- Two 27–30 B models **fit in 84 GiB** if the incumbent's KV pool is
  right-sized. Arithmetic, from tonight's measured numbers, **not yet tested**:

  | item | GiB |
  |---|---|
  | incumbent weights + overhead | 32 |
  | Qwen3.8-27B weights + overhead | 30 |
  | incumbent KV for 4 × 32,768 @ 96 KiB | 12.0 |
  | Qwen3.8 KV for 4 × 32,768 @ 64 KiB | 8.0 |
  | **total** | **82** |
  | **available** | **84** |

  ~2 GiB slack. Tight but real, and it needs no new hardware — only the
  admission that 33.59 GiB of KV for a 4-sequence scheduler is waste.

- The risk that arithmetic hides — now largely retired. The incumbent's
  prefix caching is **worth 5.23×** and 99.9% hit warm, and cutting the pool
  could in principle remove the retention it depends on. But the retained set
  is measured at **1.24 GiB (3.69% of the pool)**, because it is one 13,552-
  token voice prefix, not a function of traffic. A pool of ~16 GiB would hold
  the full 4-sequence active ceiling *and* 11× the entire reused prefix set.
  The remaining uncertainty is empirical, not arithmetic: vLLM's eviction
  behaviour under a smaller pool. That is what E4a tests, and it now looks
  much more likely to pass than to fail.

### The single next experiment

**E4a — does the incumbent's prefix-cache win survive a right-sized KV pool?**

One variable: `--gpu-memory-utilization`, 0.70 → **0.50** (≈ 48 GiB budget →
≈ 15 GiB KV ≈ 163k tokens, which is 1.25× the 4-sequence active ceiling plus
11× the measured 1.24 GiB prefix set). Nothing else changes.
Measure the same three arms the 5.23× came from (warm / shared-prefix /
busted p50, TTFT, `prefix_cache_hits_total` ÷ `queries_total`) plus ambient
p95, against tonight's re-measured idle baseline (p50 0.177 s, p95 0.224 s).

- If the win holds → ~20 GiB is free, the two-model architecture is funded,
  and E4 (Qwen3.8 thinking-OFF voice latency, the number never obtained) and
  E5 (MTP, untried, largest untested decode lever) can run against a real
  second instance.
- If the win degrades → the incumbent's pool is load-bearing, two 27–30 B
  models do not fit, and the architecture must be one strong model plus
  something much smaller than 27 B — or nothing.

This requires an incumbent restart, so it is a real maintenance window:
flag → quiesce → stash compose → change one line → measure → restore →
verify voice → clear flag. It should be batched with E4/E5 in one window
rather than taken three times.

**Blocked first:** the voice outage at the top of this document. E4a's
verification step is "voice returns real text", which cannot pass until HA
Core is restarted.
