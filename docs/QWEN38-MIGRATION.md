# Qwen3.8-27B Migration — Operational Plan

> **Status:** approved plan, pre-execution. Owner decisions D1/D2/D7/D9/D10
> are DECIDED (recorded below); D3/D4/D5/D6/D8/D11/D12 carry recommendations
> awaiting sign-off. Companion doc: `docs/QWEN38-CAPABILITY-ROADMAP.md`.
> Planning provenance: repo exploration (six research passes), two
> adversarial review rounds (eight reviewer lenses), and an in-depth model
> research sweep (official vLLM recipe read verbatim; vLLM v0.20.2 source
> read at the tag; issue-tracker sweep) — 2026-08-16.

Replace the production VLM `Qwen/Qwen3-VL-30B-A3B-Instruct-FP8` (30B MoE,
3B active) with `Qwen/Qwen3.8-27B-FP8` (dense, hybrid-attention) on the
pinned vLLM 0.20.2, RTX PRO 6000 Blackwell 96 GB (SM_120). Qualification
passed 2026-08-15 on real hardware (results:
`/srv/data/eval/qualification/qwen38-27b-fp8-20260815.json`): perfect tool
call under `--tool-call-parser qwen3_coder`, `enable_thinking:false`
honored, grammar-enforced `json_schema` exact, 1/4/8-image round-trips on
real camera frames. Raw cost: 2.4–2.6× slower decode (dense vs 3B-active
MoE); levers: MTP, reasoning-effort pin, prompt shrink.

---

## Execution order (calendar, not section order)

Phase 0 (on-host verification, read-only) → PRs 1a + 1a-bis + 1a-ter →
Phase 3 (latency matrix sessions; session 1 entry gate = streaming tool
replay; final session on the winner config collects gate evidence) → G1
blinded judging scored → cutover (1b) → 2-week soak (G3-live completes
here; **no prompt-surface edits during soak**) → Phase 2 prompt translation
(except the pre-cutover ★ items) → roadmap crawl→walk→run.

**Phase-3 true footprint: ≥4 engine configs ≈ 8–14 h of candidate
residency ≈ 3–5 evenings of voice-down-or-candidate operation.** The
cells→sessions map is published with the D1/D9 sign-off so the owner
consents to the actual number of evenings.

## PR ladder (repo work, all ceremony-disjoint — verified)

- **PR 1a (this PR):** change-notes gate fix; this doc + the roadmap doc;
  ADR-005; `stack-upgrade-candidates.json` update; doc sweep (stale model
  strings, `qwen3_xml` claims); D9 amendment draft (below). Checklist:
  `pytest tests/home_agent/test_repository_contract.py`,
  `node tools/run-stack-upgrade-plan-tests.js`,
  `node tools/release/check-change-notes.mjs` all green locally.
- **PR 1a-bis (before Phase 3):** gate/matrix harnesses under `tools/` —
  G1 frozen-corpus driver + blinded-judging tool, G2 replay driver, G4
  negatives corpus + tally, G5 leak grep, G7 `--production-parser` scoring
  mode for `probe-grounded-reasoning.py`, the matrix driver (incl. the DL
  deep-look and Q quiet-sentinel cells), golden-transcript replay.
- **PR 1a-ter (app workstream; `app/src` is ceremony-disjoint):**
  natural-look quiet-literal regex relax (**landed early in PR 1a — it is a
  model-independent one-line fix for a gate-blind regression**); tray
  ttft/turn pill thresholds retuned to D1; Lab `verySlowRatio` note/retune;
  home for G7 stripper edits; stale tooltip; sim-fixture latency scaling.
  **Added after Phase-0 measurement (all model-independent, all shippable
  before the candidate exists):**
  1. ✅ **LANDED 2026-08-16 — app stripper regexes widened.**
     `app/src/home-look.jsx` now shares one `LK_BOX_TAG_SRC` /
     `lkStripMarkup` between `lkPlain` and `lkRenderReasoning`, matching
     the sidecar's tolerance (square-bracketed coordinates, a bare `>`
     close) plus an unclosed trailing `<box`/`<ref` from generation-cap
     truncation. 16 new cases in `tools/run-look-tests.js`; the Python
     port in `qwen38_gates.py` is held to the same cases so a one-sided
     edit fails a suite instead of silently un-syncing the gate from
     production.
  2. **Repair the deployed `/reason` prompt's grounding** (v3 extracts
     0/13; v1 extracts 5/6 on the same frame). This is a prompt-surface
     change, so it lands **before cutover** or waits until Phase 2 — never
     during the soak, and never bundled with the model swap.

## Never-deploy list (repo ≠ live host; live is truth)

Redeploying the repo copy of any of these DESTROYS live behavior. The
Phase-0 archive inventory is the authoritative list; verified so far:

| Surface | Repo state | Live state |
|---|---|---|
| metrics-sidecar | no `/trace`; conversation tee containment-locked | `/trace` ingest + `traces.ndjson` exist, but ⚠ **no live producer** — see the Phase-0 `/trace` finding; it is not "the instrumentation every gate uses" |
| EOC custom component | zero-tool containment refactor (`MODEL_TOOL_CATALOG_ENABLED=False`, routing off, single-turn) | 23 tools, routing, multi-turn memory, `context_threshold` 24000 |
| vision-sidecar | `HA_TOKEN:""`, `image:4` | ambient loop live (~350/day), image limit 8 |
| intelligence | containment flags `"0"`, quarantine network (contract-test-pinned literals) | labeler active (~98% schema-valid post-fixes) |
| Host-only, no repo copy | — | bridge `main.py` prompts, deployed `spatial_model.json` (camera_edges/zone_to_room), live EOC subentry + functions YAML |

Delivery path for any change to host-image services (labeler v2, sidecar
prompt/limit edits): **off-host build + `docker save/load`** — no image
builds on the quarantined host (D9).

## Decided owner decisions

- **D1 — latency budget (binding for gate G6). ⚠ AMENDED 2026-08-16 after
  measurement — see "D1 AMENDMENT + G6 restated" below.** Ambient p95
  ≤ 1.5 s (instrument: vision-sidecar request log / vLLM Prometheus
  histogram diff — ambient bypasses the proxy), 4-frame clip ≤ 6 s, and
  TTFT ≤ 1.2 s — all three **unchanged and met by the incumbent**. The
  voice clause changed: the original "e2e p95 ≤ 4 s" is **already breached
  by the incumbent at 6.12 s**, so it is replaced by **p50 ≤ 2.5 s plus a
  paired tail cap of 1.25× the incumbent measured in the same session**,
  with the 6.12 s tail tracked as a pre-existing defect (G6-d). TTFT's
  instrument is corrected: HA `/api/conversation/process` cannot measure it. The
  deep-look multi-pass shape (3 cams × 2 `/reason_zoom` passes; up to 4
  passes per `grounded_look` with illumination retry) gets its own budget
  line or an explicit owner exclusion in the same memo.
- **D2 — topology:** single 27B. **Failure default = stay on incumbent**
  (roll back, keep harnesses, retry at the next window). Split-role
  (30B-for-vision + 27B-for-text) is a documented contingency requiring a
  separate explicit owner decision; its ops bill: sidecar single-upstream
  routing doesn't exist, supervisor gate reads `data[0].id` only, stack.sh
  and inventory tooling assume one vllm, and a second served name
  resurrects ~20 coupling points.
- **D7 — cloud fork:** no routing change during migration; post-soak,
  narrow the GENERAL regex using traffic-week data so more queries stay
  local; retirement revisited later. Privacy invariant unchanged.
- **D9 — host clearance:** full written amendment (draft below) covering
  cutover + Phase-3 sessions + recurring inference lanes; 5-step admission
  check before/after every session; no host image builds.
