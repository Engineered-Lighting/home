"""Read-only stage graph, replay timeline and compact replay reads for the Lab UI.

Pure functions over the ledger. They never run inference, contact a model,
provider or worker, or write: opening the graph or seeking replay only reads
what was recorded. Every read is bounded. A binary search on the ledger clock
fixes the causal event horizon, each per-kind lookup walks the
events(session_id,kind,id) index backwards over at most SCAN_ROWS rows, native
snippets are at most SNIPPET_BYTES and a response at most RESPONSE_BYTES.

Causality: an event is visible at a cursor only once it was available: payload
available_at, else effective_at (experiment.activated), else completed_at
(perception.delivery), else the ledger's created_at. Unknown stays unknown: a
source without recorded evidence is never reported as producing, prepared
evidence exists only where an admitted request saved it, tracks are not
identities and human labels are never model inputs.

Cost: ledger events are append-only and immutable, so everything derived only
from given event ids (stage projections of an admitted request, bounded
snippets of recorded payloads) is kept in a small per-store LRU keyed by those
ids. Repeated live polls then redo only the indexed reads and the cursor-
dependent fields (ages, freshness, in-flight state); nothing is extrapolated.
"""
from collections import OrderedDict
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path
import json
import re
import threading
import weakref

from reasoning import finite
from store import LabError, encode

CONTRACT = "perception-lab-graph-1"
# Additive revisions of the same contract id: 2 adds reasoning.lifecycle markers,
# per-arm preparation blocks, the live frame_age block and ha_unavailable ids;
# 3 adds the timeline's facts-bound valid_through_ms on JEV results, the
# jev_smoothed track (code-owned activity strip) and reasoning.gate markers.
CONTRACT_REVISION = 3
SNIPPET_BYTES = 2048
SCAN_ROWS = 256
RESPONSE_BYTES = 1024*1024
# created_at is written at or after availability; a later row is outside the
# horizon unless its write lagged availability by more than this.
HORIZON_SLACK_S = 5
PRODUCING_WINDOW_S = 10
TRACK_POINTS = 600
TRACK_ITEMS = 600
TIMELINE_ROWS = 40000
# Routine drops are written per frame per arm; JEV rows carry whole packets.
DROP_ROWS = 160000
JEV_ROWS = 4000
# reasoning.lifecycle receipts are small and at most two per offer.
LIFECYCLE_ROWS = 16000
# Replacement of a pending request by a newer offer is routine: counted only.
ROUTINE_LIFECYCLE = ("replaced",)
PREPARATION_ITEMS = 16
REASON_LOOKUPS = 64
MEMO_ITEMS = 256  # immutable, event-derived projections kept per store
TRUNCATED = "__truncated__"
INACTIVE = ("contextual_beliefs", "policy", "proposed_action", "authorization", "execution", "verification", "human_review")
FAILURES = ("perception.expired", "perception.cancelled", "perception.indeterminate", "perception.lookup_unproven")
ROUTINE_DROPS = ("source_sampling_cadence", "replaced_by_latest")
# Without statistics SQLite may walk events(session_id,id) and test every row's
# kind, so every per-kind read names the (session_id,kind,id) index.
_LIGHT = "json_extract(payload,'$.arm','$.available_at','$.effective_at','$.completed_at','$.mode')"
_LATEST = ("SELECT id,created_at,"+_LIGHT+" FROM events INDEXED BY events_session_kind_id"
           " WHERE session_id=? AND kind=? AND id<=? ORDER BY id DESC LIMIT ?")

# The 37 design source families (docs/PERCEPTION-LAB-MULTISOURCE-DESIGN-PROMPT.md).
# A family maps to recorded ledger nodes, or is proposed/not provisioned/excluded
# with an honest reason and never a value.
FAMILIES = (
    (1, "RGB cameras", "visual", "C", ("rgb_camera",), None),
    (2, "Stereo depth", "spatial_temporal", "C", ("depth_bundle",), None),
    (3, "Rectified left/right and RGB bundle planes", "visual", "C", ("depth_bundle",), None),
    (4, "Object detection", "visual", "C", ("specialist_a", "specialist_b", "shared_detector"), None),
    (5, "Body pose", "visual", "C", ("specialist_a", "specialist_b"), None),
    (6, "Hands / fine gestures", "visual", "P", "proposed", "no Lab adapter or evidence projection for hand landmarks"),
    (7, "Head orientation / gaze proxy", "visual", "P", "proposed", "no Lab adapter; not verified attention or gaze"),
    (8, "Instance/semantic segmentation", "visual", "C", ("specialist_a", "specialist_b"), "masks appear only when an admitted specialist returns them"),
    (9, "Frigate events and occupancy", "home_device", "C", ("ha_snapshot",), "HA occupancy entities only; no Lab Frigate event adapter; same camera origin stays correlated"),
    (10, "Facial recognition", "personal", "H", "excluded_by_policy", "excluded by the current JEV projection; recognition never grants authority"),
    (11, "Within-camera tracking", "spatial_temporal", "C", ("specialist_a", "specialist_b", "room_observation"), "track ids are not identities"),
    (12, "Cross-camera continuity", "spatial_temporal", "P", "proposed", "no Lab adapter; never automatic identity"),
    (13, "Metric localization", "spatial_temporal", "C", ("room_observation",), "stereo-surface path requires validated depth, calibration and registration"),
    (14, "Room geometry and furniture", "spatial_temporal", "C", ("room_observation",), "versioned zones through the room publisher only"),
    (15, "Person/object relationships", "spatial_temporal", "C", ("specialist_a", "specialist_b", "room_observation"), "image-plane facts are computed in prepared evidence (state.semantic); near is not touching"),
    (16, "Posture and transitions", "spatial_temporal", "H", "not_provisioned", "uncalibrated V-JEPA posture buckets are the vjepa_scene node (family 17); no transition source"),
    (17, "V-JEPA temporal representation", "spatial_temporal", "C", ("vjepa_scene",), "frozen V-JEPA 2.1 probes on LA over stored den preview clips (vjepa-den-1): uncalibrated posture, reading, zero-shot, motion and scene-change buckets; correlated with the camera"),
    (18, "Scene change / object persistence", "spatial_temporal", "H", "not_provisioned", "V-JEPA scene-change buckets are in the vjepa_scene node (family 17); no object persistence source"),
    (19, "Qwen scene interpretation", "visual", "C", ("caption_receipt", "qwen_scene"), "stored caption receipts, and the continuous perceiver's closed-vocabulary scene facts (lab-live-scene-1); both correlated with the camera"),
    (20, "HA media state", "home_device", "C", ("ha_snapshot",), "only allowlisted entities of the provisioned HA snapshot"),
    (21, "Computer activity", "home_device", "C", ("ha_snapshot",), "computer_active binding exists; its source is not verified"),
    (22, "HA lights, covers, climate, fans", "home_device", "C", ("ha_snapshot",), "native HA state only when allowlisted; no semantic binding"),
    (23, "Appliance/power signals", "home_device", "P", "proposed", "no installed sensor is assumed"),
    (24, "Independent presence/contact sensors", "home_device", "P", "proposed", "no Lab adapter"),
    (25, "Environmental sensors", "environment_time", "P", "proposed", "no Lab adapter"),
    (26, "Clock, calendar and sun position", "environment_time", "C", ("ambient_context",), "bucketed part of day, day type and local hour plus HA sun daylight, per offer; no calendar"),
    (27, "Weather/daylight forecast", "environment_time", "C", ("ambient_context",), "HA weather condition enum only, when bound; a prior, not an observation"),
    (28, "Audio activity/events", "environment_time", "H", "not_provisioned", "no audio is recorded by the Lab"),
    (29, "Speech/transcript/diarization", "personal", "H", "not_provisioned", "speech systems exist outside the Lab; no permitted Lab adapter"),
    (30, "Explicit human intent / selected mode", "personal", "P", "proposed", "no authenticated intent adapter"),
    (31, "Household/room knowledge", "personal", "H", "not_provisioned", "no governed retrieval into the Lab"),
    (32, "Personal preferences", "personal", "P", "proposed", "decision-policy input; the Lab policy stage is inactive"),
    (33, "Recent activity/episode history", "personal", "P", "proposed", "episode memory is not a Lab source; the per-arm JEV evidence window is recorded in each request"),
    (34, "Mobile/home-presence context", "personal", "P", "proposed", "no permitted presence projection"),
    (35, "Source and processing health", "operational", "C", ("processing_health",), None),
    (36, "Prior actions / observed outcomes", "operational", "P", "proposed", "Lab actuation is deferred; execution and verification stages are inactive"),
    (37, "Human labels, ratings and corrections", "operational", "C", ("human_labels",), "review data only; never model inputs"),
)
_REAL = {  # node: (title, group, catalog status, implementing module)
    "rgb_camera": ("RGB camera acquisition", "visual", "C", "preview_ingest"),
    "depth_bundle": ("Stereo depth bundle", "spatial_temporal", "C", "bundle_ingest"),
    "specialist_a": ("Specialist A native output", "visual", "C", "runtime"),
    "specialist_b": ("Specialist B native output", "visual", "C", "runtime"),
    "shared_detector": ("Shared detector crops", "visual", "C", "shared_detector"),
    "ha_snapshot": ("Home Assistant snapshot", "home_device", "C", "ha_ingest"),
    "caption_receipt": ("Historical caption receipt", "visual", "C", "caption_history"),
    "room_observation": ("Room / spatial observation", "spatial_temporal", "C", "room_evidence"),
    "processing_health": ("Delivery and processing health", "operational", "C", "runtime"),
    "human_labels": ("Human labels", "operational", "C", "store"),
    "ambient_context": ("Ambient context (clock, daylight, weather)", "environment_time", "C", "ambient_context"),
    "qwen_scene": ("Qwen live scene (structured)", "visual", "C", "qwen_scene"),
    "vjepa_scene": ("V-JEPA 2.1 den buckets (structured)", "spatial_temporal", "C", "vjepa_scene"),
}
_SECRETS = {"authorization", "token", "access_token", "refresh_token", "id_token", "bearer", "cookie", "password",
            "passwd", "secret", "client_secret", "api_key", "apikey", "private_key", "credential", "credentials", "signature"}
_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|/(?!api/perception-lab/)[^/\s]+/)")
_BLOB = re.compile(r"^[A-Za-z0-9+/_=-]{512,}$")
_modules = {}


def _implemented(module):
    if module not in _modules:
        _modules[module] = find_spec(module) is not None
    return _modules[module]


_PLAIN = (int, float, bool, type(None))


@lru_cache(maxsize=4096)
def _secret(key):
    key = key.lower()
    return (key in _SECRETS or key.startswith("token") or key.endswith(("_token", "_secret", "_password", "_base64", "_credential", "_credentials"))
            or key in {"path", "file", "dir", "filename"} or key.endswith(("_path", "_file", "_dir", "_directory", "_filename")))


_local = threading.local()


def _clean(value, redacted, shared=None):
    # Keys stay visible so the schema is inspectable; private values do not.
    if type(value) in _PLAIN:
        return value
    if isinstance(value, dict):
        if shared is not None:
            # One graph read cleans the same saved dict (an evidence record,
            # the semantic state) for several snippets: clean it once.
            hit = shared.get(id(value))
            if hit is not None and hit[0] is value:
                redacted[0] += hit[2]
                return hit[1]
            before = redacted[0]
        result = {}
        for key, item in value.items():
            key = str(key)
            if _secret(key):
                redacted[0] += 1
                result[key] = "[redacted]"
            else:
                result[key] = _clean(item, redacted, shared)
        if shared is not None:
            shared[id(value)] = (value, result, redacted[0]-before)
        return result
    if isinstance(value, list):
        return [_clean(item, redacted, shared) for item in value]
    if isinstance(value, str) and (_PATH.match(value) or _BLOB.match(value)):
        redacted[0] += 1
        return "[redacted]"
    return value


class _OverBudget(Exception):
    """A snippet rung already exceeds its budget: skip to the next rung."""


def _bound(value, items, chars, depth, meter):
    # meter[0]: bytes left of a LOWER bound on the encoded size (every string
    # costs at least its length plus quotes, a key plus quotes and colon, any
    # other scalar one byte). Exceeding it proves the rung cannot fit, so it is
    # abandoned early; a rung that may fit is still built and encoded exactly.
    if type(value) in _PLAIN:
        meter[0] -= 1
        if meter[0] < 0:
            raise _OverBudget
        return value
    if isinstance(value, dict):
        keys = list(value)
        if depth <= 0:
            result = {TRUNCATED: f"{len(keys)} keys"} if keys else {}
            meter[0] -= 2+sum(len(k)+len(v)+5 for k, v in result.items())
            if meter[0] < 0:
                raise _OverBudget
            return result
        meter[0] -= 2
        result = {}
        for key in keys[:items*2]:
            meter[0] -= len(key)+3
            if meter[0] < 0:
                raise _OverBudget
            result[key] = _bound(value[key], items, chars, depth-1, meter)
        if len(keys) > items*2:
            result[TRUNCATED] = f"+{len(keys)-items*2} keys"
            meter[0] -= len(TRUNCATED)+len(result[TRUNCATED])+5
            if meter[0] < 0:
                raise _OverBudget
        return result
    if isinstance(value, list):
        if depth <= 0:
            result = [f"{TRUNCATED}:{len(value)} items"] if value else []
            meter[0] -= 2+sum(len(v)+2 for v in result)
            if meter[0] < 0:
                raise _OverBudget
            return result
        meter[0] -= 2
        result = [_bound(item, items, chars, depth-1, meter) for item in value[:items]]
        if len(value) > items:
            result.append(f"{TRUNCATED}:+{len(value)-items} items")
            meter[0] -= len(result[-1])+2
            if meter[0] < 0:
                raise _OverBudget
        return result
    if isinstance(value, str):
        if len(value) > chars:
            value = value[:chars]+"…"+TRUNCATED
        meter[0] -= len(value)+2
        if meter[0] < 0:
            raise _OverBudget
        return value
    meter[0] -= 1
    if meter[0] < 0:
        raise _OverBudget
    return value


