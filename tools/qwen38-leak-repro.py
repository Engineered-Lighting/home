#!/usr/bin/env python3
"""qwen38-leak-repro.py — reproduce the reasoning leak against the engine
directly, and find the minimal ingredient that triggers it.

Two cutover attempts were rolled back on `</think>` reaching spoken output
at roughly a 15% rate. Both diagnoses were made from 10-minute cutover
cycles, and both were wrong. This replaces that loop: it drives the engine
straight, ablating one ingredient at a time, so a hypothesis costs seconds.

**What the EOC path actually does** (read from the archived live component,
`entity.py`):

  * `stream: True` with `stream_options.include_usage` — every earlier probe
    was BUFFERED, which is the most obvious untested difference;
  * the 33,760-char system prompt;
  * the 23-tool catalogue with `tool_choice`;
  * a multi-turn shape — assistant tool_call, tool result, then the answer;
  * `max_tokens` from the subentry (2000).

And the decisive line, `entity.py:437`: the stream transform consumes
`delta.content` and **never reads `delta.reasoning`**. So with no
`--reasoning-parser` configured, vLLM has nowhere to put think markup except
`content`, and EOC forwards it verbatim to TTS. That is the mechanism this
tool is built to confirm or refute.

Each variant reports: leak rate in `content`, whether the separate
`reasoning` fields are EVER populated (which tells you if a parser is
active), and duplicate-answer rate.

Usage:
  tools/qwen38-leak-repro.py                 # all variants, n=12
  tools/qwen38-leak-repro.py --n 25
  tools/qwen38-leak-repro.py --only D,E
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import qwen38_gates as gates  # noqa: E402

SIDECAR = "http://127.0.0.1:8000/v1/chat/completions"
PHASE0 = pathlib.Path("/srv/data/eval/migration/phase0")
LIVE_PROMPT = PHASE0 / "eoc" / "prompt.live.txt"
LIVE_FUNCS = PHASE0 / "eoc" / "functions.live.yaml"
SHORT = "You are a home assistant."
Q = "What rooms are in my home?"


def long_prompt() -> str:
    if LIVE_PROMPT.exists():
        return LIVE_PROMPT.read_text(encoding="utf-8")
    print("  ⚠ live prompt not archived; using a short one — results will "
          "NOT represent production.")
    return SHORT


def live_tools() -> list[dict]:
    """The real 23-tool catalogue, converted to OpenAI tool shape."""
    if not LIVE_FUNCS.exists():
        return []
    try:
        import yaml
    except ImportError:
        return []
    out = []
    for entry in yaml.safe_load(LIVE_FUNCS.read_text(encoding="utf-8")) or []:
        spec = entry.get("spec") if isinstance(entry, dict) else None
        if not spec:
            continue
        out.append({"type": "function", "function": {
            "name": spec.get("name"),
            "description": (spec.get("description") or "")[:900],
            "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
        }})
    return out


def call(endpoint, model, messages, stream, tools=None, max_tokens=2000,
         temperature=1.0, timeout=180):
    """One completion. Returns (content, reasoning_fields_seen, error)."""
    body = {"model": model, "messages": messages, "stream": bool(stream),
            "max_tokens": max_tokens, "temperature": temperature}
    if stream:
        body["stream_options"] = {"include_usage": True}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        endpoint, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    seen_reasoning = set()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if not stream:
                resp = json.loads(r.read())
                msg = (resp.get("choices") or [{}])[0].get("message") or {}
                for f in gates.REASONING_FIELDS:
                    if msg.get(f):
                        seen_reasoning.add(f)
                c = msg.get("content")
                return (c if isinstance(c, str) else ""), seen_reasoning, None
            pieces = []
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                delta = ((chunk.get("choices") or [{}])[0] or {}).get("delta") or {}
                for f in gates.REASONING_FIELDS:
                    if delta.get(f):
                        seen_reasoning.add(f)
                # Mirror entity.py:437 exactly — content only.
                if delta.get("content"):
                    pieces.append(delta["content"])
            return "".join(pieces), seen_reasoning, None
    except Exception as e:  # noqa: BLE001
        return "", seen_reasoning, f"{type(e).__name__}: {e}"


def variants(tools):
    lp = long_prompt()
    tool_turn = [
        {"role": "system", "content": lp},
        {"role": "user", "content": Q},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "areas_in_home", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1",
         "content": json.dumps({"areas": ["living room", "kitchen",
                                          "dining room", "workshop",
                                          "driveway"]})},
    ]
    return {
        "A": ("buffered, short prompt, no tools  (the old probe)",
              dict(messages=[{"role": "system", "content": SHORT},
                             {"role": "user", "content": Q}],
                   stream=False, tools=None)),
        "B": ("STREAMING, short prompt, no tools",
              dict(messages=[{"role": "system", "content": SHORT},
                             {"role": "user", "content": Q}],
                   stream=True, tools=None)),
        "C": ("STREAMING, live 33.7k prompt, no tools",
              dict(messages=[{"role": "system", "content": long_prompt()},
                             {"role": "user", "content": Q}],
                   stream=True, tools=None)),
        "D": ("STREAMING, live prompt + 23 tools",
              dict(messages=[{"role": "system", "content": long_prompt()},
                             {"role": "user", "content": Q}],
                   stream=True, tools=tools)),
        "E": ("STREAMING, live prompt + tools + tool RESULT in history "
              "(the real EOC shape)",
              dict(messages=tool_turn, stream=True, tools=tools)),
        "F": ("buffered, live prompt + tools + tool result "
              "(same as E but not streamed)",
              dict(messages=tool_turn, stream=False, tools=tools)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", default=SIDECAR)
    ap.add_argument("--model", default="qwen3-vl-30b")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--only", help="comma-separated variant ids, e.g. D,E")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--show", action="store_true", help="print a leaking sample")
    ap.add_argument("--capture", help="write every leaking completion to this "
                                      "NDJSON file, and score downstream "
                                      "recovery against all of them")
    args = ap.parse_args()

    tools = live_tools()
    print(f"endpoint  : {args.endpoint}")
    print(f"live tools: {len(tools)}   live prompt: {len(long_prompt()):,} chars")
    print(f"samples   : {args.n} per variant\n")
    if not tools:
        print("  ⚠ no live tool catalogue found — variants D/E/F are degraded.\n")

    captured = []
    vs = variants(tools)
    picked = ([v.strip().upper() for v in args.only.split(",")]
              if args.only else list(vs))

    print(f"{'':2} {'variant':62} {'leak':>6} {'dup':>5} {'reasoning field':>16}")
    print("-" * 96)
    worst, sample = None, None
    for vid in picked:
        if vid not in vs:
            continue
        label, kw = vs[vid]
        leaks = dups = errs = 0
        fields = set()
        for _ in range(args.n):
            content, seen, err = call(args.endpoint, args.model,
                                      temperature=args.temperature, **kw)
            if err:
                errs += 1
                continue
            fields |= seen
            if gates.find_reasoning_leaks({"content": content}):
                leaks += 1
                captured.append({"variant": vid, "content": content})
                if sample is None:
                    sample = (vid, content)
            if len(content) > 80 and content.count(content[:40]) >= 2:
                dups += 1
        rate = leaks / max(args.n - errs, 1)
        if worst is None or rate > worst[1]:
            worst = (vid, rate)
        flag = "  <-- LEAKS" if leaks else ""
        print(f"{vid:2} {label:62} {leaks:>3}/{args.n - errs:<2} {dups:>5} "
              f"{(','.join(sorted(fields)) or 'never populated'):>16}{flag}")
        if errs:
            print(f"{'':2} {'':62} ({errs} request error(s))")

    if args.capture and captured:
        import sys as _s
        _s.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                              / "stack" / "services" / "metrics-sidecar" / "patches"))
        from thinking_routing import recover_leaked_content
        with open(args.capture, "w", encoding="utf-8") as f:
            for c in captured:
                c["recovered"] = recover_leaked_content(c["content"])
                f.write(json.dumps(c) + "\n")
        clean = sum(1 for c in captured
                    if "</think>" not in c["recovered"]
                    and "<think" not in c["recovered"])
        nodup = sum(1 for c in captured
                    if not (len(c["recovered"]) > 80
                            and c["recovered"].count(c["recovered"][:40]) >= 2))
        nonempty = sum(1 for c in captured if c["recovered"].strip())
        print(f"\n=== downstream recovery scored on {len(captured)} REAL leaks ===")
        print(f"  recovered free of think markup : {clean}/{len(captured)}")
        print(f"  not duplicated                 : {nodup}/{len(captured)}")
        print(f"  non-empty (did not lose the answer): {nonempty}/{len(captured)}")
        print(f"  captured -> {args.capture}")

    print()
    if worst and worst[1] > 0:
        print(f"Minimal trigger found: variant {worst[0]} leaks at "
              f"{worst[1] * 100:.0f}%.")
        print("If the `reasoning field` column says 'never populated', no "
              "reasoning parser is active, so vLLM has nowhere to put think "
              "markup except `content` — and EOC (entity.py:437) forwards "
              "`delta.content` straight to TTS. That is the case "
              "`--reasoning-parser qwen3` exists for.")
        if args.show and sample:
            print(f"\n--- leaking sample from variant {sample[0]} ---")
            print(sample[1][:700])
    else:
        print("No leak reproduced in any variant at this sample size. Raise "
              "--n before concluding it is fixed: at n=12 a 15% fault is "
              "still missed about 14% of the time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
