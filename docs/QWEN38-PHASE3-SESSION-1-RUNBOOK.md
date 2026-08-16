# Phase-3 Session 1 — candidate residency runbook (OWNER-RUN)

> Prepared 2026-08-16 from measured Phase-0 evidence. Every expected value
> below was observed on this host, not assumed. Companion:
> `docs/QWEN38-MIGRATION.md`.
>
> **This session is reversible and bounded.** It loads the candidate model,
> runs the matrix cells, and restores the incumbent before you walk away.
> It is NOT the cutover — nothing stays changed.

**Time:** ~35 min hands-on, ~50 min wall clock.
**Prerequisite:** a quiet house. Not while anyone is using voice.

---

## 0. What this session answers

One question decides the migration, and it is cheap to answer:

**Does prefix caching survive on the candidate's hybrid attention?**

Measured on the incumbent, prefix caching is worth **5.23×** on the LLM leg
(0.068 s warm vs 0.356 s busted, real 8,682-token production prompt), and
**9×** on TTFT (0.037 s vs 0.332 s). R4 predicts it may be inert on the
candidate. If it is, every voice turn pays full prefill *before* the
candidate's 2.4–2.6× slower decode applies. That is why the D-cells run
first: if they come back inert, you have your answer in ten minutes and can
restore without running anything else.

---

## 1. Preconditions — all must be true

Run the preflight, which checks every item mechanically:

```bash
cd ~/code/home-el
python3 tools/qwen38-phase3-preflight.py
```

It verifies: system running · all `hav-*` healthy · disk + temps · no new
kernel errors · no failed units · **both weight sets cached** · engine image
digest matches the Phase-0 archive · candidate config present · VRAM
headroom · ambient traffic quiet · maintenance flag absent · ceremony
quiescent.

**Abort without retrying if any check fails** (stability-review rule). Do
not "fix and continue" — reschedule.

Additionally, by hand:
- [ ] You are **not travelling** and not about to leave.
- [ ] Nobody in the house needs voice for the next hour.
- [ ] The Home app is **closed**. The AI-Stack card will show `partial`
      and offer "start ai stack" — **DO NOT PRESS IT**.

---

## 2. Admission check (abort-no-retry)

Included in the preflight, but the rule is yours to enforce: any failure
aborts the session. There is no retry, no "it was probably fine".

---

## 3. Flag and quiesce

```bash
sudo touch /run/ha-maintenance          # suppresses paging only
docker stop hav-personaplex-bridge      # ambient /describe driver #1
```

⚠ **Corrected from the plan:** ambient traffic is ~**1,400 /describe per
day**, not the ~350 the plan assumed, and it has **two** drivers —
`hav-personaplex-bridge` (172.18.0.3) and Home Assistant (192.168.0.125).
Stopping the bridge removes one. For the HA side, either disable the
perception automation or accept it and note it in the session record.

**Verify quiesce empirically rather than trusting the list** — a driver
nobody enumerated is exactly the sort of thing this migration keeps
finding:

```bash
# Should print a small number. If /describe is still flowing, find the
# caller in the log line before proceeding.
docker logs --since 2m hav-vision-sidecar 2>&1 | grep -c "POST /describe"
docker logs --since 2m hav-vision-sidecar 2>&1 | grep -oE '^INFO: +[0-9.]+:' | sort | uniq -c
```

---

## 4. Stash the compose (this IS the rollback)

```bash
cd /opt/home-ai-voice
cp -a docker-compose.yml docker-compose.yml.pre-qwen38.$(date +%Y%m%d-%H%M)
nvidia-smi > ~/phase3-session1-nvidia-before.txt
ls -la docker-compose.yml.pre-qwen38.*
```

**Rollback is: restore that file and `docker compose up -d vllm`.** ~3–5
min, both weight sets stay cached, nothing re-downloads.

---

## 5. The compose edit — exactly four lines

In `/opt/home-ai-voice/docker-compose.yml`, the `vllm:` service (block
starts at line 12):

| line | from | to |
|---|---|---|
| `--model` value | `Qwen/Qwen3-VL-30B-A3B-Instruct-FP8` | `Qwen/Qwen3.8-27B-FP8` |
| `--tool-call-parser` value | `hermes` | `qwen3_coder` |
| `--default-chat-template-kwargs` | `'{"enable_thinking": false}'` | `'{"enable_thinking": false, "preserve_thinking": false}'` |
| add after the kwargs line | — | `- --generation-config`<br>`- vllm` |