def snippet(value, budget=SNIPPET_BYTES):
    """Valid, syntax-ready JSON of at most `budget` bytes with explicit markers.

    Arrays keep a prefix plus "__truncated__:+N items", objects a prefix of keys
    plus "__truncated__", long strings end in "…__truncated__". Secret-, path-
    and blob-like values are replaced by "[redacted]" before bounding.
    """
    redacted = [0]
    clean = _clean(value, redacted, getattr(_local, "shared", None))
    size = len(encode(clean).encode())
    if size <= budget:
        return {"json": clean, "bytes": size, "truncated": False, "redacted": redacted[0]}
    for items, chars, depth in ((16, 240, 8), (8, 120, 6), (4, 80, 5), (3, 48, 4), (2, 32, 3), (1, 24, 2), (1, 16, 1)):
        try:
            bounded = _bound(clean, items, chars, depth, [budget])
        except _OverBudget:
            continue
        if len(encode(bounded).encode()) <= budget:
            return {"json": bounded, "bytes": size, "truncated": True, "redacted": redacted[0]}
    return {"json": {TRUNCATED: "exceeds_snippet_budget"}, "bytes": size, "truncated": True, "redacted": redacted[0]}


_memos = weakref.WeakKeyDictionary()
_memo_lock = threading.Lock()


def _memo(store, key, build):
    """build() once per key for this store; key must name only immutable ledger
    rows (event or media ids) plus the flags the value depends on. The shared
    value is read-only: graph responses are serialized, never mutated."""
    with _memo_lock:
        cache = _memos.get(store)
        if cache is None:
            cache = _memos[store] = OrderedDict()
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
    value = build()
    with _memo_lock:
        cache[key] = value
        while len(cache) > MEMO_ITEMS:
            cache.popitem(last=False)
    return value


def _numbers(value, depth=3):
    # Timing dictionaries only: finite numbers, bounded nesting and width.
    if not isinstance(value, dict) or depth <= 0:
        return None
    result = {}
    for key, item in list(value.items())[:32]:
        if finite(item):
            result[str(key)[:64]] = item
        elif isinstance(item, dict):
            nested = _numbers(item, depth-1)
            if nested:
                result[str(key)[:64]] = nested
    return result or None


def _table(store, name):
    with store.lock:
        return store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _horizon(store, sid, limit):
    """Largest session event id recorded by `limit` on the ledger clock.

    Ids and created_at increase together, so a binary search over the
    events(session_id,id) index costs O(log n) probes regardless of history.
    """
    with store.lock:
        low, high = store.db.execute("SELECT min(id),max(id) FROM events WHERE session_id=?", (sid,)).fetchone()
        if low is None:
            return 0
        last = store.db.execute("SELECT created_at FROM events WHERE id=?", (high,)).fetchone()[0]
        if last <= limit:
            return high
        best = 0
        while low <= high:
            middle = (low+high)//2
            row = store.db.execute("SELECT id,created_at FROM events WHERE session_id=? AND id>=? ORDER BY id LIMIT 1", (sid, middle)).fetchone()
            if row is not None and row[1] <= limit:
                best, low = row[0], row[0]+1
            else:
                high = middle-1
        return best


def _time(light, created):
    return next((value for value in light[1:4] if finite(value)), created)


def _load(store, event_id):
    with store.lock:
        row = store.db.execute("SELECT id,kind,created_at,payload FROM events WHERE id=?", (event_id,)).fetchone()
    return None if row is None else {"id": row[0], "kind": row[1], "created_at": row[2], "payload": json.loads(row[3])}


def _latest(store, sid, kind, horizon, cutoff, *, arm=None, live_only=False, scan=SCAN_ROWS):
    """Latest `kind` event available by `cutoff`, walking at most `scan` rows.

    Returns (record, bounded): bounded is True when the walk ended at `scan`
    rows without a match, so older evidence may exist but is not claimed.
    """
    with store.lock:
        cursor = store.db.execute(_LATEST, (sid, kind, horizon, scan))
        try:
            seen = 0
            for event_id, created, light in cursor:
                seen += 1
                light = json.loads(light)
                at = _time(light, created)
                if at > cutoff or (arm is not None and light[0] != arm) or (live_only and light[4] != "live"):
                    continue
                record = _load(store, event_id)
                record["at"] = at
                return record, False
        finally:
            cursor.close()
    return None, seen >= scan


def _age(record, cutoff):
    return None if record is None else round(cutoff-record["at"], 3)


def _freshness(valid_until, cutoff, exists=True):
    if not exists:
        return "none"
    return ("fresh" if valid_until >= cutoff else "stale") if finite(valid_until) else "unknown"


def _node(key, cutoff, *, record=None, bounded=False, observed=None, valid_until=None, freshness=None, provisioned=None,
          basis="not_recorded", declared=None, declared_basis=None, consumed=None, version=None, failure=None, native=None, counts=None, reason=None, status=None):
    title, group, catalog, module = _REAL[key]
    available = record["at"] if record else None
    fresh = freshness or _freshness(valid_until, cutoff, record is not None)
    producing = bool(record) and available > cutoff-PRODUCING_WINDOW_S
    newer = failure is not None and (record is None or failure.get("at", 0) > available)
    # failed: a failure is newer than the latest record; stale: the latest record
    # expired by the cursor; idle: still valid but nothing new in the window.
    status = status or ("failed" if newer else "stale" if record and fresh == "stale" else "producing" if producing
                        else "idle" if record else "no_recent_records" if bounded else "no_records")
    return {"id": key, "title": title, "group": group, "families": [f[0] for f in FAMILIES if isinstance(f[4], tuple) and key in f[4]],
            "catalog_status": catalog, "kind": "ledger", "implemented": _implemented(module), "provisioned": provisioned,
            "provisioned_basis": basis, "producing": producing, "freshness": fresh, "validated": None,
            "consumed_by": consumed or {"a": "not_a_jev_input", "b": "not_a_jev_input"},
            "declared_by_setup": declared or {"a": None, "b": None}, "declared_basis": declared_basis or {"a": None, "b": None}, "last_observed_at": observed, "last_available_at": available,
            "valid_until": valid_until if finite(valid_until) else None, "age_s": _age(record, cutoff), "version": version,
            "failure": failure, "status": status, "reason": reason if reason else "older_than_scan_bound" if bounded and not record else None,
            "native": native, "counts": counts}


def _proposed(number, title, group, catalog, status, reason):
    return {"id": f"family_{number:02d}", "title": title, "group": group, "families": [number], "catalog_status": catalog,
            "kind": "proposed", "implemented": False, "provisioned": False, "provisioned_basis": "no_lab_adapter",
            "producing": False, "freshness": "not_applicable", "validated": None,
            "consumed_by": {"a": "not_implemented", "b": "not_implemented"}, "declared_by_setup": {"a": None, "b": None},
            "declared_basis": {"a": None, "b": None},
            "last_observed_at": None, "last_available_at": None, "valid_until": None, "age_s": None, "version": None,
            "failure": None, "status": status, "reason": reason, "native": None, "counts": None}


