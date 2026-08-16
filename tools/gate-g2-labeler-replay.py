#!/usr/bin/env python3
"""gate-g2-labeler-replay.py — acceptance gate G2 (docs/QWEN38-MIGRATION.md).

G2 asks whether the candidate produces valid labeler JSON at least as often
as the incumbent, on the SAME inputs, scored by McNemar.

⚠ The gate as originally written is not runnable, and this tool implements
the restatement. Verified 2026-08-16:

  * There is **no `request_json`** anywhere in the labeler — not a table,
    not a column. Raw model requests are never persisted.
  * There are **no "last-14-days live rows"**: the newest row in
    `videolabeler.db` is 2026-06-12.
  * The stored prelabels were produced by **`qwen2.5vl:32b`**, not the
    incumbent `qwen3-vl-30b`, so they are not an incumbent baseline either.

What IS recoverable is better than a request log: the labeler retains
**keyframes per analysis window** — 683 windows × 9 frames, the exact model
input. So G2 becomes a true paired replay: run BOTH arms over the same
windows with the live two-pass prompt, and compare validity. n=683 clears
the n>=500 bar. This is the migration doc's own "dormant fallback: replay
the same rows vs incumbent", made concrete.

**Parity with production.** The enums come from the live `ontology` module
in the Phase-0 archive (imported, not copied), and the JSON extraction
mirrors `vlm/schema.py:extract_json` including its fenced/inline/balanced
scan. `tools/test-qwen38-g2-replay.py` asserts the field set still matches
the live `Pass2Result`, so a labeler change fails a test instead of
silently making this gate measure the wrong contract. Production validates
with pydantic, which is not installed on the AI box; the validation here is
a faithful reimplementation held to the live contract by that test.

The live labeler uses `response_format: {"type": "json_object"}` — NOT
`json_schema`. Nothing is grammar-constrained today, so xgrammar's
unsupported-feature list does not apply to the labeler, and "schema
validity" means "the reply parses and conforms", checked in Python.

Usage:
  tools/gate-g2-labeler-replay.py run   --arm incumbent --out CORPUS
  tools/gate-g2-labeler-replay.py run   --arm candidate --out CORPUS
  tools/gate-g2-labeler-replay.py score CORPUS --candidate-arm candidate
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import qwen38_capture as capture  # noqa: E402
import qwen38_gates as gates  # noqa: E402

PHASE0 = pathlib.Path("/srv/data/eval/migration/phase0")
LIVE_LABELER = PHASE0 / "live-sources" / "video-labeler"
LABELER_DB = pathlib.Path("/opt/home-ai-voice/video-labeler-data/videolabeler.db")
KEYFRAME_CONTAINER = "hav-video-labeler"
DEFAULT_ENDPOINT = "http://127.0.0.1:8000/v1/chat/completions"

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def live_enums() -> dict:
    """Enums straight from the archived live ontology — never hand-copied."""
    sys.path.insert(0, str(LIVE_LABELER))
    try:
        from videolabeler import ontology  # noqa: PLC0415
    except ImportError as e:
        sys.exit(f"cannot import the live labeler ontology from {LIVE_LABELER}: {e}\n"
                 "Run tools/qwen38-phase0-archive.sh first.")
    return {
        "activity": tuple(ontology.ACTIVE_SET) + ("other",),
        "posture": tuple(ontology.POSTURE),
        "quality_flags": tuple(ontology.QUALITY_FLAGS),
    }


def live_prompts():
    """Import the LIVE prompt builders so the replay sends production's
    exact wording — the alternative is copying 100 lines of prompt text and
    watching it drift.

    `vlm/prompts.py` imports `vlm/schema.py`, which imports pydantic, which
    is not installed on the AI box. pydantic is used there ONLY to declare
    the Pass2Result model; the two names `prompts` actually reads
    (VLM_ACTIVITY_VALUES, VLM_POSTURE_VALUES) are plain tuples built from
    `ontology`. So a minimal stub lets the real prompt module load without
    touching a single character of prompt text. The stub validates nothing
    — this tool has its own validator, held to the live contract by
    tools/test-qwen38-g2-replay.py — and the enums are re-checked against
    `ontology` below so a bad stub cannot pass silently.
    """
    import types  # noqa: PLC0415
    if "pydantic" not in sys.modules:
        stub = types.ModuleType("pydantic")

        class _Base:                      # noqa: D401 - stand-in only
            def __init_subclass__(cls, **kw):
                super().__init_subclass__(**kw)

        def _field(*a, **kw):
            return None

        def _validator(*a, **kw):
            def deco(fn):
                return fn
            return deco

        stub.BaseModel = _Base
        stub.Field = _field
        stub.ValidationError = ValueError
        stub.field_validator = _validator
        sys.modules["pydantic"] = stub

    sys.path.insert(0, str(LIVE_LABELER))
    from videolabeler.vlm import prompts, schema  # noqa: PLC0415
    from videolabeler import ontology  # noqa: PLC0415

    # If the stub had perturbed anything, these would diverge.
    assert tuple(schema.VLM_ACTIVITY_VALUES) == tuple(ontology.ACTIVE_SET) + ("other",), \
        "live activity enum did not survive the pydantic stub"
    assert tuple(schema.VLM_POSTURE_VALUES) == tuple(ontology.POSTURE), \
        "live posture enum did not survive the pydantic stub"
    return prompts


def extract_json(text) -> dict:
    """Mirror of vlm/schema.py:extract_json — raw, then fenced, then a
    balanced scan from each '{'. The tolerance is part of the contract: a
    stricter parser here would fail replies production accepts."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty response, expected a JSON object")
    candidates = [text.strip()]
    m = _FENCE_RE.search(text)
    if m:
        candidates.insert(0, m.group(1).strip())
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        idx = text.find("{", idx + 1)
    raise ValueError("no JSON object found in response")


