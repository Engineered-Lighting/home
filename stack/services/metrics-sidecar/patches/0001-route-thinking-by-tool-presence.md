# metrics-sidecar patch 0001 — route thinking by tool presence

**Status:** written 2026-08-16, NOT built, NOT deployed.
**Target:** the LIVE metrics-sidecar source, archived at
`/srv/data/eval/migration/phase0/live-sources/metrics-sidecar/main.py`.
**Delivery:** off-host build + `docker save/load` (D9 bars host image builds).

⚠ **Do not apply this to the repo copy of the sidecar and deploy that.** The
repo copy diverges from live (never-deploy list); this patch is written
against the archived live source and must be applied to it.

---

## What problem this solves

The Qwen3.8-27B candidate has two configurations and neither ships alone:

| config | reasoning leak | ambient p95 |
|---|---|---|
| thinking OFF (no reasoning parser) | **3/20 (15%)** — `</think>` reaches TTS | 0.18 s ✅ |
| thinking ON + `--reasoning-parser qwen3` | **0/40** ✅ | **4.22 s** ❌ (budget 1.5 s) |

Measured with `tools/qwen38-leak-repro.py`, n=20–40 per cell.

But the leak is **not** a property of thinking-off. It is a property of
thinking-off **with a tool catalogue attached**:

| shape | leak rate |
|---|---|
| streaming + 33.7k prompt, **no tools** | **0/20** |
| streaming + 33.7k prompt + **23 tools** | **3/20** |

It fires on the first turn, while the model decides whether to call a tool.

So the two halves of the house want opposite settings, and the request
itself already says which is which:

| surface | ~volume/day | tools attached | wants |
|---|---|---|---|
| ambient `/describe` | ~1,400 | no | thinking OFF — fast, and proven not to leak |
| grounded `/reason`, clips, labeler | low | no | thinking OFF |
| voice (EOC) | interactive | **yes** | thinking ON + parser — proven not to leak |

## Why the proxy is the right place

Every caller already goes through this proxy — vLLM is not host-published.
Putting the rule here fixes all of them at once, rather than patching the
vision-sidecar, the labeler and the EOC component separately (three
never-deploy surfaces, three builds, three chances to diverge).

The discriminator needs no model judgement and no extra round-trip: a
request with no `tools` is a captioning request, and captioning never needs
reasoning. That is visible before the model sees it.

**Server default becomes thinking ON + `--reasoning-parser qwen3`** (the
safe setting for the tool path). This patch turns it back OFF for the
tool-free majority.

## The patch

Insert after `_user_message_from_body` and call it once in
`proxy_chat_completions`, before the stream/non-stream branch — both paths
forward the same `body` object, so one mutation covers both.

```python
# ── Thinking routing (2026-08-16) ──────────────────────────────────────
# The Qwen3.8 checkpoint leaks `</think>` into `content` at ~15% when
# thinking is suppressed AND a tool catalogue is attached; with no tools
# it never leaked (0/20). Thinking left on costs ~10x latency, which
# blows the ambient budget (4.22s p95 against 1.5s).
#
# So: leave thinking ON for tool-bearing requests (voice), turn it OFF for
# everything else (ambient captions, grounded look, clips, labeler). The
# request already tells us which it is — no model judgement, no extra
# round-trip.
#
# Set THINKING_ROUTING=off to disable this entirely and pass every request
# through untouched; that is the rollback if the routing ever misbehaves,
# and it needs no rebuild.
THINKING_ROUTING = os.environ.get("THINKING_ROUTING", "on").strip().lower()


def _route_thinking(body: dict) -> str | None:
    """Suppress thinking on tool-free requests. Returns the decision for
    logging, or None if nothing was changed.

    Caller intent wins: a request that already carries
    `chat_template_kwargs` is left exactly as sent, so the vision-sidecar
    or a probe can opt in or out explicitly without fighting the proxy.
    """
    if THINKING_ROUTING != "on":
        return None
    if "chat_template_kwargs" in body:
        return None                      # explicit caller intent — hands off
    if body.get("tools"):
        return "on (tools present)"      # server default already thinks
    body["chat_template_kwargs"] = {"enable_thinking": False}
    return "off (no tools)"
```

and in `proxy_chat_completions`, immediately after `user_msg` is computed:

```python
    thinking = _route_thinking(body)
```

Optionally add `"thinking": thinking` to both `_broadcast_completion`
payloads so the Lab tab can show which mode a turn used. Existing consumers
ignore unknown keys.

## Why it is safe

- **Additive and reversible.** `THINKING_ROUTING=off` restores the previous
  behaviour with an env change and a container recreate — no rebuild.
- **Caller intent is never overridden.** Anything that sets
  `chat_template_kwargs` itself passes through untouched, so the leak
  reproducer and any future per-surface override still work.
- **No behaviour change on the incumbent.** The incumbent's template
  ignores `enable_thinking`, so shipping this before the model migration is
  a no-op — it can be built and deployed independently, and should be,
  so it is not one more variable inside a cutover window.
- **Both proxy paths covered.** Streaming and buffered forward the same
  `body` dict (`main.py:405` and `:451`).

## What it does NOT fix

Voice latency. Voice keeps thinking on, so the interactive path still pays
it. That path's budget is 4 s p50 rather than 1.5 s, and its numbers under
this config are **not yet measured** — the V cell was not run with thinking
enabled. **Measure that before any go-live decision**; this patch only
rescues the ambient budget.

## Build and verify (off-host)

```bash
# off-host, from the ARCHIVED LIVE source — not the repo copy
docker build -t home-ai-voice/metrics-sidecar:thinking-routing .
docker save home-ai-voice/metrics-sidecar:thinking-routing | gzip > ms.tgz
# transfer, then on the host:  gunzip -c ms.tgz | docker load
```

Verify before trusting it:

```bash
# tool-free request -> proxy should inject enable_thinking:false
# tool-bearing request -> untouched
python3 tools/qwen38-leak-repro.py --n 20        # D must stay 0/20
python3 tools/qwen38-matrix-driver.py run --out /tmp/s --cells A --n 60 \
    --thinking-routed                            # ambient p95 back under 1.5 s
```

The gate is: **variant D stays at 0/N and ambient p95 returns to ~0.2 s.**
Either one failing means the routing is not doing what it claims.
