#!/usr/bin/env python3
"""gate-g4-negatives.py — acceptance gate G4 (docs/QWEN38-MIGRATION.md).

G4 asks whether the candidate invents things on frames where nothing is
happening: "false-presence ≤ incumbent, paired", over 25 empty/night/
cup-misfire negatives plus 10 quiet-literal sentinels.

**What counts as a hallucination is decided by production, not by taste.**
Every caption is scored with `classifyLookFinding` — the app's own
classifier, ported in `qwen38_gates` and cross-asserted against the JS in
`tools/run-look-tests.js`. So the question G4 answers is the operational
one: *would the app have raised a notable finding on a frame where nothing
happened?* A caption that reads oddly but files as `quiet` costs the user
nothing; one that files as `person` (importance 90) wakes them up.

`activity` (importance 55) counts as false presence too — that is exactly
what the pre-PR-1a quiet-literal bug produced on every idle camera.

**The corpus comes from live Frigate, not from imagination.** The kitchen
camera emits ~2,300 `cup` events in six hours — Frigate re-detecting a cup
on the counter. Those are real frames where a detector fired and nothing
happened, which is precisely the negative this gate wants, and there are
thousands of them. Sources, in order of preference:

  cup_misfire  static-object events (cup/bottle/sink/chair/suitcase)
  night        events during dark hours
  empty        events on cameras with no overlapping `person` event

⚠ **Ground truth is proposed, then confirmed.** "No `person` event
overlapped" is Frigate's opinion, and a detector that missed a person would
hand this gate a frame that is not actually negative — poisoning it in the
direction of failing an honest model. `build` therefore only PROPOSES; a
human runs `verify` and rejects any frame that is not truly empty before
the corpus is frozen.

Usage:
  tools/gate-g4-negatives.py build  --out CORPUS --negatives 25 --sentinels 10
  tools/gate-g4-negatives.py verify CORPUS
  tools/gate-g4-negatives.py run    CORPUS --arm incumbent
  tools/gate-g4-negatives.py score  CORPUS --candidate-arm candidate
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import qwen38_capture as capture  # noqa: E402
import qwen38_gates as gates  # noqa: E402

FRIGATE = "http://192.168.0.125:5000"
DEFAULT_ENDPOINT = "http://127.0.0.1:8000/v1/chat/completions"

# Objects Frigate re-detects while nothing happens. A frame whose only
# event is one of these is, by construction, a "detector fired, nothing
# happened" negative.
STATIC_LABELS = ("cup", "bottle", "sink", "chair", "book",
                 "potted plant", "vase", "bowl")

# Living things must be ABSENT for a frame to be a negative at all — an
# invented person is the failure this gate exists to catch, so there is no
# tolerating one that was really there.
PERSON_LABELS = ("person", "dog", "cat", "bird", "horse")

# Things that may legitimately sit in frame without anything happening. A
# parked car on a driveway is the obvious case: "a car is parked outside"
# is TRUE, and scoring it as a hallucination would penalise an accurate
# caption. Rather than throw those frames away — which would delete the
# driveway from the corpus entirely, the very camera most likely to invent
# a person or a package — the frame records what is legitimately there and
# the corresponding category stops counting as false presence FOR THAT
# FRAME ONLY. Everything else still does.
TOLERATED_LABELS = {
    "car": "vehicle", "truck": "vehicle", "bus": "vehicle",
    "motorcycle": "vehicle", "bicycle": "vehicle",
    "package": "package", "suitcase": "package",
    "handbag": "package", "backpack": "package",
}
NIGHT_HOURS = set(range(21, 24)) | set(range(0, 6))

# Byte-identical to the live vision-sidecar /describe handler; the sentinels
# are only meaningful against the prompt production actually uses.
DESCRIBE_SYSTEM = (
    "You are reading a single snapshot from a home security "
    "camera. Answer briefly and naturally for a voice assistant. "
    "Use one short sentence. Don't say 'in the image' or "
    "'I see' — describe directly."
)
DESCRIBE_QUESTION = "What is happening right now?"

# The quiet-literal sentinels must run against the surface that ASKS for the
# quiet literal. `/describe` says "describe directly", so it answers with
# room inventories and never emits the phrase — measured on the incumbent,
# 0/10. The deep-look path is the one that says "If nothing important is
# happening, say exactly: No obvious activity."
#
# Composed exactly as production does it: buildFocusedLookQuestion() in
# app/src/home-natural-look.js (quick_scan branch) wrapped in the
# vision-sidecar's REASON_SYSTEM / REASON_USER_TMPL.
SENTINEL_USER_QUESTION = "Anything I should know about?"


def deep_look_question(camera: str, question: str = SENTINEL_USER_QUESTION) -> str:
    place = camera.replace("_", " ") if camera else "this camera"
    return " ".join([
        f'Look at the {place} camera for this user question: "{question}"',
        "Answer in one short, plain sentence for a home-monitoring dashboard.",
        "Focus first on anything the user needs to care about.",
        "Use zoom/crop plus segmentation or bounding boxes when useful.",
        "Only call out people, activity, packages, pets, vehicles, open"
        " doors/windows, hazards, lights, leaks, smoke, or unusual changes.",
        "If nothing important is happening, say exactly: No obvious activity.",
        "Do not list ordinary furniture or room contents unless they are the"
        " important finding.",
    ])


REASON_SYSTEM = (
    "You are a visual reasoning assistant that thinks by pointing. "
    "Whenever you refer to a specific real thing in the image, give it "
    "immediately as <ref>short label</ref><box>x1,y1,x2,y2</box>, with "
    "integer coordinates from 0 to 1000 (top-left origin). Rules: box "
    "only things you can actually, distinctly see \u2014 never invent objects "
    "or tile boxes to pad a count; box each distinct object exactly once; "
    "if unsure whether something is there, do not box it; to compare "
    "positions, reason from the box coordinates."
)
REASON_USER_TMPL = (
    "Answer the question. Reference each real object you reason about as "
    "<ref>label</ref><box>...</box>, once. Be decisive and concise \u2014 a "
    "few sentences, not a long list. End with a line 'ANSWER: ...'.\n\n"
    "Question: {question}"
)


def _get(path: str, timeout: int = 30):
    with urllib.request.urlopen(f"{FRIGATE}{path}", timeout=timeout) as r:
        return json.load(r)


def _snapshot(event_id: str) -> bytes | None:
    try:
        with urllib.request.urlopen(
                f"{FRIGATE}/api/events/{event_id}/snapshot.jpg", timeout=25) as r:
            b = r.read()
        return b if len(b) > 2000 else None
    except Exception:  # noqa: BLE001
        return None


def cmd_build(args) -> int:
    root = pathlib.Path(args.out)
    (root / "frames").mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    if manifest_path.exists() and not args.force:
        sys.exit(f"{manifest_path} exists — the corpus is frozen. --force to rebuild.")

    now = int(time.time())
    ev = _get(f"/api/events?after={now - args.window_h * 3600}&before={now}"
              f"&limit=10000&include_thumbnails=0")
    print(f"events in the last {args.window_h}h: {len(ev)}")

    # Living-thing spans per camera — the hard exclusion mask.
    spans: dict[str, list[tuple[float, float]]] = {}
    # Tolerated-object spans, kept so each frame can declare what really is
    # in it rather than being discarded.
    tol: dict[str, list[tuple[float, float, str]]] = {}
    for e in ev:
        st = e.get("start_time")
        if not st:
            continue
        en = (e.get("end_time") or st)
        if e.get("label") in PERSON_LABELS:
            spans.setdefault(e["camera"], []).append(
                (st - args.margin_s, en + args.margin_s))
        elif e.get("label") in TOLERATED_LABELS:
            tol.setdefault(e["camera"], []).append(
                (st - args.margin_s, en + args.margin_s,
                 TOLERATED_LABELS[e["label"]]))
    print(f"living-thing spans masked : {sum(len(v) for v in spans.values())}")
    print(f"tolerated-object spans    : {sum(len(v) for v in tol.values())}"
          f"  (recorded per frame, not excluded)")

    def person_free(e) -> bool:
        st, en = e["start_time"], (e.get("end_time") or e["start_time"])
        return not any(st <= b and a <= en for a, b in spans.get(e["camera"], []))

    def allowed_for(e) -> list[str]:
        st, en = e["start_time"], (e.get("end_time") or e["start_time"])
        return sorted({cat for a, b, cat in tol.get(e["camera"], [])
                       if st <= b and a <= en})

    pool = [e for e in ev
            if e.get("has_snapshot") and e.get("start_time") and person_free(e)]
    rng = random.Random(args.seed)

    # Later picks must not re-select what an earlier pick already took, or
    # the dedupe downstream silently shrinks the corpus below the gate's
    # stated size (25 negatives + 10 sentinels) without saying so.
    taken: set[str] = set()

    def pick(pred, n, kind):
        cands = [e for e in pool if pred(e) and e["id"] not in taken]
        rng.shuffle(cands)
        # Spread across cameras so one busy camera cannot own the corpus.
        by_cam: dict[str, list] = {}
        for e in cands:
            by_cam.setdefault(e["camera"], []).append(e)
        out, i = [], 0
        while len(out) < n and any(by_cam.values()):
            for cam in sorted(by_cam):
                if by_cam[cam] and len(out) < n:
                    e = by_cam[cam].pop()
                    taken.add(e["id"])
                    out.append((e, kind))
            i += 1
            if i > n + 5:
                break
        if len(out) < n:
            print(f"  ⚠ only {len(out)}/{n} '{kind}' frames available "
                  "from the live event pool")
        return out

    def is_night(e):
        return time.localtime(e["start_time"]).tm_hour in NIGHT_HOURS

    selected = []
    selected += pick(lambda e: e["label"] in STATIC_LABELS and not is_night(e),
                     args.negatives // 2, "cup_misfire")
    selected += pick(lambda e: is_night(e) and e["label"] in STATIC_LABELS,
                     args.negatives // 4, "night")
    selected += pick(lambda e: e["label"] in STATIC_LABELS,
                     args.negatives - len(selected), "empty")
    sentinels = pick(lambda e: e["label"] in STATIC_LABELS, args.sentinels,
                     "sentinel")

    seen, entries = set(), []
    for e, kind in selected + sentinels:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        jpeg = _snapshot(e["id"])
        if not jpeg:
            continue
        fid = f"{kind}_{len(entries):03d}_{e['camera']}"
        (root / "frames" / f"{fid}.jpg").write_bytes(jpeg)
        entries.append({
            "id": fid, "kind": kind, "camera": e["camera"],
            "event_id": e["id"], "label": e["label"],
            "start_time": e["start_time"],
            "local_time": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(e["start_time"])),
            "bytes": len(jpeg),
            "allowed_categories": allowed_for(e),
            "ground_truth": "no person/pet event overlapped on this camera "
                            f"(±{args.margin_s}s); detector fired on the "
                            f"static object '{e['label']}'",
            "verified": None,
        })
        print(f"  {fid:34s} {e['label']:10s} {entries[-1]['local_time']}")

    manifest_path.write_text(json.dumps({
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "window_h": args.window_h, "seed": args.seed,
        "margin_s": args.margin_s,
        "system": DESCRIBE_SYSTEM, "question": DESCRIBE_QUESTION,
        "frames": entries,
    }, indent=2), encoding="utf-8")

    kinds: dict[str, int] = {}
    for e in entries:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(f"\n{len(entries)} frames -> {root}")
    print(f"  by kind: {kinds}")
    print("\nPROPOSED, not confirmed. Frigate's detector missing a person "
          "would hand this gate a frame that is not negative, which fails an "
          "honest model. Run `verify` before using this corpus.")
    return 0


def cmd_verify(args) -> int:
    root = pathlib.Path(args.corpus)
    mpath = root / "manifest.json"
    if not mpath.exists():
        sys.exit(f"no manifest at {mpath} — run `build` first.")
    man = json.loads(mpath.read_text(encoding="utf-8"))

    print("Confirm each frame really is a negative: no person, no pet, no "
          "package, nothing happening.")
    print("  y = negative (keep)   n = something IS there (reject)")
    print("  s = skip   q = save and quit\n")
    for fr in man["frames"]:
        if fr.get("verified") is not None and not args.redo:
            continue
        print(f"--- {fr['id']} ---")
        print(f"  camera={fr['camera']} label={fr['label']} at {fr['local_time']}")
        print(f"  why proposed: {fr['ground_truth']}")
        print(f"  image: {root / 'frames' / (fr['id'] + '.jpg')}")
        while True:
            try:
                raw = input("  really empty? [y/n/s/q]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                raw = "q"
            if raw in ("y", "n", "s", "q"):
                break
        if raw == "q":
            break
        if raw == "s":
            continue
        fr["verified"] = (raw == "y")
        print()

    mpath.write_text(json.dumps(man, indent=2), encoding="utf-8")
    ok = sum(1 for f in man["frames"] if f.get("verified") is True)
    no = sum(1 for f in man["frames"] if f.get("verified") is False)
    pend = sum(1 for f in man["frames"] if f.get("verified") is None)
    print(f"confirmed={ok} rejected={no} unreviewed={pend} -> {mpath}")
    return 0


def cmd_run(args) -> int:
    root = pathlib.Path(args.corpus)
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    frames = [f for f in man["frames"]
              if args.include_unverified or f.get("verified") is True]
    if not frames:
        sys.exit("no verified frames — run `verify` first, or pass "
                 "--include-unverified for a dry run (NOT gate evidence).")
    out_path = root / f"g4.{args.arm}.json"
    if out_path.exists() and not args.force:
        sys.exit(f"{out_path} exists — use --force.")

    cap = capture.Capturer(args.endpoint, model=args.model,
                           measurement_point=args.measurement_point)
    results = []
    for fr in frames:
        jpeg = (root / "frames" / f"{fr['id']}.jpg").read_bytes()
        img = {"type": "image_url",
               "image_url": {"url": capture.encode_image(jpeg)}}
        sentinel = fr["kind"] == "sentinel"
        if sentinel:
            # Deep-look surface: the one that asks for the quiet literal.
            system = REASON_SYSTEM
            user_text = REASON_USER_TMPL.format(
                question=deep_look_question(fr["camera"]))
            budget = max(args.max_tokens, 700)
        else:
            # Ambient /describe surface: the one that captions every frame.
            system = man["system"]
            user_text = f"Camera: camera.{fr['camera']}. {man['question']}"
            budget = args.max_tokens
        turn = cap.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": [img, {"type": "text", "text": user_text}]}],
            cell=f"G4-{args.arm}-{'sentinel' if sentinel else 'ambient'}",
            cache_state="cold",
            meta={"frame": fr["id"], "kind": fr["kind"]},
            max_tokens=budget, temperature=args.temperature)
        raw = turn.content().strip()
        # /reason returns the extracted answer, which is what the app
        # classifies — scoring the raw trace would score markup.
        caption = gates.sidecar_extract_answer(raw) if sentinel else raw
        category = gates.classify_look_finding(caption)
        allowed = set(fr.get("allowed_categories") or ())
        # A category that is legitimately in frame is not a hallucination.
        false_presence = gates.is_false_presence(caption) and category not in allowed
        rec = {
            "frame": fr["id"], "kind": fr["kind"], "camera": fr["camera"],
            "caption": caption, "category": category,
            "importance": gates.look_importance(category),
            "false_presence": false_presence,
            "allowed_categories": sorted(allowed),
            "quiet_literal": gates.is_quiet_literal(caption),
            "low_signal": gates.is_low_signal(caption),
            "surface": "deep_look" if fr["kind"] == "sentinel" else "ambient",
            "leaks": len(gates.scan_response(turn.response)),
            "latency_s": round(turn.latency_s, 3), "status": turn.status,
        }
        results.append(rec)
        mark = "  <-- FALSE PRESENCE" if rec["false_presence"] else ""
        print(f"  {fr['id']:34s} {category:11s} {caption[:56]!r}{mark}")

    out_path.write_text(json.dumps(
        {"arm": args.arm, "model": args.model, "results": results},
        indent=2), encoding="utf-8")
    cap.write(str(root / f"g4.turns.{args.arm}.ndjson"))

    fp = sum(r["false_presence"] for r in results)
    sent = [r for r in results if r["kind"] == "sentinel"]
    sq = sum(r["quiet_literal"] for r in sent)
    sl = sum(r["low_signal"] for r in sent)
    leaks = sum(r["leaks"] for r in results)
    print(f"\nfalse presence   : {fp}/{len(results)}")
    if sent:
        print(f"quiet sentinels  : {sq}/{len(sent)} filed as quiet, "
              f"{sl}/{len(sent)} low-signal (quiet or inventory)")
    print(f"G5 (free ride)   : {leaks} leak(s)"
          + ("  <-- G5 FAILS" if leaks else ""))
    print(capture.summarize(cap.turns, f"G4-{args.arm}"))
    print(f"-> {out_path}")
    return 0


def cmd_score(args) -> int:
    root = pathlib.Path(args.corpus)
    def load(arm):
        p = root / f"g4.{arm}.json"
        if not p.exists():
            sys.exit(f"missing {p} — run the '{arm}' arm first.")
        return {r["frame"]: r for r in json.loads(
            p.read_text(encoding="utf-8"))["results"]}

    inc, cand = load(args.incumbent_arm), load(args.candidate_arm)
    shared = sorted(set(inc) & set(cand))
    if not shared:
        sys.exit("the two arms share no frames")

    print("=== G4 hallucination on negatives ===")
    print(f"paired frames: {len(shared)}")
    print(f"  incumbent false presence: "
          f"{sum(inc[f]['false_presence'] for f in shared)}/{len(shared)}")
    print(f"  candidate false presence: "
          f"{sum(cand[f]['false_presence'] for f in shared)}/{len(shared)}\n")

    # "good" == did NOT hallucinate, so a candidate win means the candidate
    # stayed quiet where the incumbent invented something.
    res = gates.mcnemar_from_pairs(
        "G4 false presence (fewer is better)",
        ((not inc[f]["false_presence"], not cand[f]["false_presence"])
         for f in shared),
        margin=args.margin, alpha=args.alpha)
    print(res.summary())

    sent = [f for f in shared if inc[f]["kind"] == "sentinel"]
    ok = True
    if sent:
        si = sum(inc[f]["quiet_literal"] for f in sent)
        sc = sum(cand[f]["quiet_literal"] for f in sent)
        print(f"quiet-literal sentinels: incumbent {si}/{len(sent)}, "
              f"candidate {sc}/{len(sent)}")
        sres = gates.mcnemar_from_pairs(
            "G4 quiet-literal sentinels",
            ((inc[f]["quiet_literal"], cand[f]["quiet_literal"]) for f in sent),
            margin=args.margin, alpha=args.alpha)
        print(sres.summary())
        ok = sres.passed or sc >= si

    # A gate that only reports "not significantly worse" hides a real drop,
    # so show the raw direction too.
    worse = [f for f in shared
             if cand[f]["false_presence"] and not inc[f]["false_presence"]]
    if worse:
        print(f"\nframes the candidate hallucinated on and the incumbent did not "
              f"({len(worse)}):")
        for f in worse[:8]:
            print(f"  {f}: [{cand[f]['category']}] {cand[f]['caption'][:70]!r}")

    overall = res.passed and ok
    print(f"\nG4 VERDICT: {'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="propose a negatives corpus from live Frigate")
    b.add_argument("--out", required=True)
    b.add_argument("--negatives", type=int, default=25)
    b.add_argument("--sentinels", type=int, default=10)
    b.add_argument("--window-h", type=int, default=24)
    b.add_argument("--margin-s", type=int, default=30,
                   help="seconds of person-event margin to exclude around a frame")
    b.add_argument("--seed", type=int, default=20260816)
    b.add_argument("--force", action="store_true")
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("verify", help="human confirmation of each negative")
    v.add_argument("corpus")
    v.add_argument("--redo", action="store_true")
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("run", help="caption the corpus with one arm")
    r.add_argument("corpus")
    r.add_argument("--arm", required=True)
    r.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    r.add_argument("--model", default="qwen3-vl-30b")
    r.add_argument("--measurement-point", default="sidecar",
                   choices=sorted(capture.MEASUREMENT_POINTS))
    r.add_argument("--max-tokens", type=int, default=200)
    r.add_argument("--temperature", type=float, default=0.2)
    r.add_argument("--include-unverified", action="store_true",
                   help="dry run over unconfirmed frames; NOT gate evidence")
    r.add_argument("--force", action="store_true")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("score", help="paired comparison of the two arms")
    s.add_argument("corpus")
    s.add_argument("--candidate-arm", default="candidate")
    s.add_argument("--incumbent-arm", default="incumbent")
    s.add_argument("--margin", type=float, default=0.10)
    s.add_argument("--alpha", type=float, default=0.05)
    s.set_defaults(func=cmd_score)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
