#!/usr/bin/env python3
"""probe-why-light-evidence.py — exercise the 4 REST endpoints that the
Home app's /why-light slash command calls, validate schemas, and compose a
sample output. Standalone (stdlib only), no HA imports.

Use this after deploys / HA restarts to confirm the lighting-evidence
introspection surface is healthy end-to-end. Codex M3 added the JSONL
fallback to /recent_overrides so /why-light keeps working after restart;
this probe is the operator-side verifier.

USAGE
  python tools/probe-why-light-evidence.py \
      --url http://192.168.0.125:8123 \
      --token <long-lived-token> \
      --zone office

  Or set HA_URL + HA_TOKEN env vars and omit those flags.

PRIVACY
  - The HA token is never printed (only its length).
  - source_user_id in any override_event payload is redacted before display.
  - Raw JSON output is OFF by default; pass --raw to dump full bodies
    (still redacts tokens + source_user_id).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Force UTF-8 stdout so warning glyphs render cross-platform (cp1252 on
# Windows shells otherwise raises UnicodeEncodeError on emoji/Unicode).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


# ── schema contracts ──────────────────────────────────────────────────
# Mirror docs/fixtures/lighting_evidence/README.md.

OVERRIDE_EVENT_REQUIRED_TOP = {
    "kind", "ts", "zone", "light_entity", "context_id",
    "owning_zones", "predictions_by_zone",
    "observed", "predicted", "delta_pct",
}
# In `context.*` for JSONL records, OR flat top-level for normalized
# memory/REST shape. Probe checks both.
OVERRIDE_EVENT_REQUIRED_FLAT_OR_CTX = {
    "shadow_mode", "profile", "state", "tv_playing", "asleep",
    "night_safe", "user_at_home", "any_occupied",
}

PENDING_PREFERENCE_REQUIRED_TOP = {
    "kind", "ts", "zone", "primary_zone", "light_entity",
    "owning_zones", "captured_state", "what_automation_would_have_done",
    "predictions_by_zone", "prediction_consistency", "capture_provenance",
    "occupancy", "context",
}
PENDING_PREFERENCE_CAPTURE_PROVENANCE_KEYS = {
    "capture_trigger", "trigger_entity_id", "trigger_from",
    "trigger_to", "trigger_for_s",
}
PENDING_PREFERENCE_PREDICTION_CONSISTENCY_KEYS = {
    "primary_prediction_pct", "would_have_done_brightness_pct",
    "prediction_mismatch_pct",
}
PENDING_PREFERENCE_OCCUPANCY_KEYS = {
    "entity_id", "state", "last_changed", "age_s", "flicker_count_5min",
}

# Normalized recent_overrides REST response record — flat shape that
# home-app.jsx renders. Subset of the union above.
RECENT_OVERRIDES_REST_REQUIRED = {
    "actual_pct", "predicted_pct", "zone", "ts", "shadow_mode",
}


# ── helpers ───────────────────────────────────────────────────────────


def _redact(rec: Any) -> Any:
    """Strip/replace any source_user_id field in-place (deep). Returns the
    same object (mutated). Token redaction is handled at the request layer
    — tokens never reach payloads we'd display."""
    if isinstance(rec, dict):
        # Top-level normalized record OR JSONL context.source_user_id.
        if "source_user_id" in rec:
            rec["source_user_id"] = "<redacted>" if rec.get("source_user_id") else None
        for v in rec.values():
            _redact(v)
    elif isinstance(rec, list):
        for v in rec:
            _redact(v)
    return rec


