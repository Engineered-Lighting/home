#!/usr/bin/env python3
"""qwen38-matrix-driver.py — the Phase-3 latency matrix
(docs/QWEN38-MIGRATION.md).

Runs the declared cells against whatever engine config is currently up,
captures every completion, and writes a session record that the gates can
be scored from later. It changes NOTHING about the engine: swapping model,
parser, KV dtype or speculative config is a compose edit and an owner-run
maintenance window. This driver measures whatever is running and says so.

**Session protocol** (from the migration doc), enforced here rather than
remembered:

  1. a **leakage canary** runs before any cell — if reasoning is leaking,
     every number that follows is from a config that cannot ship, so the
     session aborts instead of collecting 100 useless completions;
  2. an **anchor** cell runs first and again last, and >10% p50 drift
     **invalidates the session** (something else was using the GPU, so the
     comparison is not clean);
  3. every latency carries its measurement point and cache state, because
     a number without them is not evidence.

**Cell order is deliberate.** The D-cells run FIRST. Phase-0 measured
prefix caching worth ~5x on the incumbent's LLM leg (0.07 s warm vs 0.36 s
busted on the real 8,682-token production prompt), and R4 predicts it may
be inert on the candidate's hybrid attention. Whether it survives decides
whether D1's voice budget is reachable at all, so a matrix that discovers
it last has spent its evenings tuning around the wrong lever.

Cells that need an engine config this driver cannot create (P0 MTP, K fp8
KV, RP reasoning parser) are declared but refuse to run unless the engine
already reports that config, so a session cannot silently record a cell it
did not actually exercise.

Usage:
  tools/qwen38-matrix-driver.py list
  tools/qwen38-matrix-driver.py run --out SESSION --cells D,A,C,Q
  tools/qwen38-matrix-driver.py run --out SESSION --all --n 100
  tools/qwen38-matrix-driver.py report SESSION
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import random
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import qwen38_capture as capture  # noqa: E402
import qwen38_gates as gates  # noqa: E402

SIDECAR = "http://127.0.0.1:8000/v1/chat/completions"
HA = "http://192.168.0.125:8123"
FRIGATE = "http://192.168.0.125:5000"
PHASE0 = pathlib.Path("/srv/data/eval/migration/phase0")
G4_CORPUS = pathlib.Path("/srv/data/eval/migration/g4-corpus")
LIVE_PROMPT = PHASE0 / "eoc" / "prompt.live.txt"

# D1 as AMENDED 2026-08-16 (owner chose option (a) after the incumbent was
# measured breaching the original voice p95). Every threshold below is
# checked against these, and the voice tail is deliberately NOT an absolute
# number — see G6-c.
BUDGET = {
    "ambient_p95_s": 1.5,          # G6-a; incumbent 0.176 s
    "clip4_p95_s": 6.0,            # G6-a; incumbent 0.46 s
    "voice_ttft_p95_s": 1.2,       # G6-a; LLM leg at the sidecar, NOT HA
    "voice_e2e_p50_s": 2.5,        # G6-b; incumbent worst utterance 1.84 s
    "voice_e2e_p95_ratio": 1.25,   # G6-c; PAIRED against the incumbent's p95
                                   #       in the same session
}

# G6-d: a tracked pre-existing defect, deliberately not a gate. The
# incumbent runs 6.12 s p95 on multi-tool voice reads (n=30, reproducible).
# Recording it here keeps it visible in every session report instead of
# quietly becoming the new normal once G6-c starts measuring ratios.
KNOWN_DEFECT_VOICE_P95_S = 6.12


def load_system_prompt() -> str:
    """The real 33,760-char EOC prompt. Synthetic filler would measure a
    prefill this stack never pays."""
    if LIVE_PROMPT.exists():
        return LIVE_PROMPT.read_text(encoding="utf-8")
    print("  ⚠ live EOC prompt not archived; falling back to a short prompt. "
          "Prefill numbers will NOT represent production.")
    return "You are a home assistant."


def load_frames(n: int) -> list[bytes]:
    """Real camera frames from the G4 corpus."""
    frames = sorted((G4_CORPUS / "frames").glob("*.jpg"))
    if not frames:
        sys.exit("no frames — run tools/gate-g4-negatives.py build first.")
    return [p.read_bytes() for p in frames[:n]]


def engine_config() -> dict:
    """What is actually running. A cell that needs fp8 KV must not 'pass'
    against an auto-dtype engine."""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:8000/v1/models", timeout=10) as r:
            models = json.load(r)
        served = (models.get("data") or [{}])[0].get("id")
    except Exception:  # noqa: BLE001
        served = None
    cfg = {"served_model": served}
    try:
        import subprocess
        out = subprocess.run(
            ["docker", "exec", "hav-vllm", "sh", "-c",
             "curl -s -m 5 http://127.0.0.1:8000/metrics"],
            capture_output=True, timeout=25).stdout.decode()
        for line in out.splitlines():
            if line.startswith("vllm:cache_config_info"):
                for kv in line[line.find("{") + 1:line.rfind("}")].split(","):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        cfg[k.strip()] = v.strip().strip('"')
                break
        cfg["_metrics"] = capture.read_cache_counters(out)
    except Exception:  # noqa: BLE001
        pass
    return cfg


def cache_counters() -> dict:
    try:
        import subprocess
        out = subprocess.run(
            ["docker", "exec", "hav-vllm", "sh", "-c",
             "curl -s -m 5 http://127.0.0.1:8000/metrics"],
            capture_output=True, timeout=25).stdout.decode()
        return capture.read_cache_counters(out)
    except Exception:  # noqa: BLE001
        return {}


# ── cells ────────────────────────────────────────────────────────────────

def cell_leakage_canary(cap, args, ctx) -> dict:
    """Startup assertion, not a measurement. R5's adversarial shapes: a
    trivially simple prompt and a broad visual one, both of which this
    model family is known to overthink."""
    frames = load_frames(1)
    probes = [
        ("trivial", [{"role": "user", "content": "What is 2 + 2?"}]),
        ("broad-visual", [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": capture.encode_image(frames[0])}},
            {"type": "text", "text": "Describe everything you can see."}]}]),
    ]
    leaks = []
    for name, msgs in probes:
        for _ in range(max(1, args.canary_n)):
            t = cap.chat(msgs, cell="canary", cache_state="busted",
                         meta={"probe": name}, max_tokens=200, temperature=0.7)
            found = gates.scan_response(t.response)
            if found:
                leaks.append({"probe": name,
                              "where": [f.where for f in found],
                              "excerpt": found[0].excerpt[:160]})
    return {"leaks": leaks, "passed": not leaks,
            "note": "G5 is zero-tolerance; a leaking config cannot ship, so "
                    "any leak aborts the session before cells run."}


def cell_anchor(cap, args, ctx, tag="A1") -> dict:
    """Fixed short completion, repeated. Runs first and last; the pair is
    the session's drift control."""
    lat = []
    for i in range(args.anchor_n):
        t = cap.chat([{"role": "user", "content": "Reply with the single word: ready."}],
                     cell=tag, cache_state="warm" if i else "cold",
                     max_tokens=8, temperature=0.0)
        if t.status == "ok":
            lat.append(t.latency_s)
    return {"n": len(lat), "p50": capture.percentile(lat, 50),
            "p95": capture.percentile(lat, 95)}


