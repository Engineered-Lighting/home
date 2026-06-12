"""Segments-canonical label domain logic (M1): validation, reconcile,
review transitions, the VLM suggestion layer, custom-label usage.

Layering contract (red-team: background prelabel runs must never collide
with a human save):
  * CANONICAL lane = segments rows with review_state != 'prelabel'. The
    labels doc (GET/PUT /videos/{id}/labels) reads and reconciles ONLY this
    lane; a PUT is the full new state for each submitted axis (ids missing
    from the payload are retired). Per-(video, axis) NON-OVERLAP is enforced
    here, on this lane only.
  * SUGGESTION layer = rows inserted by insert_suggestion_segments() with
    source='vlm', review_state='prelabel'. They NEVER bump
    video_label_state.revision, are invisible to the labels doc, and survive
    human PUTs untouched -- so a prelabel batch landing mid-edit can neither
    409 a human save nor be deleted by it. Suggestions carry the overlapping
    analysis-window geometry, so they are exempt from non-overlap. The review
    endpoint graduates one into the canonical lane (accept -> 'accepted');
    that IS a human action and bumps the revision.
  * History: changed rows are SUPERSEDED -- the old row keeps its data with
    is_current=0 and the REPLACEMENT row's superseded_by points at the old
    id. Deleted rows are just retired (is_current=0, superseded_by NULL).

API axis names: db axis 'activity' is exposed as 'activity_primary';
posture/quality/custom pass through. The quality axis value is a STRING
ARRAY (multi-select, one lane) stored as a JSON array in segments.value.
"""
from __future__ import annotations

import json
import uuid

from . import db, ontology

MIN_SEGMENT_S = 0.2
OVERLAP_EPS = 1e-6

# person_slot grammar: null = the primary occupant; p2..p9 = additional
# people disambiguated by the labeler; track:<id> reserved for the future
# per-tracklet pipeline (plan M8). The CLIENT must send person_slot on every
# activity/posture segment it submits — an omitted slot groups as primary
# for the non-overlap check (slot inheritance across supersedes is resolved
# at reconcile, after validation).
import re as _re
_PERSON_SLOT_RE = _re.compile(r"^(p[2-9]|track:[A-Za-z0-9_-]+)$")

API_AXES = ("activity_primary", "posture", "quality", "custom")
_API_TO_DB_AXIS = {
    "activity_primary": "activity",
    "activity": "activity",      # tolerated alias
    "posture": "posture",
    "quality": "quality",
    "quality_flags": "quality",  # tolerated alias (the ontology doc's name)
    "custom": "custom",
}
_DB_TO_API_AXIS = {"activity": "activity_primary", "posture": "posture",
                   "quality": "quality", "custom": "custom"}
_SOURCES = ("vlm", "human", "cluster", "rule")
_CANONICAL = {"activity": ontology.ACTIVITY_PRIMARY, "posture": ontology.POSTURE}

# review actions -> resulting review_state ('reopen' deliberately lands on
# needs_review: reopening means "look at this again", not "it was reviewed")
REVIEW_ACTIONS = {
    "accept": "accepted",
    "reject": "rejected",
    "needs_review": "needs_review",
    "exclude": "excluded_from_export",
    "reopen": "needs_review",
}


class LabelValidationError(Exception):
    """400 payload: .details is a list of {axis, id, error} dicts."""

    def __init__(self, details: list):
        super().__init__(f"{len(details)} label validation error(s)")
        self.details = details


class RevisionConflict(Exception):
    """PUT revision != stored revision; .revision is the server's."""

    def __init__(self, revision: int):
        super().__init__(f"stale revision (server is at {revision})")
        self.revision = revision


class SegmentConflict(Exception):
    """Review action on a superseded/retired segment."""


def new_segment_id() -> str:
    return "seg_" + uuid.uuid4().hex[:12]


def get_revision(conn, video_id: str) -> int:
    row = conn.execute("SELECT revision FROM video_label_state WHERE video_id = ?",
                       (video_id,)).fetchone()
    return row["revision"] if row is not None else 0


