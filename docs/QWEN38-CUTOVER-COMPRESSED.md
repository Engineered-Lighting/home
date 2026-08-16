# Qwen3.8-27B — compressed cutover (OWNER-RUN)

> **Owner decision, 2026-08-16: evaluate the candidate in daily use rather
> than through a multi-evening Phase-3 comparison matrix.** This supersedes
> `docs/QWEN38-PHASE3-SESSION-1-RUNBOOK.md` as the active path. That runbook
> stays valid if you ever want the full matrix; nothing in it is wasted, and
> its cells still run on demand.

**Hands-on: ~20 minutes.** ~10 to swap and start, ~3 for the smoke, the rest
watching. Rollback is 3–5 minutes at any point, forever.

---

## Why this is a defensible trade

For a single-household system where the owner *is* the evaluator, "I will
notice if it gets worse" is a real evaluation strategy — provided two things
hold, and here they do:

1. **Rollback is cheap and rehearsed.** Both weight sets stay cached, so
   reverting is a file copy plus one container recreate. Nothing
   re-downloads.
2. **The failures you would NOT notice as "worse" are checked anyway.** Some
   failures don't degrade quality, they break the house: a voice turn that
   never returns, or the assistant speaking its own reasoning aloud through
   the speakers. Those take three minutes to rule out, not three evenings.

What is being deferred is the *comparison* evidence: blinded caption
judging (G1), the 683-window labeler replay (G2), and paired
hallucination scoring (G4). All three harnesses exist and their incumbent
arms are already captured, so you can run them any time — including after
the fact, on the candidate, from daily-use data.

**What you give up by skipping them:** you will not have a defensible answer
to "is it *actually* better, or does it just feel different?" If it feels
worse in week two, you will be reasoning from memory rather than from 50
blind judgements. That is a real cost, and it is reversible — the corpora
are frozen and waiting.

---

## Preconditions

```bash
cd ~/code/home-el && python3 tools/qwen38-phase3-preflight.py
```

Must print **GO**. It checks 22 things mechanically, including that both
weight sets are cached (rollback depends on it) and the engine image digest
still matches the qualified one.

Also: the Home app **closed**, and nobody needing voice for the next
20 minutes. The AI-Stack card will show `partial` and offer "start ai
stack" — **do not press it**.

---

## 1. Flag and quiesce

```bash
sudo touch /run/ha-maintenance
docker stop hav-personaplex-bridge     # one of two ambient /describe drivers
```

---

## 2. Stash the compose — this IS your rollback

```bash
cd /opt/home-ai-voice
cp -a docker-compose.yml docker-compose.yml.pre-qwen38.$(date +%Y%m%d-%H%M)
ls -la docker-compose.yml.pre-qwen38.*
```

Write that filename down. It is the whole safety net.

---

## 3. Edit four lines in the `vllm:` service

| from | to |
|---|---|
| `--model Qwen/Qwen3-VL-30B-A3B-Instruct-FP8` | `--model Qwen/Qwen3.8-27B-FP8` |
| `--tool-call-parser hermes` | `--tool-call-parser qwen3_coder` |
| `'{"enable_thinking": false}'` | `'{"enable_thinking": false, "preserve_thinking": false}'` |
| — | add `- --generation-config` / `- vllm` |

**Change nothing else.** Leave the image digest, the served name
`qwen3-vl-30b`, `--gpu-memory-utilization 0.70`, `--max-model-len 32768`,
`--max-num-seqs 4`, `--limit-mm-per-prompt`, and `--enable-prefix-caching`
exactly as they are. Do **not** add `--kv-cache-dtype fp8`,
`--speculative-config`, or `--reasoning-parser` — each is a separate
experiment, and bundling them means you will not know which one broke
something.

---

## 4. Up and smoke

```bash
cd /opt/home-ai-voice && docker compose up -d vllm
docker logs -f hav-vllm        # wait for "Application startup complete", then Ctrl-C
```

First start is slower than usual: `torch.compile` has no cached entry for
this architecture yet. That is expected once, not a symptom.

```bash
cd ~/code/home-el && python3 tools/qwen38-cutover-smoke.py
```

Three minutes. It prints one of three verdicts:

- **KEEP IT** — nothing that would break the house. Go live with it.
- **KEEP IT, WATCH THESE** — warnings only. Live with it and see.
- **ROLL BACK NOW** — it prints the exact rollback command. Do it; do not
  debug a broken house at midnight.

What it rules out, in order of how badly it would ruin your week:
reasoning spoken aloud by TTS · a zero-argument tool call that hangs
forever (two of your live tools have that shape, and the parser change is
what exposes it) · the served name breaking ~20 consumers · wrong KV
geometry · voice tool calls failing through HA · raw `<box>` markup in
`/look`.

---

## 5. Go live

```bash
docker start hav-personaplex-bridge
sudo rm -f /run/ha-maintenance
```

Then open the Home app and use it. Clear the Lab baseline first
(`localStorage["hg-lab-turns-v1"]`) — its "very slow" tint is calibrated to
the old model and will light up for a while regardless.

---

## Rollback — any time, no debate

```bash
cd /opt/home-ai-voice
cp -a docker-compose.yml.pre-qwen38.<STAMP> docker-compose.yml
docker compose up -d vllm
```

Roll back the moment any of these show up, without investigating first:

- the assistant speaks its own reasoning
- a voice command hangs and never answers
- garbled or looping replies
- lights actuate in the wrong room
- ambient captions become obviously wrong (not just differently worded)

None of these is worth living with for a day to gather data. The candidate
will still be there next week.

---

## What to watch in daily use

Living with it *is* the evaluation now, so here is what actually matters,
with the numbers the current model produces so you have something to
compare against:

| what to notice | incumbent today |
|---|---|
| voice feels slower | 1.8 s typical, 6.1 s worst on multi-tool reads |
| ambient captions get vaguer | 0.18 s, accurate on 50 daylight frames |
| `/look` shows raw `<box>` markup | never (strippers widened 2026-08-16) |
| assistant invents people | 3 of 35 verified-empty frames |
| replies get long-winded | a known defect; fix prepared, not applied |

**One caveat on latency:** the candidate is 2.4–2.6× slower at decode. Every
model-side measurement today sits at 8–17% of its budget, so there is real
headroom — but if prefix caching goes inert on the new architecture (which
the research flags as plausible), voice gets slower in a way you *will*
feel. If that happens, the smoke will not catch it; your ears will. That is
the one thing daily-use evaluation is genuinely good at.

Any time you want the rigorous answer instead of the felt one, the corpora
are frozen and the harnesses are one command each:

```bash
python3 tools/gate-g1-captions.py run  /srv/data/eval/migration/g1-corpus --arm candidate
python3 tools/gate-g4-negatives.py run /srv/data/eval/migration/g4-corpus --arm candidate
python3 tools/gate-g4-negatives.py score /srv/data/eval/migration/g4-corpus
python3 tools/qwen38-matrix-driver.py run --out /srv/data/eval/migration/candidate --cells D,A,E,B,Q,V --n 100
```
