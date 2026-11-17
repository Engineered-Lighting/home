"""Encode exported den clips with the pinned frozen V-JEPA 2.1 encoder (AL-T, #327).

LA host only (EngineeredLightingServer1), run once per owner-approved tuning
run inside a transient, resource-bounded systemd unit with the probe venv's
interpreter (~/vjepa-probe-0/.venv/bin/python -I -B), from a staged directory
that also holds vjepa_pins.py and vjepa_worker.py. It is not part of the Lab
worker's runtime revision and is never launched by the lifecycle supervisor.

It reuses the worker's own checks and loader: verify_artifacts (every pinned
probe/Meta source, head and the NTU head), gpu_admission immediately before
any weights load, and vjepa_worker.ProbeModel (the probe's crop code, the
pinned encoder and the six pinned heads plus the NTU head). Nothing is
trained here and no GPU memory is held after the process exits.

Per clip (vjepa_tune_export's clips.jsonl and frames/), per crop:
  union : the exported person union box (the live crop producer)
  hands : a box around the wrists and elbows of the single matched pose,
          side max(hands_min_side_px, hands_box_scale * extent); skipped when
          fewer than four frames have both a wrist and an elbow
raw tokens [2304, 768] (grid 4 x 24 x 24) are average-pooled to pool_grid
(default 4 x 12 x 12) and stored float16. The pinned heads run on the same raw
tokens, so the baseline (posture, reading, NTU family sums) is scored on the
identical clips.

Output (--out, new, 0700): tokens.npy [N, T*H*W, 768] float16 and index.jsonl
(vjepa-den-cache-1). Private biometric features: never copied off LA.

Usage (LA, inside the bounded unit of the run proposal):
  python -I -B vjepa_den_encode.py --clips DIR --out DIR [--config CONFIG.json]
                                   [--crops union hands] [--max-clips 4000] [--max-runtime-s 3000]
"""
import argparse
import json
import math
import os
from pathlib import Path
import socket
import sys
import time

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vjepa_pins as pins  # noqa: E402

CACHE_SCHEMA = "vjepa-den-cache-1"
CLIPS_SCHEMA = "vjepa-den-clips-1"
HOST = "EngineeredLightingServer1"
DEFAULTS = {"crops": ["union", "hands"], "pool_grid": [4, 12, 12], "hands_box_scale": 2.2, "hands_min_side_px": 96}


def hands_box(joints_per_frame, *, scale, min_side, size=pins.IMAGE_SIZE, min_frames=4):
    """A square source-pixel box around wrists and elbows over the clip, or None."""
    points, frames = [], 0
    for joints in joints_per_frame:
        wrists = [joints[k] for k in ("left_wrist", "right_wrist") if k in joints]
        elbows = [joints[k] for k in ("left_elbow", "right_elbow") if k in joints]
        if wrists and elbows:
            frames += 1
            points.extend(wrists + elbows)
    if frames < min_frames:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    side = max(min_side, scale * max(max(xs) - min(xs), max(ys) - min(ys)))
    side = min(side, size[0], size[1])
    x0 = min(max(0.0, cx - side / 2), size[0] - side)
    y0 = min(max(0.0, cy - side / 2), size[1] - side)
    return [x0, y0, x0 + side, y0 + side]