def bump_revision(conn, video_id: str, now: str) -> int:
    """+1 the optimistic-concurrency counter. HUMAN/geometry writes only --
    VLM suggestion writers must never call this. Call inside an open tx."""
    conn.execute("INSERT OR IGNORE INTO video_label_state (video_id, revision,"
                 " updated_at) VALUES (?, 0, ?)", (video_id, now))
    conn.execute("UPDATE video_label_state SET revision = revision + 1,"
                 " updated_at = ? WHERE video_id = ?", (now, video_id))
    return get_revision(conn, video_id)


def decode_value(db_axis: str, stored: str):
    """Storage form -> API form (quality is a JSON array string)."""
    if db_axis != "quality":
        return stored
    try:
        return json.loads(stored)
    except ValueError:
        return [stored]  # defensive: never 500 the doc over one bad row


def seg_to_api(row) -> dict:
    """sqlite Row -> SEG contract dict (aux key on activity segments only)."""
    seg = {
        "id": row["id"],
        "start_s": row["start_s"],
        "end_s": row["end_s"],
        "value": decode_value(row["axis"], row["value"]),
        "review_state": row["review_state"],
        "source": row["source"],
        "confidence": row["confidence"],
        "person_slot": row["person_slot"],
    }
    if row["axis"] == "activity":
        seg["aux"] = json.loads(row["aux_json"]) if row["aux_json"] else None
    return seg


def labels_doc(conn, video_id: str) -> dict:
    """Full canonical labels doc: {revision, axes: {<api axis>: [SEG...]}},
    segments sorted by start_s, is_current=1 only, suggestion layer excluded."""
    axes: dict = {api: [] for api in API_AXES}
    rows = conn.execute(
        "SELECT * FROM segments WHERE video_id = ? AND is_current = 1"
        " AND review_state != 'prelabel' ORDER BY start_s, id", (video_id,))
    for r in rows:
        axes[_DB_TO_API_AXIS[r["axis"]]].append(seg_to_api(r))
    return {"revision": get_revision(conn, video_id), "axes": axes}


# ---------------------------------------------------------------- validation

def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _usable_custom_slugs(conn) -> set:
    """Slugs legal in segment values: any non-merged custom label (merged
    slugs were rewritten away; 'hidden' is a picker affordance, not a write
    ban, and 'promoted' stays usable until the export-time rewrite)."""
    return {r["slug"] for r in conn.execute(
        "SELECT slug FROM custom_labels WHERE status != 'merged'")}


def _encode_value(db_axis: str, value, usable_slugs: set, err) -> str | None:
    """API value -> storage string (JSON array for quality), or None after
    reporting through err(msg)."""
    if db_axis == "quality":
        if not isinstance(value, list) or not value:
            err("quality value must be a non-empty array of flags")
            return None
        seen = set()
        for v in value:
            if not isinstance(v, str):
                err("quality flags must be strings")
                return None
            if v in seen:
                err(f"duplicate quality flag '{v}'")
                return None
            seen.add(v)
            if v in ontology.QUALITY_FLAGS:
                continue
            if ontology.is_custom_value(v):
                if v[len("custom:"):] not in usable_slugs:
                    err(f"unknown custom label '{v}'")
                    return None
                continue
            err(f"'{v}' is not a quality flag")
            return None
        return json.dumps(value)
    if not isinstance(value, str) or not value:
        err("value must be a non-empty string")
        return None
    if ontology.is_custom_value(value):
        if value[len("custom:"):] not in usable_slugs:
            err(f"unknown custom label '{value}'")
            return None
        return value
    if db_axis == "custom":
        err("custom axis takes custom:<slug> values only")
        return None
    if value not in _CANONICAL[db_axis]:
        err(f"'{value}' is not a canonical {_DB_TO_API_AXIS[db_axis]} value")
        return None
    return value