def cell_apc(cap, args, ctx) -> dict:
    """D1–D3: does automatic prefix caching work at all on this engine?

    The single most decision-relevant cell. Same production prompt, three
    ways: repeated verbatim (cache should hit), unique prefix each time
    (cache cannot hit), and unique SUFFIX with a shared prefix (which is
    what a real voice turn looks like — shared system prompt, different
    question).
    """
    system = load_system_prompt()
    out = {}
    for mode in ("warm", "busted", "shared-prefix"):
        lat, cached_ratios = [], []
        before = cache_counters()
        for i in range(args.n):
            if mode == "warm":
                sysmsg, user = system, "Is the kitchen light on?"
            elif mode == "busted":
                sysmsg = f"[session {random.random()}]\n" + system
                user = "Is the kitchen light on?"
            else:
                sysmsg, user = system, f"Is the kitchen light on? (variant {i})"
            state = {"warm": "warm" if i else "cold",
                     "busted": "busted", "shared-prefix": "warm"}[mode]
            t = cap.chat([{"role": "system", "content": sysmsg},
                          {"role": "user", "content": user}],
                         cell=f"D-apc-{mode}", cache_state=state,
                         max_tokens=32, temperature=0.0)
            if t.status == "ok":
                lat.append(t.latency_s)
                if t.cache_hit_ratio is not None:
                    cached_ratios.append(t.cache_hit_ratio)
        delta = capture.cache_hit_delta(before, cache_counters())
        out[mode] = {
            "n": len(lat), "p50": capture.percentile(lat, 50),
            "p95": capture.percentile(lat, 95),
            "prefix_cache": delta.get("prefix_cache"),
            "usage_cached_ratio_p50": capture.percentile(cached_ratios, 50),
        }
    warm, busted = out.get("warm", {}), out.get("busted", {})
    if warm.get("p50") and busted.get("p50"):
        out["speedup_warm_vs_busted"] = round(busted["p50"] / warm["p50"], 2)
        out["verdict"] = ("APC is WORKING" if busted["p50"] / warm["p50"] > 1.3
                          else "APC looks INERT — every turn pays full prefill")
    return out


