#!/usr/bin/env python3
"""analyze-flutter.py — post-run analyzer for probe-lighting-latency.py output.

Reads tools/reports/lighting-latency-*.jsonl and computes metrics the
harness's own report doesn't include:

  1. Brightness FLUTTER count per light — direction-reversals in the
     brightness trajectory (down→up→down or up→down→up patterns),
     smoothed to ignore <5% noise.
  2. Service-call THRASH count per entity — clusters of call_service
     events landing within 3s for the SAME entity, at different
     brightness targets. Each cluster of N calls contributes N-1
     thrashes.
  3. Per-light brightness trace dumps for the top-flutter offenders
     so user can SEE the up/down/up sequence with timestamps.

Outputs:
  - Appends a "Flutter analysis" section to the existing
    lighting-latency-<ts>.md report (in place)
  - OR writes a sidecar file lighting-latency-<ts>-flutter.md if
    --separate is passed

Usage:
  py -3 tools/analyze-flutter.py                       # latest run
  py -3 tools/analyze-flutter.py --jsonl <path>        # specific run
  py -3 tools/analyze-flutter.py --separate            # don't edit the md
  py -3 tools/analyze-flutter.py --ignore-pct 8        # bigger smoothing
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO / "tools" / "reports"


def _bri_to_pct(bri_0_255: Any) -> float | None:
    """HA stores brightness as 0..255 in attributes. Convert to 0..100."""
    if bri_0_255 is None:
        return None
    try:
        v = int(bri_0_255)
        if v < 0:
            return 0.0
        return min(100.0, v / 2.55)
    except (TypeError, ValueError):
        return None


def find_latest_jsonl() -> Path | None:
    files = sorted(REPORT_DIR.glob("lighting-latency-*.jsonl"), reverse=True)
    # Exclude flutter-output sidecars
    files = [f for f in files if "-flutter" not in f.name]
    return files[0] if files else None


def load_events(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def per_entity_brightness_series(rows: list[dict]) -> dict[str, list[tuple[float, float]]]:
    """For each light/switch entity, return time-ordered (received_ts, bri_pct)
    samples. Switches don't have brightness — included only if state was 'on'
    (treated as 100) or 'off' (treated as 0) so we capture flutter for
    binary devices too."""
    series: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        if r.get("kind") != "state_changed":
            continue
        eid = r.get("entity_id", "")
        if not (eid.startswith("light.") or eid.startswith("switch.")):
            continue
        ts = r.get("received_ts")
        if ts is None:
            continue
        bri = _bri_to_pct(r.get("attr_brightness"))
        if bri is None:
            # Treat as binary on/off
            s = r.get("new_state")
            if s == "on":
                bri = 100.0
            elif s == "off":
                bri = 0.0
            else:
                continue
        series[eid].append((ts, bri))
    # Sort each entity's series
    for eid in series:
        series[eid].sort(key=lambda x: x[0])
    return dict(series)


def count_reversals(samples: list[tuple[float, float]], ignore_pct: float = 5.0) -> int:
    """Count direction-sign reversals in the brightness signal.

    Smoothing: drop consecutive samples whose brightness differs by less
    than `ignore_pct` from the prior surviving sample. After smoothing,
    a "reversal" is any sign change in consecutive deltas.

    Example trajectories (ignore_pct=5):
      [10, 50, 90] → 0 reversals (monotonic up)
      [10, 50, 90, 50, 10] → 1 reversal (up then down)
      [10, 50, 10, 50, 10] → 3 reversals (up-down-up-down)
      [10, 12, 14, 16] → 0 (deltas all <5% → smoothed away)
    """
    if len(samples) < 3:
        return 0
    cleaned: list[tuple[float, float]] = [samples[0]]
    for ts, bri in samples[1:]:
        if abs(bri - cleaned[-1][1]) >= ignore_pct:
            cleaned.append((ts, bri))
    if len(cleaned) < 3:
        return 0
    reversals = 0
    prev_dir = None
    for i in range(1, len(cleaned)):
        delta = cleaned[i][1] - cleaned[i - 1][1]
        if delta == 0:
            continue
        cur_dir = 1 if delta > 0 else -1
        if prev_dir is not None and cur_dir != prev_dir:
            reversals += 1
        prev_dir = cur_dir
    return reversals


def collect_service_call_thrash(rows: list[dict], window_s: float = 3.0) -> dict[str, int]:
    """Count call_service event clusters per entity.

    For each entity, find runs where 2+ call_service events for that
    entity land within `window_s` of each other. Each such run of N
    calls contributes (N-1) thrashes.
    """
    per_entity_calls: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    for r in rows:
        if r.get("kind") != "call_service":
            continue
        if r.get("domain") not in ("light", "switch"):
            continue
        ts = r.get("ts")
        if ts is None:
            continue
        data = r.get("data") or {}
        ents: list[str] = []
        eid = data.get("entity_id")
        if isinstance(eid, str):
            ents = [eid]
        elif isinstance(eid, list):
            ents = list(eid)
        for ent in ents:
            per_entity_calls[ent].append((ts, data))

    thrash: dict[str, int] = {}
    for ent, calls in per_entity_calls.items():
        calls.sort(key=lambda x: x[0])
        cluster_size = 1
        for i in range(1, len(calls)):
            if calls[i][0] - calls[i - 1][0] <= window_s:
                cluster_size += 1
            else:
                if cluster_size > 1:
                    thrash[ent] = thrash.get(ent, 0) + (cluster_size - 1)
                cluster_size = 1
        if cluster_size > 1:
            thrash[ent] = thrash.get(ent, 0) + (cluster_size - 1)
    return thrash


def reversals_per_minute(samples: list[tuple[float, float]], ignore_pct: float) -> tuple[int, int]:
    """Return (total_reversals, peak_reversals_in_any_60s_window)."""
    if len(samples) < 3:
        return 0, 0
    total = count_reversals(samples, ignore_pct)
    # Peak: sliding 60s window
    peak = 0
    n = len(samples)
    for start_i in range(n):
        t0 = samples[start_i][0]
        window = [(t, b) for t, b in samples[start_i:] if t - t0 <= 60.0]
        peak = max(peak, count_reversals(window, ignore_pct))
    return total, peak


def render_flutter_section(
    rows: list[dict],
    ignore_pct: float,
    cluster_window_s: float,
) -> str:
    series = per_entity_brightness_series(rows)
    thrash = collect_service_call_thrash(rows, cluster_window_s)

    # Per-entity flutter
    flutter_rows: list[tuple[str, int, int, int, int]] = []
    for ent, samples in series.items():
        total, peak = reversals_per_minute(samples, ignore_pct)
        if total == 0 and ent not in thrash:
            continue
        sample_count = len(samples)
        thrash_count = thrash.get(ent, 0)
        flutter_rows.append((ent, total, peak, sample_count, thrash_count))
    flutter_rows.sort(key=lambda x: -x[1] - 0.01 * x[4])  # primary: reversals, tie-break: thrash

    # Run window for normalizing to per-minute rates
    run_start = None
    run_end = None
    for r in rows:
        if r.get("kind") == "run_start":
            run_start = r.get("ts")
        elif r.get("kind") == "run_end":
            run_end = r.get("ts")
    duration_s = (run_end - run_start) if (run_start and run_end) else None
    if duration_s is None and rows:
        # Fall back to received_ts spread
        ts_vals = [r.get("received_ts") for r in rows if r.get("received_ts")]
        if ts_vals:
            duration_s = max(ts_vals) - min(ts_vals)
    duration_min = (duration_s / 60.0) if duration_s else None

    lines: list[str] = []
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Flutter analysis (added post-run)")
    lines.append("")
    lines.append(f"_Smoothing threshold: ignore brightness changes < {ignore_pct:.0f}% as noise._")
    if duration_min:
        lines.append(f"_Run duration: {duration_min:.1f} min — per-min rates computed against this window._")
    lines.append("")
    lines.append("**Flutter** = number of direction-reversals in the brightness trajectory.")
    lines.append("**Thrash** = clusters of `call_service` events firing within "
                 f"{cluster_window_s:.0f}s for the same entity (each cluster of N calls counts as N-1 thrashes).")
    lines.append("")
    lines.append("| Entity | Reversals (total) | Peak/60s | Reversals/min | Sample count | Service thrashes |")
    lines.append("|---|---|---|---|---|---|")
    for ent, total, peak, sc, th in flutter_rows[:20]:
        rpm = (total / duration_min) if duration_min else None
        rpm_str = f"{rpm:.1f}" if rpm is not None else "-"
        flag = " ⚠" if total > 5 or peak > 3 else ""
        lines.append(f"| {ent}{flag} | {total} | {peak} | {rpm_str} | {sc} | {th} |")
    if not flutter_rows:
        lines.append("| (no flutter detected) | - | - | - | - | - |")
    lines.append("")

    # Top-3 entity brightness traces
    lines.append("### Top-flutter brightness traces (smoothed, first 30 samples)")
    lines.append("")
    for ent, total, peak, sc, th in flutter_rows[:3]:
        samples = series[ent]
        # Smooth same as count_reversals
        cleaned: list[tuple[float, float]] = [samples[0]] if samples else []
        for ts, bri in samples[1:]:
            if abs(bri - cleaned[-1][1]) >= ignore_pct:
                cleaned.append((ts, bri))
        if not cleaned:
            continue
        lines.append(f"**{ent}** — {len(cleaned)} smoothed samples ({total} reversals)")
        if cleaned:
            t0 = cleaned[0][0]
            lines.append("```")
            for ts, bri in cleaned[:30]:
                rel = ts - t0
                bar = "█" * int(bri / 5)
                lines.append(f"  t+{rel:6.1f}s  {bri:5.1f}%  {bar}")
            if len(cleaned) > 30:
                lines.append(f"  ... {len(cleaned) - 30} more samples")
            lines.append("```")
        lines.append("")

    # Service-call thrash (separate ranking — entities not in flutter list)
    flutter_ents = {r[0] for r in flutter_rows}
    extra_thrash = [(e, c) for e, c in thrash.items() if e not in flutter_ents and c > 0]
    extra_thrash.sort(key=lambda x: -x[1])
    if extra_thrash:
        lines.append("### Service-call thrash for entities with no detected reversals")
        lines.append("(rapid `call_service` events but the brightness did not actually reverse — possibly")
        lines.append(" the service-call payloads were redundant + the light didn't fully respond)")
        lines.append("")
        lines.append("| Entity | Thrashes |")
        lines.append("|---|---|")
        for ent, c in extra_thrash[:10]:
            lines.append(f"| {ent} | {c} |")
        lines.append("")

    # Auto-recommendations
    lines.append("### Flutter recommendations")
    lines.append("")
    recs: list[str] = []
    for ent, total, peak, sc, th in flutter_rows[:5]:
        if total > 10 or peak > 5:
            recs.append(
                f"**`{ent}`** fluttered {total}× total (peak {peak}/min). "
                f"Likely cause: the matching zone's classifier oscillates between active_presence/linger/vacant, "
                f"causing the actuator to fire repeated `light.turn_on` calls at different brightness targets. "
                f"Inspect the zone's classifier transition pattern in the main report; consider dwell-threshold "
                f"hysteresis or a per-zone cooldown."
            )
    if not recs:
        recs.append("(no entity exceeded the flutter alert thresholds — system appears smooth)")
    for r in recs:
        lines.append(f"- {r}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jsonl", type=Path, help="Specific JSONL to analyze (default: latest)")
    ap.add_argument("--separate", action="store_true",
                    help="Write to a sidecar -flutter.md file instead of editing the main report")
    ap.add_argument("--ignore-pct", type=float, default=5.0,
                    help="Smoothing threshold (brightness change below this = noise)")
    ap.add_argument("--cluster-window-s", type=float, default=3.0,
                    help="Service-call thrash detection window")
    args = ap.parse_args()

    jsonl = args.jsonl or find_latest_jsonl()
    if not jsonl or not jsonl.exists():
        print("FATAL: no JSONL found", file=sys.stderr)
        return 2
    print(f"Reading: {jsonl}")
    rows = load_events(jsonl)
    print(f"Events: {len(rows)}")

    section = render_flutter_section(rows, args.ignore_pct, args.cluster_window_s)

    md_path = jsonl.with_suffix(".md")
    if args.separate or not md_path.exists():
        out = jsonl.parent / (jsonl.stem + "-flutter.md")
        out.write_text(
            f"# Flutter analysis — {jsonl.name}\n_Generated: {datetime.now(timezone.utc).isoformat()}_\n{section}",
            encoding="utf-8",
        )
        print(f"Written: {out}")
    else:
        # Append to existing main report
        existing = md_path.read_text(encoding="utf-8")
        # If already contains a Flutter analysis section, replace it
        marker = "## Flutter analysis (added post-run)"
        if marker in existing:
            head = existing.split(marker, 1)[0].rstrip()
            md_path.write_text(head + "\n" + section, encoding="utf-8")
            print(f"Replaced flutter section in: {md_path}")
        else:
            md_path.write_text(existing.rstrip() + "\n" + section, encoding="utf-8")
            print(f"Appended flutter section to: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