def _normalize_aux(aux, err) -> str | None:
    """aux dict -> normalized aux_json (sort_keys so unchanged-detection is
    byte-stable); None clears."""
    if not aux:
        return None
    if not isinstance(aux, dict):
        err("aux must be an object")
        return None
    unknown = sorted(set(aux) - set(ontology.AUX_AXES))
    if unknown:
        err(f"unknown aux keys: {', '.join(unknown)}")
        return None
    mi = aux.get("motion_intensity")
    if mi is not None and mi not in ontology.MOTION_INTENSITY:
        err(f"motion_intensity must be one of {ontology.MOTION_INTENSITY}")
        return None
    ph = aux.get("activity_phase")
    if ph is not None and ph not in ontology.ACTIVITY_PHASE:
        err(f"activity_phase must be one of {ontology.ACTIVITY_PHASE}")
        return None
    return json.dumps(aux, sort_keys=True)


def _normalize_segment(api_axis, db_axis, idx, raw, usable_slugs, errors):
    """One raw SEG dict -> normalized dict (optional fields present only when
    the client sent them, so reconcile can inherit on supersede), collecting
    problems into errors. Returns None on fatally-malformed input."""
    rid = raw.get("id") if isinstance(raw, dict) else None
    where = {"axis": api_axis, "id": rid if isinstance(rid, str) else f"#{idx}"}

    def err(msg):
        errors.append({**where, "error": msg})

    if not isinstance(raw, dict):
        err("segment must be an object")
        return None
    if rid is not None and not isinstance(rid, str):
        err("id must be a string")
        return None
    s, e = raw.get("start_s"), raw.get("end_s")
    if not _is_num(s) or not _is_num(e):
        err("start_s/end_s must be numbers")
        return None
    s, e = float(s), float(e)
    if s < 0:
        err("start_s must be >= 0")
    if e <= s:
        err("start_s must be < end_s")
    elif e - s < MIN_SEGMENT_S - 1e-9:
        err(f"segment is shorter than {MIN_SEGMENT_S}s")
    seg = {"id": rid, "start_s": s, "end_s": e,
           "value_enc": _encode_value(db_axis, raw.get("value"), usable_slugs, err)}
    if "review_state" in raw:
        rs = raw["review_state"]
        if rs == "prelabel":
            err("review_state 'prelabel' is reserved for the suggestion layer")
        elif rs not in ontology.REVIEW_STATES:
            err(f"unknown review_state '{rs}'")
        else:
            seg["review_state"] = rs
    if "source" in raw:
        if raw["source"] not in _SOURCES:
            err(f"unknown source '{raw['source']}'")
        else:
            seg["source"] = raw["source"]
    if "confidence" in raw:
        c = raw["confidence"]
        if c is not None and not _is_num(c):
            err("confidence must be a number or null")
        else:
            seg["confidence"] = float(c) if c is not None else None
    if "person_slot" in raw:
        ps = raw["person_slot"]
        if ps is not None and not isinstance(ps, str):
            err("person_slot must be a string or null")
        elif ps is not None and not _PERSON_SLOT_RE.match(ps):
            err("person_slot must be null (primary), 'p2'..'p9', or a"
                " 'track:<id>' ref")
        elif ps is not None and db_axis not in ("activity", "posture"):
            err("person_slot applies to activity/posture segments only")
        else:
            seg["person_slot"] = ps
    if "aux" in raw:
        if db_axis != "activity" and raw["aux"]:
            err("aux is allowed on activity segments only")
        else:
            seg["aux_json"] = _normalize_aux(raw["aux"], err)
    return seg


def _check_overlap(api_axis, segs, errors):
    """Non-overlap per (lane, person_slot) — touching boundaries are fine.

    Multi-person scenes (a third of the v1 corpus): activity/posture
    segments for DIFFERENT people legitimately coexist on the same time
    range, keyed by person_slot (null/absent = the primary occupant; "p2",
    "p3", ... or future track refs for others). Two segments only conflict
    when they describe the SAME person."""
    by_slot: dict = {}
    for s in segs:
        by_slot.setdefault(s.get("person_slot") or None, []).append(s)
    for slot, group in by_slot.items():
        ordered = sorted(group, key=lambda x: (x["start_s"], x["end_s"]))
        for a, b in zip(ordered, ordered[1:]):
            if b["start_s"] < a["end_s"] - OVERLAP_EPS:
                who = f" (person {slot})" if slot else ""
                errors.append({
                    "axis": api_axis, "id": b["id"] or "?",
                    "error": f"overlaps segment {a['id'] or '?'}{who}"
                             f" ({a['start_s']:.3f}-{a['end_s']:.3f} vs"
                             f" {b['start_s']:.3f}-{b['end_s']:.3f})"})