def _http_get(url: str, token: str, timeout: float = 8.0) -> tuple[int, dict | list | None, str | None]:
    """GET with bearer auth. Returns (status_code, parsed_json_or_none,
    err_msg_or_none). Never raises."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read()
            try:
                return status, json.loads(body), None
            except json.JSONDecodeError as e:
                return status, None, f"non-json body ({len(body)} bytes): {e}"
    except urllib.error.HTTPError as e:
        # Try to surface the body even on 4xx/5xx so e.g. 404 with JSON err
        # surfaces useful info.
        try:
            body = e.read()
            try:
                return e.code, json.loads(body), None
            except Exception:
                return e.code, None, body.decode("utf-8", errors="replace")[:200]
        except Exception:
            return e.code, None, str(e)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, None, str(e)


def _validate_override_event(rec: dict, source: str) -> list[str]:
    """Return list of missing/wrong fields. Empty list = ok. `source` is
    one of 'jsonl' (nested observed/predicted/context) or 'rest_normalized'
    (flat actual_pct/predicted_pct/shadow_mode at top level)."""
    missing: list[str] = []
    if source == "jsonl":
        for k in OVERRIDE_EVENT_REQUIRED_TOP:
            if k not in rec:
                missing.append(k)
        ctx = rec.get("context") or {}
        for k in OVERRIDE_EVENT_REQUIRED_FLAT_OR_CTX:
            if k not in ctx:
                missing.append(f"context.{k}")
    else:  # rest_normalized
        for k in RECENT_OVERRIDES_REST_REQUIRED:
            if k not in rec:
                missing.append(k)
        # Additional sanity: predictions_by_zone exists and is a dict
        if "predictions_by_zone" in rec and not isinstance(rec.get("predictions_by_zone"), dict):
            missing.append("predictions_by_zone(not-a-dict)")
    return missing


def _validate_pending_preference(rec: dict) -> list[str]:
    missing: list[str] = []
    for k in PENDING_PREFERENCE_REQUIRED_TOP:
        if k not in rec:
            missing.append(k)
    cp = rec.get("capture_provenance") or {}
    for k in PENDING_PREFERENCE_CAPTURE_PROVENANCE_KEYS:
        if k not in cp:
            missing.append(f"capture_provenance.{k}")
    pc = rec.get("prediction_consistency") or {}
    for k in PENDING_PREFERENCE_PREDICTION_CONSISTENCY_KEYS:
        if k not in pc:
            missing.append(f"prediction_consistency.{k}")
    occ = rec.get("occupancy") or {}
    for k in PENDING_PREFERENCE_OCCUPANCY_KEYS:
        if k not in occ:
            missing.append(f"occupancy.{k}")
    return missing


# ── main probe ────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("HA_URL"),
                    help="HA base URL (or HA_URL env). e.g. http://192.168.0.125:8123")
    ap.add_argument("--token", default=os.environ.get("HA_TOKEN"),
                    help="HA long-lived token (or HA_TOKEN env). NEVER printed.")
    ap.add_argument("--zone", default="office",
                    help="zone slug to probe (default: office)")
    ap.add_argument("--hours", type=float, default=24.0,
                    help="hours window for overrides + decisions (default: 24)")
    ap.add_argument("--raw", action="store_true",
                    help="dump full response bodies (still redacts tokens + source_user_id)")
    args = ap.parse_args()

    if not args.url or not args.token:
        print("ERROR: --url + --token (or HA_URL + HA_TOKEN env) required.", file=sys.stderr)
        return 2

    url = args.url.rstrip("/")
    zone_q = urllib.parse.quote(args.zone)
    print(f"# probe-why-light-evidence — zone={args.zone} hours={args.hours} url={url}")
    print(f"# HA token: <{len(args.token)} chars, redacted>")
    print()

    # The 4 endpoints /why-light calls.
    endpoints = [
        ("classifier",          f"{url}/api/states/sensor.{_camera_for(args.zone)}_{args.zone}_lighting_state"),
        ("recent_overrides",    f"{url}/api/extended_openai_conversation/recent_overrides?zone={zone_q}&hours={args.hours}"),
        ("lighting_decisions",  f"{url}/api/extended_openai_conversation/lighting_decisions?zone={zone_q}&tail=10&hours={args.hours}"),
        ("recent_pending_prefs",f"{url}/api/extended_openai_conversation/recent_pending_preferences?zone={zone_q}&tail=3&hours=72"),
    ]

    results: dict[str, tuple[int, Any, str | None]] = {}
    for name, u in endpoints:
        status, body, err = _http_get(u, args.token)
        results[name] = (status, body, err)
        _redact(body)
        ok = "OK " if status == 200 else f"FAIL ({status})"
        # Extract a short summary.
        summary = "-"
        if status == 200 and isinstance(body, dict):
            if name == "classifier":
                summary = f"state={body.get('state')!r} pred={body.get('attributes', {}).get('predicted_brightness_pct')}"
            elif name == "recent_overrides":
                ev = body.get("evidence") or {}
                summary = (f"count={body.get('count')} (mem={ev.get('memory_count')}, "
                           f"jsonl_added={ev.get('jsonl_added_after_dedupe')}, "
                           f"mem_ready={ev.get('memory_ready')})")
            elif name == "lighting_decisions":
                summary = (f"count={body.get('count')} scanned={body.get('scanned_bytes')}B "
                           f"cap_hit={body.get('hit_scan_cap')}")
            elif name == "recent_pending_prefs":
                summary = (f"count={body.get('count')} scanned={body.get('scanned_bytes')}B")
        print(f"[{ok:>9}] {name:22s} {summary}")
        if err:
            print(f"            err: {err}")

    print()

    # ── schema validation ─────────────────────────────────────────────
    print("# schema validation")

    # Recent_overrides — REST normalized shape (each entry should be flat).
    ro_body = results.get("recent_overrides", (0, None, None))[1]
    ro_entries = (ro_body or {}).get("data", []) if isinstance(ro_body, dict) else []
    if ro_entries:
        per_source: dict[str, list[list[str]]] = {"memory": [], "jsonl": []}
        for r in ro_entries:
            src = r.get("evidence_source") or "?"
            miss = _validate_override_event(r, "rest_normalized")
            per_source.setdefault(src, []).append(miss)
        for src, all_miss in per_source.items():
            n = len(all_miss)
            n_clean = sum(1 for m in all_miss if not m)
            uniq_miss = sorted({mk for m in all_miss for mk in m})
            tag = "OK" if n_clean == n else "MISS"
            print(f"  [{tag:>4}] recent_overrides[evidence_source={src}] {n_clean}/{n} clean"
                  + (f" — missing: {uniq_miss}" if uniq_miss else ""))
    else:
        print(f"  [ -- ] recent_overrides: no records to validate")

    # Recent_pending_preferences — full M2 schema.
    rpp_body = results.get("recent_pending_prefs", (0, None, None))[1]
    rpp_entries = (rpp_body or {}).get("entries", []) if isinstance(rpp_body, dict) else []
    if rpp_entries:
        all_miss = [_validate_pending_preference(r) for r in rpp_entries]
        n_clean = sum(1 for m in all_miss if not m)
        uniq_miss = sorted({mk for m in all_miss for mk in m})
        tag = "OK" if n_clean == len(all_miss) else "MISS"
        print(f"  [{tag:>4}] recent_pending_prefs {n_clean}/{len(all_miss)} clean"
              + (f" — missing: {uniq_miss}" if uniq_miss else ""))
        # Bonus: provenance breakdown.
        prov_counts: dict[str, int] = {}
        for r in rpp_entries:
            cp = r.get("capture_provenance") or {}
            prov_counts[cp.get("capture_trigger", "?")] = prov_counts.get(cp.get("capture_trigger", "?"), 0) + 1
        print(f"         provenance breakdown: {prov_counts}")
    else:
        print(f"  [ -- ] recent_pending_prefs: no records to validate")

    # ── M4: evidence hygiene report ─────────────────────────────────────
    print()
    print("# M4 evidence hygiene")
    # We want a wider window than the slash-command default (3 records) to
    # see dedupe behaviour. Re-fetch with tail=200 over 24h.
    m4_url = (f"{url}/api/extended_openai_conversation/recent_pending_preferences"
              f"?zone={zone_q}&tail=200&hours=24")
    m4_status, m4_body, m4_err = _http_get(m4_url, args.token)
    _redact(m4_body)
    m4_entries = (m4_body or {}).get("entries", []) if isinstance(m4_body, dict) else []
    if m4_status != 200:
        print(f"  [FAIL] could not fetch 24h pending: http {m4_status} {m4_err or ''}")
    else:
        # Total + duplicate detection by dedupe_key clustering.
        from collections import Counter as _Counter
        dedupe_counts = _Counter(r.get("dedupe_key") or "(none)" for r in m4_entries)
        # "duplicate-looking" = same dedupe_key appearing 2+ times in window.
        dup_keys = {k: c for k, c in dedupe_counts.items() if c >= 2 and k != "(none)"}
        total_dup_records = sum(dup_keys.values())
        print(f"  pending_preferences (last 24h, zone={args.zone}): {len(m4_entries)}")
        print(f"  unique dedupe_keys: {len(dedupe_counts)} | duplicate clusters: {len(dup_keys)} | duplicate-looking records: {total_dup_records}")
        if dup_keys:
            for k, c in sorted(dup_keys.items(), key=lambda kv: -kv[1])[:3]:
                print(f"    cluster x{c}: {k}")
        suppressed_counts = [r.get("capture_suppressed_count") for r in m4_entries
                             if isinstance(r.get("capture_suppressed_count"), int)]
        if suppressed_counts:
            print(f"  capture_suppressed_count: max={max(suppressed_counts)} total={sum(suppressed_counts)}"
                  f" non_zero={sum(1 for c in suppressed_counts if c > 0)}/{len(suppressed_counts)}")
        else:
            print(f"  capture_suppressed_count: <no integer values in records>")
        if m4_entries:
            latest = m4_entries[-1]
            print(f"  latest record:")
            print(f"    ts: {latest.get('ts')}")
            print(f"    evidence_id: {latest.get('evidence_id') or '<absent>'}")
            print(f"    dedupe_key: {latest.get('dedupe_key') or '<absent>'}")
            print(f"    dedupe_window_s: {latest.get('dedupe_window_s')}")
            print(f"    capture_suppressed_count: {latest.get('capture_suppressed_count')}")
            print(f"    related_override_context_id: {latest.get('related_override_context_id') or '<none>'}")
            occ = latest.get('occupancy') or {}
            fcount = occ.get('flicker_count_5min')
            fpresent = fcount is not None
            print(f"    occupancy.flicker_count_5min: {fcount} ({'populated' if fpresent else 'null/absent'})")
            # Was the LATEST attempt suppressed or emitted? An emitted record
            # has capture_suppressed_count set to the # suppressed BEFORE it
            # (could be 0 if no prior suppressions). We can't tell from the
            # JSONL alone whether ANY new suppression fired since the last
            # emit — that lives in input_text. Note this as a structural
            # observation.
            print(f"    note: this latest record is an EMIT (suppressions, if any since this, live in HA input_text)")

    print()

    # ── composed /why-light sample output ─────────────────────────────
    print("# composed /why-light output (what home-app.jsx renders)")
    classifier_body = results.get("classifier", (0, None, None))[1] or {}
    lines = [f"**{args.zone.replace('_', ' ')}** — /why-light"]
    if isinstance(classifier_body, dict) and classifier_body.get("state"):
        st = classifier_body.get("state")
        a = classifier_body.get("attributes", {}) or {}
        pred = a.get("predicted_brightness_pct")
        dwell = a.get("dwell_ms")
        lm = a.get("last_manual_at")
        lines.append(f"current state: {st}"
                     + (f" (predicted {pred}%)" if pred is not None else "")
                     + (f" · dwell {dwell}ms" if dwell is not None else "")
                     + (f" · last manual: {lm}" if lm else ""))
    else:
        lines.append("classifier sensor unavailable")

    if ro_entries:
        lines.append(f"last {int(args.hours)}h: {len(ro_entries)} manual override(s) (showing last 3)")
        for r in ro_entries[-3:]:
            ts = r.get("ts", "?")
            ap = r.get("actual_pct")
            pp = r.get("predicted_pct")
            shadow = " (shadow)" if r.get("shadow_mode") else ""
            src = r.get("evidence_source")
            src_tag = f" [{src}]" if src else ""
            lines.append(f"  • {ts} — actual {ap}% vs predicted {pp}%{shadow}{src_tag}")
    else:
        lines.append(f"no manual overrides in the last {int(args.hours)}h")

    if rpp_entries:
        latest = rpp_entries[-1]
        cap = (latest.get("captured_state") or {}).get("brightness_pct")
        cp = latest.get("capture_provenance") or {}
        prov = cp.get("capture_trigger")
        prov_tag = "natural" if prov == "vacancy_settle" else "manually triggered" if prov == "manual_automation_trigger" else (prov or "?")
        lines.append(f"latest preference capture: {latest.get('ts')} @ {cap}% ({prov_tag})")
        consistency = latest.get("prediction_consistency") or {}
        mismatch = consistency.get("prediction_mismatch_pct")
        if mismatch is not None and abs(mismatch) > 10:
            lines.append(f"  [!] prediction mismatch {mismatch}% "
                         f"-- classifier {consistency.get('primary_prediction_pct')}% "
                         f"vs cache {consistency.get('would_have_done_brightness_pct')}%")
        occ = latest.get("occupancy") or {}
        if occ.get("state") is not None and occ.get("age_s") is not None:
            flick = "  [!] (occupancy unstable at capture)" if (occ.get("age_s") or 0) < 10 else ""
            lines.append(f"  occupancy at capture: {occ.get('state')} for {occ.get('age_s')}s{flick}")
    else:
        lines.append("no pending preferences captured for this zone yet")

    ld_body = results.get("lighting_decisions", (0, None, None))[1]
    ld_entries = (ld_body or {}).get("entries", []) if isinstance(ld_body, dict) else []
    if ld_entries:
        lines.append("last classifier transitions (up to 5):")
        for d in list(reversed(ld_entries))[:5]:
            ts = d.get("ts", "?")
            f = d.get("from_state", "?")
            t = d.get("to_state", "?")
            bri = (d.get("predicted") or {}).get("brightness_pct")
            lines.append(f"  • {ts} {f} -> {t}" + (f" @ {bri}%" if bri is not None else ""))
    else:
        lines.append("no shadow-log transitions in the window")

    for L in lines:
        print("  " + L)

    if args.raw:
        print()
        print("# raw responses (redacted)")
        for name, (status, body, _) in results.items():
            print(f"--- {name} (http {status}) ---")
            try:
                print(json.dumps(body, indent=2, sort_keys=True, default=str)[:4000])
            except Exception as e:
                print(f"(unprintable: {e})")

    # Exit code: 0 if all 4 endpoints HTTP 200, 1 otherwise.
    failures = [n for n, (s, _, _) in results.items() if s != 200]
    if failures:
        print()
        print(f"# FAILED endpoints: {failures}")
        return 1
    return 0


# ── camera-for-zone map (mirrors home-app.jsx ZONE_CAMERA_MAP) ────────
def _camera_for(zone: str) -> str:
    return {
        "dining_left": "dining_room", "dining_right": "dining_room",
        "sink": "kitchen", "island_left": "kitchen", "island_right": "kitchen",
        "sofa": "living_room", "front_left": "living_room", "weights": "living_room",
        "office": "living_room", "front_door": "living_room",
    }.get(zone, "living_room")


if __name__ == "__main__":
    raise SystemExit(main())
