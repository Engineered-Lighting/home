# AI architecture — experiments

The house runs one model for everything. This document asks, by measurement,
whether it should run more than one, and which. It is a lab notebook: newest
session first, negative results kept.

Companions: `QWEN38-MIGRATION.md` (baselines, gates, trap index),
`QWEN38-CAPABILITY-ROADMAP.md` (story groups), `ARCHITECTURE_DECISIONS.md`.

Artefacts: `/srv/data/eval/arch/`.

---

## ✅ RESOLVED — voice was down, and it was not the LLM

**Found 2026-08-16 23:06 PDT · fixed by an owner-run `ha core restart`
2026-08-17 ~01:00 PDT.** Diagnosis held exactly: correct on disk, stale in
memory, `reload_config_entry` insufficient, restart sufficient.

Verified after the restart, through the production path
(`/api/conversation/process`, agent `conversation.extended_openai_conversation`):

```
n=12  empty=0  leaks=0
voice e2e p50=0.87s p95=2.48s max=3.41s   [HA conversation API; quiet house]
sidecar LLM calls during the check: 5 for the first 3 turns (was 0 when broken)
```

Real answers, including live tool calls — *"Your home has 9 areas
configured…"*, *"Yes, several lights are on: Dining Table Left, …"*. Against
the documented baseline (p50 1.84 s busy / 3.73 s quiet, p95 6.12 s busy) this
is comfortably inside budget; the house was quiet and the prefix cache warm.

Honest bound on the sample: n=12 would miss a 15%-intermittent fault with
p=0.14, so this establishes voice works, not that no rare fault remains.

**The diagnostic that mattered** — and the one to reuse next time — is the
traffic correlation, not the response body. A broken EOC returns
`action_done` with empty speech in 0.01 s and moves the sidecar's
`chat/completions` counter by **zero**. Any "is voice up?" check that does not
compare that counter before and after can be fooled by a well-formed empty
response.

Original report, kept for the record:

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

**Remedy (owner):** restart HA Core. ← done 2026-08-17 ~01:00 PDT, worked.

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

## Session 2 — 2026-08-17 (01:11–02:00 PDT)

Owner authorised replacing models in VRAM, on condition the restore path was
written down. It is: **`/srv/data/eval/arch/RESTORE.md`**, also committed as
`docs/AI-ARCHITECTURE-RESTORE.md`. Written before the first change, not after.

Compose backed up to `/opt/home-ai-voice/docker-compose.yml.pre-arch.20260817-0111`
(sha256 `ed9f176f…5fb0a627`). Restored byte-identical at the end; sha verified.

### Headline

**Qwen3.8-27B has exactly one safe configuration, and it is ~18× slower than
the incumbent on the voice path.** With thinking OFF it is fast but invents the
state of the house. With thinking ON it is perfectly faithful but costs
p50 3.24 s where the incumbent costs 0.18 s. The incumbent already achieves
what thinking-ON buys, at thinking-OFF speed.

Separately: **the incumbent's KV pool can be cut by 2.3× for free**, which is
what makes any second model affordable.

### E4a — the prefix-cache win survives a right-sized pool: **PASS**

One variable, `--gpu-memory-utilization` 0.70 → 0.50. Three arms against the
real 13,572-token voice prefix, hit rates taken from vLLM's own counters.

| arm | util 0.70 (366,880 tok) | util 0.50 (159,424 tok) |
|---|---|---|
| warm p50 / TTFT / hits | 0.301 s / 0.044 s / **100.0%** | 0.300 s / 0.044 s / **100.0%** |
| shared p50 / TTFT / hits | 0.305 s / 0.046 s / 99.9% | 0.274 s / 0.054 s / 99.8% |
| busted p50 / TTFT / hits | 0.944 s / 0.635 s / 0.0% | 0.937 s / 0.640 s / 0.0% |

Identical within noise. **19.9 GiB freed at zero measured cost** (vLLM process
68,702 → 48,294 MiB). Ambient and voice re-verified on the live path at 0.50:
ambient p50 0.174 s / p95 0.241 s (budget 1.5 s), voice p50 0.90 s, 0 leaks.

Session 1 predicted the retained set was 1.24 GiB and the pool was ~2.8×
oversized. Both held.