def validate_axes(conn, axes) -> dict:
    """{api axis: [raw SEG...]} -> {db axis: [normalized seg...]}.
    Raises LabelValidationError carrying ALL problems (one round trip)."""
    if not isinstance(axes, dict):
        raise LabelValidationError(
            [{"axis": None, "id": None, "error": "axes must be an object"}])
    usable = _usable_custom_slugs(conn)
    errors: list = []
    parsed: dict = {}
    for api_axis, raw_segs in axes.items():
        db_axis = _API_TO_DB_AXIS.get(api_axis)
        if db_axis is None:
            errors.append({"axis": api_axis, "id": None, "error": "unknown axis"})
            continue
        if db_axis in parsed:
            errors.append({"axis": api_axis, "id": None,
                           "error": "duplicate axis in payload"})
            continue
        if not isinstance(raw_segs, list):
            errors.append({"axis": api_axis, "id": None,
                           "error": "axis value must be a list of segments"})
            continue
        segs, ids = [], set()
        for idx, raw in enumerate(raw_segs):
            seg = _normalize_segment(api_axis, db_axis, idx, raw, usable, errors)
            if seg is None:
                continue
            if seg["id"]:
                if seg["id"] in ids:
                    errors.append({"axis": api_axis, "id": seg["id"],
                                   "error": "duplicate segment id"})
                    continue
                ids.add(seg["id"])
            segs.append(seg)
        _check_overlap(api_axis, segs, errors)
        parsed[db_axis] = segs
    if errors:
        raise LabelValidationError(errors)
    return parsed


# ----------------------------------------------------------------- reconcile

def _resolve(seg, key, old, default):
    """Client-sent fields win; absent fields inherit from the superseded row
    or take the insert default."""
    if key in seg:
        return seg[key]
    return old[key] if old is not None else default


def _changed(old, seg) -> bool:
    if abs(old["start_s"] - seg["start_s"]) > 1e-9:
        return True
    if abs(old["end_s"] - seg["end_s"]) > 1e-9:
        return True
    if old["value"] != seg["value_enc"]:
        return True
    for key in ("review_state", "source", "person_slot"):
        if key in seg and seg[key] != old[key]:
            return True
    if "confidence" in seg:
        a, b = seg["confidence"], old["confidence"]
        if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-9):
            return True
    if "aux_json" in seg and (seg["aux_json"] or None) != (old["aux_json"] or None):
        return True
    return False


def _insert_row(conn, seg_id, video_id, db_axis, seg, now,
                superseded_by=None, old=None) -> None:
    review_state = _resolve(seg, "review_state", old, "needs_review")
    if old is not None and old["review_state"] == review_state:
        reviewed_by, reviewed_at = old["reviewed_by"], old["reviewed_at"]
    elif review_state != "needs_review":
        reviewed_by, reviewed_at = "human", now  # PUT is a human surface
    else:
        reviewed_by, reviewed_at = None, None
    conn.execute(
        "INSERT INTO segments (id, video_id, axis, start_s, end_s, value,"
        " review_state, source, confidence, aux_json, person_slot,"
        " evidence_json, reviewed_by, reviewed_at, is_current, superseded_by,"
        " created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (seg_id, video_id, db_axis, seg["start_s"], seg["end_s"],
         seg["value_enc"], review_state,
         _resolve(seg, "source", old, "human"),
         _resolve(seg, "confidence", old, None),
         _resolve(seg, "aux_json", old, None),
         _resolve(seg, "person_slot", old, None),
         old["evidence_json"] if old is not None else None,
         reviewed_by, reviewed_at, superseded_by, now, now))