def validate_pass2(data: dict, enums: dict) -> None:
    """Faithful reimplementation of Pass2Result. Raises ValueError on the
    first problem, matching production's fail-fast behaviour."""
    if not isinstance(data, dict):
        raise ValueError("top level is not an object")
    for field in ("activity_primary", "posture", "uncertainty", "evidence"):
        if field not in data:
            raise ValueError(f"missing required field '{field}'")
    if data["activity_primary"] not in enums["activity"]:
        raise ValueError(f"'{data['activity_primary']}' is not an allowed activity_primary")
    if data["posture"] not in enums["posture"]:
        raise ValueError(f"'{data['posture']}' is not an allowed posture")
    if not isinstance(data["evidence"], str):
        raise ValueError("evidence must be a string")

    flags = data.get("quality_flags", [])
    if not isinstance(flags, list):
        raise ValueError("quality_flags must be a list")
    bad = [f for f in flags if f not in enums["quality_flags"]]
    if bad:
        raise ValueError(f"unknown quality_flags {bad}")

    unc = data["uncertainty"]
    if not isinstance(unc, dict):
        raise ValueError("uncertainty must be an object")
    for k in ("activity", "posture"):
        if k not in unc:
            raise ValueError(f"uncertainty.{k} missing")
        v = unc[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"uncertainty.{k} must be a number")
        if not 0.0 <= float(v) <= 1.0:
            raise ValueError(f"uncertainty.{k}={v} outside [0,1]")

    ts = data.get("evidence_timestamps", [])
    if not isinstance(ts, list) or any(
            not isinstance(t, (int, float)) or isinstance(t, bool) for t in ts):
        raise ValueError("evidence_timestamps must be a list of numbers")

    hint = data.get("activity_other_hint")
    if hint is not None and not isinstance(hint, str):
        raise ValueError("activity_other_hint must be a string or null")


def load_windows(limit: int | None) -> list[dict]:
    """Analysis windows that still have keyframes on disk."""
    if not LABELER_DB.exists():
        sys.exit(f"labeler DB not found at {LABELER_DB}")
    c = sqlite3.connect(f"file:{LABELER_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "select aw.id, aw.video_id, aw.start_s, aw.end_s, aw.prelabel_status,"
        "       v.filename "
        "from analysis_windows aw join videos v on v.id = aw.video_id "
        "where aw.prelabel_status like 'done:%' order by aw.id").fetchall()
    out = [dict(r) for r in rows]
    return out[:limit] if limit else out


def keyframes_for(window_id: str, video_id: str) -> list[bytes]:
    """Read a window's keyframes out of the labeler container (read-only)."""
    import subprocess
    path = f"/data/keyframes/{video_id}/{window_id}"
    ls = subprocess.run(
        ["docker", "exec", KEYFRAME_CONTAINER, "sh", "-c", f"ls {path} 2>/dev/null"],
        capture_output=True, timeout=30)
    names = sorted(n for n in ls.stdout.decode().split() if n.endswith(".jpg"))
    frames = []
    for n in names:
        r = subprocess.run(
            ["docker", "exec", KEYFRAME_CONTAINER, "cat", f"{path}/{n}"],
            capture_output=True, timeout=30)
        if r.returncode == 0 and len(r.stdout) > 500:
            frames.append(r.stdout)
    return frames