- **D10 — long context:** research-corrected KV math (hybrid attention:
  only 16 of 64 layers hold paged KV → **64 KiB/token bf16, 32 fp8**) makes
  262K reachable (~16 GiB bf16). Cutover ships 32768 unchanged; post-soak
  raise to 131072 (KV-cheap; EOC `context_threshold` raised coherently).
  Binding constraints are prefill latency and MTP context-decay, not VRAM.

Open (recommendations recorded in the roadmap doc): D3 identity policy
(hard-gates roadmap A1/E1/digest), D4 bake-off conversion (recommended:
these gates + permanent answer key), D5 served-name freeze (recommended:
keep; `PROMPT_VERSION` bump rule compensates for prelabel idempotency
blindness), D6 repo-compose sync (recommended: mirror), D8 Frigate
recording, D11 `/trace` retention acknowledgment (must cover F0 AND G2
replay AND matrix image traffic), D12 browser exposure of `describe_event`.

## Model-research findings that bind the runbook (R1–R9)

1. **KV math:** 16 full-attention layers × 4 KV heads × 256 head_dim →
   64 KiB/token bf16. GDN recurrent state ≈ 75–150 MiB **per sequence**
   (contiguous). Verify against the startup "GPU KV cache size" line — a
   pool near ~170k tokens at bf16 would mean the geometry assumption is
   wrong again.