def _reconcile_axis(conn, video_id, db_axis, segs, now, id_map) -> None:
    """The submitted list IS the new canonical state for this axis: new ids
    insert, known-but-changed ids supersede (history kept), omitted ids
    retire. Suggestion rows (review_state='prelabel') are out of scope."""
    existing = {r["id"]: r for r in conn.execute(
        "SELECT * FROM segments WHERE video_id = ? AND axis = ?"
        " AND is_current = 1 AND review_state != 'prelabel'",
        (video_id, db_axis))}
    seen = set()
    for seg in segs:
        cid = seg["id"]
        old = existing.get(cid) if cid else None
        if old is not None:
            seen.add(cid)
            if not _changed(old, seg):
                continue
            nid = new_segment_id()
            conn.execute("UPDATE segments SET is_current = 0, updated_at = ?"
                         " WHERE id = ?", (now, cid))
            _insert_row(conn, nid, video_id, db_axis, seg, now,
                        superseded_by=cid, old=old)
            id_map[cid] = nid
        else:
            # new segment (tmp_* / absent / unknown id): server assigns the id
            nid = new_segment_id()
            _insert_row(conn, nid, video_id, db_axis, seg, now)
            if cid:
                id_map[cid] = nid
    for gone in set(existing) - seen:
        conn.execute("UPDATE segments SET is_current = 0, updated_at = ?"
                     " WHERE id = ?", (now, gone))


def save_labels(conn, video_id: str, revision: int, axes) -> tuple:
    """Validate + reconcile a labels PUT. The revision check and every write
    share ONE short transaction so two human writers serialize cleanly.
    Returns (new_revision, id_map: client id -> server id for every inserted
    or superseded row). Raises LabelValidationError / RevisionConflict."""
    parsed = validate_axes(conn, axes)
    now = db.iso_now()
    id_map: dict = {}
    with db.tx(conn):
        conn.execute("INSERT OR IGNORE INTO video_label_state (video_id,"
                     " revision, updated_at) VALUES (?, 0, ?)", (video_id, now))
        current = get_revision(conn, video_id)
        if revision != current:
            raise RevisionConflict(current)
        holdout = _is_holdout(conn, video_id)
        for db_axis, segs in parsed.items():
            _reconcile_axis(conn, video_id, db_axis, segs, now, id_map)
            if not holdout:
                _calibrate_put_overlap(conn, video_id, db_axis, segs, now)
        new_revision = bump_revision(conn, video_id, now)
    return new_revision, id_map


# -------------------------------------------------------------------- review

def review_segment(conn, video_id: str, segment_id: str, action: str) -> tuple:
    """Apply a review action in place (review is not geometry -- no supersede
    churn) and bump the revision. Accepting a 'prelabel' row is exactly how a
    suggestion graduates into the canonical lane. Returns (revision, SEG+axis).
    Raises ValueError (bad action), KeyError (not found), SegmentConflict."""
    state = REVIEW_ACTIONS.get(action)
    if state is None:
        raise ValueError(f"unknown review action '{action}'"
                         f" (one of: {', '.join(sorted(REVIEW_ACTIONS))})")
    now = db.iso_now()
    with db.tx(conn):
        row = conn.execute("SELECT * FROM segments WHERE id = ? AND video_id = ?",
                           (segment_id, video_id)).fetchone()
        if row is None:
            raise KeyError(segment_id)
        if not row["is_current"]:
            raise SegmentConflict(f"segment {segment_id} is superseded")
        # M2 calibration: the FIRST human verdict on a fresh (never stamped)
        # NON-HOLDOUT vlm suggestion is the calibration event. reviewed_by
        # being set (by a prior review or a PUT-overlap stamp) means the row
        # was already counted -- never double-count.
        if (action in ("accept", "reject") and row["source"] == "vlm"
                and row["review_state"] == "prelabel"
                and row["reviewed_by"] is None
                and not _is_holdout(conn, video_id)):
            _bump_calibration(conn, row["axis"], row["value"],
                              row["confidence"], action == "accept", now)
        conn.execute("UPDATE segments SET review_state = ?, reviewed_by = 'human',"
                     " reviewed_at = ?, updated_at = ? WHERE id = ?",
                     (state, now, now, segment_id))
        revision = bump_revision(conn, video_id, now)
    fresh = conn.execute("SELECT * FROM segments WHERE id = ?",
                         (segment_id,)).fetchone()
    seg = seg_to_api(fresh)
    seg["axis"] = _DB_TO_API_AXIS[fresh["axis"]]
    return revision, seg


