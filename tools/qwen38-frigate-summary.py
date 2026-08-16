#!/usr/bin/env python3
"""qwen38-frigate-summary.py — summarise Frigate's RUNNING config.

Why this exists rather than a grep over `config.yaml`: this host has two
Frigate addon config directories, and the one a naive Phase-0 pass reads
first is three months stale. Reading it yields the wrong camera list and
the wrong face-recognition posture — the stale file mentions `face` zero
times while the running service has face recognition enabled on four of
five cameras. Ask the service, not the filesystem.

Input is the JSON body of Frigate's `GET /api/config`.

Usage:
  curl -s http://<frigate>:5000/api/config -o cfg.json
  tools/qwen38-frigate-summary.py cfg.json
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:  # noqa: BLE001 - any failure is "no evidence"
        print(f"could not read the running Frigate config: {e}")
        print("NOTE: absence of evidence is not evidence of absence — do not "
              "record a Frigate finding without this.")
        return 1

    cams = d.get("cameras") or {}
    rec_global = d.get("record") or {}
    face_global = d.get("face_recognition") or {}

    print(f"frigate cameras (running): {len(cams)}")
    for cam, v in sorted(cams.items()):
        rec = v.get("record") or {}
        snap = v.get("snapshots") or {}
        face = v.get("face_recognition") or {}
        retain = (rec.get("retain") or {}).get("days")
        print(f"  {cam:14s} record={rec.get('enabled')} "
              f"retain_days={retain} "
              f"snapshots={snap.get('enabled')} "
              f"face_recognition={face.get('enabled')} "
              f"camera_enabled={v.get('enabled')}")

    print()
    print(f"global record.enabled : {rec_global.get('enabled')}")
    for key in ("alerts", "detections"):
        blk = rec_global.get(key) or {}
        ret = (blk.get("retain") or {})
        if blk:
            print(f"  {key}: pre={blk.get('pre_capture')} "
                  f"post={blk.get('post_capture')} "
                  f"retain_days={ret.get('days')} mode={ret.get('mode')}")

    print()
    print("global face_recognition:", json.dumps(face_global))
    for key in ("semantic_search", "lpr", "genai"):
        blk = d.get(key)
        if isinstance(blk, dict):
            print(f"{key} enabled: {blk.get('enabled')}")

    # A camera configured but not streaming is invisible to a config read
    # and decisive for any capacity estimate, so say it out loud.
    print()
    print("NOTE: `enabled: true` means CONFIGURED, not STREAMING. Cross-check "
          "GET /api/stats for camera_fps == 0 before sizing storage or a "
          "parsing lane off this list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
