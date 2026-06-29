"""Frigate face-recognition bench analyzer (Addendum 25).

Reads all option-*/ directories produced by run-bench.sh and emits a
markdown report comparing per-option:
  - p50 / p95 face_recognition_speed (ms)
  - p50 / p95 image_embedding_speed (ms)
  - mean embeddings_manager CPU%
  - mean intel-vaapi GPU%
  - aggregate detection_fps (sanity check object detector unaffected)
  - recognition_rate, false_positive_rate, unknown_rate, mean_confidence
  - per-camera recognition breakdown
  - environmental drift check (if A run multiple times)

Run:
    py -3 tools/frigate-bench/analyze-bench.py
    py -3 tools/frigate-bench/analyze-bench.py --target marcelo
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

# Cameras tagged as INDOOR — included in the strict "only Marcelo home"
# recognition rate. The driveway is reported separately because random
# passersby will trigger false-positive person events that aren't
# really model errors.
INDOOR_CAMERAS = {"kitchen", "living_room", "dining_room", "workshop"}
OUTDOOR_CAMERAS = {"driveway"}

# Parse sub_label of the form "marcelo-0.94" → ("marcelo", 0.94)
_SUBLABEL_RE = re.compile(r"^(?P<name>[a-zA-Z\s]+?)(?:-(?P<score>[\d.]+))?$")


def pctile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def fmt_ms(v: float | None) -> str:
    return "—" if v is None else f"{v:6.1f}"


def fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v:5.1f}%"


def fmt_rate(num: int, denom: int) -> str:
    if denom == 0:
        return "n=0"
    return f"{(num / denom) * 100:5.1f}% ({num}/{denom})"


def parse_sublabel(raw):
    """Frigate sub_label is sometimes a string, sometimes a [name, score]
    list, sometimes None / "Unknown". Normalize to (name_lower, score)."""
    if raw is None:
        return (None, None)
    if isinstance(raw, list) and len(raw) >= 1:
        name = (raw[0] or "").strip().lower()
        score = float(raw[1]) if len(raw) > 1 and raw[1] is not None else None
        return (name or None, score)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("", "unknown", "none"):
            return (None, None)
        m = _SUBLABEL_RE.match(s)
        if not m:
            return (s, None)
        return (m.group("name").strip() or None,
                float(m.group("score")) if m.group("score") else None)
    return (None, None)


def analyze_stats(stats_files: list[Path]) -> dict:
    """Aggregate /api/stats samples for one option."""
    face_speeds = []
    img_speeds = []
    emb_cpu = []
    igpu = []
    det_fps = []
    detector_speed = []

    for p in stats_files:
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        emb = d.get("embeddings") or {}
        if "face_recognition_speed" in emb:
            face_speeds.append(float(emb["face_recognition_speed"]))
        if "image_embedding_speed" in emb:
            img_speeds.append(float(emb["image_embedding_speed"]))
        # Find embeddings_manager CPU%
        cpus = d.get("cpu_usages") or {}
        for pid, info in cpus.items():
            cmd = (info or {}).get("cmdline", "")
            if "embeddings_manager" in cmd:
                try:
                    emb_cpu.append(float(info.get("cpu", 0)))
                except (ValueError, TypeError):
                    pass
                break
        gpus = d.get("gpu_usages") or {}
        intel = gpus.get("intel-vaapi") or {}
        gpu_str = (intel.get("gpu") or "0%").rstrip("%")
        try:
            igpu.append(float(gpu_str))
        except ValueError:
            pass
        if "detection_fps" in d:
            det_fps.append(float(d["detection_fps"]))
        for det_name, det_info in (d.get("detectors") or {}).items():
            if "inference_speed" in (det_info or {}):
                try:
                    detector_speed.append(float(det_info["inference_speed"]))
                except (ValueError, TypeError):
                    pass

    return {
        "samples": len(stats_files),
        "face_p50_ms": pctile(face_speeds, 0.5),
        "face_p95_ms": pctile(face_speeds, 0.95),
        "img_p50_ms": pctile(img_speeds, 0.5),
        "img_p95_ms": pctile(img_speeds, 0.95),
        "emb_cpu_mean": mean(emb_cpu),
        "intel_igpu_mean": mean(igpu),
        "detection_fps_mean": mean(det_fps),
        "detector_inf_p50": pctile(detector_speed, 0.5),
    }


def analyze_events(events_file: Path, target: str) -> dict:
    """Compute recognition stats for one option.

    'only Marcelo home' methodology: every person event during the
    capture window should be tagged as `target` (default "marcelo").
    Anything else = miss.
    """
    target = target.lower()
    try:
        data = json.loads(events_file.read_text())
    except Exception:
        return {"error": "no events file"}

    # Frigate /api/events returns a list directly OR a dict with "events".
    events = data if isinstance(data, list) else data.get("events", [])

    indoor_total = 0
    indoor_target = 0
    indoor_other = 0
    indoor_unknown = 0
    outdoor_total = 0
    outdoor_target = 0
    outdoor_other = 0
    outdoor_unknown = 0
    target_scores: list[float] = []
    per_cam: dict[str, dict] = {}

    for ev in events:
        cam = ev.get("camera") or ""
        sub = ev.get("sub_label")
        name, score = parse_sublabel(sub)
        bucket = per_cam.setdefault(cam, {
            "total": 0, "target": 0, "other": 0, "unknown": 0,
            "scores": [],
        })
        bucket["total"] += 1
        if cam in INDOOR_CAMERAS:
            indoor_total += 1
        elif cam in OUTDOOR_CAMERAS:
            outdoor_total += 1
        if name is None:
            bucket["unknown"] += 1
            if cam in INDOOR_CAMERAS:
                indoor_unknown += 1
            elif cam in OUTDOOR_CAMERAS:
                outdoor_unknown += 1
        elif name == target:
            bucket["target"] += 1
            if score is not None:
                bucket["scores"].append(score)
                target_scores.append(score)
            if cam in INDOOR_CAMERAS:
                indoor_target += 1
            elif cam in OUTDOOR_CAMERAS:
                outdoor_target += 1
        else:
            bucket["other"] += 1
            if cam in INDOOR_CAMERAS:
                indoor_other += 1
            elif cam in OUTDOOR_CAMERAS:
                outdoor_other += 1

    return {
        "indoor": {
            "total": indoor_total,
            "target": indoor_target,
            "other": indoor_other,
            "unknown": indoor_unknown,
            "recognition_rate": (indoor_target / indoor_total) if indoor_total else None,
            "false_positive_rate": (indoor_other / indoor_total) if indoor_total else None,
            "unknown_rate": (indoor_unknown / indoor_total) if indoor_total else None,
        },
        "outdoor": {
            "total": outdoor_total,
            "target": outdoor_target,
            "other": outdoor_other,
            "unknown": outdoor_unknown,
        },
        "target_score_mean": mean(target_scores),
        "target_score_p50": pctile(target_scores, 0.5),
        "per_camera": per_cam,
    }


def render_report(results: dict, target: str) -> str:
    lines: list[str] = []
    lines.append(f"# Frigate face-recognition bench — REPORT\n")
    lines.append(
        f"Target identity: `{target}` (only person at home during bench).\n"
    )
    lines.append("## Per-option summary\n")
    lines.append(
        "| Option | face p50 / p95 ms | CLIP p50 ms | emb CPU% | iGPU% | det fps | "
        "indoor reco | false-pos | unknown | mean conf |\n"
        "|---|---|---|---|---|---|---|---|---|---|"
    )
    for label in sorted(results.keys()):
        r = results[label]
        s = r["stats"]
        e = r["events"]
        indoor = e.get("indoor", {})
        reco = indoor.get("recognition_rate")
        fp = indoor.get("false_positive_rate")
        un = indoor.get("unknown_rate")
        lines.append(
            f"| **{label}** | "
            f"{fmt_ms(s['face_p50_ms'])} / {fmt_ms(s['face_p95_ms'])} | "
            f"{fmt_ms(s['img_p50_ms'])} | "
            f"{fmt_pct(s['emb_cpu_mean'])} | "
            f"{fmt_pct(s['intel_igpu_mean'])} | "
            f"{fmt_ms(s['detection_fps_mean'])} | "
            f"{fmt_rate(indoor.get('target', 0), indoor.get('total', 0))} | "
            f"{fmt_rate(indoor.get('other', 0), indoor.get('total', 0))} | "
            f"{fmt_rate(indoor.get('unknown', 0), indoor.get('total', 0))} | "
            f"{fmt_ms(e.get('target_score_mean') * 100 if e.get('target_score_mean') else None)} |"
        )

    lines.append("\n## Per-camera recognition rate (indoor cameras)\n")
    cams = sorted(INDOOR_CAMERAS) + sorted(OUTDOOR_CAMERAS)
    header = "| Option | " + " | ".join(cams) + " |"
    sep = "|---|" + "|".join(["---"] * len(cams)) + "|"
    lines.append(header)
    lines.append(sep)
    for label in sorted(results.keys()):
        r = results[label]
        per = r["events"].get("per_camera", {})
        cells = []
        for cam in cams:
            b = per.get(cam, {"total": 0, "target": 0})
            cells.append(fmt_rate(b.get("target", 0), b.get("total", 0)))
        lines.append(f"| **{label}** | " + " | ".join(cells) + " |")

    # Environmental drift check — compare A vs A2 if both ran
    if "A" in results and "A2" in results:
        a_reco = results["A"]["events"].get("indoor", {}).get("recognition_rate") or 0
        a2_reco = results["A2"]["events"].get("indoor", {}).get("recognition_rate") or 0
        delta = abs(a_reco - a2_reco)
        lines.append(f"\n## Environmental drift\n")
        lines.append(f"A run #1 indoor reco: {a_reco*100:.1f}%")
        lines.append(f"A run #2 indoor reco: {a2_reco*100:.1f}%")
        lines.append(f"Delta: **{delta*100:.1f} percentage points**")
        if delta > 0.10:
            lines.append(
                "\n⚠ **WARNING** — drift > 10 pp. Lighting/environment "
                "shifted during the bench window. Re-run during a more "
                "stable window."
            )
        else:
            lines.append("\n✓ Drift within tolerance (≤ 10 pp).")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", default=str(Path(__file__).parent),
        help="Bench directory (default: this script's dir)",
    )
    parser.add_argument(
        "--target", default="marcelo",
        help="Expected sub_label for every person event (default: marcelo)",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output report path (default: <root>/REPORT.md)",
    )
    args = parser.parse_args()

    root = Path(args.root)
    out_path = Path(args.out) if args.out else (root / "REPORT.md")

    results: dict[str, dict] = {}
    for opt_dir in sorted(root.glob("option-*")):
        label = opt_dir.name.replace("option-", "")
        stats_files = sorted(opt_dir.glob("stats-*.json"))
        events_file = opt_dir / "events.json"
        if not stats_files:
            print(f"[analyze] skipping {label}: no stats samples")
            continue
        results[label] = {
            "stats": analyze_stats(stats_files),
            "events": (
                analyze_events(events_file, args.target)
                if events_file.exists()
                else {"error": "no events file"}
            ),
        }
        print(f"[analyze] {label}: {results[label]['stats']['samples']} stats samples")

    if not results:
        print("[analyze] no option-*/ directories found under", root)
        return 1

    report = render_report(results, args.target)
    out_path.write_text(report, encoding="utf-8")
    print(f"[analyze] wrote {out_path}")
    print()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