# ----------------------------------------------------------------- calibration
# calibration_stats accumulates HUMAN verdicts over vlm prelabels per
# (db axis, value, confidence decile). Two event sources, each counted at
# most once per suggestion row (guarded by reviewed_by IS NULL):
#   * explicit accept/reject of a source='vlm' prelabel (review_segment)
#   * a human PUT whose canonical segment overlaps an un-verdicted prelabel
#     (value match -> accepted, mismatch -> rejected); the suggestion is
#     stamped reviewed_by='put_overlap' but STAYS in the suggestion layer.
# Holdout videos never feed calibration (red-team: leak path).

BULK_TOP_DECILES = (8, 9)     # axis-pooled "top deciles" = confidence >= 0.8
BULK_WILSON_MIN = 0.95
WILSON_Z = 1.96               # 95% confidence


def _is_holdout(conn, video_id: str) -> bool:
    row = conn.execute("SELECT is_holdout FROM videos WHERE id = ?",
                       (video_id,)).fetchone()
    return bool(row["is_holdout"]) if row is not None else False


def conf_decile(confidence) -> int:
    """0..9 bucket; decile d covers [d/10, (d+1)/10), with 1.0 in decile 9."""
    c = min(max(float(confidence), 0.0), 1.0)
    return min(int(c * 10), 9)


def wilson_lower(accepted: int, n: int, z: float = WILSON_Z) -> float:
    """Wilson score interval lower bound for a binomial proportion:
        lower = (p + z^2/(2n) - z*sqrt(p(1-p)/n + z^2/(4n^2))) / (1 + z^2/n)
    with p = accepted/n. Returns 0.0 when n == 0 (no evidence -> no claim)."""
    if n <= 0:
        return 0.0
    p = accepted / n
    z2 = z * z
    num = p + z2 / (2 * n) - z * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5)
    return max(0.0, num / (1 + z2 / n))


def _bump_calibration(conn, db_axis: str, value: str, confidence,
                      accepted: bool, now: str) -> None:
    """+1 one (axis, value, decile) cell. Call inside an open tx. Suggestions
    without a confidence cannot be binned and are skipped."""
    if confidence is None:
        return
    conn.execute(
        "INSERT INTO calibration_stats (axis, value, conf_decile,"
        " n_prelabeled, n_accepted, n_rejected, updated_at)"
        " VALUES (?, ?, ?, 1, ?, ?, ?)"
        " ON CONFLICT (axis, value, conf_decile) DO UPDATE SET"
        " n_prelabeled = n_prelabeled + 1,"
        " n_accepted = n_accepted + excluded.n_accepted,"
        " n_rejected = n_rejected + excluded.n_rejected,"
        " updated_at = excluded.updated_at",
        (db_axis, value, conf_decile(confidence),
         1 if accepted else 0, 0 if accepted else 1, now))


def _calibrate_put_overlap(conn, video_id: str, db_axis: str, segs,
                           now: str) -> None:
    """A human PUT overlapping an un-verdicted vlm prelabel is an implicit
    calibration verdict: same value -> accepted, different -> rejected.
    Stamps the suggestion (reviewed_by='put_overlap', review_state kept
    'prelabel') so repeat PUTs cannot double-count it. Call inside the
    save_labels tx, after _reconcile_axis for the same axis."""
    if not segs:
        return
    suggestions = conn.execute(
        "SELECT * FROM segments WHERE video_id = ? AND axis = ?"
        " AND is_current = 1 AND review_state = 'prelabel'"
        " AND source = 'vlm' AND reviewed_by IS NULL",
        (video_id, db_axis)).fetchall()
    for sg in suggestions:
        hit = None
        for seg in segs:
            if (seg["start_s"] < sg["end_s"] - OVERLAP_EPS
                    and seg["end_s"] > sg["start_s"] + OVERLAP_EPS):
                hit = seg
                break
        if hit is None:
            continue
        _bump_calibration(conn, db_axis, sg["value"], sg["confidence"],
                          hit["value_enc"] == sg["value"], now)
        conn.execute("UPDATE segments SET reviewed_by = 'put_overlap',"
                     " reviewed_at = ?, updated_at = ? WHERE id = ?",
                     (now, now, sg["id"]))


