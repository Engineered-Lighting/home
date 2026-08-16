#!/usr/bin/env python3
"""gate-g1-captions.py — acceptance gate G1 (docs/QWEN38-MIGRATION.md).

G1 asks whether the candidate's ambient captions are non-inferior to the
incumbent's, judged blind on a FROZEN corpus of real camera frames.

The gate is a five-step pipeline, one subcommand each, so the slow parts
(capture, run) can happen on different days from the human part (judge):

  capture  freeze N real frames + a manifest        (read-only, on-host)
  run      caption every frame with one arm         (one model, one config)
  pair     build the blinded judging file + decoys  (offline)
  judge    record verdicts, one pair at a time      (human, offline)
  score    unblind and apply the non-inferiority test

Three things this design refuses to get wrong:

  * **The blinding is real.** `pair` writes the judging file and the answer
    key as SEPARATE files, A/B order is randomised per pair, and the
    judging file carries no arm names, no model ids, and no ordering
    signal. `judge` never reads the key.
  * **Decoys calibrate the judge, not the model.** A fraction of pairs are
    incumbent-vs-incumbent. A judge who declares a winner on those far
    above chance is measuring style and noise, not quality, and `score`
    says so instead of quietly reporting a verdict.
  * **Ties are not evidence.** Scoring conditions on the non-tied count
    (`qwen38_gates.noninferiority_test`), which is what the migration doc
    means by "binomial critical value on the actual non-tied count".

The caption prompt is copied byte-for-byte from the live vision-sidecar
(`/describe`). If the sidecar's prompt changes, this gate is measuring
something production does not do — `run` re-checks it against the Phase-0
archive when that archive is present.

Usage:
  tools/gate-g1-captions.py capture --out CORPUS --n 50 --ha-token TOK
  tools/gate-g1-captions.py run CORPUS --arm incumbent --endpoint URL
  tools/gate-g1-captions.py pair CORPUS --a incumbent --b candidate
  tools/gate-g1-captions.py judge CORPUS
  tools/gate-g1-captions.py score CORPUS
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import qwen38_capture as capture  # noqa: E402
import qwen38_gates as gates  # noqa: E402

HA_URL = "http://192.168.0.125:8123"
DEFAULT_ENDPOINT = "http://127.0.0.1:8000/v1/chat/completions"
CAMERAS = ["living_room", "dining_room", "kitchen", "back_door", "driveway"]

# Byte-identical to the live vision-sidecar /describe handler.
DESCRIBE_SYSTEM = (
    "You are reading a single snapshot from a home security "
    "camera. Answer briefly and naturally for a voice assistant. "
    "Use one short sentence. Don't say 'in the image' or "
    "'I see' — describe directly."
)
DESCRIBE_QUESTION = "What is happening right now?"
DESCRIBE_MAX_TOKENS = 200          # live VISION_MAX_TOKENS

PHASE0_SIDECAR = pathlib.Path(
    "/srv/data/eval/migration/phase0/live-sources/vision-sidecar/app.py")


def _corpus(path: str) -> pathlib.Path:
    p = pathlib.Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load(path: pathlib.Path, what: str):
    if not path.exists():
        sys.exit(f"missing {what}: {path}\nRun the earlier subcommand first.")
    return json.loads(path.read_text(encoding="utf-8"))


# ── capture ──────────────────────────────────────────────────────────────
def cmd_capture(args) -> int:
    """Freeze real frames. The corpus is frozen ONCE and reused by every
    arm — a corpus that drifts between arms is not a paired comparison."""
    root = _corpus(args.out)
    frames_dir = root / "frames"
    frames_dir.mkdir(exist_ok=True)
    manifest_path = root / "manifest.json"
    if manifest_path.exists() and not args.force:
        sys.exit(f"{manifest_path} exists — the corpus is frozen. "
                 "Use --force only if you intend to invalidate every arm "
                 "already captured against it.")

    cams = args.cameras or CAMERAS
    entries, i = [], 0
    while len(entries) < args.n:
        cam = cams[i % len(cams)]
        i += 1
        ent = cam if cam.startswith("camera.") else f"camera.{cam}"
        req = urllib.request.Request(
            f"{args.ha_url}/api/camera_proxy/{ent}",
            headers={"Authorization": f"Bearer {args.ha_token}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                jpeg = r.read()
        except Exception as e:  # noqa: BLE001
            print(f"  {ent}: fetch failed ({e}) — skipping")
            continue
        fid = f"f{len(entries):03d}_{cam}"
        (frames_dir / f"{fid}.jpg").write_bytes(jpeg)
        entries.append({"id": fid, "camera": cam, "entity_id": ent,
                        "bytes": len(jpeg)})
        print(f"  {fid}: {len(jpeg)} bytes")
        if args.interval:
            import time
            time.sleep(args.interval)

    manifest_path.write_text(json.dumps({
        "n": len(entries),
        "cameras": cams,
        "question": DESCRIBE_QUESTION,
        "system": DESCRIBE_SYSTEM,
        "frames": entries,
    }, indent=2), encoding="utf-8")
    print(f"\nfrozen corpus: {len(entries)} frames -> {root}")
    print("This corpus is now the G1 reference. Do not re-capture between "
          "arms; both models must see identical pixels.")
    return 0


# ── run ──────────────────────────────────────────────────────────────────
def _verify_prompt_matches_live() -> None:
    if not PHASE0_SIDECAR.exists():
        print("  note: Phase-0 sidecar archive not present; cannot verify "
              "the caption prompt still matches production.")
        return
    live = PHASE0_SIDECAR.read_text(encoding="utf-8", errors="replace")
    probe = "You are reading a single snapshot from a home security "
    if probe not in live:
        print("  WARNING: the live /describe prompt no longer matches this "
              "gate's copy. G1 would be measuring something production does "
              "not do. Reconcile before trusting a result.")
    else:
        print("  caption prompt matches the Phase-0 live sidecar archive.")


def cmd_run(args) -> int:
    root = _corpus(args.corpus)
    manifest = _load(root / "manifest.json", "corpus manifest")
    out_path = root / f"captions.{args.arm}.json"
    if out_path.exists() and not args.force:
        sys.exit(f"{out_path} exists — use --force to overwrite.")

    _verify_prompt_matches_live()
    cap = capture.Capturer(args.endpoint, model=args.model,
                           measurement_point=args.measurement_point)
    results = []
    for fr in manifest["frames"]:
        jpeg = (root / "frames" / f"{fr['id']}.jpg").read_bytes()
        # Each frame is distinct, so the multimodal cache misses naturally;
        # the label records that rather than implying a busted cache.
        turn = cap.chat(
            [{"role": "system", "content": manifest["system"]},
             {"role": "user", "content": [
                 {"type": "image_url",
                  "image_url": {"url": capture.encode_image(jpeg)}},
                 {"type": "text",
                  "text": f"Camera: {fr['entity_id']}. {manifest['question']}"},
             ]}],
            cell=f"G1-{args.arm}", cache_state="cold",
            meta={"frame": fr["id"], "camera": fr["camera"]},
            max_tokens=DESCRIBE_MAX_TOKENS, temperature=args.temperature)
        caption = turn.content().strip()
        leaks = gates.scan_response(turn.response)
        results.append({"frame": fr["id"], "camera": fr["camera"],
                        "caption": caption, "latency_s": turn.latency_s,
                        "leaks": len(leaks), "status": turn.status})
        flag = "  LEAK!" if leaks else ""
        print(f"  {fr['id']}: {turn.latency_s:5.2f}s  {caption[:70]!r}{flag}")

    out_path.write_text(json.dumps({
        "arm": args.arm, "model": args.model, "endpoint": args.endpoint,
        "temperature": args.temperature, "captions": results,
    }, indent=2), encoding="utf-8")
    cap.write(str(root / f"turns.{args.arm}.ndjson"))

    n_leak = sum(r["leaks"] for r in results)
    print(f"\n{len(results)} captions -> {out_path}")
    print(capture.summarize(cap.turns, f"G1-{args.arm} caption latency"))
    # G5 rides along for free: these are exactly the harness-captured
    # responses G5 must be scored on now that /trace is dead.
    print(f"G5 (free ride): {n_leak} leak(s) across {len(results)} captions"
          + ("  <-- G5 FAILS" if n_leak else ""))
    return 0


# ── pair ─────────────────────────────────────────────────────────────────
def cmd_pair(args) -> int:
    root = _corpus(args.corpus)
    a = _load(root / f"captions.{args.a}.json", f"arm '{args.a}'")
    b = _load(root / f"captions.{args.b}.json", f"arm '{args.b}'")
    by_frame_a = {c["frame"]: c for c in a["captions"]}
    by_frame_b = {c["frame"]: c for c in b["captions"]}
    shared = [f for f in by_frame_a if f in by_frame_b]
    if not shared:
        sys.exit("the two arms share no frames — were they run on the same corpus?")

    rng = random.Random(args.seed)
    pairs, key = [], []

    for frame in sorted(shared):
        ca, cb = by_frame_a[frame]["caption"], by_frame_b[frame]["caption"]
        flip = rng.random() < 0.5
        pairs.append({"pair_id": len(pairs), "frame": frame,
                      "A": cb if flip else ca, "B": ca if flip else cb})
        key.append({"pair_id": len(key), "frame": frame, "kind": "real",
                    "A_arm": args.b if flip else args.a,
                    "B_arm": args.a if flip else args.b})

    # Decoys: same arm on both sides. A judge who picks winners here is
    # responding to noise, and score/ reports that before any verdict.
    decoy_frames = rng.sample(shared, min(args.decoys, len(shared)))
    for frame in decoy_frames:
        src = by_frame_a if rng.random() < 0.5 else by_frame_b
        arm = args.a if src is by_frame_a else args.b
        caption = src[frame]["caption"]
        pairs.append({"pair_id": len(pairs), "frame": frame,
                      "A": caption, "B": caption})
        key.append({"pair_id": len(key), "frame": frame, "kind": "decoy",
                    "A_arm": arm, "B_arm": arm})

    rng.shuffle(pairs)
    order = {p["pair_id"]: i for i, p in enumerate(pairs)}
    key.sort(key=lambda k: order[k["pair_id"]])

    (root / "judging.json").write_text(json.dumps(
        {"pairs": pairs}, indent=2), encoding="utf-8")
    (root / "judging.key.json").write_text(json.dumps(
        {"a_arm": args.a, "b_arm": args.b, "seed": args.seed, "key": key},
        indent=2), encoding="utf-8")
    print(f"{len(pairs)} blinded pairs ({len(shared)} real + "
          f"{len(decoy_frames)} decoys) -> {root/'judging.json'}")
    print(f"answer key (do NOT open before judging) -> {root/'judging.key.json'}")
    return 0


# ── judge ────────────────────────────────────────────────────────────────
def cmd_judge(args) -> int:
    root = _corpus(args.corpus)
    pairs = _load(root / "judging.json", "judging file")["pairs"]
    verdict_path = root / "verdicts.json"
    verdicts = (json.loads(verdict_path.read_text(encoding="utf-8"))
                if verdict_path.exists() else {})

    print("For each pair: which caption describes the frame better?")
    print("  a = A better   b = B better   t = tie/indistinguishable")
    print("  ha / hb / hboth = also mark a caption as naming something "
          "that is NOT in the frame (hallucination)")
    print("  s = skip   q = save and quit\n")

    for p in pairs:
        pid = str(p["pair_id"])
        if pid in verdicts and not args.redo:
            continue
        print(f"--- pair {p['pair_id']}  (frame {p['frame']}) ---")
        print(f"  A: {p['A']}")
        print(f"  B: {p['B']}")
        print(f"  frame image: {root/'frames'/(p['frame'] + '.jpg')}")
        while True:
            try:
                raw = input("  verdict [a/b/t/ha/hb/hboth/s/q]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                raw = "q"
            if raw in ("a", "b", "t", "s", "q", "ha", "hb", "hboth"):
                break
            print("  (unrecognised)")
        if raw == "q":
            break
        if raw == "s":
            continue
        if raw.startswith("h"):
            hall = {"ha": ["A"], "hb": ["B"], "hboth": ["A", "B"]}[raw]
            verdicts.setdefault(pid, {})["hallucinated"] = hall
            print("  recorded hallucination; now the quality verdict:")
            while True:
                try:
                    q = input("  verdict [a/b/t]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    q = "t"
                if q in ("a", "b", "t"):
                    break
            verdicts[pid]["winner"] = q
        else:
            verdicts.setdefault(pid, {})["winner"] = raw
            verdicts[pid].setdefault("hallucinated", [])
        print()

    verdict_path.write_text(json.dumps(verdicts, indent=2), encoding="utf-8")
    print(f"{len(verdicts)}/{len(pairs)} judged -> {verdict_path}")
    return 0


# ── score ────────────────────────────────────────────────────────────────
def cmd_score(args) -> int:
    root = _corpus(args.corpus)
    key_doc = _load(root / "judging.key.json", "answer key")
    verdicts = _load(root / "verdicts.json", "verdicts")
    key = {k["pair_id"]: k for k in key_doc["key"]}
    a_arm, b_arm = key_doc["a_arm"], key_doc["b_arm"]

    real_wins = {a_arm: 0, b_arm: 0}
    real_ties = 0
    decoy_decided = decoy_total = 0
    hall_pairs = []

    for pid_s, v in verdicts.items():
        k = key.get(int(pid_s))
        if not k:
            continue
        winner = v.get("winner")
        if k["kind"] == "decoy":
            decoy_total += 1
            if winner in ("a", "b"):
                decoy_decided += 1
            continue
        if winner == "t" or winner is None:
            real_ties += 1
        elif winner == "a":
            real_wins[k["A_arm"]] += 1
        elif winner == "b":
            real_wins[k["B_arm"]] += 1

        hall = v.get("hallucinated") or []
        if hall:
            arms = {k["A_arm"] if s == "A" else k["B_arm"] for s in hall}
            hall_pairs.append(arms)

    cand, inc = args.candidate_arm, args.incumbent_arm
    if cand not in real_wins or inc not in real_wins:
        sys.exit(f"arms in the key are {sorted(real_wins)}; "
                 f"--candidate-arm/--incumbent-arm must name those.")

    print("=== G1 ambient caption quality ===")
    print(f"corpus  : {root}")
    print(f"arms    : candidate={cand}  incumbent={inc}")
    print(f"judged  : {len(verdicts)} pairs\n")

    # Decoy calibration FIRST — it decides whether the verdict means anything.
    if decoy_total:
        rate = decoy_decided / decoy_total
        print(f"decoy calibration: {decoy_decided}/{decoy_total} identical "
              f"pairs were given a winner ({rate:.0%})")
        if rate > args.decoy_threshold:
            print(f"  ⚠ ABOVE the {args.decoy_threshold:.0%} threshold. The "
                  "judge is separating identical captions, so the real-pair "
                  "verdict is measuring noise or style, not quality. "
                  "G1 IS NOT VALID — re-judge with clearer criteria.")
        else:
            print("  within threshold — the judge is not inventing differences.")
    else:
        print("decoy calibration: NO DECOYS JUDGED — the verdict is "
              "uncalibrated; treat the result as provisional.")
    print()

    result = gates.noninferiority_test(
        "G1 caption quality", candidate_wins=real_wins[cand],
        incumbent_wins=real_wins[inc], ties=real_ties,
        margin=args.margin, alpha=args.alpha)
    print(result.summary())

    # Hallucination is a separate, paired, one-sided comparison.
    h_cand = sum(1 for arms in hall_pairs if cand in arms and inc not in arms)
    h_inc = sum(1 for arms in hall_pairs if inc in arms and cand not in arms)
    h_both = sum(1 for arms in hall_pairs if {cand, inc} <= arms)
    h_res = gates.noninferiority_test(
        "G1 hallucinated objects (fewer is better)",
        candidate_wins=h_inc,     # incumbent hallucinated, candidate did not
        incumbent_wins=h_cand,
        ties=h_both, margin=args.margin, alpha=args.alpha)
    print(h_res.summary())
    print(f"  raw counts: candidate-only={h_cand} incumbent-only={h_inc} "
          f"both={h_both}")

    valid = (not decoy_total) or (decoy_decided / decoy_total <= args.decoy_threshold)
    overall = result.passed and h_res.passed and valid
    print(f"\nG1 VERDICT: {'PASS' if overall else 'FAIL'}"
          + ("" if valid else "  (decoy calibration failed — result not valid)"))
    return 0 if overall else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="freeze real camera frames")
    c.add_argument("--out", required=True)
    c.add_argument("--n", type=int, default=50)
    c.add_argument("--cameras", nargs="*")
    c.add_argument("--ha-url", default=HA_URL)
    c.add_argument("--ha-token", required=True)
    c.add_argument("--interval", type=float, default=0.0,
                   help="seconds between frames; spread the corpus over time")
    c.add_argument("--force", action="store_true")
    c.set_defaults(func=cmd_capture)

    r = sub.add_parser("run", help="caption the corpus with one arm")
    r.add_argument("corpus")
    r.add_argument("--arm", required=True, help="e.g. incumbent | candidate")
    r.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    r.add_argument("--model", default="qwen3-vl-30b")
    r.add_argument("--measurement-point", default="sidecar",
                   choices=sorted(capture.MEASUREMENT_POINTS))
    r.add_argument("--temperature", type=float, default=0.2)
    r.add_argument("--force", action="store_true")
    r.set_defaults(func=cmd_run)

    p = sub.add_parser("pair", help="build the blinded judging file")
    p.add_argument("corpus")
    p.add_argument("--a", default="incumbent")
    p.add_argument("--b", default="candidate")
    p.add_argument("--decoys", type=int, default=10)
    p.add_argument("--seed", type=int, default=20260816)
    p.set_defaults(func=cmd_pair)

    j = sub.add_parser("judge", help="record verdicts (human, blinded)")
    j.add_argument("corpus")
    j.add_argument("--redo", action="store_true")
    j.set_defaults(func=cmd_judge)

    s = sub.add_parser("score", help="unblind and apply the gate")
    s.add_argument("corpus")
    s.add_argument("--candidate-arm", default="candidate")
    s.add_argument("--incumbent-arm", default="incumbent")
    s.add_argument("--margin", type=float, default=0.10)
    s.add_argument("--alpha", type=float, default=0.05)
    s.add_argument("--decoy-threshold", type=float, default=0.30,
                   help="max fraction of identical pairs the judge may "
                        "declare a winner on before G1 is invalid")
    s.set_defaults(func=cmd_score)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