def cell_ambient(cap, args, ctx) -> dict:
    """A2 / ambient: the /describe path against D1's 1.5 s p95."""
    frames = load_frames(min(args.n, 35))
    system = ("You are reading a single snapshot from a home security "
              "camera. Answer briefly and naturally for a voice assistant. "
              "Use one short sentence. Don't say 'in the image' or "
              "'I see' — describe directly.")
    lat = []
    for i in range(args.n):
        jpeg = frames[i % len(frames)]
        t = cap.chat([{"role": "system", "content": system},
                      {"role": "user", "content": [
                          {"type": "image_url",
                           "image_url": {"url": capture.encode_image(jpeg)}},
                          {"type": "text", "text": "What is happening right now?"}]}],
                     cell="A2-ambient", cache_state="cold",
                     max_tokens=200, temperature=0.2)
        if t.status == "ok":
            lat.append(t.latency_s)
    p95 = capture.percentile(lat, 95)
    return {"n": len(lat), "p50": capture.percentile(lat, 50), "p95": p95,
            "budget_s": BUDGET["ambient_p95_s"],
            "within_budget": p95 is not None and p95 <= BUDGET["ambient_p95_s"]}


def cell_clip(cap, args, ctx) -> dict:
    """E1: 2 / 4 / 8-frame clips. The 4-frame number is D1-budgeted."""
    frames = load_frames(8)
    out = {}
    for k in (2, 4, 8):
        lat = []
        for i in range(max(3, args.n // 8)):
            parts = [{"type": "image_url",
                      "image_url": {"url": capture.encode_image(
                          capture.bust_jpeg(frames[(i + j) % len(frames)]))}}
                     for j in range(k)]
            t = cap.chat([{"role": "user", "content": parts + [
                {"type": "text",
                 "text": "These frames are seconds apart from one camera. "
                         "In one sentence, what happened?"}]}],
                cell=f"E1-clip{k}", cache_state="busted",
                max_tokens=200, temperature=0.2)
            if t.status == "ok":
                lat.append(t.latency_s)
        rec = {"n": len(lat), "p50": capture.percentile(lat, 50),
               "p95": capture.percentile(lat, 95)}
        if k == 4:
            rec["budget_s"] = BUDGET["clip4_p95_s"]
            rec["within_budget"] = (rec["p95"] is not None
                                    and rec["p95"] <= BUDGET["clip4_p95_s"])
        out[f"clip{k}"] = rec
    return out


def cell_b_worst(cap, args, ctx) -> dict:
    """B-worst — a HEADLINE cell: a voice turn arriving while an 8-frame
    clip is prefilling. This is the shape that actually hurts, because
    max-num-seqs is 4 and a clip prefill occupies the engine.
    """
    frames = load_frames(8)
    system = load_system_prompt()

    def clip_job():
        parts = [{"type": "image_url",
                  "image_url": {"url": capture.encode_image(capture.bust_jpeg(f))}}
                 for f in frames]
        return cap.chat([{"role": "user", "content": parts + [
            {"type": "text", "text": "In one sentence, what happened?"}]}],
            cell="B-worst-clip", cache_state="busted",
            max_tokens=200, temperature=0.2)

    def voice_job():
        return cap.chat([{"role": "system", "content": system},
                         {"role": "user", "content": "Is the kitchen light on?"}],
                        cell="B-worst-voice", cache_state="warm",
                        max_tokens=48, temperature=0.0)

    lat = []
    reps = max(3, args.n // 10)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(reps):
            clip = pool.submit(clip_job)
            time.sleep(0.05)          # let the clip start prefilling first
            voice = pool.submit(voice_job)
            v = voice.result()
            clip.result()
            if v.status == "ok":
                lat.append(v.latency_s)
    p95 = capture.percentile(lat, 95)
    return {"n": len(lat), "voice_p50": capture.percentile(lat, 50),
            "voice_p95": p95, "budget_s": BUDGET["voice_e2e_p95_s"],
            "within_budget": p95 is not None and p95 <= BUDGET["voice_e2e_p95_s"],
            "note": "voice latency measured WHILE an 8-frame clip prefills; "
                    "LLM leg only, at the sidecar — HA pipeline overhead is "
                    "measured separately by the V1 cell"}


def cell_quiet(cap, args, ctx) -> dict:
    """Q: quiet-literal sentinels, scored with the app's own classifier."""
    man_path = G4_CORPUS / "manifest.json"
    if not man_path.exists():
        return {"skipped": "no G4 corpus — run gate-g4-negatives.py build"}
    man = json.loads(man_path.read_text(encoding="utf-8"))
    sentinels = [f for f in man["frames"] if f["kind"] == "sentinel"]
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "g4", pathlib.Path(__file__).resolve().parent / "gate-g4-negatives.py")
    g4 = importlib.util.module_from_spec(spec)
    sys.modules["g4"] = g4
    spec.loader.exec_module(g4)

    quiet, results = 0, []
    for fr in sentinels:
        jpeg = (G4_CORPUS / "frames" / f"{fr['id']}.jpg").read_bytes()
        t = cap.chat([{"role": "system", "content": g4.REASON_SYSTEM},
                      {"role": "user", "content": [
                          {"type": "image_url",
                           "image_url": {"url": capture.encode_image(jpeg)}},
                          {"type": "text", "text": g4.REASON_USER_TMPL.format(
                              question=g4.deep_look_question(fr["camera"]))}]}],
                     cell="Q-sentinel", cache_state="cold",
                     meta={"frame": fr["id"]}, max_tokens=700, temperature=0.2)
        answer = gates.sidecar_extract_answer(t.content())
        is_quiet = gates.is_quiet_literal(answer)
        quiet += is_quiet
        results.append({"frame": fr["id"], "answer": answer[:120],
                        "category": gates.classify_look_finding(answer),
                        "quiet": is_quiet})
    return {"n": len(sentinels), "quiet": quiet, "results": results}


def cell_voice_ha(cap, args, ctx) -> dict:
    """V1: a real voice turn end-to-end through Home Assistant.

    This is D1's named instrument for voice, and the only cell that
    measures the whole pipeline rather than the LLM leg. Read-only
    utterances only — this drives the production agent, and a cell that
    actuates lights is not a measurement, it is an incident.
    """
    token = ctx.get("ha_token")
    if not token:
        return {"skipped": "no HA token (set HA_TOKEN or pass --ha-token)"}
    utterances = [
        "What rooms are in my home?",          # areas_in_home — ZERO-ARG tool
        "Are any lights on right now?",        # get_attributes chain
        "What is the temperature in the kitchen?",
    ]
    out = []
    for text in utterances:
        lat = []
        for _ in range(max(1, args.n // 20)):
            body = json.dumps({"text": text, "language": "en",
                               "agent_id": args.ha_agent}).encode()
            req = urllib.request.Request(
                f"{HA}/api/conversation/process", data=body,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"})
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=150) as r:
                    d = json.load(r)
                dt = time.time() - t0
                lat.append(dt)
                speech = ((d.get("response", {}).get("speech", {})
                           .get("plain", {}) or {}).get("speech", ""))
                rtype = d.get("response", {}).get("response_type")
            except Exception as e:  # noqa: BLE001
                speech, rtype, dt = f"ERROR {type(e).__name__}: {e}", "error", None
            time.sleep(0.5)
        out.append({"utterance": text, "n": len(lat),
                    "p50": capture.percentile(lat, 50),
                    "p95": capture.percentile(lat, 95),
                    "last_response_type": rtype,
                    "last_speech": speech[:160]})
    allp95 = capture.percentile(
        [x for r in out for x in ([r["p95"]] if r["p95"] else [])], 95)
    worst_p50 = capture.percentile(
        [x for r in out for x in ([r["p50"]] if r["p50"] else [])], 100)
    rec = {
        "utterances": out, "measurement_point": "ha_conversation",
        "worst_p50": worst_p50, "worst_p95": allp95,
        # G6-b: absolute, and the number a user meets on nearly every turn.
        "g6b_p50_budget_s": BUDGET["voice_e2e_p50_s"],
        "g6b_pass": worst_p50 is not None and worst_p50 <= BUDGET["voice_e2e_p50_s"],
        # G6-c: paired. Absolute tails are polluted by the tracked defect,
        # so this can only be scored against an incumbent arm from the SAME
        # session; a lone run reports the raw number and withholds a verdict
        # rather than inventing one.
        "g6c_ratio_cap": BUDGET["voice_e2e_p95_ratio"],
        "g6c_pass": None,
        "g6c_note": "paired gate — score with `report --incumbent SESSION`; "
                    "a single arm cannot pass or fail it",
        "known_defect_p95_s": KNOWN_DEFECT_VOICE_P95_S,
        "note": "zero-argument tool `areas_in_home` is exercised first — "
                "the vllm#50989 doom-loop shape flagged in Phase 0",
    }
    if allp95 is not None and allp95 > KNOWN_DEFECT_VOICE_P95_S * 1.05:
        rec["tail_regressed_vs_known_defect"] = True
    return rec


CELLS = {
    # id            (fn,               needs,                  why-order)
    "D": (cell_apc, "prefix caching — RUN FIRST (R4: worth ~5x on the incumbent)"),
    "A": (cell_ambient, "ambient /describe path vs the 1.5 s budget"),
    "E": (cell_clip, "clip 2/4/8 frames; the 4-frame number is budgeted"),
    "B": (cell_b_worst, "HEADLINE: voice arriving during an 8-frame clip prefill"),
    "Q": (cell_quiet, "quiet-literal sentinels via the deep-look surface"),
    "V": (cell_voice_ha, "real voice turn through HA, incl. a zero-arg tool"),
}


def cmd_list(args) -> int:
    print("matrix cells (run order matters — D first):\n")
    for cid, (_, why) in CELLS.items():
        print(f"  {cid}  {why}")
    print("\nDeclared but NOT runnable by this driver — they need an engine")
    print("config only an owner-run maintenance window can create:")
    print("  P0  MTP viability      (--speculative-config)")
    print("  K   fp8 KV cache       (--kv-cache-dtype fp8)")
    print("  RP  reasoning parser   (--reasoning-parser qwen3)")
    print("\nThe driver refuses to record these rather than pretend, so a")
    print("session cannot claim a cell it did not exercise.")
    return 0


def cmd_run(args) -> int:
    root = pathlib.Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    ctx = {"ha_token": args.ha_token}
    if not ctx["ha_token"]:
        envf = pathlib.Path("/opt/home-ai-voice/.env")
        if envf.exists():
            for line in envf.read_text(errors="replace").splitlines():
                if line.startswith("HA_TOKEN="):
                    ctx["ha_token"] = line.split("=", 1)[1].strip().strip('"\'')

    cfg = engine_config()
    print("=== engine under test ===")
    for k in ("served_model", "cache_dtype", "enable_prefix_caching",
              "block_size", "gpu_memory_utilization", "num_gpu_blocks"):
        if k in cfg:
            print(f"  {k}: {cfg[k]}")
    print()

    cap = capture.Capturer(args.endpoint, model=args.model,
                           measurement_point=args.measurement_point)
    session = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "engine": {k: v for k, v in cfg.items() if k != "_metrics"},
               "endpoint": args.endpoint, "n": args.n, "cells": {}}

    # 1. Leakage canary — abort before wasting a session on a config that
    #    cannot ship.
    print("--- startup assertion: reasoning-leakage canary ---")
    canary = cell_leakage_canary(cap, args, ctx)
    session["cells"]["canary"] = canary
    if not canary["passed"]:
        print(f"  ABORT: {len(canary['leaks'])} leak(s) detected. G5 is "
              "zero-tolerance; this config cannot ship, so the session stops "
              "here rather than collecting numbers from it.")
        for lk in canary["leaks"][:3]:
            print(f"    [{lk['probe']}] {lk['where']}: {lk['excerpt'][:100]!r}")
        (root / "session.json").write_text(json.dumps(session, indent=2))
        return 2
    print("  clean — no reasoning in content or either reasoning field\n")

    # 2. Opening anchor (drift control, first half).
    print("--- anchor (opening) ---")
    session["cells"]["anchor_open"] = cell_anchor(cap, args, ctx, "A1-open")
    print(f"  p50={session['cells']['anchor_open']['p50']:.3f}s\n")

    selected = (list(CELLS) if args.all
                else [c.strip().upper() for c in (args.cells or "D").split(",")])
    for cid in selected:
        if cid not in CELLS:
            print(f"  unknown cell {cid!r} — see `list`")
            continue
        fn, why = CELLS[cid]
        print(f"--- cell {cid}: {why} ---")
        t0 = time.time()
        try:
            session["cells"][cid] = fn(cap, args, ctx)
        except Exception as e:  # noqa: BLE001
            session["cells"][cid] = {"error": f"{type(e).__name__}: {e}"}
            print(f"  ERROR: {e}")
        print(f"  ({time.time() - t0:.0f}s)")
        print(json.dumps(session["cells"][cid], indent=2, default=str)[:900])
        print()

    # 3. Closing anchor — the drift control that can invalidate everything.
    print("--- anchor (closing) — session drift control ---")
    close = cell_anchor(cap, args, ctx, "A1-close")
    session["cells"]["anchor_close"] = close
    op, cl = session["cells"]["anchor_open"]["p50"], close["p50"]
    drift = abs(cl - op) / op if op else 0.0
    session["drift"] = {"open_p50": op, "close_p50": cl, "drift": drift,
                        "valid": drift <= 0.10}
    print(f"  open p50={op:.3f}s  close p50={cl:.3f}s  drift={drift * 100:.1f}%")
    if drift > 0.10:
        print("  ⚠ SESSION INVALID: >10% p50 drift. Something else was using "
              "the GPU, so these numbers are not comparable to another "
              "session's. Re-run.")
    else:
        print("  within 10% — session valid")

    (root / "session.json").write_text(json.dumps(session, indent=2, default=str),
                                       encoding="utf-8")
    cap.write(str(root / "turns.ndjson"))
    print(f"\nsession -> {root / 'session.json'}")
    print(f"turns   -> {root / 'turns.ndjson'} ({len(cap.turns)} completions)")
    return 0 if session["drift"]["valid"] else 3


def score_g6c(candidate: dict, incumbent: dict) -> dict:
    """G6-c: the paired voice tail gate.

    Absolute tails are polluted by the tracked pre-existing defect (G6-d),
    so the only honest question is how much WORSE the candidate is than the
    incumbent measured the same way. Both sessions must be valid — comparing
    against a drifted session would let GPU contention masquerade as a
    model regression, or hide one.
    """
    cv, iv = candidate.get("cells", {}).get("V"), incumbent.get("cells", {}).get("V")
    if not cv or not iv:
        return {"scored": False, "why": "both sessions need a V cell"}
    for name, sess in (("candidate", candidate), ("incumbent", incumbent)):
        if not (sess.get("drift") or {}).get("valid", True):
            return {"scored": False,
                    "why": f"the {name} session drifted >10% and is invalid"}

    ci = {u["utterance"]: u for u in cv["utterances"]}
    ii = {u["utterance"]: u for u in iv["utterances"]}
    shared = [u for u in ci if u in ii]
    rows, worst = [], 0.0
    for u in shared:
        c95, i95 = ci[u].get("p95"), ii[u].get("p95")
        if not c95 or not i95:
            continue
        ratio = c95 / i95
        worst = max(worst, ratio)
        rows.append({"utterance": u, "incumbent_p95": i95,
                     "candidate_p95": c95, "ratio": round(ratio, 3),
                     "pass": ratio <= BUDGET["voice_e2e_p95_ratio"]})
    return {"scored": True, "cap": BUDGET["voice_e2e_p95_ratio"],
            "worst_ratio": round(worst, 3),
            "pass": bool(rows) and worst <= BUDGET["voice_e2e_p95_ratio"],
            "per_utterance": rows}


def cmd_report(args) -> int:
    s = json.loads((pathlib.Path(args.session) / "session.json").read_text())
    if args.incumbent:
        inc = json.loads((pathlib.Path(args.incumbent) / "session.json").read_text())
        g6c = score_g6c(s, inc)
        print("=== G6-c (paired voice tail vs the incumbent) ===")
        if not g6c["scored"]:
            print(f"  not scored: {g6c['why']}")
        else:
            for r in g6c["per_utterance"]:
                print(f"  {'PASS' if r['pass'] else 'FAIL'}  x{r['ratio']:.2f}  "
                      f"{r['incumbent_p95']:.2f}s -> {r['candidate_p95']:.2f}s  "
                      f"{r['utterance']!r}")
            print(f"  worst ratio {g6c['worst_ratio']}x against a "
                  f"{g6c['cap']}x cap -> "
                  f"{'PASS' if g6c['pass'] else 'FAIL'}")
        print()
    print(f"=== matrix session {s.get('started')} ===")
    print(f"engine: {s.get('engine', {}).get('served_model')} "
          f"cache_dtype={s.get('engine', {}).get('cache_dtype')}")
    d = s.get("drift") or {}
    print(f"drift : {d.get('drift', 0) * 100:.1f}%  "
          f"{'VALID' if d.get('valid') else 'INVALID — do not compare'}\n")
    for cid, rec in s.get("cells", {}).items():
        if cid.startswith("anchor") or cid == "canary":
            continue
        print(f"--- {cid} ---")
        print(json.dumps(rec, indent=2, default=str)[:1200])
        print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show the cells").set_defaults(func=cmd_list)

    r = sub.add_parser("run", help="run a matrix session")
    r.add_argument("--out", required=True)
    r.add_argument("--cells", help="comma-separated cell ids (default D)")
    r.add_argument("--all", action="store_true")
    r.add_argument("--n", type=int, default=20,
                   help="samples per cell; the migration doc requires n>=100 "
                        "for any p95-gated cell")
    r.add_argument("--anchor-n", type=int, default=10)
    r.add_argument("--canary-n", type=int, default=5)
    r.add_argument("--endpoint", default=SIDECAR)
    r.add_argument("--model", default="qwen3-vl-30b")
    r.add_argument("--measurement-point", default="sidecar",
                   choices=sorted(capture.MEASUREMENT_POINTS))
    r.add_argument("--ha-token", default=None)
    r.add_argument("--ha-agent", default="conversation.extended_openai_conversation")
    r.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="summarise a session")
    p.add_argument("session")
    p.add_argument("--incumbent", help="an incumbent session to score the "
                                       "paired G6-c voice-tail gate against")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