def calibration_doc(conn, api_axis=None) -> dict:
    """Per-axis acceptance curves + the bulk-accept gate.

    bulk_ok requires the AXIS-POOLED top deciles (BULK_TOP_DECILES, i.e.
    confidence >= 0.8) to clear wilson_lower(accepted, n) >= BULK_WILSON_MIN.
    EXPECTED to stay false for the entire v1 corpus (plan: UI shows progress).
    Raises ValueError on an unknown axis."""
    if api_axis is not None:
        db_axis = _API_TO_DB_AXIS.get(api_axis)
        if db_axis is None:
            raise ValueError(f"unknown axis: {api_axis}")
        db_axes = [db_axis]
    else:
        db_axes = ["activity", "posture", "quality", "custom"]
    out: dict = {}
    for db_axis in db_axes:
        rows = conn.execute(
            "SELECT conf_decile, SUM(n_prelabeled) AS n_prelabeled,"
            " SUM(n_accepted) AS n_accepted, SUM(n_rejected) AS n_rejected"
            " FROM calibration_stats WHERE axis = ? GROUP BY conf_decile",
            (db_axis,)).fetchall()
        by_decile = {r["conf_decile"]: r for r in rows}
        deciles = []
        for d in range(10):
            r = by_decile.get(d)
            n_acc = r["n_accepted"] if r else 0
            n_rej = r["n_rejected"] if r else 0
            verdicts = n_acc + n_rej
            deciles.append({
                "decile": d,
                "conf_lo": round(d / 10, 1), "conf_hi": round((d + 1) / 10, 1),
                "n_prelabeled": r["n_prelabeled"] if r else 0,
                "n_accepted": n_acc, "n_rejected": n_rej,
                "acceptance": (n_acc / verdicts) if verdicts else None,
            })
        top_acc = sum(deciles[d]["n_accepted"] for d in BULK_TOP_DECILES)
        top_n = top_acc + sum(deciles[d]["n_rejected"] for d in BULK_TOP_DECILES)
        lower = wilson_lower(top_acc, top_n)
        out[_DB_TO_API_AXIS[db_axis]] = {
            "deciles": deciles,
            "top_deciles": {"deciles": list(BULK_TOP_DECILES), "n": top_n,
                            "accepted": top_acc,
                            "acceptance": (top_acc / top_n) if top_n else None,
                            "wilson_lower": round(lower, 6)},
            "bulk_ok": top_n > 0 and lower >= BULK_WILSON_MIN,
        }
    return {"axes": out, "wilson_min": BULK_WILSON_MIN}


# ---------------------------------------------------------- suggestion layer