**Change NOTHING else.** Specifically leave alone:
- `image:` — the digest is pinned and qualification is valid only on it
- `--served-model-name qwen3-vl-30b` — the freeze collapses ~20 coupling points
- `--gpu-memory-utilization 0.70` — the plan's 0.80 is a *separate* change;
  one variable at a time or the D-cell result is uninterpretable
- `--max-model-len 32768`, `--max-num-seqs 4`, `--block-size 32`,
  `--limit-mm-per-prompt '{"image": 8, "video": 0}'`
- `--enable-prefix-caching` — **must stay on; it is what the D-cells measure**
- Do **not** add `--kv-cache-dtype fp8` (that is cell K, a later session)
- Do **not** add `--speculative-config` (that is cell P0)
- Do **not** add `--reasoning-parser` (that is cell RP)

`preserve_thinking: false` matters: it is a NEW default-**on** kwarg that
replays prior-turn reasoning into history. vLLM silently drops unknown
template kwargs, so its presence proves nothing — the canary in step 7 is
what actually verifies it.

⚠ `VLLM_USE_DEEP_GEMM=0` is **not** set today and should **not** be added
here. R7 recommends it, but adding it now changes two variables at once,
and DeepGEMM is not the selected backend anyway (the engine picks TRITON).
Test it separately if ever.

---

## 6. Bring up

```bash
cd /opt/home-ai-voice
docker compose up -d vllm            # vllm ONLY — never `up -d` bare
docker logs -f hav-vllm              # watch until "Application startup complete"
```

First load may take longer than the incumbent's — weights are cached, but
`torch.compile` will not have an AOT entry for this architecture yet.

---

## 7. Startup assertions — STOP conditions

```bash
docker logs hav-vllm 2>&1 | grep -iE "GPU KV cache size|Maximum concurrency|attention backend|Graph capturing|error|Traceback"
curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool
```

| assert | expected | if not |
|---|---|---|
| `/v1/models` id | **`qwen3-vl-30b`** (the frozen served name) | STOP — every consumer is coupled to this string |
| weights loaded, no traceback | clean startup | STOP |
| **GPU KV cache size** | **≥ 300,000 tokens** | **STOP if ~120–170k** — see below |
| Maximum concurrency for 32,768 | comfortably > 4 | investigate |
| Graph capturing | completes | note, continue |
| parser registered | no "tool parser not found" | STOP |

**The KV assertion is the geometry check, and it is unambiguous.** The
candidate holds paged KV on only 16 of 64 layers → **64 KiB/token**, so a
pool of X GiB gives X × 16,384 tokens. If the geometry assumption were
wrong (all 64 layers), you would see ~4× fewer. A pool near 120–170k
tokens means the assumption is wrong — **stop and roll back**, because
every VRAM and long-context conclusion in the plan rests on it.

⚠ **A startup failure is mis-diagnosed by the app's AI-Stack card as
"Container image unavailable."** Read the actual `docker logs`. Readiness is
the supervisor `overall` plus the card's `model:` line ONLY — the sidecar's
`model` field is sticky-forever and its `ttft_ms` is a cumulative mean.
**Never edit `EXPECTED_VLLM_MODEL`.**

Then the reasoning-pin canary — the session's entry gate:

```bash
cd ~/code/home-el
python3 tools/gate-g5-leak-grep.py --help >/dev/null && echo "G5 tool ready"
sudo install -d -o $USER -g $USER /srv/data/eval/migration/phase3-s1
python3 tools/qwen38-matrix-driver.py run \
  --out /srv/data/eval/migration/phase3-s1-canary --cells D --n 1 --canary-n 8
```

The driver runs the leakage canary **before** any cell and **aborts the
session** if reasoning appears in content or in either reasoning field. A
leaking config cannot ship, so there is no point measuring it.

---

## 8. Run the cells — D first

```bash
cd ~/code/home-el
python3 tools/qwen38-matrix-driver.py run \
  --out /srv/data/eval/migration/phase3-s1 \
  --cells D,A,E,B,Q,V --n 100
```

~15 min. Read the **D-cell verdict as soon as it prints**:

- **"APC is WORKING"** (busted/warm > 1.3×) → continue; the plan's latency
  assumptions hold.
