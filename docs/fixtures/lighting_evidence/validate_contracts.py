#!/usr/bin/env python3
"""validate_contracts.py — assert the lighting-evidence fixtures match the
documented schemas (see README.md). Run any time you suspect drift between
the generator output, the REST endpoints, and the docs.

USAGE
  python docs/fixtures/lighting_evidence/validate_contracts.py
  python docs/fixtures/lighting_evidence/validate_contracts.py --strict
  python docs/fixtures/lighting_evidence/validate_contracts.py --extra path/to/sample.jsonl

Exit code: 0 = all good. Non-zero = one or more contracts violated.

Stdlib-only; no HA or pytest imports. Mirrors the schema checks in
tools/probe-why-light-evidence.py so the rules live in two places only
(here + the probe), and the probe re-uses the same constants by import.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


# Required keys per record kind. Keep in sync with the README + the
# generator output. If you change one of these sets, update both.
OVERRIDE_EVENT_REQUIRED_TOP = {
    "kind", "ts", "zone", "light_entity", "context_id",
    "owning_zones", "predictions_by_zone",
    "observed", "predicted", "delta_pct",
    "debug_match_reason",
}
OVERRIDE_EVENT_REQUIRED_CTX = {
    "shadow_mode", "profile", "state", "tv_playing", "asleep",
    "night_safe", "user_at_home", "any_occupied", "source_user_id",
}

PENDING_PREFERENCE_REQUIRED_TOP = {
    "kind", "ts", "zone", "primary_zone", "light_entity",
    "owning_zones", "captured_state", "what_automation_would_have_done",
    "predictions_by_zone", "prediction_consistency", "capture_provenance",
    "occupancy", "context",
}
PENDING_PREFERENCE_CP_KEYS = {
    "capture_trigger", "trigger_entity_id", "trigger_from",
    "trigger_to", "trigger_for_s",
}
PENDING_PREFERENCE_PC_KEYS = {
    "primary_prediction_pct", "would_have_done_brightness_pct",
    "prediction_mismatch_pct",
}
PENDING_PREFERENCE_OCC_KEYS = {
    "entity_id", "state", "last_changed", "age_s", "flicker_count_5min",
}

# M4: optional top-level fields. Validated when present; not required for
# pre-M4 records to remain valid.
PENDING_PREFERENCE_M4_OPTIONAL = {
    "evidence_id", "dedupe_key", "dedupe_window_s",
    "capture_suppressed_count", "related_override_context_id",
}

# REST-normalized recent_overrides record shape (post-merge, what
# home-app.jsx renders). Subset; the response also carries the other
# context fields flattened.
RECENT_OVERRIDES_NORMALIZED_REQUIRED = {
    "actual_pct", "predicted_pct", "zone", "ts", "shadow_mode",
}


FIXTURES_DIR = Path(__file__).resolve().parent
OVERRIDE_FIXTURE = FIXTURES_DIR / "override_event.example.jsonl"
PENDING_FIXTURE = FIXTURES_DIR / "pending_preference.example.jsonl"


def _check_keys(rec: dict, required: set, label: str) -> list[str]:
    return [f"{label}.{k}" for k in required if k not in rec]


def _validate_override_event(rec: dict) -> list[str]:
    """JSONL-shape override_event: nested observed/predicted/context."""
    miss: list[str] = []
    miss += _check_keys(rec, OVERRIDE_EVENT_REQUIRED_TOP, "")
    # context.* — required for downstream join with pending_preference.
    miss += _check_keys(rec.get("context") or {}, OVERRIDE_EVENT_REQUIRED_CTX, "context")
    # observed.* must have brightness_pct + light_state.
    obs = rec.get("observed") or {}
    if "brightness_pct" not in obs:
        miss.append("observed.brightness_pct")
    if "light_state" not in obs:
        miss.append("observed.light_state")
    # predicted.brightness_pct is what delta_pct is computed against.
    pred = rec.get("predicted") or {}
    if "brightness_pct" not in pred:
        miss.append("predicted.brightness_pct")
    # predictions_by_zone must be a non-empty dict with the primary zone.
    pbz = rec.get("predictions_by_zone")
    if not isinstance(pbz, dict) or not pbz:
        miss.append("predictions_by_zone(empty-or-not-dict)")
    elif rec.get("zone") and rec["zone"] not in pbz:
        miss.append(f"predictions_by_zone[{rec['zone']!r}](missing-primary-zone-key)")
    # owning_zones must include the zone.
    oz = rec.get("owning_zones") or []
    if rec.get("zone") and rec["zone"] not in oz:
        miss.append(f"owning_zones(missing-{rec['zone']})")
    return miss


def _validate_pending_preference(rec: dict) -> list[str]:
    miss: list[str] = []
    miss += _check_keys(rec, PENDING_PREFERENCE_REQUIRED_TOP, "")
    miss += _check_keys(rec.get("capture_provenance") or {},
                        PENDING_PREFERENCE_CP_KEYS, "capture_provenance")
    miss += _check_keys(rec.get("prediction_consistency") or {},
                        PENDING_PREFERENCE_PC_KEYS, "prediction_consistency")
    miss += _check_keys(rec.get("occupancy") or {},
                        PENDING_PREFERENCE_OCC_KEYS, "occupancy")
    # primary_zone === zone for capture-on-vacant (the capture zone owns it).
    if rec.get("primary_zone") and rec.get("zone") and rec["primary_zone"] != rec["zone"]:
        miss.append(f"primary_zone({rec['primary_zone']!r})!=zone({rec['zone']!r})")
    # capture_trigger must be one of the documented enum values.
    ct = (rec.get("capture_provenance") or {}).get("capture_trigger")
    if ct not in ("vacancy_settle", "manual_automation_trigger"):
        miss.append(f"capture_provenance.capture_trigger(invalid-value={ct!r})")
    # predictions_by_zone must include all owning_zones.
    pbz = rec.get("predictions_by_zone") or {}
    for z in (rec.get("owning_zones") or []):
        if z not in pbz:
            miss.append(f"predictions_by_zone(missing-{z})")
    # M4: when M4 fields are present, validate their shape. Records without
    # them are still valid (backward-compat with pre-M4 captures on disk).
    if "evidence_id" in rec:
        if not isinstance(rec["evidence_id"], str) or not rec["evidence_id"].startswith("pp-"):
            miss.append(f"evidence_id(bad-format={rec['evidence_id']!r})")
    if "dedupe_key" in rec:
        if not isinstance(rec["dedupe_key"], str) or "|" not in rec["dedupe_key"]:
            miss.append(f"dedupe_key(bad-format={rec['dedupe_key']!r})")
        # The first segment of the key MUST equal primary_zone — sanity check.
        elif rec.get("primary_zone") and not rec["dedupe_key"].startswith(rec["primary_zone"] + "|"):
            miss.append(f"dedupe_key(prefix!=primary_zone): {rec['dedupe_key']!r} vs {rec['primary_zone']!r}")
    if "dedupe_window_s" in rec:
        if not isinstance(rec["dedupe_window_s"], int) or rec["dedupe_window_s"] <= 0:
            miss.append(f"dedupe_window_s(non-positive-int={rec['dedupe_window_s']!r})")
    if "capture_suppressed_count" in rec:
        if not isinstance(rec["capture_suppressed_count"], int) or rec["capture_suppressed_count"] < 0:
            miss.append(f"capture_suppressed_count(invalid={rec['capture_suppressed_count']!r})")
    if "related_override_context_id" in rec:
        v = rec["related_override_context_id"]
        if v is not None and (not isinstance(v, str) or not v.startswith("ovr-")):
            miss.append(f"related_override_context_id(bad-format={v!r})")
    return miss


def _validate_normalized_recent_override(rec: dict) -> list[str]:
    """REST-normalized shape (flat). What home-app.jsx receives from
    /recent_overrides after the M3 memory+JSONL merge."""
    miss: list[str] = []
    miss += _check_keys(rec, RECENT_OVERRIDES_NORMALIZED_REQUIRED, "")
    # evidence_source is M3-added; if absent, that's a contract miss.
    if "evidence_source" not in rec:
        miss.append("evidence_source")
    elif rec["evidence_source"] not in ("memory", "jsonl"):
        miss.append(f"evidence_source(invalid={rec['evidence_source']!r})")
    return miss


def _load_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _synthesize_normalized_records(jsonl_records: list[dict]) -> list[dict]:
    """Apply the same JSONL→flat normalization the REST endpoint does, so
    we can validate the shape without a live HA. Mirrors
    _normalize_jsonl_override in __init__.py."""
    out = []
    for obj in jsonl_records:
        if obj.get("kind") != "override_event":
            continue
        observed = obj.get("observed") or {}
        predicted = obj.get("predicted") or {}
        context = obj.get("context") or {}
        out.append({
            "context_id": obj.get("context_id"),
            "ts": obj.get("ts"),
            "zone": obj.get("zone"),
            "light_entity": obj.get("light_entity"),
            "actual_pct": observed.get("brightness_pct"),
            "predicted_pct": predicted.get("brightness_pct"),
            "shadow_mode": context.get("shadow_mode"),
            "evidence_source": "jsonl",
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true",
                    help="non-zero exit on warnings (default: only on missing required fields)")
    ap.add_argument("--extra", action="append", default=[],
                    help="additional JSONL file(s) to validate (each record's `kind` discriminates)")
    args = ap.parse_args()

    failures = 0
    warnings = 0

    # ── fixture: override_event ───────────────────────────────────────
    if OVERRIDE_FIXTURE.exists():
        recs = _load_jsonl(OVERRIDE_FIXTURE)
        print(f"# override_event.example.jsonl ({len(recs)} record(s))")
        for i, rec in enumerate(recs):
            miss = _validate_override_event(rec)
            tag = "OK" if not miss else "MISS"
            print(f"  [{tag:>4}] line {i+1}: zone={rec.get('zone')} ctx_id={rec.get('context_id')}"
                  + (f" — missing: {miss}" if miss else ""))
            if miss:
                failures += 1
        # Bonus: validate normalized REST shape (synthesized from same JSONL).
        norm = _synthesize_normalized_records(recs)
        print(f"  + synthesized REST-normalized shape ({len(norm)} record(s))")
        for i, rec in enumerate(norm):
            miss = _validate_normalized_recent_override(rec)
            tag = "OK" if not miss else "MISS"
            print(f"  [{tag:>4}] norm line {i+1}: zone={rec.get('zone')} src={rec.get('evidence_source')}"
                  + (f" — missing: {miss}" if miss else ""))
            if miss:
                failures += 1
    else:
        print(f"  [WARN] {OVERRIDE_FIXTURE} not found — skipping")
        warnings += 1

    # ── fixture: pending_preference ───────────────────────────────────
    print()
    if PENDING_FIXTURE.exists():
        recs = _load_jsonl(PENDING_FIXTURE)
        print(f"# pending_preference.example.jsonl ({len(recs)} record(s))")
        prov_counts: dict[str, int] = {}
        for i, rec in enumerate(recs):
            miss = _validate_pending_preference(rec)
            tag = "OK" if not miss else "MISS"
            prov = (rec.get("capture_provenance") or {}).get("capture_trigger", "?")
            prov_counts[prov] = prov_counts.get(prov, 0) + 1
            print(f"  [{tag:>4}] line {i+1}: zone={rec.get('zone')} provenance={prov}"
                  + (f" — missing: {miss}" if miss else ""))
            if miss:
                failures += 1
        print(f"  provenance breakdown: {prov_counts}")
        # Want at least one of each provenance variant in the fixture so we
        # have full schema coverage for downstream consumers.
        if "vacancy_settle" not in prov_counts:
            print(f"  [WARN] fixture missing a vacancy_settle (natural) example")
            warnings += 1
        if "manual_automation_trigger" not in prov_counts:
            print(f"  [WARN] fixture missing a manual_automation_trigger example")
            warnings += 1
    else:
        print(f"  [WARN] {PENDING_FIXTURE} not found — skipping")
        warnings += 1

    # ── extra files (e.g. live-sampled JSONL) ─────────────────────────
    for ep in args.extra:
        epath = Path(ep)
        if not epath.exists():
            print(f"\n[WARN] --extra {ep} does not exist; skipping")
            warnings += 1
            continue
        print(f"\n# --extra {ep}")
        recs = _load_jsonl(epath)
        for i, rec in enumerate(recs):
            kind = rec.get("kind")
            if kind == "override_event":
                miss = _validate_override_event(rec)
            elif kind == "pending_preference":
                miss = _validate_pending_preference(rec)
            else:
                print(f"  [SKIP] line {i+1}: unknown kind={kind!r}")
                continue
            tag = "OK" if not miss else "MISS"
            print(f"  [{tag:>4}] line {i+1}: kind={kind} zone={rec.get('zone')}"
                  + (f" — missing: {miss}" if miss else ""))
            if miss:
                failures += 1

    # ── summary ────────────────────────────────────────────────────────
    print()
    print(f"# summary: failures={failures} warnings={warnings}")
    if failures:
        return 1
    if args.strict and warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