def _recipe(experiment_id):
    try:
        raw = json.loads(Path(__file__).with_name("experiments.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    items = raw.get("experiments", []) if isinstance(raw, dict) else raw
    return next((item for item in items if isinstance(item, dict) and item.get("id") == experiment_id), None)


# Setup fields the activation records per arm (payload path), and the fields
# only today's catalog knows. A replay cursor never shows present configuration
# as what the session's arms declared: catalog-only fields are then null.
_RECORDED_SETUP = {"localization": ("spatial_policy", "localization"), "room_map": ("spatial_policy", "room_relations"),
                   "history": ("reasoning_windows",), "sampling_hz": ("sampling_hz",), "ha_evidence": ("ha_evidence",),
                   "caption_evidence": ("caption_evidence",), "ambient_evidence": ("source_policy", "ambient"),
                   "qwen_scene_evidence": ("source_policy", "qwen_scene"),
                   "temporal_encoder_evidence": ("source_policy", "temporal_encoder")}
_CATALOG_SETUP = ("input", "detector", "pose", "masks", "reasoner", "device", "precision")
# ProfileResolver's defaults when a recipe omits the field.
_CATALOG_DEFAULTS = {"ha_evidence": True, "caption_evidence": False, "ambient_evidence": "granted", "qwen_scene_evidence": "granted"}
# Family grants carried in a recipe's per-arm source_policy (baseline granted).
_FAMILY_SETUP = {"ambient_evidence": "ambient", "qwen_scene_evidence": "qwen_scene",
                 "temporal_encoder_evidence": "temporal_encoder"}
# Families outside the baseline: granted only where a recipe's source_policy says so.
_FAMILY_WITHHELD = {"temporal_encoder"}


def _recorded_setup(activation, arm, path):
    value = activation.get(path[0]) if isinstance(activation, dict) else None
    if path[0] == "source_policy":
        # Recorded family statuses are keyed family first: {family: {arm: status}}.
        value = value.get(path[1]) if isinstance(value, dict) else None
        return value.get(arm) if isinstance(value, dict) else None
    value = value.get(arm) if isinstance(value, dict) else None
    if len(path) > 1:
        value = value.get(path[1]) if isinstance(value, dict) else None
    return value


def _setup(activation, recipe, arm, live):
    """Per-arm setup at the cursor: the recorded activation first; the current
    catalog only for a live cursor and labelled so; else null (not_recorded)."""
    catalog = recipe.get(arm) if live and recipe and isinstance(recipe.get(arm), dict) else None
    values, basis = {}, {}
    for field in _CATALOG_SETUP+tuple(_RECORDED_SETUP):
        value = _recorded_setup(activation, arm, _RECORDED_SETUP[field]) if field in _RECORDED_SETUP else None
        if value is not None:
            values[field], basis[field] = value, "recorded_activation"
        elif catalog is not None and field in _FAMILY_SETUP:
            policy = catalog.get("source_policy") if isinstance(catalog.get("source_policy"), dict) else {}
            family = _FAMILY_SETUP[field]
            if family in _FAMILY_WITHHELD:
                varied = ("source_policy." + family) in ((recipe.get("lever") or {}).get("paths") or [])
                value = "granted" if policy.get(family) is True else "withheld_by_experiment" if varied else "not_in_baseline"
            else:
                value = "withheld_by_experiment" if policy.get(family) is False else "granted"
            values[field], basis[field] = value, "current_catalog"
        elif catalog is not None and (field in catalog or field in _CATALOG_DEFAULTS):
            values[field], basis[field] = catalog.get(field, _CATALOG_DEFAULTS.get(field)), "current_catalog"
        else:
            values[field], basis[field] = None, "not_recorded"
    return values, basis


def _cursor(store, session, at_ms):
    now = store.clock()
    start = session["started_at"]
    if at_ms is None:
        cutoff, mode, clamped = now, "live", False
    else:
        if type(at_ms) is not int or not 0 <= at_ms <= store.settings.max_session_seconds*1000:
            raise LabError("invalid replay cursor", 422, "invalid_cursor")
        if not finite(start):
            raise LabError("session has no recording origin", 409, "no_recording_origin")
        cutoff, mode = start+at_ms/1000, "replay"
        # A cursor ahead of the ledger cannot see what has not happened yet.
        clamped = cutoff > now
        cutoff = min(cutoff, now)
    return {"mode": mode, "at_ms": at_ms, "cutoff": cutoff, "clamped_to_now": clamped,
            "session_time_s": round(cutoff-start, 3) if finite(start) else None, "server_time": now}


def _session(session):
    return {k: session.get(k) for k in ("id", "site_id", "camera_id", "experiment_id", "name", "state", "reason",
                                        "started_at", "ended_at", "generation", "revision", "reasoning_enabled", "reasoning_revision")}


def _state(record):
    # The admitted packet's state; tolerate malformed rows instead of failing.
    packet = record["payload"].get("request") if record else None
    state = packet.get("state") if isinstance(packet, dict) else None
    return state if isinstance(state, dict) else {}


def _evidence(record):
    evidence = _state(record).get("evidence")
    return [item for item in evidence if isinstance(item, dict)] if isinstance(evidence, list) else []


def _request_kinds(record):
    if record is None:
        return None
    return {item.get("source_kind") or "perception.output" for item in _evidence(record)}


def _consumed(requests, kinds, *, withheld=None, arm_only=None, unknown=None):
    result = {}
    for arm in ("a", "b"):
        if arm_only is not None and arm != arm_only:
            result[arm] = "other_arm"
        elif requests[arm] is None:
            result[arm] = ("withheld_by_setup" if withheld and withheld.get(arm) is False
                           else "unknown_older_than_scan_bound" if unknown and unknown.get(arm) else "no_request")
        elif requests[arm] & kinds:
            result[arm] = "consumed"
        else:
            result[arm] = "withheld_by_setup" if withheld and withheld.get(arm) is False else "not_in_request"
    return result


def _reasoning_reason(store, request_id, cache):
    # The terminal reason is written with the delivery event in one transaction.
    if request_id in cache:
        return cache[request_id]
    reason = None
    if len(cache) < REASON_LOOKUPS and _table(store, "lab_reasoning_requests"):
        with store.lock:
            row = store.db.execute("SELECT json_extract(result,'$.reason') FROM lab_reasoning_requests WHERE id=?", (request_id,)).fetchone()
        reason = row[0] if row and isinstance(row[0], str) else None
    elif len(cache) >= REASON_LOOKUPS:
        reason = "reason_lookup_bound_reached"
    cache[request_id] = reason
    return reason


def _rejected(store, sid, arm, generation, cutoff):
    if type(generation) is not int or not _table(store, "paired_requests"):
        return None
    with store.lock:
        row = store.db.execute("""SELECT id,updated_at,json_extract(result,'$.result.reason') FROM paired_requests
            WHERE session_id=? AND arm=? AND generation=? AND state='rejected' AND updated_at<=?
            ORDER BY updated_at DESC LIMIT 1""", (sid, arm, generation, cutoff)).fetchone()
    return None if row is None else {"kind": "perception.rejected", "request_id": row[0], "at": row[1], "reason": row[2]}


def _specialist(store, sid, arm, horizon, cutoff, experiment):
    output, bounded = _latest(store, sid, "perception.output", horizon, cutoff, arm=arm, live_only=True)
    failures = []
    for kind in FAILURES:
        record, _ = _latest(store, sid, kind, horizon, cutoff, arm=arm, scan=64)
        if record:
            value = record["payload"]
            failures.append({"kind": kind, "event_id": record["id"], "request_id": value.get("request_id"), "at": record["at"],
                             "reason": value.get("reason")})
    rejected = _rejected(store, sid, arm, experiment.get("generation") if experiment else None, cutoff)
    if rejected:
        failures.append(rejected)
    failure = max(failures, key=lambda item: item["at"]) if failures else None
    delivery = None
    if output:
        candidate = _load(store, output["id"]-1)
        # Delivery and output of one paired completion are written together.
        if (candidate and candidate["kind"] == "perception.delivery"
                and candidate["payload"].get("request_id") == output["payload"].get("request_id")):
            delivery = candidate["payload"]
    return output, bounded, failure, delivery


def _specialist_stage(arm, output, bounded, failure, delivery, experiment, cutoff, consumed_ids):
    stage = {"stage": "specialist_output", "source_id": "specialist_"+arm, "status": "none", "failure": failure,
             "reason": "older_than_scan_bound" if bounded and not output else None}
    if not output:
        return stage
    value = output["payload"]
    result = value.get("result") if isinstance(value.get("result"), dict) else {}
    snapshot = value.get("arm_snapshot") if isinstance(value.get("arm_snapshot"), dict) else {}
    generation = experiment.get("generation") if experiment else None
    stale = None
    if generation is not None and value.get("generation") != generation:
        stale = "experiment_generation_changed"
    elif not finite(value.get("valid_until")) or value["valid_until"] < cutoff:
        stale = "expired"
    frame = snapshot.get("frame") if isinstance(snapshot.get("frame"), dict) else {}
    timings = {"arm_snapshot": _numbers(snapshot.get("timing")), "worker": _numbers(result.get("timings_ms")),
               "stages": _numbers(result.get("stage_timings_ms"))}
    if delivery:
        timings["delivery"] = _numbers({k: delivery.get(k) for k in ("encode_ms", "transport_ms", "body_bytes")})
        if finite(delivery.get("dispatched_at")) and finite(value.get("available_at")):
            timings["dispatch_to_available_ms"] = round((value["available_at"]-delivery["dispatched_at"])*1000, 3)
    count = lambda field: len(result[field]) if isinstance(result.get(field), list) else None
    stage.update(status="stale" if stale else "fresh", stale_reason=stale, event_id=output["id"], request_id=value.get("request_id"),
                 frame_id=value.get("frame_id"), generation=value.get("generation"), observed_at=value.get("observed_at"),
                 available_at=value.get("available_at"), valid_until=value.get("valid_until"),
                 source_end_s=value.get("source_end_s"), available_at_s=value.get("available_at_s"),
                 model_version=value.get("model_version"), artifact_sha256=value.get("artifact_sha256"), engine_epoch=value.get("engine_epoch"),
                 counts={"objects": count("objects"), "pose_instances": count("pose_instances"), "masks": count("masks"), "tracks": count("tracks")},
                 validation=[str(v)[:64] for v in snapshot.get("validation", [])[:16]] if isinstance(snapshot.get("validation"), list) else [],
                 frame={k: frame.get(k) for k in ("id", "media_id", "captured_at", "session_time_s", "width", "height")} if frame else None,
                 timings={k: v for k, v in timings.items() if v is not None},
                 consumed_by_latest_request=None if consumed_ids is None else str(output["id"]) in consumed_ids)
    return stage


def _reasoning(store, sid, arm, horizon, cutoff, reasons):
    # The walks cover both arms: a bound hit without this arm's row is unknown.
    request, request_bounded = _latest(store, sid, "reasoning.request", horizon, cutoff, arm=arm)
    delivery, delivery_bounded = _latest(store, sid, "reasoning.delivery", horizon, cutoff, arm=arm)
    output = None
    if delivery:
        candidate = _load(store, delivery["id"]+1)
        # A published judgment is written right after its delivery receipt.
        if (candidate and candidate["kind"] == "reasoning.output"
                and candidate["payload"].get("request_id") == delivery["payload"].get("request_id")):
            output = candidate
    reason = None
    if delivery:
        value = delivery["payload"]
        reason = value.get("reason")
        if reason is None and value.get("state") in {"failed", "indeterminate"}:
            reason = _reasoning_reason(store, value.get("request_id"), reasons)
    lifecycle, lifecycle_bounded = _latest(store, sid, "reasoning.lifecycle", horizon, cutoff, arm=arm, scan=64)
    return request, request_bounded, delivery, delivery_bounded, output, reason, _lifecycle_item(lifecycle, lifecycle_bounded)


def _lifecycle_item(record, bounded):
    # A pending request that ended without dispatch (replaced, expired, cancelled,
    # failed locally). Sessions recorded before these receipts existed have none.
    if record is None:
        return {"status": "unknown", "reason": "older_than_scan_bound"} if bounded else None
    value = record["payload"]
    return {"status": "recorded", "event_id": record["id"], "at": record["at"],
            **{k: value.get(k) for k in ("request_id", "generation", "state", "reason", "replaced_by")}}


def _exclusions(request):
    # Only what the admitted request itself saved; the projection writes none today.
    packet = request["payload"].get("request") if isinstance(request["payload"].get("request"), dict) else {}
    for basis, value in (("admitted_request.state.exclusions", _state(request).get("exclusions")),
                         ("admitted_request.exclusions", packet.get("exclusions")),
                         ("reasoning.request.exclusions", request["payload"].get("exclusions"))):
        if value is not None:
            return {"status": "recorded", "basis": basis, "snippet": snippet(value)}
    return "not_recorded"


def _section(basis, items):
    # Saved by the request: recorded, possibly with zero items.
    return {"status": "recorded", "basis": basis, "items": len(items), "items_truncated": len(items) > PREPARATION_ITEMS,
            "snippet": snippet(items[:PREPARATION_ITEMS])}


def _preparation(request, enabled, bounded=False):
    """Preparation facts exactly as the arm's latest admitted request saved them.

    Nothing is recomputed: sections are copied from state.evidence and
    state.semantic (bounded snippets), or reported as not recorded.
    """
    if request is None:
        return {"status": "unknown" if bounded else "not_saved", "basis": "admitted_request",
                "reason": "older_than_scan_bound" if bounded else "reasoning_off" if not enabled else "no_admitted_request"}
    state = _state(request)
    evidence = _evidence(request)
    semantic = state.get("semantic") if isinstance(state.get("semantic"), dict) else None
    observations = ([o for o in semantic["observations"] if isinstance(o, dict)]
                    if semantic and isinstance(semantic.get("observations"), list) else [])
    poses = [{"evidence_id": item.get("id"), "observed_at": item.get("observed_at"), "pose_instances": item["pose_instances"]}
             for item in evidence if isinstance(item.get("pose_instances"), list) and item["pose_instances"]]
    # state.evidence is always saved: no pose instances is zero recorded items.
    sections = {"pose_geometry": _section("admitted_request.state.evidence[].pose_instances", poses)}
    if semantic:
        facts = [{"evidence_id": o.get("evidence_id"), "facts": o["facts"]} for o in observations if isinstance(o.get("facts"), list) and o["facts"]]
        context_keys = ("device_state", "room_relations", "camera_derived_device_facts", "spatial_tracks")
        context = [{k: o.get(k) for k in ("evidence_id", "correlated_with")+context_keys}
                   for o in observations if any(o.get(k) for k in context_keys)]
        sections.update(
            wrist_object_relations=_section("admitted_request.state.semantic.observations[].facts", facts),
            observations=_section("admitted_request.state.semantic.observations[]", [
                {**{k: o.get(k) for k in ("evidence_id", "age_seconds", "freshness", "person_count", "detected_objects", "depth", "tracking",
                                          "attention", "book_open_or_closed")},
                 # den-semantics-6: static scene objects kept apart from cues.
                 **({"background_objects": o["background_objects"]} if "background_objects" in o else {})} for o in observations]),
            context_facts=_section("admitted_request.state.semantic.observations[] device, room and track facts", context),
            historical_caption=(_section("admitted_request.state.semantic.historical_caption",
                                         [] if semantic["historical_caption"] is None else [semantic["historical_caption"]])
                                if "historical_caption" in semantic else {"status": "not_recorded"}))
    else:
        sections.update({k: {"status": "not_recorded"} for k in ("wrist_object_relations", "observations", "context_facts", "historical_caption")})
    coverage = semantic.get("source_coverage") if semantic and isinstance(semantic.get("source_coverage"), dict) else None
    counts = {}
    for o in observations:
        if isinstance(o.get("freshness"), str):
            counts[o["freshness"][:32]] = counts.get(o["freshness"][:32], 0)+1
    latest_id = semantic.get("latest_visual_evidence_id") if semantic else None
    latest = next((o for o in observations if latest_id is not None and o.get("evidence_id") == latest_id), None)
    return {"status": "saved", "basis": "admitted_request", "event_id": request["id"], "request_id": request["payload"].get("request_id"),
            "generation": request["payload"].get("generation"), "admitted_at": request["at"],
            "observation_cutoff": state.get("observation_cutoff"), "records": len(evidence),
            "semantic_version": semantic.get("version") if semantic else None, "semantic_recorded": semantic is not None,
            "source_coverage": {str(k)[:32]: str(v)[:64] for k, v in list(coverage.items())[:16]} if coverage else None,
            "freshness": ({"basis": "admitted_request.state.semantic.observations[].freshness", "counts": counts,
                           "latest_visual_evidence_id": latest_id, "latest_visual_age_seconds": latest.get("age_seconds") if latest else None}
                          if semantic else {"basis": "not_recorded"}),
            "sections": sections, "exclusions": _exclusions(request)}


def _frame_age(cursor, frame, specialists):
    """Live only: the newest recorded camera frame against each arm's newest
    analyzed frame, both on the controller clock. Never extrapolated."""
    if cursor["mode"] != "live":
        return None
    now = cursor["server_time"]
    age = lambda t: round(now-t, 3) if finite(t) else None
    camera = None
    if frame:
        meta = json.loads(frame[4])
        available = meta["available_at"] if finite(meta.get("available_at")) else frame[3]
        observed = meta["observed_at"] if finite(meta.get("observed_at")) else None
        # Clock-mapped as analysis_inputs, presentation and qwen_adapter require.
        mapped = meta.get("clock_mapping_status") == "verified" and bool(meta.get("clock_mapping_id"))
        camera = {"source": "recorded_preview_media", "media_id": frame[0], "frame_id": meta.get("frame_id"),
                  "observed_at": observed, "available_at": available, "observed_age_s": age(observed), "available_age_s": age(available),
                  "clock_mapping_status": meta.get("clock_mapping_status"), "clock_mapped": mapped}
    arms = {}
    for arm in ("a", "b"):
        output, bounded = specialists[arm][0], specialists[arm][1]
        if output is None:
            arms[arm] = {"status": "unknown" if bounded else "none", "reason": "older_than_scan_bound" if bounded else "no_analyzed_frame",
                         "behind_camera_s": None}
            continue
        value = output["payload"]
        observed = value["observed_at"] if finite(value.get("observed_at")) else None
        item = {"status": "recorded", "event_id": output["id"], "frame_id": value.get("frame_id"), "generation": value.get("generation"),
                "observed_at": observed, "available_at": output["at"], "observed_age_s": age(observed), "available_age_s": age(output["at"]),
                "behind_camera_s": None, "comparison_reason": None}
        if camera is None:
            item["comparison_reason"] = "no_camera_frame"
        elif not camera["clock_mapped"] or camera["observed_at"] is None or observed is None:
            item["comparison_reason"] = "observation_time_not_clock_mapped"
        else:
            item["behind_camera_s"] = round(camera["observed_at"]-observed, 3)
        arms[arm] = item
    return {"basis": "controller_clock", "server_time": now, "camera": camera, "arms": arms,
            "note": "behind_camera_s is the newest recorded camera frame's observation time minus the arm's newest analyzed frame's; live pump counters stay on /live"}


def _context_block(request):
    """The admitted packet's shared context block (semantic or plain), if any."""
    state = _state(request)
    semantic = state.get("semantic") if isinstance(state.get("semantic"), dict) else None
    block = semantic.get("context") if semantic is not None else state.get("shared_context")
    return block if isinstance(block, dict) else None


def _cutoff_of(record):
    value = _state(record).get("observation_cutoff") if record else None
    return value if finite(value) else None


def _tick_pair(store, sid, latest):
    """Both arms' admitted requests from one scheduler tick (one observation
    cutoff), or None. The arms are offered independently, so each arm's latest
    request may come from a different tick; comparing those digests would say
    nothing. Searches the arm with the newer latest request for the other
    arm's tick over at most 64 of its own requests; memoized by both ids."""
    if latest["a"] is None or latest["b"] is None:
        return None
    if _cutoff_of(latest["a"]) is not None and _cutoff_of(latest["a"]) == _cutoff_of(latest["b"]):
        return latest["a"], latest["b"]
    newer = "a" if latest["a"]["id"] > latest["b"]["id"] else "b"
    older = "b" if newer == "a" else "a"
    target = _cutoff_of(latest[older])

    def search():
        if target is None:
            return None
        with store.lock:
            rows = store.db.execute("""SELECT id,json_extract(payload,'$.request.state.observation_cutoff') FROM events
                INDEXED BY events_session_kind_id WHERE session_id=? AND kind='reasoning.request' AND id<?
                AND json_extract(payload,'$.arm')=? ORDER BY id DESC LIMIT 64""", (sid, latest[newer]["id"], newer)).fetchall()
        return next((event_id for event_id, value in rows if value == target), None)
    partner = _memo(store, ("tick_pair", latest["a"]["id"], latest["b"]["id"]), search)
    if partner is None:
        return None
    pair = {older: latest[older], newer: _load(store, partner)}
    return pair["a"], pair["b"]


def _ambient_node(store, cutoff, latest, unknown, declared, sid=None):
    """Ambient priors exactly as each arm's latest admitted request carried them."""
    blocks = {arm: _context_block(latest[arm]) for arm in ("a", "b")}
    coverage = {arm: (blocks[arm].get("coverage") or {}).get("ambient") if blocks[arm] else None for arm in ("a", "b")}
    consumed = {arm: ("unknown_older_than_scan_bound" if unknown.get(arm) else "no_request") if latest[arm] is None
                else "consumed" if coverage[arm] == "supplied" else "withheld_by_setup" if coverage[arm] == "withheld_by_experiment"
                else "not_in_request" for arm in ("a", "b")}
    record = max((latest[arm] for arm in ("a", "b") if blocks[arm]), key=lambda r: r["id"], default=None)
    block = _context_block(record) if record else None
    known = [c for c in coverage.values() if c]
    # Digests are compared only between requests of one tick (same cutoff).
    pair = _tick_pair(store, sid, latest) if sid is not None else None
    digests = {arm: (_context_block(request) or {}).get("shared_context_digest") for arm, request in zip(("a", "b"), pair)} if pair else {}
    return _node("ambient_context", cutoff, record=record, observed=_state(record).get("observation_cutoff") if record else None,
        freshness="not_applicable", provisioned=None if not known else any(c in ("supplied", "withheld_by_experiment", "timezone_unavailable") for c in known),
        basis="admitted_request" if record else "not_recorded", declared=declared[0], declared_basis=declared[1], consumed=consumed,
        version={"schema": (block.get("ambient") or {}).get("schema"), "context_schema": block.get("schema"),
                 "digests_equal": digests["a"] == digests["b"] if len(digests) == 2 and all(digests.values()) else None,
                 "digest_basis": "same_tick" if pair else "no_same_tick_pair_in_scan",
                 "compared_cutoff": _cutoff_of(pair[0]) if pair else None} if block else None,
        native={"kind": "reasoning.request", "event_id": record["id"], "snippet": _memo(store, ("ambient", record["id"]),
                lambda: snippet({"coverage": block.get("coverage"), "ambient": block.get("ambient"),
                                 "digest_scope": block.get("digest_scope"), "shared_context_digest": block.get("shared_context_digest")}))} if record else None)


def _qwen_node(store, sid, horizon, cutoff, latest, unknown, requests, declared):
    """The continuous Qwen scene source: per-arm coverage from each arm's
    latest admitted request, the last projection an admitted request carried
    (closed-vocabulary tokens only) and the latest receipt's enum facts. No
    digest, identifier, time or reply text is copied."""
    receipt, bounded = _latest(store, sid, "qwen.observation", horizon, cutoff)
    blocks = {arm: _context_block(latest[arm]) for arm in ("a", "b")}
    coverage = {arm: (blocks[arm].get("coverage") or {}).get("qwen_scene") if blocks[arm] else None for arm in ("a", "b")}
    carried = [latest[arm] for arm in ("a", "b") if latest[arm] is not None
               and any(item.get("source_kind") == "qwen.observation" for item in _evidence(latest[arm]))]
    request = max(carried, key=lambda r: r["id"], default=None)
    projection = None
    if request is not None:
        projection = _memo(store, ("qwen_projection", request["id"]), lambda: next(
            (item.get("qwen_scene") for item in _evidence(request) if item.get("source_kind") == "qwen.observation"), None))
    value = receipt["payload"] if receipt else {}
    consumed = _consumed(requests, {"qwen.observation"}, unknown=unknown)
    for arm in ("a", "b"):
        if consumed[arm] == "not_in_request" and coverage[arm] == "withheld_by_experiment":
            consumed[arm] = "withheld_by_setup"
    known = [c for c in coverage.values() if c]
    return _node("qwen_scene", cutoff, record=receipt, bounded=bounded, observed=value.get("observed_at"), valid_until=value.get("valid_until"),
        provisioned=True if receipt else (None if not known else any(c not in ("not_provisioned",) for c in known)),
        basis="recorded_events" if receipt else "admitted_request" if known else "not_recorded",
        declared=declared[0], declared_basis=declared[1], consumed=consumed,
        version={"result_schema": value.get("result_schema"), "prompt_version": value.get("prompt_version"),
                 "projection_schema": (projection or {}).get("schema"), "coverage": coverage} if receipt or known else None,
        native={"kind": "qwen.observation", "event_id": receipt["id"] if receipt else None,
                "snippet": _memo(store, ("qwen_native", receipt["id"] if receipt else None, request["id"] if request else None),
                                 lambda: snippet({"last_projection": projection,
                                                  "latest_receipt": {k: value.get(k) for k in ("status", "invalid_reason", "facts")}
                                                  if receipt else None}))} if receipt or projection else None)


def _vjepa_node(store, sid, horizon, cutoff, latest, unknown, requests, declared):
    """The V-JEPA 2.1 den source (temporal_encoder): per-arm coverage from each
    arm's latest admitted request, the last projection an admitted request
    carried (bucket tokens only) and the latest receipt's status. No
    probability, box, digest, identifier or time is copied."""
    receipt, bounded = _latest(store, sid, "vjepa.observation", horizon, cutoff)
    blocks = {arm: _context_block(latest[arm]) for arm in ("a", "b")}
    coverage = {arm: (blocks[arm].get("coverage") or {}).get("temporal_encoder") if blocks[arm] else None for arm in ("a", "b")}
    carried = [latest[arm] for arm in ("a", "b") if latest[arm] is not None
               and any(item.get("source_kind") == "vjepa.observation" for item in _evidence(latest[arm]))]
    request = max(carried, key=lambda r: r["id"], default=None)
    projection = None
    if request is not None:
        projection = _memo(store, ("vjepa_projection", request["id"]), lambda: next(
            (item.get("vjepa_scene") for item in _evidence(request) if item.get("source_kind") == "vjepa.observation"), None))
    value = receipt["payload"] if receipt else {}
    consumed = _consumed(requests, {"vjepa.observation"}, unknown=unknown)
    for arm in ("a", "b"):
        if consumed[arm] == "not_in_request" and coverage[arm] in ("withheld_by_experiment", "not_in_baseline"):
            consumed[arm] = "withheld_by_setup"
    known = [c for c in coverage.values() if c]
    return _node("vjepa_scene", cutoff, record=receipt, bounded=bounded, observed=value.get("observed_at"), valid_until=value.get("valid_until"),
        provisioned=True if receipt else (None if not known else any(c not in ("not_provisioned", "not_in_baseline") for c in known)),
        basis="recorded_events" if receipt else "admitted_request" if known else "not_recorded",
        declared=declared[0], declared_basis=declared[1], consumed=consumed,
        version={"result_schema": value.get("result_schema"), "receipt_schema": value.get("schema"),
                 "projection_schema": (projection or {}).get("schema"), "coverage": coverage} if receipt or known else None,
        native={"kind": "vjepa.observation", "event_id": receipt["id"] if receipt else None,
                "snippet": _memo(store, ("vjepa_native", receipt["id"] if receipt else None, request["id"] if request else None),
                                 lambda: snippet({"last_projection": projection,
                                                  "latest_receipt": {k: value.get(k) for k in ("status", "invalid_reason")}
                                                  if receipt else None}))} if receipt or projection else None)


def _prepared_stage(request, enabled, bounded=False):
    if request is None:
        return {"stage": "prepared_evidence", "status": "unknown" if bounded else "not_saved",
                "reason": "older_than_scan_bound" if bounded else "reasoning_off" if not enabled else "no_admitted_request",
                "note": "prepared evidence is persisted only inside admitted JEV requests"}
    state = _state(request)
    evidence = _evidence(request)
    by_source = {}
    for item in evidence:
        kind = item.get("source_kind") or "perception.output"
        by_source[kind] = by_source.get(kind, 0)+1
    semantic = state.get("semantic") if isinstance(state.get("semantic"), dict) else None
    return {"stage": "prepared_evidence", "status": "saved", "event_id": request["id"], "request_id": request["payload"].get("request_id"),
            "observation_cutoff": state.get("observation_cutoff"), "records": len(evidence), "by_source": by_source,
            "evidence": [{k: item.get(k) for k in ("id", "source_kind", "observed_at", "available_at", "valid_until", "frame_id", "evidence_roots")}
                         for item in evidence[:64]],
            "evidence_truncated": len(evidence) > 64, "semantic_version": semantic.get("version") if semantic else None,
            "exclusions": _exclusions(request), "snippet": snippet(evidence), "semantic_snippet": snippet(semantic) if semantic else None}


def _request_stage(request, output, bounded=False):
    if request is None:
        return {"stage": "consumed_request", "status": "unknown", "reason": "older_than_scan_bound"} if bounded else {"stage": "consumed_request", "status": "none"}
    value = request["payload"]
    packet = value.get("request") if isinstance(value.get("request"), dict) else {}
    judgment = output["payload"].get("judgment") if output and output["payload"].get("request_id") == value.get("request_id") else None
    diagnostics = judgment.get("diagnostics") if isinstance(judgment, dict) and isinstance(judgment.get("diagnostics"), dict) else {}
    state = packet.get("state", {}) if isinstance(packet.get("state"), dict) else {}
    semantic = state.get("semantic") if isinstance(state.get("semantic"), dict) else {}
    return {"stage": "consumed_request", "status": "admitted", "event_id": request["id"], "request_id": value.get("request_id"),
            "admitted_at": request["at"], "generation": value.get("generation"), "packet_sha256": value.get("packet_sha256"),
            "transport_status": value.get("status"), "bytes": len(encode(packet).encode()), "model": packet.get("model"),
            "question_ids": sorted(packet.get("questions", {})) if isinstance(packet.get("questions"), dict) else [],
            "question_version": diagnostics.get("question_version"), "projection_version": diagnostics.get("projection_version"),
            # Equal across arms offered in one tick unless a family is the lever.
            "shared_context_digest": (_context_block(request) or {}).get("shared_context_digest"),
            "semantic_version": semantic.get("version"), "versions_basis": "result_diagnostics" if diagnostics else "result_not_recorded",
            "snippet": snippet(packet)}


def _result_stage(request, delivery, output, reason, cutoff, ended, bounded=False, lifecycle=None, store=None):
    stage = {"stage": "result", "status": "none", "in_flight": None, "latest_lifecycle": lifecycle}
    request_id = request["payload"].get("request_id") if request else None
    if request and (delivery is None or (delivery["payload"].get("request_id") != request_id and delivery["at"] <= request["at"])):
        # Without this arm's delivery inside the scan bound its result is unknown.
        stage["in_flight"] = {"request_id": request_id, "admitted_at": request["at"], "age_s": round(cutoff-request["at"], 3),
                              "status": "unknown_older_than_scan_bound" if delivery is None and bounded
                              else "no_result_recorded" if ended else "awaiting_result"}
    if delivery is None:
        if bounded:
            stage.update(status="unknown", reason="older_than_scan_bound")
        elif stage["in_flight"]:
            stage["status"] = stage["in_flight"]["status"]
        return stage
    value = delivery["payload"]
    stage.update(status=value.get("status") or value.get("state"), state=value.get("state"), request_id=value.get("request_id"),
                 same_request=value.get("request_id") == request_id, event_id=delivery["id"], available_at=delivery["at"],
                 current=value.get("current"), failure_reason=reason)
    if request and value.get("request_id") == request_id:
        stage["timings"] = {"admission_to_result_ms": round((delivery["at"]-request["at"])*1000, 3)}
        cutoff_at = _state(request).get("observation_cutoff")
        if finite(cutoff_at):
            stage["timings"]["evidence_cutoff_to_admission_ms"] = round((request["at"]-cutoff_at)*1000, 3)
    if output:
        out = output["payload"]
        judgment = out.get("judgment") if isinstance(out.get("judgment"), dict) else {}
        diagnostics = judgment.get("diagnostics") if isinstance(judgment.get("diagnostics"), dict) else {}
        stage.update(status=out.get("status"), output_event_id=output["id"], provider_status=out.get("provider_status"),
                     abstained=out.get("provider_status") == "abstain", activity=judgment.get("activity"),
                     probability=judgment.get("probability"), confidence=judgment.get("confidence"),
                     distribution=judgment.get("distribution", [])[:16] if isinstance(judgment.get("distribution"), list) else None,
                     model_version=judgment.get("model_version"), fresh_on_arrival=out.get("fresh_on_arrival"),
                     valid_until=out.get("valid_until"), source_end_s=out.get("source_end_s"), available_at_s=out.get("available_at_s"),
                     freshness=_freshness(out.get("valid_until"), cutoff), usage=diagnostics.get("usage"),
                     usage_status=diagnostics.get("usage_status"), policy_result=out.get("policy_result"),
                     answers=(None if diagnostics.get("answers") is None else snippet(diagnostics["answers"]) if store is None
                              else _memo(store, ("answers", output["id"]), lambda: snippet(diagnostics["answers"]))))
    return stage


def _inactive(stage):
    item = {"stage": stage, "status": "inactive", "reason": "actuation_deferred"}
    if stage == "policy":
        item["note"] = "the result's policy_result is a non-actuating suggestion; no decision policy runs"
    if stage == "human_review":
        item["note"] = "interval labels are review data, never model inputs"
    return item


def graph(store, sid, at_ms=None):
    """Stage graph at a cursor: live when at_ms is None, else replay at session ms."""
    # Cleaned dicts are shared only within this read, on this thread. Snippet
    # JSON is never mutated after it is built, so sharing it is safe.
    _local.shared = {}
    try:
        return _graph(store, sid, at_ms)
    finally:
        _local.shared = None


def _graph(store, sid, at_ms):
    session = store.get_session(sid)
    cursor = _cursor(store, session, at_ms)
    cutoff = cursor["cutoff"]
    horizon = _horizon(store, sid, cutoff+HORIZON_SLACK_S)
    activated, _ = _latest(store, sid, "experiment.activated", horizon, cutoff)
    experiment = None
    if activated:
        value = activated["payload"]
        experiment = {k: value.get(k) for k in ("experiment_id", "generation", "effective_at", "sampling_hz", "reasoning_windows", "ha_evidence", "caption_evidence",
                                                "source_policy", "lever", "baseline_sha256")}
        experiment.update(event_id=activated["id"], profiles={arm: (value.get("profiles") or {}).get(arm) for arm in ("a", "b")},
                          spatial_policy=_memo(store, ("spatial_policy", activated["id"]), lambda: snippet(value.get("spatial_policy")))
                          if value.get("spatial_policy") is not None else None)
    setting, _ = _latest(store, sid, "reasoning.setting", horizon, cutoff)
    reasoning = {"enabled": setting["payload"].get("enabled") is True if setting else False,
                 "revision": setting["payload"].get("revision") if setting else None}
    live = cursor["mode"] == "live"
    # The recipe of the experiment recorded at the cursor; the session's current
    # experiment stands in only for a live cursor before any activation.
    recipe = _recipe((experiment or {}).get("experiment_id") or (session["experiment_id"] if live else None))
    setups = {arm: _setup(activated["payload"] if activated else None, recipe, arm, live) for arm in ("a", "b")}
    ended = finite(session["ended_at"]) and cutoff >= session["ended_at"]
    reasons, arms, requests, consumed_ids, unknown, latest = {}, {}, {}, {}, {}, {}
    specialists = {}
    for arm in ("a", "b"):
        request, request_bounded, delivery, delivery_bounded, output, reason, lifecycle = _reasoning(store, sid, arm, horizon, cutoff, reasons)
        requests[arm] = _request_kinds(request)
        latest[arm] = request
        # No request within the scan bound is unknown, not a known absence.
        unknown[arm] = request is None and request_bounded
        consumed_ids[arm] = None if request is None else {str(item.get("id")) for item in _evidence(request)}
        specialists[arm] = _specialist(store, sid, arm, horizon, cutoff, experiment)
        request_id = request["id"] if request else None
        chain = [_specialist_stage(arm, *specialists[arm], experiment, cutoff, consumed_ids[arm]),
                 _memo(store, ("prepared_stage", request_id, reasoning["enabled"], request_bounded),
                       lambda: _prepared_stage(request, reasoning["enabled"], request_bounded)),
                 _memo(store, ("request_stage", request_id, output["id"] if output else None, request_bounded),
                       lambda: _request_stage(request, output, request_bounded)),
                 _result_stage(request, delivery, output, reason, cutoff, ended, delivery_bounded, lifecycle, store=store)]
        chain.extend(_inactive(stage) for stage in INACTIVE)
        values, basis = setups[arm]
        arms[arm] = {"arm": arm, "generation": (experiment or {}).get("generation"), "profile": (experiment or {}).get("profiles", {}).get(arm),
                     "setup": values if any(b != "not_recorded" for b in basis.values()) else None, "setup_basis": basis, "chain": chain,
                     "preparation": _memo(store, ("preparation", request_id, reasoning["enabled"], request_bounded),
                                          lambda: _preparation(request, reasoning["enabled"], request_bounded))}
    sources = []

    def declared(field, test):
        # Declarations follow the setup at the cursor; null when not recorded.
        values = {arm: setups[arm][0][field] for arm in ("a", "b")}
        return ({arm: None if values[arm] is None else test(values[arm]) for arm in ("a", "b")},
                {arm: setups[arm][1][field] for arm in ("a", "b")})

    def recorded(field):
        # Only a recorded per-arm false withholds a source; the catalog never does.
        return {arm: setups[arm][0][field] if setups[arm][1][field] == "recorded_activation" else None for arm in ("a", "b")}
    # RGB acquisition and depth bundles are media rows, not events.
    with store.lock:
        grouped = store.db.execute("""SELECT mime_type,json_extract(metadata,'$.stream')='rgb',count(*) FROM media
            WHERE session_id=? AND created_at<=? GROUP BY 1,2""", (sid, cutoff)).fetchall()
        counts, rgb_images = {}, 0
        for mime, rgb, total in grouped:
            counts[mime] = counts.get(mime, 0)+total
            rgb_images += total if rgb == 1 and mime in ("image/jpeg", "image/png") else 0
        deleted = store.db.execute("SELECT state FROM media_deletions WHERE session_id=?", (sid,)).fetchone()
        # The RGB stream only: bundle left/right views are PNG images too, but
        # other streams without an RGB observation time.
        frame = store.db.execute("""SELECT id,mime_type,size,created_at,metadata FROM media WHERE session_id=? AND created_at<=?
            AND mime_type IN ('image/jpeg','image/png') AND json_extract(metadata,'$.stream')='rgb'
            ORDER BY created_at DESC,rowid DESC LIMIT 1""", (sid, cutoff)).fetchone()
        bundle = store.db.execute("""SELECT id,mime_type,size,created_at,metadata FROM media WHERE session_id=? AND created_at<=?
            AND mime_type='application/x-npz' ORDER BY created_at DESC,rowid DESC LIMIT 1""", (sid, cutoff)).fetchone()
    media_reason = "media_deleted_by_user" if deleted else None
    for key, row, total in (("rgb_camera", frame, counts.get("image/jpeg", 0)+counts.get("image/png", 0)+counts.get("video/mp4", 0)),
                            ("depth_bundle", bundle, counts.get("application/x-npz", 0))):
        meta = json.loads(row[4]) if row else {}
        record = {"at": meta["available_at"] if finite(meta.get("available_at")) else row[3]} if row else None
        stereo, stereo_basis = declared("localization", lambda v: v == "stereo")
        sources.append(_node(key, cutoff, record=record, observed=meta.get("observed_at"), valid_until=meta.get("valid_until"),
            provisioned=True if total else None, basis="recorded_media" if total else "not_recorded", reason=media_reason,
            declared={"a": True, "b": True} if key == "rgb_camera" else stereo,
            declared_basis={"a": "every_setup", "b": "every_setup"} if key == "rgb_camera" else stereo_basis,
            version={k: meta.get(k) for k in ("stream", "format", "clock_mapping_status", "timestamp_semantics", "calibration_id", "archive_kind") if k in meta} or None,
            native={"kind": "media", "media_id": row[0], "content_type": row[1], "size_bytes": row[2],
                    "snippet": _memo(store, ("media", row[0]), lambda: snippet(meta))} if row else None,
            counts={"images": counts.get("image/jpeg", 0)+counts.get("image/png", 0), "rgb_images": rgb_images,
                    "video_objects": counts.get("video/mp4", 0)} if key == "rgb_camera"
                   else {"bundles": counts.get("application/x-npz", 0)}))
    profiles = (experiment or {}).get("profiles", {})
    for arm in ("a", "b"):
        output, bounded, failure, _ = specialists[arm]
        value = output["payload"] if output else {}
        profile = profiles.get(arm)
        # A recorded activation profile declares the arm's specialist; else the
        # live catalog's detector/pose, labelled current_catalog.
        spec, spec_basis = {}, {}
        for x in ("a", "b"):
            fields = setups[x]
            if profiles.get(x):
                spec[x], spec_basis[x] = x == arm, "recorded_activation"
            elif "current_catalog" in (fields[1]["detector"], fields[1]["pose"]):
                spec[x], spec_basis[x] = x == arm and (fields[0]["detector"] is not None or fields[0]["pose"] is not None), "current_catalog"
            else:
                spec[x], spec_basis[x] = None, "not_recorded"
        sources.append(_node("specialist_"+arm, cutoff, record=output, bounded=bounded, observed=value.get("observed_at"),
            valid_until=value.get("valid_until"), provisioned=True if profile else None,
            basis="experiment_activated" if profile else "not_recorded", failure=failure, declared=spec, declared_basis=spec_basis,
            consumed=_consumed(requests, {"perception.output"}, arm_only=arm, unknown=unknown),
            version={"profile": profile, "model_version": value.get("model_version"), "artifact_sha256": value.get("artifact_sha256"),
                     "engine_epoch": value.get("engine_epoch")} if output or profile else None,
            native={"kind": "perception.output", "event_id": output["id"],
                    "snippet": _memo(store, ("result", output["id"]), lambda: snippet(value.get("result")))} if output else None))
    shared, bounded = _latest(store, sid, "perception.shared_detector", horizon, cutoff)
    bundle_value = shared["payload"].get("bundle", {}) if shared else {}
    sources.append(_node("shared_detector", cutoff, record=shared, bounded=bounded, provisioned=True if shared else None,
        basis="recorded_events" if shared else "not_recorded", version=bundle_value.get("reference"),
        counts={"crops": len(bundle_value.get("crops", []))} if shared and isinstance(bundle_value.get("crops"), list) else None,
        native={"kind": "perception.shared_detector", "event_id": shared["id"],
                "snippet": _memo(store, ("shared_detector", shared["id"]), lambda: snippet(bundle_value))} if shared else None))
    ha, bounded = _latest(store, sid, "ha.observation", horizon, cutoff)
    unavailable, _ = _latest(store, sid, "ha.unavailable", horizon, cutoff, scan=16)
    value = ha["payload"] if ha else {}
    facts = value.get("ha") if isinstance(value.get("ha"), dict) else {}
    ha_declared, ha_basis = declared("ha_evidence", lambda v: v is not False)
    sources.append(_node("ha_snapshot", cutoff, record=ha, bounded=bounded, observed=value.get("observed_at"), valid_until=value.get("valid_until"),
        provisioned=True if ha else False if unavailable else None,
        basis="recorded_events" if ha else "ha_source_invalid" if unavailable else "not_recorded",
        failure={"kind": "ha.unavailable", "event_id": unavailable["id"], "at": unavailable["at"], "reason": unavailable["payload"].get("reason")} if unavailable else None,
        declared=ha_declared, declared_basis=ha_basis,
        consumed=_consumed(requests, {"ha.observation"}, withheld=recorded("ha_evidence"), unknown=unknown),
        version={"source_id": (value.get("quality") or {}).get("source_id"), "timing_basis": (value.get("quality") or {}).get("timing_basis")} if ha else None,
        counts={"entities": len(facts), "known": sum(1 for f in facts.values() if isinstance(f, dict) and f.get("status") == "known"),
                "camera_derived": sum(1 for f in facts.values() if isinstance(f, dict) and f.get("origin") == "same_camera_visual")} if ha else None,
        native={"kind": "ha.observation", "event_id": ha["id"],
                "snippet": _memo(store, ("ha", ha["id"]), lambda: snippet({k: value.get(k) for k in ("observed_at", "available_at", "valid_until", "ha", "quality", "snapshot_digest", "acquisition_duration_s")}))} if ha else None))
    caption, bounded = _latest(store, sid, "caption.receipt", horizon, cutoff)
    value = caption["payload"] if caption else {}
    projection = value.get("projection") if isinstance(value.get("projection"), dict) else {}
    caption_declared, caption_basis = declared("caption_evidence", lambda v: v is True)
    # Allowlisted receipt fields only; caption prose is never copied here.
    sources.append(_node("caption_receipt", cutoff, record=caption, bounded=bounded, observed=value.get("observed_at"), valid_until=value.get("valid_until"),
        provisioned=True if caption else None, basis="recorded_events" if caption else "not_recorded",
        declared=caption_declared, declared_basis=caption_basis,
        consumed=_consumed(requests, {"caption.receipt", "historical_qwen"}, withheld=recorded("caption_evidence"), unknown=unknown),
        version={"model": value.get("model"), "model_revision": value.get("model_revision"), "projection_version": projection.get("version")} if caption else None,
        native={"kind": "caption.receipt", "event_id": caption["id"], "snippet": _memo(store, ("caption", caption["id"]), lambda: snippet({k: value.get(k) for k in (
            "model", "model_revision", "processing_site_id", "request_id", "frame_id", "observed_at", "generated_at", "first_received_at", "available_at",
            "valid_until", "original_valid_until", "uncertainty_seconds", "timestamp_semantics", "caption_digest", "projection", "source_kind", "cloud_status")
            if k in value}))} if caption else None))
    sources.append(_ambient_node(store, cutoff, latest, unknown, declared("ambient_evidence", lambda v: v == "granted"), sid=sid))
    sources.append(_qwen_node(store, sid, horizon, cutoff, latest, unknown, requests,
                              declared("qwen_scene_evidence", lambda v: v == "granted")))
    sources.append(_vjepa_node(store, sid, horizon, cutoff, latest, unknown, requests,
                               declared("temporal_encoder_evidence", lambda v: v == "granted")))
    room, bounded = _latest(store, sid, "room.observation", horizon, cutoff)
    room_output, _ = _latest(store, sid, "room.output", horizon, cutoff, scan=64)
    value = room["payload"] if room else {}
    context = (room_output["payload"].get("room") if room_output else None) or {}
    stereo, stereo_basis = declared("localization", lambda v: v == "stereo")
    sources.append(_node("room_observation", cutoff, record=room or room_output, bounded=bounded and not room_output,
        observed=value.get("observed_at"), valid_until=value.get("valid_until"), freshness=None if room else ("unknown" if room_output else None),
        provisioned=True if room or room_output else None, basis="recorded_events" if room or room_output else "not_recorded",
        declared=stereo, declared_basis=stereo_basis,
        consumed=_consumed(requests, {"room.observation"}, unknown=unknown),
        version={"calibration_version": value.get("calibration_version") or context.get("calibration_version"),
                 "map_version": value.get("map_version") or context.get("map_version"),
                 "coordinate_frame": value.get("coordinate_frame") or context.get("coordinate_frame")} if room or room_output else None,
        native={"kind": "room.observation" if room else "room.output", "event_id": (room or room_output)["id"],
                "snippet": _memo(store, ("room", (room or room_output)["id"]), lambda: snippet(value if room else context))} if room or room_output else None))
    deliveries = {arm: specialists[arm][3] for arm in ("a", "b") if specialists[arm][3]}
    latest_output = max((specialists[arm][0] for arm in ("a", "b") if specialists[arm][0]), key=lambda r: r["at"], default=None)
    sources.append(_node("processing_health", cutoff, record=latest_output, freshness="not_applicable",
        provisioned=True if deliveries else None, basis="recorded_events" if deliveries else "not_recorded",
        # A delivery is determined by its arm's output (written together).
        native={"kind": "perception.delivery", "snippet": _memo(store, ("deliveries",)+tuple((arm, specialists[arm][0]["id"]) for arm in sorted(deliveries)),
                lambda: snippet({arm: {k: v for k, v in item.items() if k != "result"} for arm, item in deliveries.items()}))} if deliveries else None))
    with store.lock:
        labels = store.db.execute("SELECT count(*),sum(json_extract(payload,'$.reviewed')=1) FROM annotations WHERE session_id=? AND deleted=0", (sid,)).fetchone()
    sources.append(_node("human_labels", cutoff, freshness="not_applicable", provisioned=True, basis="annotation_store",
        consumed={"a": "excluded_by_policy", "b": "excluded_by_policy"}, counts={"labels": labels[0], "reviewed": labels[1] or 0},
        reason="review data only; never model inputs", status="review_only"))
    for number, title, group, catalog, target, reason in FAMILIES:
        if not isinstance(target, tuple):
            sources.append(_proposed(number, title, group, catalog, target, reason))
    families = [{"number": number, "title": title, "group": group, "catalog_status": catalog,
                 "lab_status": "recorded_source" if isinstance(target, tuple) else target,
                 "nodes": list(target) if isinstance(target, tuple) else [f"family_{number:02d}"], "note": reason}
                for number, title, group, catalog, target, reason in FAMILIES]
    result = {"version": 1, "contract": CONTRACT, "contract_revision": CONTRACT_REVISION, "site_id": store.settings.site_id, "session_id": sid,
              "session": _session(session), "cursor": cursor, "horizon_event_id": horizon, "experiment": experiment, "reasoning": reasoning,
              "sources": sources, "families": families, "arms": arms, "frame_age": _frame_age(cursor, frame, specialists), "inference": "none",
              "bounds": {"snippet_bytes": SNIPPET_BYTES, "scan_rows": SCAN_ROWS, "response_bytes": RESPONSE_BYTES,
                         "horizon_slack_s": HORIZON_SLACK_S, "producing_window_s": PRODUCING_WINDOW_S}}
    if len(encode(result).encode()) > RESPONSE_BYTES:
        raise LabError("graph exceeds response budget", 413, "graph_size_limit")
    return result


def _range(store, session, from_ms, to_ms):
    limit = store.settings.max_session_seconds*1000
    for value in (from_ms, to_ms):
        if value is not None and (type(value) is not int or not 0 <= value <= limit):
            raise LabError("invalid timeline range", 422, "invalid_range")
    if from_ms is not None and to_ms is not None and from_ms > to_ms:
        raise LabError("invalid timeline range", 422, "invalid_range")
    start, now = session["started_at"], store.clock()
    end = session["ended_at"] if finite(session["ended_at"]) else now
    duration = max(0, min(limit, round((end-start)*1000)))
    low = from_ms or 0
    high = to_ms if to_ms is not None else max(duration, low)
    return low, high, duration


def _rows(store, sql, params, limit):
    with store.lock:
        rows = store.db.execute(sql+" LIMIT ?", (*params, limit+1)).fetchall()
    return rows[:limit], len(rows) > limit


def _walk(store, sql, head, first, last, limit, chunk=1000):
    """Ascending id walk for `... AND id>? AND id<=? ORDER BY id` in chunks.

    The ledger lock is released between chunks, so a long history never stalls
    live recording writes for a whole scan. Ids after `last` are never read.
    """
    rows, low = [], first
    while len(rows) <= limit:
        with store.lock:
            part = store.db.execute(sql+" LIMIT ?", (*head, low, last, chunk)).fetchall()
        rows.extend(part)
        if len(part) < chunk:
            break
        low = part[-1][0]
    return rows[:limit], len(rows) > limit


def _downsample(points, low, high, cap):
    # Deterministic: the latest point in each of `cap` equal session-time buckets.
    if len(points) <= cap:
        return points, False
    width = max(1, high-low)
    kept = {}
    for point in points:
        kept[min(cap-1, (point["t_ms"]-low)*cap//width)] = point
    return [kept[key] for key in sorted(kept)], True


def _cap(items, cap, cut=False):
    # cut: the scan stopped at its row bound, so total is only a lower bound.
    return {"items": items[:cap], "total": len(items), "truncated": len(items) > cap or cut, "scan_truncated": cut}


def _sampled(items, low, high, cut=False):
    """Deterministic latest_per_bucket over the range instead of the earliest TRACK_ITEMS."""
    kept, downsampled = _downsample(items, low, high, TRACK_ITEMS)
    return {"items": kept, "total": len(items), "returned": len(kept), "downsampled": downsampled,
            "method": "latest_per_bucket" if downsampled else None, "truncated": downsampled or cut, "scan_truncated": cut}


def timeline(store, sid, from_ms=None, to_ms=None):
    """Bounded replay tracks over [from_ms,to_ms] of session time (default all)."""
    session = store.get_session(sid)
    base = {"version": 1, "contract": CONTRACT, "contract_revision": CONTRACT_REVISION, "site_id": store.settings.site_id, "session_id": sid,
            "session": _session(session), "inference": "none"}
    start = session["started_at"]
    if not finite(start):
        _range(store, {**session, "started_at": 0, "ended_at": 0}, from_ms, to_ms)
        return {**base, "range": None, "reason": "recording_not_started", "clock": None, "tracks": None}
    low, high, duration = _range(store, session, from_ms, to_ms)
    t0, t1 = start+low/1000, start+high/1000
    first, last = _horizon(store, sid, t0-0.001), _horizon(store, sid, t1+HORIZON_SLACK_S)
    ms = lambda t: round((t-start)*1000) if finite(t) else None
    within = lambda t: t is not None and low <= t <= high
    with store.lock:
        activations = [json.loads(row[0]) for row in store.db.execute(
            "SELECT payload FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='experiment.activated' ORDER BY id LIMIT 256", (sid,)).fetchall()]
    rows, scan_truncated = _walk(store, """SELECT id,json_extract(payload,'$.arm','$.generation','$.frame_id','$.available_at',
        '$.observed_at','$.valid_until','$.source_end_s','$.mode') FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='perception.output'
        AND id>? AND id<=? ORDER BY id""", (sid,), first, last, TIMELINE_ROWS)
    arms = {"a": [], "b": []}
    mapped = total = 0
    scanned = None
    for event_id, light in rows:
        arm, generation, frame_id, available, observed, valid, source_end, mode = json.loads(light)
        if finite(available):
            scanned = max(scanned or 0, ms(available))
        if arm not in arms or mode != "live" or not finite(available):
            continue
        t = ms(available)
        if not within(t):
            continue
        total += 1
        mapped += finite(source_end)
        arms[arm].append({"t_ms": t, "source_ms": round(source_end*1000) if finite(source_end) else None, "event_id": event_id,
                          "frame_id": frame_id, "generation": generation,
                          "status": "fresh" if finite(valid) and valid >= available else "stale"})
    perception, gaps = {}, {}
    # A cut scan saw outputs only up to its last row: no gap is claimed beyond it.
    end = min(high, duration) if not scan_truncated else min(high, duration, scanned if scanned is not None else low)
    for arm, points in arms.items():
        points.sort(key=lambda p: (p["t_ms"], p["event_id"]))
        # Gaps are measured on every point in range, before any downsampling.
        found = []
        for index, activation in enumerate(activations):
            begin = ms(activation.get("effective_at"))
            later = [p for p in points if p["generation"] == activation.get("generation")]
            if not within(begin):
                continue
            if later and later[0]["t_ms"]-begin > _threshold(activation, arm):
                found.append({"from_ms": begin, "to_ms": later[0]["t_ms"], "gap_ms": later[0]["t_ms"]-begin, "kind": "leading", "generation": activation.get("generation")})
            elif not later:
                # A setup that never delivered in range is one gap, not silence.
                following = ms(activations[index+1].get("effective_at")) if index+1 < len(activations) else None
                stop = end if following is None else min(end, following)
                if stop-begin > _threshold(activation, arm):
                    found.append({"from_ms": begin, "to_ms": stop, "gap_ms": stop-begin, "kind": "no_output", "generation": activation.get("generation")})
        for before, after in zip(points, points[1:]):
            limit = _threshold(_active(activations, before["generation"]), arm)
            if after["t_ms"]-before["t_ms"] > limit:
                found.append({"from_ms": before["t_ms"], "to_ms": after["t_ms"], "gap_ms": after["t_ms"]-before["t_ms"],
                              "kind": "transition" if after["generation"] != before["generation"] else "internal", "generation": after["generation"]})
        if points and end-points[-1]["t_ms"] > _threshold(_active(activations, points[-1]["generation"]), arm):
            found.append({"from_ms": points[-1]["t_ms"], "to_ms": end, "gap_ms": end-points[-1]["t_ms"],
                          "kind": "trailing", "generation": points[-1]["generation"]})
        found.sort(key=lambda g: (g["from_ms"], g["to_ms"]))
        if len(found) > TRACK_ITEMS:
            largest = sorted(found, key=lambda g: (-g["gap_ms"], g["from_ms"]))[:TRACK_ITEMS]
            gaps[arm] = {"items": sorted(largest, key=lambda g: (g["from_ms"], g["to_ms"])), "total": len(found), "truncated": True,
                         "scan_truncated": scan_truncated, "measured_to_ms": end}
        else:
            gaps[arm] = {"items": found, "total": len(found), "truncated": scan_truncated, "scan_truncated": scan_truncated, "measured_to_ms": end}
        kept, downsampled = _downsample(points, low, high, TRACK_POINTS)
        perception[arm] = {"points": kept, "total": len(points), "returned": len(kept), "downsampled": downsampled, "scan_truncated": scan_truncated,
                           "method": "latest_per_bucket" if downsampled else None,
                           "bucket_ms": round((high-low)/TRACK_POINTS, 3) if downsampled else None}
    counted = [p for arm in perception for p in perception[arm]["points"]]
    for index in range(0, len(counted), 500):
        chunk = counted[index:index+500]
        with store.lock:
            found = {row[0]: row[1:] for row in store.db.execute(
                "SELECT id,json_array_length(payload,'$.result.objects'),json_array_length(payload,'$.result.pose_instances') FROM events WHERE id IN (%s)"
                % ",".join("?"*len(chunk)), [p["event_id"] for p in chunk]).fetchall()}
        for point in chunk:
            point["objects"], point["poses"] = found.get(point["event_id"], (None, None))
    markers = {"a": [], "b": []}
    marker_scan_truncated = False
    for kind in FAILURES:
        found, cut = _rows(store, "SELECT id,created_at,json_extract(payload,'$.arm','$.reason','$.request_id') FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind=? AND id>? AND id<=? ORDER BY id",
                           (sid, kind, first, last), TRACK_ITEMS*4)
        marker_scan_truncated |= cut
        for event_id, created, light in found:
            arm, reason, request_id = json.loads(light)
            if arm in markers and within(ms(created)):
                markers[arm].append({"t_ms": ms(created), "kind": kind.split(".", 1)[1], "reason": reason, "request_id": request_id, "event_id": event_id})
    # Drops are per frame per arm: one chunked walk (the lock is released between
    # chunks) counts routine reasons and keeps the rare non-routine ones.
    found, drops_cut = _walk(store, """SELECT id,created_at,json_extract(payload,'$.arm','$.reason','$.request_id') FROM events INDEXED BY events_session_kind_id
        WHERE session_id=? AND kind='perception.dropped' AND id>? AND id<=? ORDER BY id""", (sid,), first, last, DROP_ROWS)
    marker_scan_truncated |= drops_cut
    routine = {"a": {}, "b": {}}
    for event_id, created, light in found:
        arm, reason, request_id = json.loads(light)
        if arm not in markers or not within(ms(created)):
            continue
        if reason in ROUTINE_DROPS:
            routine[arm][reason] = routine[arm].get(reason, 0)+1
        else:
            markers[arm].append({"t_ms": ms(created), "kind": "dropped", "reason": reason, "request_id": request_id, "event_id": event_id})
    if _table(store, "paired_requests"):
        for arm in ("a", "b"):
            for generation in sorted({a.get("generation") for a in activations if type(a.get("generation")) is int}):
                found, cut = _rows(store, """SELECT id,updated_at,json_extract(result,'$.result.reason') FROM paired_requests WHERE session_id=?
                    AND arm=? AND generation=? AND state='rejected' AND updated_at>=? AND updated_at<=? ORDER BY updated_at""",
                                   (sid, arm, generation, t0, t1), TRACK_ITEMS)
                marker_scan_truncated |= cut
                markers[arm].extend({"t_ms": ms(at), "kind": "rejected", "reason": reason, "request_id": request_id, "event_id": None}
                                    for request_id, at, reason in found)
    perception_markers = {}
    for arm, items in markers.items():
        items.sort(key=lambda m: (m["t_ms"], m["kind"]))
        perception_markers[arm] = {**_cap(items, TRACK_ITEMS, marker_scan_truncated), "routine_drops": routine[arm],
                                   "routine_drops_is_lower_bound": drops_cut}
    # One walk per kind for both arms: each payload is parsed once, in SQLite.
    # Requests and results carry whole packets: walk them in small chunks. One
    # row bound for all three kinds; a cut is reported, never a complete total.
    requests, requests_cut = _walk(store, """SELECT id,json_extract(payload,'$.arm','$.request_id','$.available_at','$.generation','$.packet_sha256')
        FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='reasoning.request' AND id>? AND id<=? ORDER BY id""",
                        (sid,), first, last, JEV_ROWS, 200)
    deliveries, deliveries_cut = _walk(store, """SELECT id,json_extract(payload,'$.arm','$.request_id','$.available_at','$.state','$.status','$.current','$.generation','$.reason')
        FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='reasoning.delivery' AND id>? AND id<=? ORDER BY id""",
                          (sid,), first, last, JEV_ROWS)
    outputs, outputs_cut = _walk(store, """SELECT id,json_extract(payload,'$.arm','$.request_id','$.status','$.provider_status','$.judgment.activity',
        '$.judgment.probability','$.judgment.confidence','$.source_end_s','$.valid_until','$.fresh_on_arrival','$.prediction.valid_through',
        '$.available_at_s')
        FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='reasoning.output' AND id>? AND id<=? ORDER BY id""",
                       (sid,), first, last, JEV_ROWS, 200)
    # Facts-bound validity (local receipts): the earliest recorded end per request.
    closures, closures_cut = _walk(store, """SELECT id,json_extract(payload,'$.request_id','$.valid_through')
        FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='reasoning.validity' AND id>? AND id<=? ORDER BY id""",
                                   (sid,), first, last, JEV_ROWS)
    closed = {}
    for _, light in closures:
        request_id, through = json.loads(light)
        if finite(through):
            closed[request_id] = min(through, closed.get(request_id, through))
    answers, arrivals = {}, {"a": [], "b": []}
    for event_id, light in outputs:
        arm, request_id, *rest = json.loads(light)
        answers[(arm, request_id)] = (event_id, *rest)
        if arm in arrivals and finite(rest[-1]):
            arrivals[arm].append(rest[-1])
    requests = [(event_id, json.loads(light)) for event_id, light in requests]
    deliveries = [(event_id, json.loads(light)) for event_id, light in deliveries]
    jev, reasons = {}, {}
    for arm in ("a", "b"):
        items = []
        for event_id, (row_arm, request_id, available, generation, packet) in requests:
            if row_arm == arm and within(ms(available)):
                items.append({"t_ms": ms(available), "request_id": request_id, "generation": generation, "packet_sha256": packet, "event_id": event_id})
        results = []
        for event_id, (row_arm, request_id, available, state, status, current, generation, reason) in deliveries:
            if row_arm != arm or not within(ms(available)):
                continue
            item = {"t_ms": ms(available), "request_id": request_id, "generation": generation, "state": state, "status": status,
                    "current": current, "event_id": event_id, "reason": reason}
            if (arm, request_id) in answers:
                output_id, out_status, provider, activity, probability, confidence, source_end, valid, fresh, through, arrived = answers[(arm, request_id)]
                item.update(status=out_status, output_event_id=output_id, activity=activity, probability=probability, confidence=confidence,
                            abstained=provider == "abstain", fresh_on_arrival=fresh,
                            source_ms=round(source_end*1000) if finite(source_end) else None, valid_until_ms=ms(valid),
                            valid_through_ms=_valid_through_ms(through, arrived, closed.get(request_id), arrivals[arm], ms(valid)))
            results.append(item)
        # Whole-session views keep the latest item per bucket, not the earliest 600.
        jev[arm] = {"requests": _sampled(items, low, high, requests_cut),
                    "results": _sampled(results, low, high, deliveries_cut or outputs_cut or closures_cut)}
        # Terminal reasons are looked up only for returned results (bounded).
        for item in jev[arm]["results"]["items"]:
            if item["reason"] is None and item["state"] in {"failed", "indeterminate"}:
                item["reason"] = _reasoning_reason(store, item["request_id"], reasons)
    # The code-owned activity strip (reasoning.smoothed, local only): each step
    # holds until the next step or its own valid_until, whichever is first.
    steps, smoothed_cut = _walk(store, """SELECT id,json_extract(payload,'$.arm','$.generation','$.available_at','$.shown','$.prediction.confidence',
        '$.source','$.prediction.valid_until','$.request_id')
        FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='reasoning.smoothed' AND id>? AND id<=? ORDER BY id""",
                                (sid,), first, last, TIMELINE_ROWS)
    strip = {"a": [], "b": []}
    for event_id, light in steps:
        arm, generation, available, shown, confidence, source, until, request_id = json.loads(light)
        if arm in strip and within(ms(available)):
            strip[arm].append({"t_ms": ms(available), "shown": shown, "confidence": confidence, "source": source, "generation": generation,
                               "request_id": request_id, "until_ms": round(until*1000) if finite(until) else None, "event_id": event_id})
    jev_smoothed = {}
    for arm, items in strip.items():
        items.sort(key=lambda s: (s["t_ms"], s["event_id"]))
        for step, following in zip(items, items[1:]+[None]):
            if following is not None and following["generation"] == step["generation"]:
                step["until_ms"] = following["t_ms"] if step["until_ms"] is None else min(step["until_ms"], following["t_ms"])
        jev_smoothed[arm] = {**_sampled(items, low, high, smoothed_cut), "run_id": "jev" + arm.upper() + "-smooth",
                             "note": "code-owned HMM and hysteresis over delivered JEV answers and local gate abstentions; never sent to JEV"}
    kinds, kinds_cut = {}, False
    # Separate index ranges: an OR across them would let SQLite scan the table.
    for where, params in (("""kind IN ('experiment.activated','pipeline.switch','reasoning.setting','reasoning.reconciled',
            'reasoning.abandoned','reasoning.ingestion_failed','session.requested','runtime.start_result','runtime.stop_result','ha.unavailable')""", ()),
            ("kind>=? AND kind<?", ("recording.", "recording/")), ("kind>=? AND kind<?", ("workers.", "workers/"))):
        found, cut = _rows(store, "SELECT kind,created_at,payload,id FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND "+where+" ORDER BY id", (sid, *params), 4096)
        kinds_cut |= cut
        for kind, created, payload, event_id in found:
            kinds.setdefault(kind.split(".", 1)[0] if kind.startswith(("recording.", "workers.")) else kind, []).append((kind, created, json.loads(payload), event_id))
    transitions = []
    for kind, created, value, event_id in kinds.get("experiment.activated", []):
        transitions.append({"t_ms": ms(value.get("effective_at", created)), "kind": kind, "generation": value.get("generation"),
                            "experiment_id": value.get("experiment_id")})
    for kind, created, value, event_id in kinds.get("pipeline.switch", []):
        outcome = value.get("outcome") if isinstance(value.get("outcome"), dict) else {}
        request = value.get("request") if isinstance(value.get("request"), dict) else {}
        transitions.append({"t_ms": ms(created), "kind": kind, "state": outcome.get("state"), "reason": outcome.get("reason"),
                            "requested": {k: request.get(k) for k in ("experiment_id", "arm", "profile_id", "reasoning_enabled") if k in request}})
    for kind, created, value, event_id in kinds.get("reasoning.setting", []):
        transitions.append({"t_ms": ms(created), "kind": kind, "enabled": value.get("enabled"), "revision": value.get("revision")})
    transitions = [t for t in transitions if within(t["t_ms"])]
    transitions.sort(key=lambda t: t["t_ms"])
    recording = [{"t_ms": ms(created), "kind": kind, "state": kind.split(".", 1)[1] if kind.startswith("recording.")
                  else "requested" if kind == "session.requested" else value.get("state"),
                  "reason": value.get("reason")}
                 for group in ("session.requested", "recording", "runtime.start_result", "runtime.stop_result") for kind, created, value, event_id in kinds.get(group, [])]
    recording = sorted((r for r in recording if within(r["t_ms"])), key=lambda r: r["t_ms"])
    workers = []
    for kind, created, value, event_id in kinds.get("workers", []):
        detail = value.get("workers") if isinstance(value.get("workers"), dict) else value.get("receipts") if isinstance(value.get("receipts"), dict) else {}
        workers.append({"t_ms": ms(created), "kind": kind, "state": value.get("state"), "reason": value.get("reason"),
                        "local_drain": value.get("local_drain"),
                        "workers": {arm: {"state": item.get("state"), "reason": item.get("reason")} for arm, item in detail.items()
                                    if arm in {"a", "b"} and isinstance(item, dict)}})
    workers = [w for w in workers if within(w["t_ms"])]
    arrival = lambda value, created: ms(value["available_at"] if finite(value.get("available_at")) else created)
    jev_markers = [{"t_ms": arrival(value, created), "kind": kind, "arm": value.get("arm"), "state": value.get("state"),
                    "reason": value.get("reason"), "request_id": value.get("request_id"), "event_id": event_id}
                   for group in ("reasoning.reconciled", "reasoning.abandoned", "reasoning.ingestion_failed")
                   for kind, created, value, event_id in kinds.get(group, [])
                   if within(arrival(value, created))]
    # Pending requests that ended without dispatch. Replacements are routine and
    # counted per arm; expiry, cancellation and local dispatch failure are markers.
    found, gates_cut = _walk(store, """SELECT id,created_at,json_extract(payload,'$.arm','$.reason','$.generation','$.available_at')
        FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='reasoning.gate' AND id>? AND id<=? ORDER BY id""",
                             (sid,), first, last, LIFECYCLE_ROWS)
    for event_id, created, light in found:
        arm, reason, generation, available = json.loads(light)
        t = ms(available if finite(available) else created)
        if within(t):
            jev_markers.append({"t_ms": t, "kind": "reasoning.gate", "arm": arm, "state": "abstain", "reason": reason,
                                "request_id": None, "generation": generation, "event_id": event_id})
    found, lifecycle_cut = _walk(store, """SELECT id,created_at,json_extract(payload,'$.arm','$.state','$.reason','$.request_id','$.generation','$.available_at')
        FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='reasoning.lifecycle' AND id>? AND id<=? ORDER BY id""",
                                 (sid,), first, last, LIFECYCLE_ROWS)
    routine_lifecycle = {"a": {}, "b": {}}
    for event_id, created, light in found:
        arm, state, reason, request_id, generation, available = json.loads(light)
        t = ms(available if finite(available) else created)
        if arm not in routine_lifecycle or not within(t):
            continue
        if state in ROUTINE_LIFECYCLE:
            routine_lifecycle[arm][state] = routine_lifecycle[arm].get(state, 0)+1
        else:
            jev_markers.append({"t_ms": t, "kind": "reasoning.lifecycle", "arm": arm, "state": state, "reason": reason,
                                "request_id": request_id, "generation": generation, "event_id": event_id})
    labels = sorted(({"id": item.get("id"), "at_ms": item.get("at_ms"), "end_ms": item.get("end_ms"), "label": str(item.get("label", ""))[:200],
                      "time_basis": item.get("time_basis", "session"), "origin": item.get("origin"), "reviewed": item.get("reviewed") is True,
                      "revision": item.get("revision")} for item in store.list_annotations(sid)), key=lambda l: (l["at_ms"], str(l["id"])))
    bookmarks, bookmarks_cut = _rows(store, "SELECT id,json_extract(payload,'$.at_ms','$.label') FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind IN ('bookmark','bookmark.added') ORDER BY id",
                         (sid,), TRACK_ITEMS*4)
    marks = []
    for event_id, light in bookmarks:
        at, label = json.loads(light)
        if finite(at) and low <= at <= high:
            marks.append({"id": event_id, "t_ms": at, "label": str(label or "")[:200]})
    # ha.unavailable: the HA reader recorded that this session's HA source is
    # invalid (its writer records at most one per session). Every item has t_ms.
    unavailable = [{"t_ms": arrival(value, created), "kind": kind, "event_id": event_id, "reason": value.get("reason")}
                   for kind, created, value, event_id in kinds.get("ha.unavailable", []) if within(arrival(value, created))]
    context = {"ha": [], "captions": [], "room": [], "ha_unavailable": unavailable[:TRACK_ITEMS], "ha_unavailable_total": len(unavailable),
               "ha_unavailable_truncated": len(unavailable) > TRACK_ITEMS or kinds_cut, "ha_unavailable_scan_truncated": kinds_cut}
    found, ha_cut = _walk(store, "SELECT id,payload FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='ha.observation' AND id>? AND id<=? ORDER BY id",
                     (sid,), first, last, TIMELINE_ROWS)
    points = []
    for event_id, payload in found:
        value = json.loads(payload)
        facts = value.get("ha") if isinstance(value.get("ha"), dict) else {}
        if within(ms(value.get("available_at"))):
            points.append({"t_ms": ms(value["available_at"]), "event_id": event_id, "observed_ms": ms(value.get("observed_at")),
                           "valid_until_ms": ms(value.get("valid_until")), "entities": len(facts),
                           "known": sum(1 for f in facts.values() if isinstance(f, dict) and f.get("status") == "known")})
    kept, downsampled = _downsample(points, low, high, TRACK_ITEMS)
    context["ha"] = {"items": kept, "total": len(points), "downsampled": downsampled, "scan_truncated": ha_cut}
    found, captions_cut = _rows(store, """SELECT id,json_extract(payload,'$.available_at','$.observed_at','$.valid_until','$.projection.activity','$.projection.version','$.cloud_status')
        FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='caption.receipt' AND id>? AND id<=? ORDER BY id""", (sid, first, last), TRACK_ITEMS*4)
    captions = []
    for event_id, light in found:
        available, observed, valid, activity, version, cloud = json.loads(light)
        if within(ms(available)):
            captions.append({"t_ms": ms(available), "event_id": event_id, "observed_ms": ms(observed), "valid_until_ms": ms(valid),
                             "projection_activity": activity, "projection_version": version, "cloud_status": cloud})
    context["captions"] = _cap(captions, TRACK_ITEMS, captions_cut)
    found, room_cut = _walk(store, """SELECT id,json_extract(payload,'$.available_at','$.observed_at','$.valid_until','$.arm','$.map_version')
        FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='room.observation' AND id>? AND id<=? ORDER BY id""", (sid,), first, last, TIMELINE_ROWS)
    room = []
    for event_id, light in found:
        available, observed, valid, arm, version = json.loads(light)
        if within(ms(available)):
            room.append({"t_ms": ms(available), "event_id": event_id, "arm": arm, "observed_ms": ms(observed), "valid_until_ms": ms(valid), "map_version": version})
    kept, downsampled = _downsample(room, low, high, TRACK_ITEMS)
    context["room"] = {"items": kept, "total": len(room), "downsampled": downsampled, "scan_truncated": room_cut}
    with store.lock:
        verified = store.db.execute("""SELECT 1 FROM media WHERE session_id=? AND mime_type='video/mp4'
            AND json_extract(metadata,'$.clock_mapping_status')='verified'
            AND json_type(metadata,'$.timeline_start_ms') IN ('integer','real') LIMIT 1""", (sid,)).fetchone() is not None
        init = store.db.execute("""SELECT id FROM media WHERE session_id=? AND mime_type='video/mp4'
            AND json_extract(metadata,'$.kind')='encoded_init' ORDER BY rowid DESC LIMIT 1""", (sid,)).fetchone()
    clock = {"session_clock_verified": verified, "basis": "verified_video_clock_mapping" if verified else "unverified",
             "encoded_video_init_id": init[0] if init else None,
             "annotation_time_bases": ["session"] if verified else ["encoded_video"] if init else [],
             "analyzed_frames": {"clock_mapped": mapped, "total": total},
             "note": "t_ms is controller arrival on the session clock; source_ms is frame observation time when clock-mapped"}
    result = {**base, "range": {"from_ms": low, "to_ms": high}, "duration_ms": duration, "clock": clock,
              "tracks": {"perception": perception, "delivery_gaps": gaps, "perception_markers": perception_markers, "jev": jev,
                         "jev_smoothed": jev_smoothed,
                         "jev_markers": {**_cap(sorted(jev_markers, key=lambda m: (m["t_ms"], m["event_id"] or 0)), TRACK_ITEMS, kinds_cut or lifecycle_cut or gates_cut),
                                         "routine_lifecycle": routine_lifecycle, "routine_lifecycle_is_lower_bound": lifecycle_cut},
                         "transitions": _cap(transitions, TRACK_ITEMS, kinds_cut), "recording": _cap(recording, TRACK_ITEMS, kinds_cut),
                         "workers": _cap(sorted(workers, key=lambda w: w["t_ms"]), TRACK_ITEMS, kinds_cut), "labels": _cap(labels, TRACK_ITEMS),
                         "bookmarks": _cap(marks, TRACK_ITEMS, bookmarks_cut), "context": context},
              # Any track's scan stopped at its row bound; each track says which.
              "scan_truncated": any((scan_truncated, marker_scan_truncated, requests_cut, deliveries_cut, outputs_cut, kinds_cut,
                                     bookmarks_cut, ha_cut, captions_cut, room_cut, lifecycle_cut, closures_cut, smoothed_cut, gates_cut)),
              "gap_rule": "consecutive live outputs of one arm on the arrival clock more than max(2/sampling_hz s, 1 s) apart; 2 s when sampling_hz is not recorded",
              "labels_note": "labels are review items only and never model inputs",
              "bounds": {"points_per_arm": TRACK_POINTS, "items_per_track": TRACK_ITEMS, "rows_scanned": TIMELINE_ROWS, "response_bytes": RESPONSE_BYTES}}
    if len(encode(result).encode()) > RESPONSE_BYTES:
        raise LabError("timeline exceeds response budget; request a narrower range", 413, "timeline_size_limit")
    return result


def _valid_through_ms(through, arrived, closed, arrivals, strict_ms):
    """Facts-bound end of one JEV answer on the session axis (ms): its own
    valid_through, cut by a recorded closure or the arm's next answer, never
    below the strict valid_until. None for answers without facts-bound validity."""
    if not finite(through) or not finite(arrived):
        return None
    ends = [through] + ([closed] if finite(closed) else []) + [a for a in arrivals if finite(a) and a > arrived][:1]
    value = round(min(ends)*1000)
    return max(value, strict_ms) if strict_ms is not None else value


def _active(activations, generation):
    return next((a for a in activations if a.get("generation") == generation), None)


def _threshold(activation, arm):
    rates = (activation or {}).get("sampling_hz")
    rate = rates.get(arm) if isinstance(rates, dict) else None
    return max(2000/rate, 1000) if finite(rate) and rate > 0 else 2000


OUTPUT_KINDS = ("perception.output", "runtime.output", "analysis.output", "reasoning.output")


def _key(value):
    # Partition values as SQLite compares json_extract results: objects and
    # arrays as their JSON text; true/false equal 1/0, 1 equals 1.0.
    return encode(value) if isinstance(value, (dict, list)) else value


def outputs_at(store, sid, at_ms):
    """Same rows and staleness as Store.outputs_at, parsing each payload once.

    Store.outputs_at partitions every candidate with several json_extract
    calls per row under one lock hold; on SQLite without a JSON parse cache
    that is several full parses of every output per seek. This walk extracts
    all fields in one call, chunked, over ids fixed when it starts.
    """
    session = store.get_session(sid)
    if type(at_ms) is not int or not 0 <= at_ms <= store.settings.max_session_seconds*1000:
        raise LabError("invalid replay cursor", 422, "invalid_cursor")
    cutoff = at_ms/1000
    with store.lock:
        activation = store.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='experiment.activated'
            AND json_extract(payload,'$.effective_at')<=? ORDER BY id DESC LIMIT 1""",
            (sid, (session.get('started_at') or 0)+cutoff)).fetchone()
        last = store.db.execute("SELECT max(id) FROM events WHERE session_id=?", (sid,)).fetchone()[0] or 0
    active_generation = json.loads(activation[0]).get('generation') if activation else None
    number = lambda value: type(value) in (int, float)
    best = {}
    for kind in OUTPUT_KINDS:
        rows, _ = _walk(store, """SELECT id,json_extract(payload,'$.job_id','$.run_id','$.arm','$.mode','$.source_end_s','$.available_at_s')
            FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind=? AND id>? AND id<=? ORDER BY id""",
                        (sid, kind), 0, last, 10**9)
        for event_id, light in rows:
            job, run, arm, mode, end, available = json.loads(light)
            if not number(end) or end > cutoff:
                continue
            # SQL: mode!='live' is NULL for a missing mode, so availability decides.
            if (mode is None or mode == "live") and not (number(available) and available <= cutoff):
                continue
            key = (_key(job), _key(run), _key(arm), _key(mode))
            if key not in best or (end, event_id) > best[key]:
                best[key] = (end, event_id)
    ids = sorted(event_id for _, event_id in best.values())
    if len(ids) > 128:
        raise LabError("select fewer analysis runs for replay", 413, "replay_run_limit")
    result, used = [], 0
    with store.lock:
        rows = store.db.execute("SELECT id,session_id,kind,created_at,payload FROM events WHERE id IN (%s) ORDER BY id" % ",".join("?"*len(ids)), ids).fetchall() if ids else []
    for row in rows:
        value = {**dict(row), "payload": json.loads(row["payload"])}
        payload = value["payload"]
        if value["kind"] == "analysis.output":
            end = payload.get("judgment_end_s")
            if type(end) in (int, float) and cutoff >= end and cutoff > payload["source_end_s"]:
                payload["status"] = "stale"
        if payload.get("mode") == "live":
            if active_generation is not None and payload.get('generation') != active_generation:
                payload['status'] = 'stale'
                payload['stale_reason'] = 'experiment_generation_changed'
                if isinstance(payload.get('arm_snapshot'), dict):
                    payload['arm_snapshot']['status'] = 'stale'
            expiry = payload.get("valid_until")
            start = session.get("started_at")
            if type(expiry) not in (int, float) or type(start) not in (int, float) or expiry < start+cutoff:
                payload["status"] = "stale"
                if isinstance(payload.get("arm_snapshot"), dict):
                    payload["arm_snapshot"]["status"] = "stale"
        used += len(encode(value).encode())
        if used > 1024**2:
            raise LabError("replay evidence exceeds response budget", 413, "replay_size_limit")
        result.append(value)
    return result


def replay(store, sid, at_ms):
    """What replayWindow needs at one cursor: causal outputs and room, no history."""
    session = store.get_session(sid)
    if type(at_ms) is not int:
        raise LabError("invalid replay cursor", 422, "invalid_cursor")
    outputs = outputs_at(store, sid, at_ms)
    from room_evidence import room_at
    room = room_at(store, session, session["started_at"]+at_ms/1000) if finite(session["started_at"]) else None
    result = {"version": 1, "contract": CONTRACT, "contract_revision": CONTRACT_REVISION, "site_id": store.settings.site_id, "session_id": sid,
              "session": _session(session), "at_ms": at_ms, "outputs": outputs, "room": room, "inference": "none"}
    if len(encode(result).encode()) > RESPONSE_BYTES:
        raise LabError("replay evidence exceeds response budget", 413, "replay_size_limit")
    return result