- **"APC looks INERT"** → this is the R4 outcome. Let the run finish for the
  record, then restore. Do not spend further evenings tuning around it;
  bring the result back and re-plan, because it changes what the candidate
  costs on every voice turn.

Then score the paired voice tail against the incumbent baseline:

```bash
python3 tools/qwen38-matrix-driver.py report \
  /srv/data/eval/migration/phase3-s1 \
  --incumbent /srv/data/eval/migration/matrix-incumbent-baseline
```

**Session validity:** the driver runs an anchor first and last; **>10% p50
drift invalidates the session**. If it reports INVALID, the numbers are not
comparable to the incumbent baseline — re-run rather than reasoning from
them.

Optional if time allows (G3-pre evidence, and the zero-arg doom-loop test
that `qwen3_coder` newly exposes):

```bash
python3 tools/gate-g4-negatives.py run /srv/data/eval/migration/g4-corpus --arm candidate
python3 tools/gate-g4-negatives.py score /srv/data/eval/migration/g4-corpus
```

⚠ The V cell exercises `areas_in_home`, one of the **two zero-argument
tools** (`get_all_rooms_state` is the other). Under `qwen3_coder` these are
the vllm#50989 doom-loop shape. On the incumbent's `hermes` parser they
complete cleanly in 1.20 s — that is the before-picture. **A turn that
never returns is the signature.** If it hangs, that is a finding, not a
malfunction: record it and roll back.

---

## 9. Rollback triggers — any one, no debate

- garbled or looping output
- reasoning leakage reaching any transcript
- a tool call that never terminates (the zero-arg doom loop)
- KV pool inconsistent with 64 KiB/token
- supervisor stuck `warming` > 20 min
- anything you did not expect and cannot explain in a sentence

---

## 10. MANDATORY restore — this is not optional

The session does not end when the cells finish. It ends here.

```bash
cd /opt/home-ai-voice
cp -a docker-compose.yml.pre-qwen38.<TIMESTAMP> docker-compose.yml
docker compose up -d vllm
docker logs -f hav-vllm                      # wait for startup complete
```

Incumbent smoke — all four must pass before you clear the flag:

```bash
curl -s http://127.0.0.1:8000/v1/models | grep -q qwen3-vl-30b && echo "1. served name OK"
docker logs hav-vllm 2>&1 | grep "GPU KV cache size" | tail -1   # expect ~366,816
cd ~/code/home-el && python3 tools/qwen38-matrix-driver.py run \
  --out /srv/data/eval/migration/phase3-s1-restore --cells D --n 10   # expect ~5x, "APC is WORKING"
```

```bash
docker start hav-personaplex-bridge          # restore ambient
sudo rm -f /run/ha-maintenance               # clear the flag LAST
```

Re-run the 5-step admission check after the session (stability-review
requirement) and confirm all `hav-*` are healthy:

```bash
systemctl is-system-running && docker ps --filter "name=hav-" --format '{{.Names}} {{.Status}}'
```

---

## 11. What this session produces

| artefact | path |
|---|---|
| session record + verdicts | `/srv/data/eval/migration/phase3-s1/session.json` |
| every completion captured | `/srv/data/eval/migration/phase3-s1/turns.ndjson` |
| paired G6-c voice-tail score | printed by `report --incumbent` |
| G4 candidate arm (if run) | `/srv/data/eval/migration/g4-corpus/g4.candidate.json` |
| incumbent reference | `/srv/data/eval/migration/matrix-incumbent-baseline/` |

Bring `session.json` back and the migration doc gets its Phase-3 status
notes. **Nothing about this session commits to the cutover** — the failure
default remains stay-on-incumbent (D2).

---

## Numbers to compare against (incumbent, measured 2026-08-16)

| cell | incumbent p50 | incumbent p95 |
|---|---|---|
| D warm | 0.068 s | 0.070 s |
| D shared-prefix (real voice shape) | 0.070 s | 0.205 s |
| D busted | 0.356 s | 0.491 s |
| TTFT warm / busted | 0.037 s / 0.332 s | 0.049 s / 0.337 s |
| A ambient | 0.149 s | 0.176 s |
| E clip4 | 0.44 s | 0.46 s |
| B-worst voice | 0.66 s | 0.69 s |
| V voice e2e (worst utterance) | 1.84 s | 6.12 s |
| G4 false presence | 5/35 | — |
| Q quiet sentinels | 7–8 of 10 | — |
| startup KV pool | 366,816 tokens | — |