def insert_suggestion_segments(conn, video_id: str, axis: str, segs,
                               source: str = "vlm") -> list:
    """M2 prelabel writer: insert review_state='prelabel' suggestion rows for
    one axis (api or db axis name). source defaults to 'vlm'; the perceive
    job's no_person auto-prelabels pass source='rule'.

    CRITICAL (red-team): this NEVER touches video_label_state.revision and
    never edits canonical rows, so a prelabel batch landing mid-edit cannot
    409 (or be deleted by) a concurrent human PUT. Suggestions carry the
    overlapping analysis-window geometry -- no non-overlap check here.
    Raises ValueError on an unknown axis/source or invalid values; returns
    the new segment ids."""
    db_axis = _API_TO_DB_AXIS.get(axis)
    if db_axis is None:
        raise ValueError(f"unknown axis: {axis}")
    if source not in _SOURCES:
        raise ValueError(f"unknown source: {source}")
    usable = _usable_custom_slugs(conn)
    problems: list = []
    rows: list = []
    for idx, seg in enumerate(segs):
        def err(msg, _i=idx):
            problems.append(f"segment #{_i}: {msg}")
        if not isinstance(seg, dict):
            err("segment must be an object")
            continue
        s, e = seg.get("start_s"), seg.get("end_s")
        if not _is_num(s) or not _is_num(e) or float(e) <= float(s):
            err("start_s/end_s must be numbers with start_s < end_s")
            continue
        enc = _encode_value(db_axis, seg.get("value"), usable, err)
        if enc is None:
            continue
        conf = seg.get("confidence")
        if conf is not None and not _is_num(conf):
            err("confidence must be a number")
            continue
        aux_json = None
        if db_axis == "activity" and seg.get("aux"):
            aux_json = _normalize_aux(seg["aux"], err)
        evidence = seg.get("evidence")
        rows.append((float(s), float(e), enc, conf, aux_json,
                     json.dumps(evidence) if evidence is not None else None,
                     seg.get("person_slot")))
    if problems:
        raise ValueError("; ".join(problems))
    now = db.iso_now()
    ids: list = []
    with db.tx(conn):
        for s, e, enc, conf, aux_json, evidence_json, person_slot in rows:
            sid = new_segment_id()
            conn.execute(
                "INSERT INTO segments (id, video_id, axis, start_s, end_s,"
                " value, review_state, source, confidence, aux_json,"
                " person_slot, evidence_json, is_current, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'prelabel', ?, ?, ?, ?, ?, 1, ?, ?)",
                (sid, video_id, db_axis, s, e, enc, source, conf, aux_json,
                 person_slot, evidence_json, now, now))
            ids.append(sid)
    return ids


# ------------------------------------------------------------- custom labels

def custom_usage_counts(conn) -> dict:
    """Live usage per custom slug over CURRENT segments (suggestions
    included): scalar axes match value='custom:<slug>' exactly; quality
    arrays count element hits. Corpus is small -- counted in Python."""
    counts: dict = {}
    for r in conn.execute("SELECT axis, value FROM segments WHERE is_current = 1"):
        if r["axis"] == "quality":
            values = decode_value("quality", r["value"])
        else:
            values = [r["value"]]
        for v in values:
            if isinstance(v, str) and v.startswith("custom:"):
                slug = v[len("custom:"):]
                counts[slug] = counts.get(slug, 0) + 1
    return counts


def rewrite_custom_value(conn, old_slug: str, new_slug: str, now: str) -> tuple:
    """Merge support: rewrite CURRENT segment values custom:<old> ->
    custom:<new> in place (a vocabulary rename, not geometry -- no supersede
    churn). Quality arrays are rewritten element-wise with dedupe; the LIKE
    is only a prefilter ('_' wildcard over-matches are filtered in Python).
    Call inside an open tx. Returns (affected_video_ids, rewritten_count)."""
    old_val, new_val = f"custom:{old_slug}", f"custom:{new_slug}"
    affected: set = set()
    rewritten = 0
    for r in conn.execute("SELECT id, video_id FROM segments WHERE is_current = 1"
                          " AND axis != 'quality' AND value = ?",
                          (old_val,)).fetchall():
        conn.execute("UPDATE segments SET value = ?, updated_at = ? WHERE id = ?",
                     (new_val, now, r["id"]))
        affected.add(r["video_id"])
        rewritten += 1
    for r in conn.execute("SELECT id, video_id, value FROM segments WHERE"
                          " is_current = 1 AND axis = 'quality' AND value LIKE ?",
                          (f'%"{old_val}"%',)).fetchall():
        flags = decode_value("quality", r["value"])
        if old_val not in flags:
            continue
        out: list = []
        for f in flags:
            f = new_val if f == old_val else f
            if f not in out:
                out.append(f)
        conn.execute("UPDATE segments SET value = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(out), now, r["id"]))
        affected.add(r["video_id"])
        rewritten += 1
    return affected, rewritten
