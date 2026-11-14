"""Synthetic den feature caches for the V-JEPA tuning tools (AL-T, #327).

No real data, no images, no model: a deterministic stand-in with the
structure of vjepa-den-cache-1 so the trainer, evaluator and tests run on the
workstation. It is NOT evidence about den accuracy.

Structure that matters for the levers being compared:
  * sessions with their own offset (camera/day shift) -> leave-one-session-out
    is harder than a random split;
  * the class signal sits in a few "hand-region" tokens only, so mean pooling
    dilutes it and attentive pooling can find it;
  * activities persist for tens of seconds, so causal smoothing helps;
  * transition has larger temporal token differences (motion);
  * weak labels are the truth with a configurable flip rate.

Usage (writes only to --out):
  python vjepa_tune_synth.py --out DIR [--sessions 6] [--clips 120] [--dim 64] [--grid 4 3 3]
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

import vjepa_tune_probe as probe

CACHE_SCHEMA = "vjepa-den-cache-1"


def make(*, sessions=6, clips=120, dim=64, grid=(4, 3, 3), stride_s=2.0, signal=0.9, hot_tokens=2,
         session_shift=0.6, weak_flip=0.15, seed=0):
    """(header, rows, tokens float16 [N, T*H*W, D])."""
    rng = np.random.default_rng(seed)
    t, h, w = grid
    spatial = h * w
    classes = probe.CLASSES
    directions = rng.normal(0, 1, (len(classes), dim))
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    hot = rng.choice(spatial, size=min(hot_tokens, spatial), replace=False)
    rows, blocks = [], []
    for s in range(sessions):
        sid = hashlib.sha256(b"synthetic-session-%d" % s).hexdigest()[:32]
        offset = rng.normal(0, session_shift, dim)
        start = 1_800_000_000.0 + 86400 * s
        cls, remaining = None, 0
        for i in range(clips):
            if remaining <= 0:
                cls = int(rng.integers(len(classes)))
                remaining = int(rng.integers(5, 25)) if classes[cls] != "transition" else int(rng.integers(1, 4))
            remaining -= 1
            base = rng.normal(0, 1, (t, spatial, dim)) + offset
            for k in hot:
                base[:, k] += signal * 3 * directions[cls]
            if classes[cls] == "transition":
                base += np.arange(t)[:, None, None] * rng.normal(0, 0.8, (1, spatial, dim))
            blocks.append(base.reshape(t * spatial, dim).astype(np.float16))
            weak = cls if rng.random() > weak_flip else int(rng.integers(len(classes)))
            anchor = start + i * stride_s
            clip_id = hashlib.sha256(("%s|%.3f|base" % (sid, anchor)).encode()).hexdigest()[:24]
            rows.append({"row": len(rows), "clip_id": clip_id, "session_id": sid, "anchor_at": anchor,
                         "anchor_rel_s": i * stride_s, "span_variant": "3.5s_8f", "crop": "union",
                         "single_person": True, "day": "synthetic-%d" % s,
                         "label": {"gold": classes[cls] if i % 3 == 0 else None, "owner": classes[cls],
                                   "weak": classes[weak], "weak_independent": classes[weak] == "computer_work"},
                         "baseline": {"reading": float(np.clip(0.5 + (0.25 if classes[cls] == "reading" else -0.1)
                                                               + rng.normal(0, 0.2), 0, 1))}})
    header = {"schema": CACHE_SCHEMA, "synthetic": True, "grid": [t, h, w], "dim": dim, "count": len(rows),
              "model_version": "synthetic", "artifact_sha256": None, "classes": list(classes)}
    return header, rows, np.stack(blocks)


def write(directory, header, rows, tokens):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    np.save(directory / "tokens.npy", tokens)
    with (directory / "index.jsonl").open("w", encoding="utf-8") as out:
        out.write(json.dumps(header, sort_keys=True) + "\n")
        for row in rows:
            out.write(json.dumps(row, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--sessions", type=int, default=6)
    parser.add_argument("--clips", type=int, default=120)
    parser.add_argument("--dim", type=int, default=64)
    parser.add_argument("--grid", type=int, nargs=3, default=[4, 3, 3])
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    header, rows, tokens = make(sessions=args.sessions, clips=args.clips, dim=args.dim, grid=tuple(args.grid),
                                seed=args.seed)
    write(args.out, header, rows, tokens)
    print("wrote %d synthetic clips to %s" % (len(rows), args.out))


if __name__ == "__main__":
    main()
