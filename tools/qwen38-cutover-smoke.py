#!/usr/bin/env python3
"""qwen38-cutover-smoke.py — the 3-minute go/rollback check after a cutover.

The owner elected to evaluate the candidate in daily use rather than through
a multi-evening comparison matrix. That is a reasonable trade for a
single-household system where rollback is a compose restore and one
container recreate — but it only works if the failures that CANNOT be
noticed as "hmm, a bit worse" are caught immediately.

This checks exactly those. Each is a failure you would otherwise meet as a
broken house, not as degraded quality:

  1. served name       — ~20 coupling points key off the frozen string
  2. KV geometry       — a wrong pool means the long-context maths is wrong
  3. reasoning leakage — the app renders thinking verbatim and TTS SPEAKS it
  4. zero-arg doom loop— vllm#50989: the turn NEVER RETURNS (a hang, not a
                         wrong answer). Two live tools have this shape.
  5. tool call through HA — the assistant can still actually do things
  6. grounded boxes    — scored with the PRODUCTION parsers, not a tolerant one
  7. ambient caption   — the vision path still answers sanely
  8. latency spot check— against the amended D1 budgets

Quality gates (G1 caption judging, G2 labeler replay, G4 negatives) are
deliberately NOT here. They compare two models on frozen corpora and are
the part a human can genuinely judge over a week of living with it.

Exit: 0 keep it · 1 ROLL BACK NOW · 2 warnings worth watching
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import qwen38_capture as capture  # noqa: E402
import qwen38_gates as gates  # noqa: E402

SIDECAR = "http://127.0.0.1:8000/v1/chat/completions"
HA = "http://192.168.0.125:8123"
G4 = pathlib.Path("/srv/data/eval/migration/g4-corpus/frames")
SERVED = "qwen3-vl-30b"

FATAL: list[str] = []
WARN: list[str] = []


def ok(name, detail=""):
    print(f"  \033[32mPASS\033[0m  {name}" + (f"  — {detail}" if detail else ""))


def fatal(name, detail=""):
    print(f"  \033[31mROLLBACK\033[0m  {name}" + (f"  — {detail}" if detail else ""))
    FATAL.append(name)


def warn(name, detail=""):
    print(f"  \033[33mWARN\033[0m  {name}" + (f"  — {detail}" if detail else ""))
    WARN.append(name)


def ha_token() -> str:
    for line in pathlib.Path("/opt/home-ai-voice/.env").read_text(
            errors="replace").splitlines():
        if line.startswith("HA_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", default=SIDECAR)
    ap.add_argument("--model", default=SERVED)
    ap.add_argument("--skip-ha", action="store_true",
                    help="skip the live voice checks (engine-only smoke)")
    args = ap.parse_args()

    print("=== cutover smoke — the failures you cannot shrug off ===\n")
    cap = capture.Capturer(args.endpoint, model=args.model)

    # 1. served name -------------------------------------------------------
    print("1. served model name")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=15) as r:
            served = (json.load(r).get("data") or [{}])[0].get("id")
        (ok if served == SERVED else fatal)(
            f"/v1/models reports {served!r}",
            "" if served == SERVED else f"expected {SERVED!r}; ~20 consumers key off it")
    except Exception as e:  # noqa: BLE001
        fatal("engine unreachable", str(e)[:90])
        print("\nThe engine is not answering. Roll back.")
        return 1

    # 2. KV geometry -------------------------------------------------------
    print("\n2. KV cache geometry")
    # vLLM splits its startup banner across both streams depending on build,
    # so read both — checking only stderr silently loses the KV line, which
    # is the one assertion that can catch wrong cache geometry.
    proc = subprocess.run(["docker", "logs", "hav-vllm"], capture_output=True,
                          timeout=60)
    logs = (proc.stderr.decode(errors="replace")
            + proc.stdout.decode(errors="replace"))
    pool = None
    for line in logs.splitlines():
        if "GPU KV cache size" in line:
            digits = "".join(c for c in line.split(":")[-1] if c.isdigit())
            pool = int(digits) if digits else None
    if pool is None:
        warn("could not read the startup KV line")
    elif pool < 131072:
        fatal(f"KV pool {pool:,} tokens", "below the 131k floor — context will starve")
    elif pool < 300000:
        warn(f"KV pool {pool:,} tokens",
             "lower than the 64 KiB/token maths predicts; re-check the geometry")
    else:
        ok(f"KV pool {pool:,} tokens", "consistent with 64 KiB/token")

    # 3. reasoning leakage -------------------------------------------------
    print("\n3. reasoning leakage (zero tolerance — TTS would SPEAK it)")
    frames = sorted(G4.glob("*.jpg"))
    probes = [("trivial", [{"role": "user", "content": "What is 2 + 2?"}])]
    if frames:
        probes.append(("broad-visual", [{"role": "user", "content": [
            {"type": "image_url", "image_url": {
                "url": capture.encode_image(frames[0].read_bytes())}},
            {"type": "text", "text": "Describe everything you can see."}]}]))
    leaks = 0
    for label, msgs in probes:
        for _ in range(4):
            t = cap.chat(msgs, cell="smoke-leak", cache_state="busted",
                         max_tokens=200, temperature=0.7)
            leaks += len(gates.scan_response(t.response))
    (ok if not leaks else fatal)(
        f"{len(probes) * 4} adversarial probes",
        "clean" if not leaks else f"{leaks} leak(s) — the app renders this verbatim")

    # 4. zero-arg doom loop ------------------------------------------------
    print("\n4. zero-argument tool (vllm#50989 — a HANG, not a wrong answer)")
    zero_arg = [{"type": "function", "function": {
        "name": "get_all_rooms_state",
        "description": "Return the state of every room.",
        "parameters": {"type": "object", "properties": {}}}}]
    t0 = time.time()
    try:
        turn = cap.chat([{"role": "user", "content": "What is the state of every room?"}],
                        cell="smoke-zeroarg", cache_state="busted",
                        tools=zero_arg, tool_choice="auto",
                        max_tokens=256, temperature=0.0)
        dt = time.time() - t0
        if turn.status != "ok":
            fatal("zero-arg tool call errored", (turn.error or "")[:90])
        elif dt > 30:
            fatal(f"zero-arg tool took {dt:.0f}s", "doom-loop shape; two live tools do this")
        else:
            calls = ((turn.response.get("choices") or [{}])[0]
                     .get("message", {}).get("tool_calls") or [])
            ok(f"returned in {dt:.1f}s", f"{len(calls)} tool call(s), finish="
                                         f"{turn.finish_reason}")
    except Exception as e:  # noqa: BLE001
        fatal("zero-arg tool hung or failed", str(e)[:90])

    # 5-8. live voice + vision --------------------------------------------
    if not args.skip_ha:
        tok = ha_token()
        print("\n5. tool call end to end through Home Assistant")
        if not tok:
            warn("no HA token; skipped")
        else:
            for utt in ("What rooms are in my home?", "Are any lights on right now?"):
                body = json.dumps({"text": utt, "language": "en",
                                   "agent_id": "conversation.extended_openai_conversation"}).encode()
                req = urllib.request.Request(
                    f"{HA}/api/conversation/process", data=body,
                    headers={"Authorization": f"Bearer {tok}",
                             "Content-Type": "application/json"})
                s = time.time()
                try:
                    with urllib.request.urlopen(req, timeout=90) as r:
                        d = json.load(r)
                    dt = time.time() - s
                    sp = ((d.get("response", {}).get("speech", {})
                           .get("plain", {}) or {}).get("speech", ""))
                    rt = d.get("response", {}).get("response_type")
                    leak = gates.find_reasoning_leaks({"content": sp})
                    if leak:
                        # Show the MATCHED excerpt, never the reply's first
                        # 70 chars. On the 2026-08-16 attempt the leak sat
                        # ~150 chars in, so the prefix looked like a
                        # perfectly good answer and the alarm read as a
                        # false positive. An operator who dismisses a
                        # correct rollback signal because the evidence was
                        # cropped is worse off than one with no check.
                        fatal("reasoning leaked into a SPOKEN reply",
                              f"[{leak[0].where}] matched {leak[0].pattern!r} in "
                              f"a {len(sp)}-char reply: …{leak[0].excerpt.strip()[:110]}…")
                    elif rt == "error" or not sp:
                        fatal(f"voice turn failed: {utt!r}", str(rt))
                    elif dt > 20:
                        fatal(f"voice turn took {dt:.0f}s", utt)
                    else:
                        (ok if dt <= 8 else warn)(f"{dt:5.2f}s  {utt}", sp[:64])
                except Exception as e:  # noqa: BLE001
                    fatal(f"voice turn errored: {utt!r}", str(e)[:70])

    print("\n6. grounded boxes through the PRODUCTION parsers")
    if frames:
        clean = extracted = 0
        for f in frames[:3]:
            t = cap.chat(
                [{"role": "system", "content":
                  "You are a visual reasoning assistant that thinks by pointing. "
                  "Whenever you refer to a specific real thing in the image, give it "
                  "immediately as <ref>short label</ref><box>x1,y1,x2,y2</box>, with "
                  "integer coordinates from 0 to 1000 (top-left origin)."},
                 {"role": "user", "content": [
                     {"type": "image_url", "image_url": {
                         "url": capture.encode_image(f.read_bytes())}},
                     {"type": "text", "text":
                      "What is in this room? Reference each object as "
                      "<ref>label</ref><box>...</box>. End with 'ANSWER: ...'."}]}],
                cell="smoke-g7", cache_state="busted", max_tokens=500, temperature=0.2)
            s = gates.score_g7(t.content())
            extracted += s.extracted
            clean += s.app_clean
        (ok if clean == 3 else fatal)(
            f"app strippers clean on {clean}/3",
            "" if clean == 3 else "raw markup would render in /look")
        (ok if extracted else warn)(
            f"boxes extracted on {extracted}/3",
            "" if extracted else "no grounding — matches the incumbent's 0/13, not a regression")

    print("\n7. ambient caption")
    if frames:
        t = cap.chat(
            [{"role": "system", "content":
              "You are reading a single snapshot from a home security camera. "
              "Answer briefly and naturally for a voice assistant. Use one short "
              "sentence. Don't say 'in the image' or 'I see' — describe directly."},
             {"role": "user", "content": [
                 {"type": "image_url", "image_url": {
                     "url": capture.encode_image(frames[-1].read_bytes())}},
                 {"type": "text", "text": "What is happening right now?"}]}],
            cell="smoke-ambient", cache_state="cold", max_tokens=200, temperature=0.2)
        cpt = t.content().strip()
        cat = gates.classify_look_finding(cpt)
        (ok if cpt and t.latency_s < 1.5 else warn)(
            f"{t.latency_s:.2f}s [{cat}]", cpt[:70])

    print("\n8. latency spot check (sidecar, warm)")
    lat = [cap.chat([{"role": "user", "content": "Reply with one word: ready."}],
                    cell="smoke-lat", cache_state="warm" if i else "cold",
                    max_tokens=8, temperature=0.0).latency_s for i in range(10)]
    p95 = capture.percentile(lat, 95)
    (ok if p95 < 2.0 else warn)(f"short-completion p95 {p95:.2f}s")

    out = pathlib.Path("/srv/data/eval/migration/cutover-smoke.ndjson")
    try:
        cap.write(str(out))
        print(f"\n{len(cap.turns)} completions archived -> {out}")
    except OSError:
        pass

    print("\n" + "=" * 62)
    if FATAL:
        print(f"\033[31mROLL BACK NOW\033[0m — {len(FATAL)} unrecoverable failure(s):")
        for f in FATAL:
            print(f"  - {f}")
        print("\n  cd /opt/home-ai-voice && \\\n"
              "  cp -a docker-compose.yml.pre-qwen38.<STAMP> docker-compose.yml && \\\n"
              "  docker compose up -d vllm")
        return 1
    if WARN:
        print(f"\033[33mKEEP IT, WATCH THESE\033[0m — {len(WARN)} warning(s):")
        for w in WARN:
            print(f"  - {w}")
        print("\nNothing here breaks the house. Live with it and see.")
        return 2
    print("\033[32mKEEP IT\033[0m — nothing that would break the house. "
          "Judge the rest by living with it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
