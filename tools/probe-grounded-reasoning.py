#!/usr/bin/env python3
"""probe-grounded-reasoning.py - Step A of "Thinking with Visual Primitives"
Phase 0 (plan: remind-me-why-we-peaceful-sonnet.md).

THE EXPERIMENT / THE GATE. Sends a camera image + a question to the local
vLLM (Qwen3-VL) with a candidate "grounded reasoning" prompt, parses the
bounding boxes the model emits, draws them on the image, and prints the
reasoning trace + per-call latency.

The question this answers: can Qwen3-VL, by prompting alone, produce a
reasoning trace where the boxes are LOAD-BEARING - used to reach the answer
(count by enumerating boxes; deduce spatial relations from coordinates) -
not decorative grounding appended to a prose answer. Iterate PROMPTS below
until it does, or conclude it cannot (-> Phase 1 trained specialist).

Throwaway-grade: edit PROMPTS, re-run, eyeball the annotated image.

Usage:
  python tools/probe-grounded-reasoning.py --image kitchen.jpg \\
      --question "How many mugs are on the counter?"
  python tools/probe-grounded-reasoning.py --camera kitchen \\
      --ha-token <token> --question "Is anyone near the stove?"

  --image FILE     a local image to reason over (reproducible; iterate fast)
  --camera NAME    instead, fetch a fresh frame from Home Assistant
  --question Q     the question to reason about
  --prompt NAME    which candidate prompt to use (see PROMPTS); default 'v1'
  --endpoint URL   vLLM chat API (default: the AI-box metrics-sidecar proxy)
  --model NAME     served model name (default qwen3-vl-30b)
  --max-tokens N   generation budget for the reasoning trace (default 1500)
  --out FILE       annotated-image output path (default probe-out.jpg)

GATE MODE (--production-parser), added for migration gate G7
(docs/QWEN38-MIGRATION.md). The tolerant `parse_boxes` below exists to
DISCOVER whatever format a model happens to emit; scoring a gate with it
would pass a model whose output production cannot consume. In gate mode the
trace is scored with the two real consumers instead, via tools/qwen38_gates:

  * the vision-sidecar's `parse_primitives` (extraction), and
  * the app's two stripper regexes from app/src/home-look.jsx (rendering).

These two DISAGREE by construction — the sidecar was widened to accept
Qwen3-VL's bracketed `<box>[[x1,y1,x2,y2]]</box>` and a bare '>' close, the
app strippers never were. Output using either variant parses server-side and
still renders as raw markup to the user, so G7 requires BOTH to accept it.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import io
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "http://192.168.0.100:8000/v1/chat/completions"
DEFAULT_MODEL = "qwen3-vl-30b"
HA_URL = "http://192.168.0.125:8123"

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
try:
    import qwen38_gates as _gates          # G7 production scoring
except ImportError:                        # pragma: no cover
    _gates = None

# ── Candidate prompts - THIS is what Step A iterates ──────────────────────
# Each entry is (system, user_template); {q} is replaced by the question.
PROMPTS = {
    # v1: explicit "point while you reason" + enumerate-to-count.
    "v1": (
        "You are a visual reasoning assistant that thinks by pointing. "
        "Whenever you refer to a specific thing in the image you give its "
        "bounding box immediately, inline, as <box>x1,y1,x2,y2</box> with "
        "integer coordinates from 0 to 1000 (top-left origin). You never "
        "refer to an object without boxing it. To count, enumerate the "
        "objects one at a time, each with its own box. To compare positions, "
        "reason from the box coordinates.",
        "Reason step by step to answer the question, pointing (boxing) every "
        "object as you go. End with a line 'ANSWER: ...'.\n\nQuestion: {q}",
    ),
    # v2: terser; leans on the model's native grounding habit.
    "v2": (
        "You reason about images by grounding every referenced object with an "
        "inline bounding box <box>x1,y1,x2,y2</box> (coordinates 0-1000).",
        "Think step by step, boxing each object you mention, then answer.\n\n"
        "{q}",
    ),
    # v3: the DEPLOYED /reason prompt (app.py REASON_SYSTEM / REASON_USER_TMPL)
    # — kept byte-identical so a probe sweep predicts the sidecar's behaviour.
    "v3": (
        "You are a visual reasoning assistant that thinks by pointing. "
        "Whenever you refer to a specific real thing in the image, give it "
        "immediately as <ref>short label</ref><box>x1,y1,x2,y2</box>, with "
        "integer coordinates from 0 to 1000 (top-left origin). Rules: box "
        "only things you can actually, distinctly see — never invent objects "
        "or tile boxes to pad a count; box each distinct object exactly once; "
        "if unsure whether something is there, do not box it; to compare "
        "positions, reason from the box coordinates.",
        "Answer the question. Reference each real object you reason about as "
        "<ref>label</ref><box>...</box>, once. Be decisive and concise — a "
        "few sentences, not a long list. End with a line 'ANSWER: ...'.\n\n"
        "Question: {q}",
    ),
    # ---- repair candidates for the deployed prompt (2026-08-17) -------------
    # v3 measures 0/6 extraction while v1 measures 5/6 on the SAME frame, so
    # the deployed prompt suppresses boxing that the checkpoint can do. v3
    # differs from v1 in three ways at once, so these isolate them. All keep
    # v3's <ref>label</ref><box> format, because the app's ref-chips need the
    # label — dropping it would trade one broken surface for another.
    #
    # v4: v3 minus the brevity clause. Tests the migration doc's hypothesis
    # that "a few sentences, not a long list" is what kills enumeration.
    "v4": (
        "You are a visual reasoning assistant that thinks by pointing. "
        "Whenever you refer to a specific real thing in the image, give it "
        "immediately as <ref>short label</ref><box>x1,y1,x2,y2</box>, with "
        "integer coordinates from 0 to 1000 (top-left origin). Rules: box "
        "only things you can actually, distinctly see — never invent objects "
        "or tile boxes to pad a count; box each distinct object exactly once; "
        "if unsure whether something is there, do not box it; to compare "
        "positions, reason from the box coordinates.",
        "Reason step by step to answer the question, pointing (boxing) every "
        "object as you go, each as <ref>label</ref><box>...</box>. End with a "
        "line 'ANSWER: ...'.\n\nQuestion: {q}",
    ),
    # v5: v3 plus v1's enumerate-to-count instruction, brevity clause KEPT.
    # Tests whether the fix is adding an explicit enumeration rule rather
    # than removing the brevity clause.
    "v5": (
        "You are a visual reasoning assistant that thinks by pointing. "
        "Whenever you refer to a specific real thing in the image, give it "
        "immediately as <ref>short label</ref><box>x1,y1,x2,y2</box>, with "
        "integer coordinates from 0 to 1000 (top-left origin). You never "
        "refer to an object without boxing it. To count, enumerate the "
        "objects one at a time, each with its own box. Rules: box only "
        "things you can actually, distinctly see — never invent objects or "
        "tile boxes to pad a count; box each distinct object exactly once; "
        "if unsure whether something is there, do not box it; to compare "
        "positions, reason from the box coordinates.",
        "Answer the question. Reference each real object you reason about as "
        "<ref>label</ref><box>...</box>, once. Be decisive and concise — a "
        "few sentences, not a long list. End with a line 'ANSWER: ...'.\n\n"
        "Question: {q}",
    ),
    # v6: both repairs together — the belt-and-braces candidate.
    "v6": (
        "You are a visual reasoning assistant that thinks by pointing. "
        "Whenever you refer to a specific real thing in the image, give it "
        "immediately as <ref>short label</ref><box>x1,y1,x2,y2</box>, with "
        "integer coordinates from 0 to 1000 (top-left origin). You never "
        "refer to an object without boxing it. To count, enumerate the "
        "objects one at a time, each with its own box. Rules: box only "
        "things you can actually, distinctly see — never invent objects or "
        "tile boxes to pad a count; box each distinct object exactly once; "
        "if unsure whether something is there, do not box it; to compare "
        "positions, reason from the box coordinates.",
        "Reason step by step to answer the question, pointing (boxing) every "
        "object as you go, each as <ref>label</ref><box>...</box>. End with a "
        "line 'ANSWER: ...'.\n\nQuestion: {q}",
    ),
}


def encode_image(data: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def bust_jpeg(img_bytes: bytes) -> bytes:
    """Perturb one random pixel and re-encode - defeats vLLM's multimodal
    prefix cache (unique bytes -> cache miss -> cold prefill) so repeated
    --runs are independent samples, not one warm-cache result N times. The
    picture stays visually identical (1 pixel in ~920k)."""
    from PIL import Image
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = im.size
    im.putpixel((random.randrange(w), random.randrange(h)),
                (random.randrange(256), random.randrange(256),
                 random.randrange(256)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def load_image(args) -> bytes:
    if args.image:
        with open(args.image, "rb") as f:
            return f.read()
    if not args.ha_token:
        sys.exit("--camera needs --ha-token (an HA long-lived access token).")
    ent = args.camera if args.camera.startswith("camera.") else f"camera.{args.camera}"
    req = urllib.request.Request(
        f"{HA_URL}/api/camera_proxy/{ent}",
        headers={"Authorization": f"Bearer {args.ha_token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def call_vllm(endpoint, model, system, user_text, image_b64, max_tokens,
              freq_penalty, presence_penalty, temperature):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_b64}},
                {"type": "text", "text": user_text},
            ]},
        ],
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "frequency_penalty": freq_penalty,
        "presence_penalty": presence_penalty,
    }).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    if "choices" not in resp:
        sys.exit(f"unexpected vLLM response: {json.dumps(resp)[:500]}")
    return resp["choices"][0]["message"]["content"], time.time() - t0


# Tolerant box parser - Step A discovers the model's actual format; this
# handles the common ones (<box>..</box>, (x,y),(x,y), bbox_2d JSON, bare).
_BOX_PATTERNS = [
    re.compile(r"<box>\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*</box>"),
    re.compile(r"\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*,\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)"),
    re.compile(r'"bbox_2d"\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]'),
    re.compile(r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]"),
]


def parse_boxes(text):
    """Return [(x1,y1,x2,y2), ...] - first regex family that matches wins."""
    for pat in _BOX_PATTERNS:
        boxes = [tuple(int(g) for g in m.groups()) for m in pat.finditer(text)]
        if boxes:
            return boxes
    return []


def runaway_score(text):
    """Crude degenerate-repetition detector for the enumerate-forever loop.
    Returns (n_sentences, max_prefix_repeat) - how many sentences share the
    single most common 5-word opening. The 'The counter is also near a X.'
    runaway scores a high max_prefix_repeat (~30); a healthy answer is 1-2."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    pref = [" ".join(s.split()[:5]).lower() for s in sents]
    c = Counter(pref)
    return len(sents), (max(c.values()) if c else 0)