2. **fp8 KV:** officially recommended for this checkpoint (ships KV
   calibration scales; no `--calculate-kv-scales`). Still garble-gated (the
   2026-06 incident + vllm#47349 fp8-KV×APC truncation). Required if MTP
   ships (vllm#52475). Never `e5m2` (#41343).
3. **MTP:** head confirmed in the checkpoint (84.8% official FP8
   acceptance); explicit `num_speculative_tokens` is MANDATORY on 0.20.2
   (reads `mtp_num_hidden_layers` off the wrong config level — verified at
   the tag). The GDN spec-decode fixes (#51812/#51674) are in NO released
   tag: MTP on this pin is present-but-unverified, with same-family crashes
   adjacent to the pin (#40756 ≥26k tokens) and value that decays with
   context (+129% @2K → −51% @30K, #47602). Constraint set if adopted:
   TP=1, n≤3, fp8 KV, `--generation-config vllm`,
   `--mamba-ssm-cache-dtype float16 --mamba-cache-dtype float16`,
   `bad_words` BOS filter. Cosmetic "no multimodal processor" warnings
   expected (#52481) — do not panic-debug.
4. **Prefix caching is likely INERT on this hybrid** (silent 0%-hit
   reports; dense-27B case unanswered upstream). Every voice turn likely
   pays full ~8.5k-token prefill → prompt SHRINK is the primary latency
   lever; instrument `num_cached_tokens` from day one.
5. **Thinking suppression:** `reasoning_effort` ∈ xhigh(default)/medium/low
   (`high` undocumented); community says `low` is not a reliable brake —
   `enable_thinking:false` is the real control. Pin
   `{"enable_thinking": false, "preserve_thinking": false}` (the latter is
   a NEW default-on kwarg that replays prior-turn reasoning into history).
   **vLLM 0.20.2 renamed `reasoning_content` → `reasoning`** — leakage
   assertions must check BOTH fields plus `<think>` in content, with
   adversarial canaries (trivially simple + broad-visual prompts — the
   family's overthinking shapes; Qwen3.6 on this GPU needed a template
   pre-filling `<think></think>`).
6. **Tool parser:** `qwen3_coder` is the official recommendation (matches
   qualification). Test explicitly: fully-buffered single-delta call
   (#45439), zero-arg-tool doom loop under strict mode (#50989 — audit the
   live subentry for empty-`properties` tools), empty-`type` final chunk
   (#38603), param-whitespace stripping (#48753). `qwen3_xml` is staged in
   0.20.2 as the escape hatch. `--reasoning-parser qwen3` only with a
   same-session grammar re-verify (#44012/#50948).
7. **SM_120 hygiene:** `VLLM_USE_DEEP_GEMM=0`; check the attention backend
   is actually FlashInfer and CUDA graphs capture (the 48-vs-58 tok/s gap);
   MXFP4/Quark-INT4 don't load; `transformers>=5.8.0` required for the
   vision processor. Vision is outside everyone's published verification
   matrix — this stack qualifies it itself (G1/G7).
8. **Grounding:** zero community reports for 3.8; family convention moved
   to `bbox_2d` JSON @0–1000, but our prompts explicitly instruct
   `<ref>/<box>` — G7 tests instruction-following; one probe on a
   known-dimensions image settles the coordinate scale.
9. **Nobody has publicly run this checkpoint on vLLM 0.20.x.** Re-run the
   community sweep in cutover week; the gate discipline is the compensating
   control.

## Phase 0 — on-host verification (read-only, ~half day)

Archive everything into `/srv/data/eval/migration/phase0/`; the archive IS
the never-deploy list. Items: live compose + `.env` + image digest +
`vram-audit` tenant map; live sources for the four diverging services +
bridge prompts; EOC subentry dump (prompt, options, functions YAML —
resolve live `context_threshold` 24000 vs repo 40000); full prompt-surface
freeze incl. app-composed prompts (`buildFocusedLookQuestion`, `/recap`,
`/find-clips`, proactive template); latency + **consumer-timeout
inventory** vs D1 (`PERCEPTION_AUTO_TIMEOUT_S=6`,
`REFRESH_PERCEPTION_TIMEOUT_S=8`, sidecar `within_ms` 30 s,
`processingGuardMs=30000`, room-binding `_TTL_SEC=90` — a wrong-room
actuation path under slower turns, natural-look 90 s×3); Frigate record
state + face-rec override placement (the repo example has the driveway
exclusion mis-nested under dining_room); qualification + traffic-week
artifact freeze; ceremony journal status; held-task-0.4 confirmation; ntfy
topology/retention; `/run/ha-maintenance` semantics; both weight sets +
disk headroom; installed transformers version; subentry tool-spec audit
(zero-arg tools, `$ref`); labeler schema validated against xgrammar's
unsupported-feature list.

## ⚠ CUTOVER ATTEMPTED AND ROLLED BACK — 2026-08-16 17:29–17:40 PDT

**Outcome: the candidate ran, most gates passed, and it was rolled back on
G5 reasoning leakage reaching spoken output.** The house is back on the
incumbent. Total exposure ≈ 10 minutes, under the maintenance flag, with
ambient quiesced.

**What passed — and some of it is genuinely better than the incumbent:**

| assertion | candidate | incumbent |
|---|---|---|
| served name `qwen3-vl-30b` | ✅ | ✅ |
| **GPU KV cache pool** | **497,097 tokens** | 366,816 |
| **zero-arg tool (vllm#50989)** | ✅ **0.4 s, no doom loop** | 1.20 s |
| grounded boxes extracted | ✅ **3/3** | **0/13** |
| app strippers clean | ✅ 3/3 | 3/3 |
| ambient caption | ✅ 0.60 s, sensible | 0.18 s |
| short-completion p95 | 0.07 s | 0.05 s |
| leakage canary (8 direct probes) | ✅ clean | ✅ clean |

**R1 is now confirmed on real hardware.** 497,097 tokens is exactly the
64 KiB/token geometry — *more* KV headroom than the incumbent despite a
larger dense model, because only 16 of 64 layers hold paged KV. Every VRAM
and long-context conclusion in this plan stands.

**vllm#50989 did not reproduce.** `get_all_rooms_state` returned a clean
tool call in 0.4 s under `qwen3_coder`. The zero-arg hazard is real in
principle but is not triggered by this checkpoint at this call site.

**G7 improved dramatically.** The candidate extracted grounding boxes on
3/3 frames where the incumbent extracts **0/13** under the same deployed
prompt. The "restate G7 as an absolute threshold" decision was right, and
the candidate would clear it.

### The blocker: `</think>` in spoken content, answer triplicated

The direct 8-probe canary passed. The failure only appeared **through the
Home Assistant conversation path**:

```
"You've got nine areas set up: Kitchen, Living Room, …
</think>

Your home has nine areas: Kitchen, Living Room, …
</think>

Your home has nine areas: Kitchen, Living Room, …"
```

Literal `</think>` delimiters in `speech.plain.speech` — which TTS reads
aloud as "slash think" — and the whole answer generated **three times**.
Intermittent: the very next query ("are any lights on") was clean, which is
worse than a consistent failure, not better; in daily use it would surface
as the assistant randomly stuttering its answer three times with markup.

**Why the canary missed it and the HA path caught it:** the canary sends
bare completions. The leak needs the EOC path's chat-template rendering with
tools and a long system prompt. **A smoke that only probed the engine
directly would have shipped this.**

**Leading hypothesis — the trap index called this exact shot.** "vLLM
silently filters unknown chat-template kwargs (verified in `renderers/hf.py`
at the tag — assertions must be behavioral)." `enable_thinking:false` and
`preserve_thinking:false` were passed via `--default-chat-template-kwargs`;
if this checkpoint's template does not accept those names, they are dropped
without error and thinking stays on. The candidate is a thinking model in a
way the incumbent is not, so nothing suppressed the delimiters.

**Next attempt should, in order:**
1. Render the chat template offline against the candidate and confirm
   whether `enable_thinking` / `preserve_thinking` are actually consumed —
   the plan's pre-day "`apply_chat_template` offline check", now clearly
   mandatory rather than optional.
2. If the kwargs are inert, use `--reasoning-parser qwen3` (plan cell RP),
   which routes think blocks into a separate field instead of content — with
   the same-session grammar re-verify that cell requires (#44012/#50948).
3. Re-run the smoke, **including the HA path**, before going live.

**Do not retry by adding the reasoning parser blind.** That changes two
things at once and the cell exists for a reason.

## Phase 0 — RESULTS (collected 2026-08-16, admission check green)

Collector: `tools/qwen38-phase0-archive.sh` (read-only, re-runnable).
Archive: `/srv/data/eval/migration/phase0/` (178+ files, `MANIFEST.txt`).
Nothing in the live stack was stopped, restarted, or reconfigured.

**Engine baseline (incumbent, util 0.70).** Image digest
`sha256:70a098d90dba…` == the running image ID (pin holds). vLLM 0.20.2,
transformers 5.8.0 (exactly R7's floor), torch 2.11.0+cu130, CUDA 13.0.2.
Startup: `GPU KV cache size: 366,816 tokens`, max concurrency 11.19× at
32768. CUDA graphs capture (0.06 GiB).

- ⚠ **R7 correction — the attention backend is `FLASH_ATTN`, not
  FlashInfer.** vLLM picks FLASH_ATTN out of `['FLASH_ATTN', 'FLASHINFER',
  'TRITON_ATTN', 'FLEX_ATTENTION']`, and the FP8 MoE backend is TRITON (not
  DEEPGEMM). R7's "check the attention backend is actually FlashInfer"
  assumed a state that has never been live. Phase 3 compares against
  FLASH_ATTN as the incumbent baseline; switching backends is a *separate*
  change and must not be bundled into the cutover.
- ⚠ **`VLLM_USE_DEEP_GEMM` is not set today.** Adding `=0` at cutover per R7
  changes two variables at once (model + engine env). Either set it in a
  Phase-3 cell first, or accept and record the confound. DeepGEMM is not
  the selected backend anyway, so the flag is likely inert here.
- The compose comment justifying util 0.70 ("Moshi listener is sitting at
  ~24 GB") is **stale** — the s2s profile is not running.

**VRAM tenant map (measured).** vLLM 68,244 MiB · chatterbox 5,282 ·
parakeet 3,392 · kokoro 1,422 · host 662 → 80,160 / 97,887 MiB. Non-vLLM
tenants total ≈ 11.6 GiB, not the ~24 GB the compose comment assumes. At
util 0.80 the engine would claim 78,310 MiB, leaving ≈ 7.5 GiB headroom —
feasible, and the number to re-verify in Phase 3 rather than assume.

**R1 CONFIRMED exactly, from the cached checkpoint config (offline).**
64 layers, `full_attention_interval: 4` → **16 full-attention layers**, 48
GDN linear-attention layers; `num_key_value_heads: 4`, `head_dim: 256` ⇒
16 × 2 × 4 × 256 × 2 B = **65,536 B = 64 KiB/token bf16** (32 fp8), exactly
as R1 predicted. 131072 ctx = 8.0 GiB, 262144 = 16.0 GiB. GDN recurrent
state = **144 MiB per sequence** (48 × 48 vheads × 128 × 128 × 4 B), the
top of R1's 75–150 MiB band. `mtp_num_hidden_layers: 1` confirms the MTP
head; `max_position_embeddings: 262144` confirms D10's ceiling.
Note the checkpoint declares `mamba_ssm_dtype: float32` — R3's proposed
`--mamba-*-dtype float16` is a deliberate deviation from the checkpoint
default, not a neutral setting.

**Both weight sets cached** (incumbent 31 G, candidate 29 G) with 554 G
free — the rollback precondition holds. No cache pruning until soak exit.

**EOC live subentry frozen** (HA storage is truth; no file copy):
`context_threshold: 24000` — **repo's 40000 is wrong, live wins**;
`max_tokens: 2000`; `chat_model: qwen3-vl-30b` (served-name freeze intact);
prompt 33,760 chars (sha256 `dad9d3cb…`); functions 21,671 chars
(sha256 `5526a526…`). Entry title still reads "Local Qwen3.6" (cosmetic).
- ⚠ **`max_function_calls_per_conversation: 3`** — the live ceiling on
  multi-step tool use. Roadmap C1's "raise scenario `max_tool_calls` 6→10"
  is a *harness* number; production stops at 3. Raising the harness alone
  would test a capability production cannot execute.

**Subentry tool-spec audit** (`tools/qwen38-toolspec-audit.py`): 23 tools,
**2 gate-blocking hazards** — `get_all_rooms_state` and `areas_in_home` are
zero-argument tools, exactly the vllm#50989 doom-loop shape under
`qwen3_coder` (R6). Plus 5 tools with properties but empty `required`
(`execute_services`, `get_history`, `find_clips`, `recap`,
`clear_presence_override`) which the model can satisfy with `{}` — the same
shape. No xgrammar-unsupported features (`$ref`/`allOf`/`oneOf`) anywhere,
so structured output compiles. **These two tools are now a mandatory named
case in the G3-pre streaming replay and the V1 matrix cell.**

**Consumer-timeout inventory vs D1** (live EOC component + sidecar):
`PERCEPTION_AUTO_TIMEOUT_S = 6.0` · `REFRESH_PERCEPTION_TIMEOUT_S = 8` ·
room-binding `_TTL_SEC = 90.0` (the wrong-room actuation path) ·
`GROUNDED_LOOK_TIMEOUT_S = 150` · `FIND_CLIPS_TIMEOUT_S = 6.0` ·
`DESCRIBE_CLIP_TIMEOUT_S = 30.0` · `RECAP_TIMEOUT_S = 8.0` ·
`DEVICE_CONV_TTL_SEC = 900` · sidecar `within_ms = 30_000` ·
`DESCRIBE_CLIP_MAX_FRAMES = 8` · app `processingGuardMs = 30000` ·
`VISION_MAX_TOKENS = 200` · labeler `VLM_TIMEOUT_S = 180`,
`VLM_KEEP_ALIVE = 30m` live (source default says 10m).

**Never-deploy list — divergence confirmed by archived evidence.** The live
EOC component has **no `MODEL_TOOL_CATALOG_ENABLED` constant at all**, while
the repo copy (`ha-config/extended_openai_conversation/const.py:555`) pins
it to `False` under `test_action_containment.py`. Deploying the repo copy
zeroes the 23-tool catalog. Live sources for metrics-sidecar,
vision-sidecar, intelligence, video-labeler and the EOC component are
archived under `live-sources/`.

**ntfy is PUBLIC `https://ntfy.sh/`** — no self-hosted instance on this
host, topic `nut-engineeredlightingserver1-…` (an unlisted public topic is
the only access control). Current senders are text-only, no attachments.
⇒ The soak alarm rule resolves to **no names, no images**, and roadmap E1's
"ntfy + snapshot" design would be cloud egress of camera imagery — barred by
the never-do list until a self-hosted instance exists.

**`/run/ha-maintenance`**: absent (no maintenance in progress). Honoured by
`/usr/local/sbin/ha-reachable` and `/usr/local/sbin/ha-health`, both of
which exit 0 early when it exists — it suppresses paging, and nothing else.

⚠ **New trap — clock skew.** vLLM container logs are **UTC**; the host
journal and baseline CSV are **PDT (UTC−7)**. Correlating a latency spike
across the two without converting reads as a 7-hour-old event. Recorded in
`clock-skew.txt`.

**Still open from Phase 0:** held-task-0.4 confirmation, ceremony-journal
status, traffic-week artifact freeze, and the `apply_chat_template` offline
kwarg-inertness check. (Labeler-schema-vs-xgrammar is **closed**: the
labeler uses `json_object`, so no grammar is compiled — see the G2 section.)

### R4 measured on the incumbent — prefix caching works, and it is worth 5×

Measured 2026-08-16 via `tools/qwen38_capture.py` against the live stack,
using the **real 33,760-char EOC system prompt** from the Phase-0 freeze.
Measurement point: metrics-sidecar `127.0.0.1:8000` → vllm (the production
path). Archive: `phase0/r4-apc-incumbent.ndjson`.

- **The live prompt is 8,682 prompt tokens** — the plan's "~8.5k-token
  prefill" estimate is confirmed to within 2%.
- **Warm (identical prefix, cache hit): p50 0.07 s.**
  **Busted (unique prefix per request): p50 0.36 s.** Same prompt, same
  engine, same everything else: prefix caching is worth roughly **5× on the
  LLM leg** of a voice turn on the incumbent.
- Lifetime engine counters: `vllm:prefix_cache_hits_total` 152,864 /
  `queries_total` 419,783 = **36.4%**; multimodal `mm_cache` 136/368 =
  **37.0%**. `cache_config_info` confirms `enable_prefix_caching="True"`,
  `block_size=32`, `num_gpu_blocks=11463` (× 32 = 366,816 tokens, exactly
  the startup KV line — the two agree).

⚠ **Correction to R4's instrumentation instruction.** vLLM 0.20.2 does
**not** emit `usage.prompt_tokens_details.cached_tokens` — verified across
7 captured turns, the field is absent, not zero. "Instrument
`num_cached_tokens` from day one" cannot be done from the response body.
The instrument that exists is the Prometheus counter pair, read either side
of a cell and differenced (`read_cache_counters` / `cache_hit_delta` in
`qwen38_capture.py`); a lifetime rate averages over months and says nothing
about a cell.

**Why this sharpens the migration's central risk.** R4 predicts prefix
caching may be INERT on the candidate's hybrid attention. We can now price
that: if it is inert, every voice turn moves from the warm path to the
busted path. On the incumbent that is 0.07 s → 0.36 s *before* accounting
for the candidate's 2.4–2.6× slower decode. The D-cells ("does APC work at
all?") are therefore not informational — they are the single measurement
that most determines whether D1's voice budget is reachable, and they
should run **first** in the matrix, not in the middle.

### ⚠ `/trace` has no producer — the gates' primary instrument is dead

The never-deploy table calls live `/trace` NDJSON "the instrumentation every
gate uses". Measured 2026-08-16, it is not instrumenting anything:

- `/app/data/traces.ndjson` holds **170 records, newest 2026-05-17** — three
  months stale. Every record is voice-latency telemetry; **none carries a
  message body**, because the conversation tee is containment-locked (and
  the roadmap's not-doing list bars re-enabling it).
- `prompt_tokens` and `cached_tokens` are present in the schema and **zero
  in all 169 records** — the token accounting was never populated.
- **Root cause:** the only producer is `hav-personaplex-bridge`
  (`main.py:3942`, fire-and-forget POST to the sidecar's `/trace`). That
  bridge is the **s2s path**, which by policy never starts. It is currently
  stuck reconnecting to `ws://moshi-listener:8899` — a service that does not
  exist — logging ~28.6k lines/day to no effect. Production voice
  (Voice PE → Parakeet → HA → EOC → vLLM → Chatterbox) never touches it, so
  `/trace` cannot record a production turn.

Consequences, all of which change the plan rather than the schedule:

1. **G5 has no live transcript source.** `gate-g5-leak-grep.py` returns
   exit 2 (INCONCLUSIVE, deliberately not PASS) against the live file. G5
   must be scored on **harness-captured responses** — the Phase-3 matrix
   driver's own saved completions — not on `/trace`.
2. **G6 must use the instrument D1 already names** — HA
   `/api/conversation/process` for voice, the vision-sidecar request log for
   ambient. Delete the "`/trace` scan" line from the soak section; it would
   scan a file nothing writes.
3. **R4's `num_cached_tokens` instrumentation does not exist yet.** "Instrument
   from day one" is a build task, not a switch: capture `usage.prompt_tokens`
   and `usage.prompt_tokens_details.cached_tokens` in the harness itself.
   (The harness already proves this works — a control request measured
   `prompt_tokens=913` with an image vs `31` without.)
4. **D11 (`/trace` retention acknowledgment) is moot as written** — nothing
   is being retained. It should be restated to cover the harness's own
   captured payloads, which is where the image traffic will actually land.
5. Separately, the bridge's reconnect loop is worth an owner decision on its
   own (log churn on a quarantined host); it is **owner-run** and out of
   scope here.

### Matrix driver built; incumbent D/A cells measured

`tools/qwen38-matrix-driver.py`. It changes nothing about the engine — it
measures whatever is running and records what that was. The session
protocol is enforced rather than remembered: a **leakage canary aborts the
session** before cells run (numbers from a config that cannot ship are
worthless), an **anchor runs first and last** with >10% p50 drift
invalidating the session, and every latency carries its measurement point
and cache state.

Cells P0 (MTP), K (fp8 KV) and RP (reasoning parser) are declared but
**refuse to run** unless the engine already reports that config, so a
session cannot claim a cell it did not exercise.

### ⚠ G6 FAILS ON THE INCUMBENT — D1's voice budget is already breached

Measured 2026-08-16 at D1's own named instrument (HA
`/api/conversation/process`), n=30 per utterance, quiet house, session
drift 1.6%:

| utterance | p50 | p95 | budget |
|---|---|---|---|
| "What rooms are in my home?" (zero-arg tool) | 0.57 s | 0.91 s | ✅ |
| **"Are any lights on right now?"** | 1.84 s | **6.12 s** | ❌ **4.0 s** |
| "What is the temperature in the kitchen?" | 0.17 s | 0.48 s | ✅ |

**The incumbent exceeds the voice p95 budget by 53% on a routine multi-tool
read**, reproducibly — first seen at n=5 (6.20 s) and confirmed at n=30
(6.12 s). The p50 is healthy at 1.84 s; the failure is entirely in the tail.

This is a **pre-existing production characteristic, not a migration
regression**, and finding it now is the whole point of baselining before
touching anything. Three consequences:

1. **G6 as written would fail the incumbent.** A gate the current system
   cannot pass cannot be used to judge a candidate — it would either be
   waived under pressure on cutover day, or it would reject a candidate for
   a fault it inherited.
2. **The tail must be attributed before cutover.** The LLM leg is not the
   suspect: the same engine serves ambient at 0.176 s p95 and voice-under-
   clip-prefill at 0.69 s p95. The 6.1 s is being spent in the HA
   conversation pipeline, the tool round-trips, or entity fan-out — none of
   which the model swap changes. Measure the split before blaming a model.
3. **D1 amended — owner chose (a), 2026-08-16.** G6 is re-scoped to a p50
   budget plus a *bounded* tail allowance, and the 6.12 s tail is tracked
   as a pre-existing defect in its own right rather than folded into a
   pass. The restatement is below.

### D1 AMENDMENT + G6 restated (owner decision, 2026-08-16)

Every threshold below is set from measured incumbent behaviour, so the gate
can be passed by a healthy candidate and failed by an unhealthy one — which
the original could do neither of.

**G6-a — absolute budgets (binding, unchanged where the incumbent meets
them):**
| measure | budget | incumbent | headroom |
|---|---|---|---|
| ambient p95 | ≤ 1.5 s | 0.176 s | 12% used |
| 4-frame clip p95 | ≤ 6 s | 0.46 s | 8% used |
| voice TTFT p95 | ≤ 1.2 s | 0.049 s warm / 0.337 s cache-broken | 4–28% used |

**G6-b — voice end-to-end p50 ≤ 2.5 s (binding).** The incumbent's worst
utterance sits at 1.84 s p50, so this is "no material regression on the
typical turn" with room for the candidate's slower decode. The p50 is the
number a user experiences on almost every turn, and it is not contaminated
by the tail defect.

**G6-c — voice end-to-end tail, PAIRED (binding): candidate p95 ≤ 1.25 ×
the incumbent p95 measured in the SAME session, on the same utterance
set.** Deliberately relative, never absolute: the absolute number is
polluted by a defect the migration did not cause, but an *unbounded* tail
allowance would let a 6 s tail become a 15 s tail unnoticed — which is
exactly the user harm the budget existed to prevent. 1.25× caps degradation
without demanding the candidate fix an inherited fault.

**G6-d — TRACKED PRE-EXISTING DEFECT: voice p95 6.12 s on multi-tool
reads. ⚠ DIAGNOSED 2026-08-16 — and the first attribution was wrong.**

The earlier note here guessed "HA conversation pipeline, tool round-trips,
or entity fan-out". Measuring against the engine's own counters settled it,
and the answer was none of those:

| turn | total | LLM calls | LLM e2e | decode | NOT-LLM |
|---|---|---|---|---|---|
| fast | 0.99 s | 2 | 0.82 s | 0.71 s | 0.18 s |
| slow | 7.04 s | 7 | 6.77 s | **6.06 s** | 0.28 s |

**Non-LLM overhead is 0.1–1.4 s and roughly constant. The variance is
decode, and decode is reply length**: turn duration correlates with
generated tokens at **r = 0.981** (n=8). It is not the pipeline at all.

**Root cause: the model recites state instead of summarising it.** Asked
"are any lights on", it names all twelve entities — sometimes as a markdown
bullet list, read aloud by TTS — and volunteers colour temperatures and
brightness percentages nobody asked for.

**The prompt already forbids this.** "## How to speak" says *"Never use
markdown, bullets… One short sentence is the default."* It is obeyed
everywhere else in the same session — `areas_in_home` answers in 33 chars
and 0.38 s. It fails **only** on live-state queries, and the reason is
positional: **`RULE 0.7b` is the last thing in the 8,682-token prompt**, it
commands the model to check state before answering, and it says nothing
about how to report what it finds. Recency wins.

A reporting clause was added to RULE 0.7b on that reasoning
(`tools/patch-subentry-prompt.py`, +545 chars).

⚠ **IT DID NOT WORK. Applied, A/B-measured, and REVERTED 2026-08-16.**
Clean A/B on identical house state, 20 runs each arm:

| | p50 | p95 | reply chars (p50) | replies quoting %/K |
|---|---|---|---|---|
| unpatched | 2.19 s | 3.73 s | 271 | 3/20 |
| patched | 2.31 s | 3.25 s | 232 | 4/20 |

A 13% tail improvement with p50 slightly worse and **the target behaviour
unchanged** — the model still recited "Ambient Left, Ambient Right, Dining
Table Left, …", which the clause explicitly forbids. That is noise, not a
fix, so the 545 chars were reverted rather than left to dilute a prompt
that already loses instructions to recency.

**What the exercise did establish, which is more useful than the patch
would have been:**

1. ⚠ **The tail is HOUSE-STATE-DEPENDENT, and 6.12 s was not typical.** The
   same query measured **3.73 s p95 unpatched** in later conditions — inside
   the 4 s budget. Reply length tracks how many lights are actually on, so
   G6-d is "slow when the house is busy", not a fixed defect. The earlier
   n=30 measurement was honest but caught a busier house.
2. **Prompt instructions are not the lever here.** Two independent rules
   ("How to speak", and the new clause) both tell the model to summarise,
   and both lose on this query type. A third would very likely lose too.
3. **The likely real lever is the TOOL layer, not the prompt.**
   `get_attributes` hands back a row per entity and the model dutifully
   reports what it was given. Returning a summarised payload would shorten
   the reply without asking the model to disobey its own input. That is EOC
   component code — live, never-deploy — so it is a Phase-2 change, not a
   quick fix.

**Consequence for the migration: none blocking.** G6-b (p50 ≤ 2.5 s) passes
at 2.19–2.31 s, and G6-c is a paired ratio that is unaffected by the
absolute tail. The verbosity remains worth fixing for its own sake, at the
tool layer, after the model change settles.

⚠ **Instrument correction.** D1 named HA `/api/conversation/process` as the
TTFT instrument. **It cannot be** — that endpoint returns a finished
response and never exposes a first token. TTFT is measurable only on the
LLM leg, via streaming at the sidecar (`Capturer.chat_stream`), and any
TTFT figure must say so instead of implying pipeline coverage. HA remains
the correct instrument for voice **end-to-end**, which is what G6-b/c use.

**One number worth keeping in view:** TTFT is 0.037 s warm against 0.332 s
with the cache broken — a **9× swing, entirely prefill**. If R4 is right
that prefix caching goes inert on the candidate's hybrid attention, TTFT
starts from the 0.33 s figure before the candidate's slower prefill is
applied. That is still inside the 1.2 s budget at 3× degradation, but it is
the clause with the least margin, and the D-cells are what will tell us.

**Incumbent baseline session, 2026-08-16** (n=100 per p95-gated cell as the
doc requires; V cell n=30/utterance). Drift 2.4% → valid. Canary clean.
518 completions archived at
`/srv/data/eval/migration/matrix-incumbent-baseline/`.

| cell | p50 | p95 | budget | verdict |
|---|---|---|---|---|
| D warm | 0.068 s | 0.070 s | — | 99.9% cache hit |
| D shared-prefix (real voice shape) | 0.070 s | 0.205 s | — | 98.7% hit |
| D busted | 0.356 s | 0.491 s | — | 0.1% hit |
| A ambient | 0.149 s | 0.176 s | 1.5 s | ✅ 12% of budget |
| E clip2 / clip4 / clip8 | 0.27 / 0.44 / 0.78 s | 0.32 / 0.46 / 0.82 s | 6 s (clip4) | ✅ 8% of budget |
| B-worst (voice during clip prefill) | 0.66 s | 0.69 s | 4 s | ✅ LLM leg only |
| Q quiet sentinels | — | — | — | 7/10 quiet |
| **V voice e2e (HA)** | 1.84 s | **6.12 s** | 4 s | ❌ **see above** |

Read together these say something specific: **every cell that measures the
model is comfortably inside budget, and the only breach is in the cell that
measures the pipeline around it.** The candidate's 2.4–2.6× slower decode
therefore applies to legs that currently use 8–12% of their budgets — and
the one number at risk is a tail the model is not causing.

### G4 corpus built from live Frigate, and the incumbent baselined

`tools/gate-g4-negatives.py`. Corpus at `/srv/data/eval/migration/g4-corpus`:
**35 frames = 25 negatives (12 cup-misfire + 6 night + 7 empty) + 10
quiet-literal sentinels**, exactly the gate's spec, drawn from real Frigate
event snapshots across kitchen / dining_room / living_room.

**The negatives are real, not authored.** Frigate emits ~2,300 `cup` events
in six hours — it re-detects a cup on the kitchen counter continuously. A
frame where the detector fired and nothing happened is precisely the
negative this gate wants, and there are thousands to draw from.

**Scoring is production's verdict, not taste.** Every caption goes through
`classifyLookFinding`, the app's own classifier (ported in `qwen38_gates`,
cross-asserted against the JS in `run-look-tests.js` — 17 shared cases).
G4 therefore answers the operational question: *would the app have raised a
notable finding on a frame where nothing happened?*

**Two design corrections found by building it:**

1. ⚠ **A parked car is not a hallucination.** The first corpus drew a night
   driveway frame from a `car` event; "a car is parked outside" is TRUE
   there, and scoring it as false presence would have penalised an accurate
   caption. Fixed by splitting the mask: living things must be **absent**
   for a frame to qualify at all, while tolerated objects (vehicles, bags)
   may be present and are recorded per frame, so that category stops
   counting as false presence **for that frame only**. Discarding those
   frames instead would have deleted the driveway — the camera most likely
   to invent a person or a package — from the corpus entirely.
2. ⚠ **The sentinels were pointed at the wrong prompt surface.** Measured
   against `/describe`, the incumbent filed **0/10** as quiet — because
   `/describe` says "describe directly", so it answers with room
   inventories and never emits the phrase. The quiet literal is asked for
   by the **deep-look** path ("If nothing important is happening, say
   exactly: No obvious activity."). Pointed at that surface — composed as
   production does, `buildFocusedLookQuestion` wrapped in the sidecar's
   `REASON_SYSTEM`/`REASON_USER_TMPL`, and scored on the sidecar's own
   extracted answer — the incumbent files **8/10**. A gate that asks a
   prompt for a phrase it never requested measures nothing.

**Corpus VERIFIED by the owner 2026-08-16: all 35 frames confirmed
person-free.** The ambiguity is therefore resolved in the direction that
makes the gate real — the incumbent's person claims are **genuine
hallucinations**, not a detector that missed someone in the dark.

**Incumbent baseline** (2026-08-16, production path, cold cache, n=35,
verified corpus): **false presence 5/35**; **quiet sentinels 8/10**;
**zero G5 leaks**; p50 0.16 s, p95 0.33 s.

| frame | category | importance | caption |
|---|---|---|---|
| night_014_living_room | person | 90 | "A person is sitting in the living room, partially visible behind a TV." |
| night_017_living_room | person | 90 | "A person is sitting in a chair in the living room." |
| sentinel_031_dining_room | person | 90 | "Open doorway with person visible inside." |
| sentinel_033_dining_room | hazard | 100 | "Open door and potential tripping hazard in hallway." |
| cup_misfire_009_kitchen | activity | 55 | "A cup is on a shelf in the kitchen." |

**The incumbent invents a person on 3 of 35 empty frames, twice in a dark
living room and once in a doorway** — each at importance 90, which is a
notification the owner would actually receive. This is a pre-existing
production behaviour, measured for the first time here, and it is the
number the candidate has to beat rather than a bar set by assumption.

⚠ One honest caveat on precision: `cup_misfire_009` is an **accurate**
caption ("a cup is on a shelf") that the classifier files as `activity`
only because "shelf" is not in its inventory vocabulary. So the absolute
5/35 slightly over-counts. It does **not** bias the gate: G4 is paired, the
same classifier scores both arms, and a systematic vocabulary gap cancels
between them. It adds noise to the absolute rate, not to the comparison.

### ⚠ G2 has no request log either — restated as a keyframe replay

The third data source checked, and the third that does not hold what the
plan assumed. Verified 2026-08-16 against the live labeler DB
(`/opt/home-ai-voice/video-labeler-data/videolabeler.db`):

- **No `request_json`** — not a table, not a column, nowhere in the schema
  or the source. Raw model requests are never persisted.
- **No "last-14-days live rows"**: the newest row is **2026-06-12**, and
  the whole prelabel corpus was written in one ~9.5-hour batch that day.
- **The stored prelabels came from `qwen2.5vl:32b`**, not the incumbent
  (`prelabel_status: done:labeler-qwen2.5vl-32b:v1|v2`), so they are not an
  incumbent baseline for anything.

What survives is better than a request log: the labeler retains
**keyframes per analysis window** — **683 windows × 9 frames**, the exact
model input, plus `evidence_json` carrying each prior structured output.
G2 therefore becomes a **true paired replay**: run both arms over the same
683 windows with the live two-pass prompt and compare validity. n=683
clears the n≥500 bar without needing live traffic, and it is repeatable,
which live traffic never was. This is the plan's own "dormant fallback",
promoted to the primary path.

**Also resolves an open Phase-0 item.** The live labeler sends
`response_format: {"type": "json_object"}` — **not `json_schema`**. Nothing
is grammar-constrained today, so **xgrammar's unsupported-feature list does
not apply to the labeler**, and "schema validity" means "the reply parses
and conforms", checked in Python by `vlm/schema.py`. Grammar-constrained
output arrives with labeler v2 in Phase 2; the xgrammar check belongs
there, not here. (The *subentry* tool specs were separately audited and are
clean — see the tool-spec audit above.)

Harness parity: `gate-g2-labeler-replay.py` imports the live `ontology`
enums and the live prompt builders rather than copying them, and
`tools/test-qwen38-g2-replay.py` asserts the live `Pass2Result` field set is
unchanged — so a labeler edit fails a test instead of silently making this
gate measure the wrong contract. Smoke-tested against the incumbent
2026-08-16: 3/3 valid, zero leaks, pass-2 p50 1.01 s (measured at the
sidecar, cold cache).

### G7 baseline measured on the incumbent — the gate must be restated

Measured 2026-08-16 with `probe-grounded-reasoning.py --production-parser`
against the live stack (measurement point: metrics-sidecar
`127.0.0.1:8000` → vllm, cache busted per run via one-pixel perturbation;
`temperature=0.2`), plus the production `POST /reason` endpoint on the live
vision-sidecar. Real camera frame (`camera.kitchen`, 1280×720) and a sim
frame, incumbent `Qwen3-VL-30B-A3B-Instruct-FP8`.

| prompt | extraction | app-strippers clean | ANSWER line | G7 pass |
|---|---|---|---|---|
| v1 (explicit box-while-reasoning) | 5/6 | 3/6 | 3/6 | 2/6 |
| v2 (terse) | 2/4 | 4/4 | 0/4 | 1/4 |
| **v3 — the DEPLOYED `/reason` prompt** | **0/13** | 13/13 | 13/13 | **0/13** |

Production `POST /reason` on `camera.kitchen` returns
`primitives: []` — confirmed against the live sidecar, not just the probe.

1. ⚠ **The incumbent grounds at zero under the deployed prompt.** The model
   is capable — v1 extracts 5/6 on the same frame — so this is the prompt,
   not the checkpoint. The deployed v3 prompt's "Be decisive and concise —
   a few sentences, not a long list" appears to suppress the per-object
   boxing that v1 elicits.
2. ⚠ **Therefore G7 as written cannot fail.** "Extraction non-inferior to
   incumbent, tolerance from incumbent re-run variance" against a baseline
   of 0/13 with zero variance is satisfied by a candidate that also emits
   nothing. **G7 must be restated before it gates anything**: either
   (a) repair the deployed prompt first so a real baseline exists, then run
   G7 as a paired non-inferiority test; or (b) restate G7 as an ABSOLUTE
   threshold on the candidate, decoupled from the incumbent.
   **DECIDED 2026-08-16: (b) for the cutover, with (a) tracked separately.**
   The prompt repair is a prompt-surface change and must not ride along
   with the model swap.

   **G7 thresholds, set from the measured evidence** (frozen corpus, the
   deployed v3 prompt, production parsers, `--production-parser`):
   - **app-strippers clean ≥ 99%** — near-absolute, because a failure here
     is raw markup on the user's screen. The app side is now widened to the
     sidecar's tolerance (PR 1a-ter item 1, landed), so the only remaining
     way to fail is a form neither consumer knows.
   - **runaway ≤ incumbent** on the same corpus, paired — the incumbent
     measured 0/13 runaway on v3, so this is a real constraint.
   - **ANSWER-line rate ≥ 95%** — incumbent measured 13/13 on v3.
   - **extraction: REPORT-ONLY at cutover, not gating.** The deployed
     prompt yields 0/13 on the incumbent, so there is no honest bar to hold
     the candidate to while that prompt ships. Extraction becomes gating in
     the same change that repairs the prompt, at which point the bar is set
     from the repaired prompt's incumbent baseline.

   Stating it plainly: **G7 at cutover verifies that grounded output which
   IS produced is consumable end-to-end, not that grounding happens.** The
   latter is a prompt problem this migration deliberately does not solve.
3. ⚠ **Live grounded-look is silently degraded today, independent of this
   migration.** `/reason` returns no primitives, so `annotate_frame` draws
   nothing and the `/look` drawer's ref-chips render nothing. Whatever the
   migration does, this is a pre-existing production regression and it
   should not be discovered mid-cutover and misattributed to the candidate.
4. ⚠ **The app-stripper divergence is real and was observed live.** Runs
   that hit the generation cap mid-markup leave a dangling `<box>` with no
   close; the app's `/<box>[\d,\s]*<\/box>/gi` requires the close, so the
   tag renders raw to the user (`residual=['<box>']`, runs 4–5 of 6 at
   `max_tokens=500`). This is the trap-index entry "test max_tokens
   truncating structured output", now with a UI-visible consequence.
   Separately and still latent: the sidecar was widened to accept
   `<box>[[x,y,z,w]]</box>` and a bare `>` close, and the app strippers
   never were — proven by unit test in `tools/test-qwen38-gates.py`. If the
   candidate prefers either variant, every grounded look renders raw markup
   while the server-side parse looks perfectly healthy.
   **G7 pass therefore requires both consumers, which is why
   `--production-parser` scores both.**

## Phase 3 — latency matrix (each session is a mini maintenance window)

Session protocol: flag up → quiesce labeler AND ambient loop → admission
check → weights check → session config manifest (that cell family's
compose) → startup assertions (leakage canary always; C-cell band once it
exists) → cells → **mandatory incumbent restore + incumbent smoke +
supervisor `ready` before clearing the flag**. Candidate windows are
excluded from G2's re-baseline window. Session 1 entry gate = the
`stream=True` qualification tool replay.

⚠ **Run the D-cells FIRST** (Phase-0 R4 measurement above): prefix caching
is worth ~5× on the incumbent's LLM leg, so whether it survives on the
hybrid decides whether D1's voice budget is reachable at all. A matrix that
discovers this last has spent its evenings tuning around the wrong lever.

Cells: P0 MTP viability (off-vs-off control → semantic garble gate; MTP ×
json_schema mandatory; long-session >26k) · K (fp8-KV garble-gated) ·
A1/A2 anchors · **B-real (numerically spec'd bursty cadence — headline)** ·
**B-worst (voice during 8-frame clip prefill — headline)** · B-sat
(informational) · C1/C2 (kwarg cost + leakage control; bare requests —
config-under-test is the server default) · D1–D3 (does APC work at all?) ·
E1 (clip 2/4/8) · V1 (voice tool-call streaming through HA incl. parser
edge cases) · RP (reasoning-parser grammar re-verify) · DL (deep-look
shape) · Q (10 empty-frame quiet-literal sentinels). N≥100 completions for
any p95-gated cell; session ends with a baseline re-run drift control
(>10% p50 drift → session invalid).

Adopt rules: MTP iff decode ≥1.3× AND B-real/B-worst voice p95 clean AND
grammar + long-session + garble clean (fallback n=1). fp8 KV iff K clean.
Effort pin iff C1→A1 shows benefit. Reasoning parser iff RP clean.

## Acceptance gates (evidence collected pre-cutover in Phase-3 sessions)

| Gate | Threshold | Notes |
|---|---|---|
| G1 ambient caption quality | one-sided non-inferiority α=0.05, binomial critical value on the actual non-tied count; hallucinated objects paired one-sided | 50 frozen frames × both models, blinded, +10 incumbent-vs-incumbent decoys |
| G2 labeler validity | ⚠ **RESTATED — see "G2 has no request log" below.** Non-inferior by McNemar over **n=683 stored analysis windows replayed through both arms** | `tools/gate-g2-labeler-replay.py`; the dormant fallback is now the primary path — there is no request log and no recent live traffic to re-baseline from |
| G3-pre (CUTOVER) | parsed-args exact, zero ParseArgumentsFailed, incl. R-edge cases | streaming replay + V1 cell; `qwen3_xml` staged |
| G3-live (SOAK-EXIT) | 20 scenarios × 5 ≥ 4/5 each; zero unsafe_tool_call; regression >1 scenario 2 days = rollback | includes `/recap` + `/find-clips` prose-invocation re-tests |
| G4 hallucination-on-negatives | false-presence ≤ incumbent, paired | 25 empty/night/cup-misfire negatives + 10 quiet-literal sentinels |
| G5 reasoning leakage | ZERO — `<think>` in content + BOTH `reasoning`/`reasoning_content` fields | all transcripts + adversarial canaries |
| G6 latency | ⚠ **RESTATED 2026-08-16 (owner chose option a).** G6-a absolute: ambient p95 ≤ 1.5 s, clip4 p95 ≤ 6 s, TTFT p95 ≤ 1.2 s. G6-b voice e2e **p50 ≤ 2.5 s**. G6-c voice e2e **p95 ≤ 1.25× the incumbent's p95 in the same session** (paired — the absolute tail is polluted by a pre-existing defect). G6-d tracks that defect separately and does not gate | headline = B-real + B-worst; DL budgeted or excluded; TTFT is the LLM leg at the sidecar, NOT HA — that endpoint cannot expose a first token |
| G7 grounded-box compat | ⚠ **RESTATED — see "G7 baseline measured" above.** The incumbent extracts 0/13 under the deployed prompt, so paired non-inferiority cannot discriminate. Score the candidate against an ABSOLUTE threshold on the frozen corpus (extraction ≥ X%, app-strippers clean ≥ Y%, runaway ≤ incumbent), with X/Y set from the v1-prompt evidence at sign-off | production parser scoring, NOT the probe's tolerant one (`--production-parser`); pass requires BOTH the sidecar parser and the two app stripper regexes; fail path: `bbox_2d` branch in sidecar + app strippers, or grammar json_schema boxes |

Winner-config rule: the final Phase-3 session runs the declared shipping
config end-to-end and re-collects G1/G4/G7 + a fresh G5 on it; any config
change after that session repeats it.

## Cutover runbook (1b)

Preconditions: Phase 3 complete on winner config; G1 judged; G2/G4/G5/G7
green; G3-pre green; rollback rehearsed; admission green; not traveling;
ceremony quiescent; `apply_chat_template` offline check done (kwarg-inert ⇒
`--chat-template` file fallback decided pre-day); both weight sets + disk
verified. Window ≈ 2 h + 2 h observation.

1. Admission check (abort-no-retry).
2. Assert local vLLM image ID == Phase-0 archived digest (`:latest` must
   not smuggle an engine change; qualification is valid only on that
   digest).
3. Stash live compose → `.pre-qwen38.<date>` + `nvidia-smi` + last baseline
   CSV row. **Rollback = stashed compose + `docker compose up -d vllm`
   (~3–5 min; both weight sets stay cached; no cache pruning until soak
   exit).**
4. `touch /run/ha-maintenance`; quiesce labeler AND ambient loop (both
   branches pre-decided in Phase 0; env-flag recreate of the vision-sidecar
   is a NAMED exception to step 5). **App hygiene: close/ignore the Home
   app — the AI-Stack card will show `partial` and offer "start ai stack";
   DO NOT press it. Known trap: a parser/template startup failure logging
   "not found" is misdiagnosed by the card as "Container image
   unavailable". Readiness = supervisor `overall` + the card's `model:`
   line ONLY (sidecar `model` is sticky-forever; `ttft_ms` is a cumulative
   mean). NEVER edit `EXPECTED_VLLM_MODEL`.**
5. Compose edit — exact diff, nothing else: model →
   `Qwen/Qwen3.8-27B-FP8`; image digest unchanged; **served name
   `qwen3-vl-30b` unchanged** (collapses ~20 coupling points; ADR-004
   precedent); parser → `qwen3_coder`; util → Phase-3-confirmed (0.80
   planned); template kwargs = Phase-3 winner (baseline
   `{"enable_thinking": false, "preserve_thinking": false}`);
   `VLLM_USE_DEEP_GEMM=0`; `--generation-config vllm` + mamba-cache-dtype
   flags per Phase-3 winner; `--reasoning-parser qwen3` only if RP passed;
   `--speculative-config` only if the full MTP rule passed;
   `--kv-cache-dtype fp8` only if K passed; keep max-model-len 32768,
   max-num-seqs 4, `image:8, video:0`. Later raises are separate changes.
6. `up -d vllm`; assert: weights loaded, parser registered, "GPU KV cache
   size" consistent with 64 KiB/token math (hundreds of k tokens; hard
   floor 131k+margin; a ~170k bf16 pool means the geometry assumption is
   wrong — stop), GDN state line sane, no CUDA-graph/spec errors.
7. Startup assertions: `/v1/models` = `qwen3-vl-30b` via sidecar;
   supervisor `warming→ready`; reasoning-pin 3-layer verification
   (pre-day template render diff; behavioral canary n≥10 incl. trivial +
   broad-visual bait, both reasoning fields empty; xhigh differential
   proves the knob live and the default low); multi-turn prompt-token diff
   confirms `preserve_thinking:false`; one grammar request exact.
8. Smoke fastest-first: raw completion → `/describe` → grounded probe ×3
   (production parser + app stripper regexes) → labeler packet replay →
   `npm run llm:test:quick` → `/describe_clip` 4,8 →
   `llm:test:travel-mode` (100% write block).
9. Quick gate re-asserts; **clear the Lab baseline**
   (`localStorage["hg-lab-turns-v1"]` / `hg-lab-persist="off"` — the 2.5×
   "very_slow" threshold equals the expected regression; rollback
   judgments come from `/trace`/measurement rows, never the Lab tint).
10. Soak entry (order per Phase-0 flag semantics): rm flag / re-enable
    labeler+ambient / 2 h armed observation ≥95% schema-valid, zero
    sanitizer trips. "Gates green" = G1/G2/G4/G5/G6/G7 + G3-pre.
11. Rollback triggers (any one, no debate): garbled/looping output;
    leakage reaching TTS; tool-parse failure rate > incumbent; labeler
    validity < re-baseline − 5pp sustained ≥200 packets; voice p95 >
    budget 2 h; ambient p95 > budget 2 h (vision-sidecar log/M0 — the
    proxy never sees ambient); supervisor stuck `warming` >20 min; G3-live
    regression >1 scenario 2 days. Mechanics (incl. mid-soak): flag →
    quiesce (labeler, ambient, F0 lane once Crawl begins) → restore
    stashed compose → up → step-7/8 assertions vs the incumbent →
    re-enable → rm flag → ADR note → `PROMPT_VERSION` bump before any
    further prelabel lane.

## Phase 2 — prompt translation (post-soak; ★ = pre-cutover)

EOC subentry 33.7k: verbatim first, then SHRINK (the primary lever — APC
likely inert) with golden-transcript replay per edit; volatile-state-to-
tail restructure only if D-cells show APC works. Tool descriptions:
qwen3_coder idiom pass. `/describe`/`/describe_clip`: near-verbatim.
`/reason`/`/reason_zoom`: G7-decided. Labeler ★(re-baseline after 0.4
fixes pre-cutover); v2 + grammar output post-soak via off-host build.
Bridge: verify + pin (80-token ceiling documented). App-composed prompts:
translate + re-test (VL-specific segmentation line per G7 outcome).
Consumer timeouts: retune row (sidecar `within_ms`, EOC 6/8 s,
`processingGuardMs` toward the 120 s HA ceiling, room-binding TTL vs
measured follow-up p95) — **roadmap C1 is gated on this row**.
reasoning_effort policy: server default low/off; ambient/labeler/bridge
off-low; voice A/B before higher.

## Soak (2 weeks)

Daily 10-caption judged sample vs the frozen G1 corpus (alarm per the ntfy
payload rule: no names, no images unless self-hosted verified); nightly
baseline CSV + `/trace` scan (voice-only by construction) + labeler
validity DB query + ambient garble/latency scan; weekly G3-live batches;
weekly weight-cache + disk re-verify; no prompt-surface edits; no
Lab-tint-driven decisions.

## D9 clearance amendment (draft — owner ratifies by merging into the
stability review)

> The conditional clearance of `docs/UBUNTU-AI-HOST-STABILITY-REVIEW-
> 2026-08-13.md` is extended to cover, as reviewed production-maintenance
> operations: (a) the Qwen3.8-27B cutover (compose edit + container
> recreate; weights pre-cached; no image builds); (b) bounded Phase-3
> benchmarking sessions under the session protocol (flag, quiesce,
> admission checks before/after, mandatory incumbent restore); (c)
> recurring inference lanes against the already-running vLLM (nightly
> video-labeler prelabels, daily soak judging, scheduled digest). The
> 5-step maintenance admission check remains mandatory before and after
> every session; any failed check aborts without retry. Host image builds
> remain prohibited; images are built off-host and transferred via
> `docker save/load`.

## Trap index (learned the hard way — do not rediscover)

kv-fp8 garble incident (compose:59-63, EXPERIMENTS-S2S.md:290-297; revised
posture per R2 above) · vLLM silently filters unknown chat-template kwargs
(verified in `renderers/hf.py` at the tag — assertions must be behavioral)
· `MODEL_CONFIG_PATTERNS` `^o[1-4]|^gpt-5` (served-name freeze avoids) ·
EOC subentry is storage, not files · repo threshold 40000 vs live 24000 ·
bridge `VLLM_MAX_TOKENS=80` · test max_tokens truncating structured output
· every latency number states its measurement point + cache state · prod
restarts flush caches (first-turn spike; ambient backlog) · s2s profile
never starts during GPU-tight operations · sidecar Prometheus parsing is
metric-name- and `model_name=`-label-coupled · `stack.sh` SERVICES omits
chatterbox-tts and intelligence · the app renders leaked thinking verbatim
(no client-side guard) · supervisor reads `data[0].id` only.