def cmd_run(args) -> int:
    enums = live_enums()
    prompts = live_prompts()

    out_root = pathlib.Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    result_path = out_root / f"g2.{args.arm}.json"
    if result_path.exists() and not args.force:
        sys.exit(f"{result_path} exists — use --force to overwrite.")

    windows = load_windows(args.limit)
    print(f"windows with completed prelabels: {len(windows)}")
    print(f"labeler PROMPT_VERSION: {prompts.PROMPT_VERSION}")
    if len(windows) < 500:
        print(f"⚠ n={len(windows)} is below the gate's n>=500 bar; the result "
              "will be reported but is under-powered.")

    cap = capture.Capturer(args.endpoint, model=args.model,
                           measurement_point=args.measurement_point)
    results = []
    for i, w in enumerate(windows, 1):
        frames = keyframes_for(w["id"], w["video_id"])
        if not frames:
            results.append({"window": w["id"], "status": "no_keyframes"})
            continue
        parts = [{"type": "image_url",
                  "image_url": {"url": capture.encode_image(f)}}
                 for f in frames[:args.max_frames]]
        room = prompts.room_hint(w["filename"])

        # Pass 1 — open description.
        t1 = cap.chat(prompts.pass1_messages(room, w["start_s"], w["end_s"],
                                             None, parts),
                      cell=f"G2-{args.arm}-pass1", cache_state="cold",
                      meta={"window": w["id"]},
                      max_tokens=args.max_tokens, temperature=args.temperature)
        if t1.status != "ok":
            results.append({"window": w["id"], "status": "pass1_error",
                            "error": t1.error})
            continue

        # Pass 2 — strict JSON. This is what G2 scores.
        times = [round(w["start_s"] + j, 1) for j in range(len(parts))]
        t2 = cap.chat(prompts.pass2_messages(t1.content(), room, w["start_s"],
                                             w["end_s"], times, parts),
                      cell=f"G2-{args.arm}-pass2", cache_state="cold",
                      meta={"window": w["id"]},
                      max_tokens=args.max_tokens, temperature=args.temperature,
                      response_format={"type": "json_object"})
        rec = {"window": w["id"], "status": t2.status,
               "latency_s": round(t2.latency_s, 3),
               "leaks": len(gates.scan_response(t2.response))}
        if t2.status != "ok":
            rec.update(valid=False, error=t2.error)
        else:
            try:
                validate_pass2(extract_json(t2.content()), enums)
                rec.update(valid=True)
            except ValueError as e:
                rec.update(valid=False, error=str(e)[:200],
                           reply=t2.content()[:300])
        results.append(rec)
        if i % 25 == 0 or i == len(windows):
            ok = sum(1 for r in results if r.get("valid"))
            print(f"  {i}/{len(windows)}  valid={ok}", flush=True)

    result_path.write_text(json.dumps(
        {"arm": args.arm, "model": args.model, "endpoint": args.endpoint,
         "prompt_version": prompts.PROMPT_VERSION, "results": results},
        indent=2), encoding="utf-8")
    cap.write(str(out_root / f"g2.turns.{args.arm}.ndjson"))

    scored = [r for r in results if "valid" in r]
    ok = sum(1 for r in scored if r["valid"])
    leaks = sum(r.get("leaks", 0) for r in results)
    print(f"\nvalidity: {ok}/{len(scored)} "
          f"({ok / len(scored) * 100:.1f}%)" if scored else "no scored windows")
    print(f"G5 (free ride): {leaks} leak(s)" + ("  <-- G5 FAILS" if leaks else ""))
    print(capture.summarize([t for t in cap.turns if t.cell.endswith("pass2")],
                            f"G2-{args.arm} pass2 latency"))
    print(f"-> {result_path}")
    return 0


def cmd_score(args) -> int:
    root = pathlib.Path(args.corpus)
    def load(arm):
        p = root / f"g2.{arm}.json"
        if not p.exists():
            sys.exit(f"missing {p} — run the '{arm}' arm first.")
        return json.loads(p.read_text(encoding="utf-8"))

    a, b = load(args.incumbent_arm), load(args.candidate_arm)
    va = {r["window"]: r["valid"] for r in a["results"] if "valid" in r}
    vb = {r["window"]: r["valid"] for r in b["results"] if "valid" in r}
    shared = sorted(set(va) & set(vb))
    if not shared:
        sys.exit("the two arms share no scored windows")

    print("=== G2 labeler JSON validity ===")
    print(f"incumbent : {args.incumbent_arm} ({a.get('model')})")
    print(f"candidate : {args.candidate_arm} ({b.get('model')})")
    print(f"prompt_version: {a.get('prompt_version')} / {b.get('prompt_version')}")
    if a.get("prompt_version") != b.get("prompt_version"):
        print("  ⚠ PROMPT VERSIONS DIFFER — the arms are not comparable.")
    print(f"paired windows: {len(shared)}"
          + ("" if len(shared) >= 500 else "   ⚠ below the n>=500 bar"))
    print(f"  incumbent valid: {sum(va[w] for w in shared)}/{len(shared)}")
    print(f"  candidate valid: {sum(vb[w] for w in shared)}/{len(shared)}\n")

    res = gates.mcnemar_from_pairs(
        "G2 labeler validity", ((va[w], vb[w]) for w in shared),
        margin=args.margin, alpha=args.alpha)
    print(res.summary())
    print(f"\nG2 VERDICT: {'PASS' if res.passed else 'FAIL'}")
    return 0 if res.passed else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="replay the labeler over stored keyframes")
    r.add_argument("--arm", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    r.add_argument("--model", default="qwen3-vl-30b")
    r.add_argument("--measurement-point", default="sidecar",
                   choices=sorted(capture.MEASUREMENT_POINTS))
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--max-frames", type=int, default=8,
                   help="live limit-mm-per-prompt is image:8")
    r.add_argument("--max-tokens", type=int, default=700)
    r.add_argument("--temperature", type=float, default=0.2)
    r.add_argument("--force", action="store_true")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("score", help="McNemar over the two arms")
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