def pool_grid(raw, grid, target):
    """[T*H*W, D] -> [t*h*w, D] by block averaging (T, H, W divisible by the target)."""
    import numpy as np
    t, h, w = grid
    pt, ph, pw = target
    if t % pt or h % ph or w % pw:
        raise ValueError("pool grid must divide the raw grid")
    x = np.asarray(raw, dtype=np.float32).reshape(pt, t // pt, ph, h // ph, pw, w // pw, -1)
    return x.mean(axis=(1, 3, 5)).reshape(pt * ph * pw, -1)


def read_clips(directory):
    lines = (Path(directory) / "clips.jsonl").read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    if header.get("schema") != CLIPS_SCHEMA:
        raise SystemExit("clips schema %s required" % CLIPS_SCHEMA)
    return header, [json.loads(line) for line in lines[1:] if line.strip()]


class TorchAdapter:
    """ProbeModel behind two calls: encode(frames, box) -> raw [2304, 768] numpy,
    heads(raw, single_person) -> the worker's posture / reading / NTU outputs."""

    def __init__(self, model):
        self.model = model

    def encode(self, frames, box):
        m = self.model
        crops = m.crop_frames(frames, [list(box)] * len(frames), pins.PROFILE)
        raw = m.encode_crop_tokens(m.encoder, crops, pins.PROFILE, m.device)
        m.torch.cuda.synchronize()
        self._raw = raw
        return raw[0].float().cpu().numpy()

    def heads(self, raw, single_person):
        import vjepa_worker as worker
        m, torch = self.model, self.model.torch
        tokens = self._raw.half().float()
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            posture = torch.stack([h(tokens).float() for h in m.heads["posture"]]).mean(0).softmax(-1)[0]
            activity = torch.stack([h(tokens).float() for h in m.heads["activity"]]).mean(0).sigmoid()[0]
            ntu = m.ntu(self._raw).float().softmax(-1)[0] if single_person else None
        return {"posture": dict(zip(pins.POSTURES, (float(p) for p in posture.cpu().tolist()))),
                "reading": float(activity[pins.ACTIVITIES.index("reading")]),
                "activity": dict(zip(pins.ACTIVITIES, (float(p) for p in activity.cpu().tolist()))),
                "zero_shot": worker.ntu_zero_shot(ntu.cpu().tolist()) if ntu is not None else None}


def encode(adapter, clips_dir, clips, out, config, *, max_clips, max_runtime_s, clock=time.monotonic,
           thermal=None, log=print):
    """Write the cache. Returns (rows written, skip counts)."""
    import numpy as np
    import vjepa_worker as worker
    started = clock()
    grid, target = pins.PROFILE["raw_grid"], config["pool_grid"]
    jobs = []
    for clip in clips[:max_clips]:
        for crop in config["crops"]:
            box = clip["box"] if crop == "union" else hands_box(
                clip.get("joints") or [], scale=config["hands_box_scale"], min_side=config["hands_min_side_px"])
            jobs.append((clip, crop, box))
    out = Path(out)
    tokens = np.lib.format.open_memmap(out / "tokens.npy", mode="w+", dtype=np.float16,
                                       shape=(len(jobs), int(np.prod(target)), 768))
    skipped, rows = {}, []
    for clip, crop, box in jobs:
        if clock() - started > max_runtime_s:
            skipped["runtime_limit"] = skipped.get("runtime_limit", 0) + 1
            continue
        if box is None:
            skipped["hands_unavailable"] = skipped.get("hands_unavailable", 0) + 1
            continue
        if thermal is not None and len(rows) % 100 == 0:
            thermal()
        images = [(Path(clips_dir) / "frames" / (f["media_id"] + ".jpg")).read_bytes() for f in clip["frames"]]
        frames = worker.decode_frames(images)
        raw = adapter.encode(frames, box)
        if raw.shape != (math.prod(grid), 768):
            raise ValueError("unexpected token geometry")
        tokens[len(rows)] = pool_grid(raw, grid, target).astype(np.float16)
        rows.append({"row": len(rows), "clip_id": clip["clip_id"], "session_id": clip["session_id"],
                     "day": clip.get("day"), "anchor_at": clip["anchor_at"], "anchor_rel_s": clip.get("anchor_rel_s"),
                     "span_variant": clip["span_variant"], "crop": crop, "single_person": clip["single_person"],
                     "label": clip.get("label") or {}, "baseline": adapter.heads(raw, clip["single_person"])})
        if len(rows) % 200 == 0:
            log("encoded %d / %d" % (len(rows), len(jobs)))
    tokens.flush()
    del tokens
    if len(rows) != len(jobs):  # shrink to the rows actually written
        full = np.load(out / "tokens.npy", mmap_mode="r")
        kept = np.array(full[:len(rows)])
        del full
        np.save(out / "tokens.npy", kept)
    header = {"schema": CACHE_SCHEMA, "synthetic": False, "grid": list(target), "dim": 768, "count": len(rows),
              "model_version": pins.MODEL_VERSION, "artifact_sha256": pins.ARTIFACT_SHA256,
              "input_profile": pins.INPUT_PROFILE, "raw_grid": list(grid), "crops": config["crops"],
              "classes": ["reading", "computer_work", "phone_use", "resting", "transition"]}
    with (out / "index.jsonl").open("w", encoding="utf-8") as index:
        index.write(json.dumps(header, sort_keys=True) + "\n")
        for row in rows:
            index.write(json.dumps(row, sort_keys=True) + "\n")
    for name in ("tokens.npy", "index.jsonl"):
        os.chmod(out / name, 0o600)
    return len(rows), skipped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clips", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--config")
    parser.add_argument("--crops", nargs="+")
    parser.add_argument("--max-clips", type=int, default=4000)
    parser.add_argument("--max-runtime-s", type=float, default=3000)
    args = parser.parse_args(argv)
    if sys.platform != "linux" or socket.gethostname() != HOST:
        raise SystemExit("Explicit LA host required")
    config = dict(DEFAULTS)
    if args.config:
        config.update(json.loads(Path(args.config).read_text(encoding="utf-8")).get("encode", {}))
    if args.crops:
        config["crops"] = args.crops
    out = Path(args.out)
    if out.exists():
        raise SystemExit("--out must be a new directory")
    os.umask(0o077)
    out.mkdir(mode=0o700, parents=True)
    _, clips = read_clips(args.clips)
    import vjepa_worker as worker
    worker.verify_artifacts()
    worker.gpu_admission()  # immediately before any weights load
    adapter = TorchAdapter(worker.ProbeModel())
    written, skipped = encode(adapter, args.clips, clips, out, config, max_clips=args.max_clips,
                              max_runtime_s=args.max_runtime_s, thermal=worker.gpu_admission)
    print(json.dumps({"written": written, "skipped": skipped}))


if __name__ == "__main__":
    main(sys.argv[1:])