def annotate(img_bytes, boxes, out_path):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  (Pillow not installed - skipping annotated image; "
              "`pip install Pillow` to enable)")
        return
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    for i, (x1, y1, x2, y2) in enumerate(boxes):
        px = (x1 / 1000 * w, y1 / 1000 * h, x2 / 1000 * w, y2 / 1000 * h)
        d.rectangle(px, outline=(255, 60, 60), width=3)
        d.text((px[0] + 4, px[1] + 4), str(i + 1), fill=(255, 60, 60))
    im.save(out_path, "JPEG", quality=90)
    print(f"  annotated image -> {out_path}  ({len(boxes)} box(es))")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image")
    ap.add_argument("--camera")
    ap.add_argument("--question", required=True)
    ap.add_argument("--prompt", default="v1", choices=sorted(PROMPTS))
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ha-token", default=None)
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--freq-penalty", type=float, default=0.0,
                    help="anti-runaway frequency_penalty; ramps with token "
                         "count; may suppress multi-box output if too high")
    ap.add_argument("--presence-penalty", type=float, default=0.0,
                    help="anti-runaway presence_penalty; flat once-seen "
                         "penalty, does not ramp")
    ap.add_argument("--temperature", type=float, default=0.2,
                    help="sampling temperature; 0 = greedy, the worst case "
                         "for repetition loops (deterministic repro)")
    ap.add_argument("--brief", action="store_true",
                    help="print only the one-line metric summary")
    ap.add_argument("--runs", type=int, default=1,
                    help="repeat the call N times; prints a runaway-rate / "
                         "box-count tally (statistical test for the flaky "
                         "runaway)")
    ap.add_argument("--bust-cache", action="store_true",
                    help="perturb one pixel before each run so vLLM's prefix "
                         "cache misses - makes --runs independent samples")
    ap.add_argument("--tally-only", action="store_true",
                    help="with --runs >1, print only the SUMMARY line")
    ap.add_argument("--production-parser", action="store_true",
                    help="GATE G7 mode: score with the sidecar's real "
                         "parse_primitives AND the app's stripper regexes "
                         "instead of the tolerant discovery parser")
    ap.add_argument("--out", default="probe-out.jpg")
    args = ap.parse_args()
    if not args.image and not args.camera:
        ap.error("need --image FILE or --camera NAME")
    if args.production_parser and _gates is None:
        ap.error("--production-parser needs tools/qwen38_gates.py on the path")

    img = load_image(args)
    system, user_tmpl = PROMPTS[args.prompt]
    user_text = user_tmpl.format(q=args.question)
    runs = max(1, args.runs)
    results = []
    for run_i in range(1, runs + 1):
        img_i = bust_jpeg(img) if args.bust_cache else img
        try:
            content, latency = call_vllm(
                args.endpoint, args.model, system, user_text,
                encode_image(img_i), args.max_tokens, args.freq_penalty,
                args.presence_penalty, args.temperature)
        except urllib.error.URLError as e:
            sys.exit(f"vLLM call failed ({args.endpoint}): {e}")
        if args.production_parser:
            g7 = _gates.score_g7(content)
            boxes = [tuple(p["bbox_1000"]) for p in _gates.parse_primitives(content)]
            n_sent, max_rep, runaway = g7.n_sentences, g7.max_prefix_repeat, g7.runaway
        else:
            g7 = None
            boxes = parse_boxes(content)
            n_sent, max_rep = runaway_score(content)
            runaway = max_rep >= 4 or len(content) > 1400
        results.append((content, latency, boxes, n_sent, max_rep, runaway, g7))
        prefix = f"run {run_i}/{runs} " if runs > 1 else ""
        line = (f"{prefix}[fp={args.freq_penalty} pp={args.presence_penalty} "
                f"temp={args.temperature}] {args.question!r} -> {latency:.1f}s "
                f"boxes={len(boxes)} chars={len(content)} sentences={n_sent} "
                f"max_prefix_repeat={max_rep} "
                f"runaway={'YES' if runaway else 'no'}")
        if g7 is not None:
            line += (f" answer_line={'yes' if g7.has_answer_line else 'NO'}"
                     f" app_clean={'yes' if g7.app_clean else 'NO'}"
                     f" G7={'pass' if g7.passed else 'FAIL'}")
            if not g7.app_clean:
                line += f" residual={g7.residual_markup[:3]}"
        if (args.brief or runs > 1) and not args.tally_only:
            print(line)

    if runs > 1:
        box_counts = sorted(len(r[2]) for r in results)
        n_runaway = sum(r[5] for r in results)
        print(f"  SUMMARY [fp={args.freq_penalty} pp={args.presence_penalty} "
              f"temp={args.temperature}] {args.question!r}: "
              f"runaway={n_runaway}/{runs}  boxes min={box_counts[0]} "
              f"median={box_counts[len(box_counts) // 2]} "
              f"max={box_counts[-1]}")
        if args.production_parser:
            g7s = [r[6] for r in results]
            n_extract = sum(1 for g in g7s if g.extracted)
            n_app = sum(1 for g in g7s if g.app_clean)
            n_ans = sum(1 for g in g7s if g.has_answer_line)
            n_single = sum(1 for g in g7s if g.n_primitives == 1)
            n_pass = sum(1 for g in g7s if g.passed)
            print(f"  G7 [production parsers] extraction={n_extract}/{runs} "
                  f"app_strippers_clean={n_app}/{runs} answer_line={n_ans}/{runs} "
                  f"single_box={n_single}/{runs} runaway={n_runaway}/{runs} "
                  f"-> G7 pass {n_pass}/{runs}")
            if n_app < runs:
                print("  G7 NOTE: the app strippers rejected output the sidecar "
                      "parsed. That is a UI-visible raw-markup regression, not "
                      "a parsing nicety — see the fail path in the migration doc.")
        return 0

    content, latency, boxes, n_sent, max_rep, runaway, g7 = results[0]
    summary = (f"[fp={args.freq_penalty} pp={args.presence_penalty} "
               f"temp={args.temperature}] {args.question!r} -> {latency:.1f}s "
               f"boxes={len(boxes)} chars={len(content)} sentences={n_sent} "
               f"max_prefix_repeat={max_rep} "
               f"runaway={'YES' if runaway else 'no'}")
    if args.brief:
        print(summary)
        return 0
    print(f"=== prompt '{args.prompt}' | model {args.model} | "
          f"{args.endpoint} ===")
    print(f"\n--- reasoning  ({latency:.1f}s | {len(boxes)} box(es) parsed) ---")
    print(content)
    print("\n--- parsed boxes (0-1000) ---")
    for i, b in enumerate(boxes):
        print(f"  {i + 1}: {b}")
    annotate(img, boxes, args.out)
    print("\n" + summary)
    if g7 is not None:
        print("\n--- G7 (production parsers) ---")
        print(f"  sidecar parse_primitives : {g7.n_primitives} primitive(s)"
              f"  -> {'OK' if g7.extracted else 'NO EXTRACTION'}")
        print(f"  app stripper regexes     : "
              f"{'clean' if g7.app_clean else 'RESIDUAL MARKUP ' + str(g7.residual_markup)}")
        print(f"  ANSWER: line             : {'present' if g7.has_answer_line else 'absent'}")
        print(f"  runaway                  : {'YES' if g7.runaway else 'no'}")
        print(f"  G7 verdict               : {'PASS' if g7.passed else 'FAIL'}")
        print("\n  what the app would render:")
        print("    " + (_gates.app_render(content)[:300] or "(empty)"))
    print("\nEYEBALL CHECK (the gate): are the boxes LOAD-BEARING - does the "
          "reasoning use them to reach the answer (enumerated to count, "
          "coordinates to compare) - or just decorative? Do they land on the "
          "right things? Test 3+ instances. That verdict is Phase 0's result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