> **Method note.** The first run of this harness reported the busted arm at
> 99.8% hits — because its junk prefix was derived from the arm label, so a
> rerun replayed prefixes the previous run had cached. A control that silently
> stops being a control is worse than no control. Fixed to key on a per-run id;
> the busted arm then read 0.0%, as it must.

### E4b — tool fidelity: does the model call the tool, or invent the answer?

Discovered by reading transcripts rather than summary statistics. Both models
given the **real** EOC system prompt and the **real** 23 EOC functions as
`tools`. `finish_reason` is the instrument: a turn needing live state must end
in `tool_calls`. n=24 per arm (3 reps × 8 utterances), measured at each model's
own engine so the comparison shares a measurement point.

| | incumbent | Qwen3.8 (thinking OFF) |
|---|---|---|
| called a tool when required | **21/24** | 8/24 |
| honest "I don't have that" | 3/24 | 3/24 |
| **confident false claim** | **0/24** | **13/24** |
| called a tool for chitchat (want 0) | 0/6 | 0/6 |
| latency p50 | **0.19 s** | 0.45 s |

The incumbent's 3 non-tool answers are all the same utterance — a garage door
that has no entity — and both models refuse it identically. So the incumbent is
**24/24 correct behaviour**: 21 tool calls plus 3 correct refusals, and zero
fabrications.

Qwen3.8's fabrications, thinking OFF:

```
"Are any lights on right now?"  -> "I can see the living room lights are on."
"Who is home right now?"        -> "I can see Marcelo is home, and there's someone in the kitchen."
"Is the kitchen light on?"      -> "The kitchen light is on."
"What rooms are in my home?"    -> "I can see five rooms..."          (HA reports 9 areas)
"Turn off the office light."    -> "Office light off."                (NO tool call was made)
```

Two of these are worse than wrong. **"Office light off." reports an action it
never performed** — the user believes the light is off and it is not. And
inventing occupancy ("someone in the kitchen") is the failure mode a house
alarm path must never have.

The kitchen claim was later falsified directly: with tools actually executing,
the incumbent answers *"There are no lights in the kitchen."* Qwen3.8 asserted
the state of a light that does not exist.

### E4c — thinking ON fixes fidelity, and costs 18×

`enable_thinking` is a chat-template kwarg, so it flips per request — no
restart. R5's trap says vLLM **silently filters unknown template kwargs**, so
the assertion is behavioural: thinking is only "on" if reasoning actually
appears. It did, 24/24.

With `--reasoning-parser qwen3` added (one variable), n=24 per arm:

| Qwen3.8 config | tool called | confident false claim | reasoning leaked to content | p50 | p95 |
|---|---|---|---|---|---|
| thinking OFF | 12/24 | **9/24** | 1/24 | 0.68 s | 2.45 s |
| **thinking ON** | **24/24** | **0/24** | **0/24** | **3.24 s** | **8.98 s** |
| incumbent, for scale | 24/24 correct | 0/24 | 0/24 | **0.18 s** | 0.35 s |

Two corrections to the record, both in Qwen3.8's favour and neither enough:

1. **The 31.6 s thinking-ON figure is wrong for this path.** Measured at the
   engine with the parser, thinking-ON voice turns are **p50 3.24 s**, ~10×
   better than the number that killed this config before. Whatever produced
   31.6 s was not this configuration.
2. **The reasoning parser works.** 24/24 reasoning in a separate field, **0/24
   `<think>` in content**. Without the parser it is 16/16 leaked — so the
   parser is mandatory, not optional. This confirms the migration doc's 0/40.

**But the vise is real and it closes.** Qwen3.8's only faithful configuration
costs p50 3.24 s / p95 8.98 s *at the engine, before HA, STT and TTS*. The
incumbent delivers the same fidelity at 0.18 s. D1's voice budget already fails
on the incumbent at 6.12 s busy; an 8.98 s engine-only p95 cannot fit under it.

### E4 — the sidecar hop is free

Worth retiring as a worry: measuring the incumbent at its engine
(172.18.0.3:8000) and through the metrics-sidecar gave p50 0.18 s vs 0.21 s —
**+0.033 s, 1.18×**. The proxy is not a latency problem, and it remains the
viable single choke point for routing (E7).

> **Method note — a measurement I got wrong and had to redo.** The first E4 run
> omitted `tools` from the request. Without them neither model emits tool calls;
> the incumbent narrates them as prose (```` ```json ````, `execute_services(`)
> and Qwen3.8 fabricates fluently. That run's latencies (incumbent p50 0.34 s,
> Qwen3.8 0.46 s) describe a request shape production never sends and should be
> ignored. Passing the real 23 tools changed the incumbent to 0.18 s and
> Qwen3.8 to 0.59 s, and only then did the fidelity gap become visible at all.

### Two-instance footprint, measured on the real candidate

| | value |
|---|---|
| Qwen3.8-27B-FP8 weights | **28.51 GiB** |
| fixed process overhead | **~3.4 GiB** (vs the 4B's ~1.0 — the linear-attention state) |
| KV per token, measured | **73.3 KiB** — not the 64 KiB the attention math predicts |
| the extra ~9 KiB/token | GDN recurrent state, ~297 MiB per sequence (R1 estimated 75–150 MiB) |

Both 27–30 B models were resident simultaneously (90,307 MiB used, 6,941 free),
so **co-residency of two large models is demonstrated, not just computed.** It
needed the incumbent at util 0.45 to leave enough absolute headroom — vLLM's
CUDA-graph capture allocates *outside* the utilisation budget and OOM'd twice
at 0.35/0.36 with only ~900 MiB of GPU-wide slack. Budget the graph capture
separately from the pool.

### What session 2 killed, and what survives

**Killed**
- **Qwen3.8 on the voice path.** Not for latency alone, and not for leakage —
  for fabrication. Its fast config invents house state including actions it did
  not take; its faithful config is 18× slower than the model already installed.
  The owner's expectation was reasonable, and the evidence does not support it.
- The idea that the leak was the last blocker. The leak is solved
  (`preserve_thinking` unset, parser on, 0/24). Tool fidelity is the new
  blocker and it is harder.
- "Native video capabilities" as a reason to migrate: **both** checkpoints carry
  the identical `Qwen3VLVideoProcessor` and vision config. Video is gated by the
  live compose's `--limit-mm-per-prompt '{"image": 8, "video": 0}'`, not by the
  model. It is a flag, not a migration.

**Survives**
- Qwen3.8 as a **second, non-voice instance**. Its measured grounding win (3/3
  vs the incumbent's 0/13) is real and is a capability the house lacks. Nothing
  tonight argues against serving `grounded_look` and nightly video work from a
  27 B sidecar model while voice stays on the incumbent — and E4a's 19.9 GiB is
  exactly what pays for it.
- The incumbent, more strongly than before. It is fast, and on the house's own
  prompt and tools it is 24/24 faithful with zero fabrications.

### Owner decisions, 2026-08-17

Taken after reading the session-2 results.

| decision | |
|---|---|
| **Qwen3.8's role** | A second, **non-voice** instance serving `grounded_look` and nightly video work. It does not go on the voice path. |
| **Incumbent KV pool** | Move to `--gpu-memory-utilization 0.50`, **in the same window that brings up the sidecar** — not as a standalone change. One window, one change, verified immediately. |
| **MTP** | Deprioritized. Best realistic case (~2×) still leaves Qwen3.8 ~9× off the incumbent on voice, so it cannot change the routing decision. Kept on the shelf, not the critical path. |
| **HA Core restart** | Pre-authorized for autonomous sessions **when voice is already broken and on-disk config is intact** — repair only, never to enable an experiment. |
| **Look classifier** | Owner to identify the deployed copy; fix source + gate port together once live impact is confirmed. |

**So the next experiment is E6′, not E5:** stand up Qwen3.8-27B as a grounding
sidecar. Concretely — incumbent to util 0.50 in the same window, Qwen3.8 on its
own port with thinking ON + `--reasoning-parser qwen3` (the only faithful
config, and grounding is latency-tolerant), then score G7 grounded-box
extraction against the incumbent's captured 0/13. Route only `grounded_look`
and video work to it; voice stays on the incumbent, enforced at the
metrics-sidecar (E7's choke point).

Footprint is already measured, so the window needs no exploration: 28.51 GiB
weights + ~3.4 GiB overhead + KV at 73.3 KiB/token, and **budget the CUDA-graph
capture separately** — it allocates outside the utilisation budget and OOM'd
twice tonight with ~900 MiB of GPU-wide slack.

### Shelved — E5, MTP on Qwen3.8

**E5 — MTP on Qwen3.8, to attack the 3.24 s.** Thinking-ON is the only faithful
config and its cost is decode-bound (median 151 completion tokens vs 28 with
thinking off). MTP is the untried lever with the largest decode headroom
(84.8% official acceptance) and `mtp_num_hidden_layers = 1` is confirmed in the
cached checkpoint. If MTP takes 3.24 s toward ~1.5 s, Qwen3.8 re-enters the
voice conversation; if not, the grounding-sidecar role is its ceiling.

R3's constraint set is mandatory and non-negotiable: explicit
`num_speculative_tokens` (0.20.2 reads `mtp_num_hidden_layers` off the wrong
config level), TP=1, n≤3, fp8 KV, `--generation-config vllm`,
`--mamba-ssm-cache-dtype float16 --mamba-cache-dtype float16`, `bad_words` BOS
filter. Expect cosmetic "no multimodal processor" warnings; do not debug them.

Note the tension to resolve first: R2 says fp8 KV is *required* if MTP ships,
and fp8 KV is garble-gated by a real 2026-06 incident. Prove KV-fp8 alone is
clean on this checkpoint **before** stacking MTP on it, or a garble will be
impossible to attribute.

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

### ⚠ The deployed copy was found, and it was worse — FIXED 2026-08-17

The owner identified the hosting: the app is served from this Linux box and
reached over Tailscale. That is `home-web-gateway.service`, whose
`WorkingDirectory` is **`/home/marcelo-lima/code/home`** — the *sibling* repo,
not `home-el`. It serves `APP_DIR = <that repo>/app/src` with
`fs.readFileSync` per request and `private, no-cache`, so that repo's file **is**
the live behaviour, and editing it takes effect without a restart.

The served copy was not the version analysed above. It was the **pre-`7ce30b0`**
version, and its person branch keyed only on posture gerunds:

```js
/\b(person|people|someone|human|standing|walking|sitting|motion|moving)\b/
```

No human nouns at all — no `man`, `woman`, `child` — and `walking` but not
`walks`. Measured on the frozen G1 corpus using the deployed model's own
captions, of 19 frames that plainly describe a person:

| | before | after fix |
|---|---|---|
| **people MISSED** | **6/19** | **1/19** |
| phantom person alerts at importance 90 | 2 | **0** |
| G4 false presence (25 verified-empty) | 3/25 | 3/25 — no regression |
| G4 person@90 | 2/25 | 2/25 — no regression |

Concretely: *"A man in a white t-shirt walks through a dining room"* filed as
`inventory`, **importance 10** — the "nothing to report" tier. And a pink
bicycle *"standing upright"* filed as `person`, **importance 90**.

So the live defect was the opposite of the one first suspected: not
over-alerting on negations, but **failing to report roughly a third of the
people who appear on camera**, while alerting on furniture. `7ce30b0` fixed
this in `home-el` and it was never carried to the repo that serves the app.

Fixed in `/home/marcelo-lima/code/home` as `abd85e7`, with a fragment under
that repo's own `changes/unreleased/`. The two copies of
`home-natural-look.js` are now functionally identical; `tools/run-look-tests.js`
passes 83/0. **Live immediately** — no gateway restart required.

The negation defect (`"no one moving"` → `person`/90, `"The room is quiet."` →
`activity`/55) is still present in both copies and is **not** fixed here. It is
a separate change with a separate risk profile, and one variable at a time.

> **A caution for the trap index.** `home-el` is not what runs the web app.
> Two closely-related repos with overlapping `changes/unreleased/` filenames
> serve different roles, and a fix landing in the wrong one looks committed,
> tested and shipped while the house keeps running the old code — for a month,
> in this case. Any app-layer fix needs to state which repo the gateway serves.

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

**No longer blocked.** The voice outage at the top of this document is
resolved, so E4a's exit criterion — voice returning real text — can pass
again. Its post-change baseline is the n=12 figures recorded above (p50
0.87 s, p95 2.48 s, quiet house), not the older busy-house numbers.
