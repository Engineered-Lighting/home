from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .ingest import canonical_json, stable_hash, utc_now


def _json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _date_part(ts: str | None) -> str | None:
    if not ts or len(str(ts)) < 10:
        return None
    return str(ts)[:10]


def _validate_date(date: str | None) -> str:
    if date:
        datetime.strptime(date, "%Y-%m-%d")
        return date
    return datetime.now(timezone.utc).date().isoformat()


def _latest_evidence_date(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT ts
        FROM preference_observations
        WHERE ts IS NOT NULL
        ORDER BY ts DESC
        LIMIT 1
        """
    ).fetchone()
    return _date_part(row["ts"]) if row else datetime.now(timezone.utc).date().isoformat()


def _bool_text(value: Any) -> str:
    if value is None:
        return "-"
    return "true" if bool(value) else "false"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{round(float(value))}%"
    except (TypeError, ValueError):
        return "-"


def _zone_label(zone: str | None) -> str:
    return (zone or "unknown").replace("_", " ")


def _candidate_context(row: sqlite3.Row) -> dict[str, Any]:
    return _json_loads(row["context_json"], {})


def _candidate_evidence_ids(row: sqlite3.Row) -> list[str]:
    raw = _json_loads(row["evidence_ids_json"], [])
    return [str(x) for x in raw if x]


def _candidate_blockers(row: sqlite3.Row) -> list[str]:
    return [str(x) for x in _json_loads(row["blockers_json"], []) if x]


def _candidate_warnings(row: sqlite3.Row) -> list[dict[str, Any]]:
    warnings = _json_loads(row["quality_warnings_json"], [])
    return [w for w in warnings if isinstance(w, dict)]


def _context_label(ctx: dict[str, Any]) -> str:
    parts = []
    for key in ("profile", "state", "tv_playing", "asleep", "night_safe", "user_at_home"):
        value = ctx.get(key)
        if value is None:
            continue
        if key in ("tv_playing", "asleep", "night_safe", "user_at_home"):
            value = _bool_text(value)
        parts.append(f"{key}={value}")
    if ctx.get("light_state"):
        parts.append(f"light_state={ctx['light_state']}")
    return ", ".join(parts) if parts else "unknown context"


def _target_text(row: sqlite3.Row) -> str:
    target = _json_loads(row["suggested_target_json"], {})
    if target.get("light_state") == "off":
        return "off"
    return _fmt_pct(target.get("brightness_pct"))


def _candidate_confidence(row: sqlite3.Row) -> float:
    evidence = int(row["evidence_count"] or 0)
    days = int(row["days_seen"] or 0)
    linked = int(row["linked_override_count"] or 0)
    blockers = len(_candidate_blockers(row))
    warnings = len(_candidate_warnings(row))
    shadow = float(row["shadow_mode_ratio"] or 0.0)
    duplicate = float(row["duplicate_pending_ratio"] or 0.0)
    confidence = (
        0.24
        + min(evidence, 8) * 0.04
        + min(days, 3) * 0.06
        + min(linked, 2) * 0.08
        - shadow * 0.15
        - duplicate * 0.20
        - warnings * 0.035
        - blockers * 0.025
    )
    if row["status"] == "candidate":
        confidence += 0.12
    return round(max(0.05, min(0.85, confidence)), 3)


def _candidate_claim(row: sqlite3.Row) -> dict[str, Any]:
    ctx = _candidate_context(row)
    zone = row["zone"]
    claim = (
        f"{_zone_label(zone)} lighting may prefer {_target_text(row)} "
        f"in {_context_label(ctx)}; observed median actual "
        f"{_fmt_pct(row['median_actual_brightness_pct'])} vs predicted "
        f"{_fmt_pct(row['median_predicted_brightness_pct'])} across "
        f"{int(row['evidence_count'] or 0)} evidence rows."
    )
    return {
        "id": f"wiki:{stable_hash(row['id'] + ':preference-claim')[:24]}",
        "page_path": f"runtime/intelligence/wiki/rooms/{zone}.md",
        "claim_type": "hypothesis",
        "claim": claim,
        "confidence": _candidate_confidence(row),
        "review_state": "unreviewed",
        "evidence_ids": _candidate_evidence_ids(row),
    }


def _issue_claim(row: sqlite3.Row) -> dict[str, Any] | None:
    blockers = _candidate_blockers(row)
    warning_codes = {str(w.get("code")) for w in _candidate_warnings(row)}
    issue_reasons = []
    for blocker in blockers:
        if blocker in (
            "high variance",
            "conflicting opposite-direction overrides",
            "duplicate-heavy pending captures",
        ):
            issue_reasons.append(blocker)
    if "prediction_mismatch" in warning_codes:
        issue_reasons.append("prediction mismatch")
    if "occupancy_flicker" in warning_codes:
        issue_reasons.append("occupancy flicker")
    if not issue_reasons:
        return None
    zone = row["zone"]
    claim = (
        f"Open question: {_zone_label(zone)} lighting evidence is not clean enough "
        f"for a durable rule yet ({', '.join(sorted(set(issue_reasons)))})."
    )
    return {
        "id": f"wiki:{stable_hash(row['id'] + ':open-question')[:24]}",
        "page_path": f"runtime/intelligence/wiki/issues/{zone}-lighting-evidence.md",
        "claim_type": "hypothesis",
        "claim": claim,
        "confidence": 0.5,
        "review_state": "open_question",
        "evidence_ids": _candidate_evidence_ids(row),
    }


def _journal_body(
    *,
    date: str,
    observations: list[sqlite3.Row],
    candidates: list[sqlite3.Row],
    episodes: list[sqlite3.Row],
) -> tuple[str, list[str]]:
    evidence_ids = sorted({row["id"] for row in observations})
    zones: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in observations:
        zones[row["zone"]].append(row)
    kind_counts = Counter(row["kind"] for row in observations)
    m4_count = sum(
        1
        for row in observations
        if row["kind"] == "pending_preference"
        and (row["evidence_id"] or row["dedupe_key"] or row["related_override_context_id"])
    )
    suppressed_total = sum(int(row["capture_suppressed_count"] or 0) for row in observations)
    link_counts = Counter()
    for episode in episodes:
        summary = _json_loads(episode["summary_json"], {})
        link_counts[summary.get("link_method") or "unlinked"] += 1
        for evidence_id in summary.get("evidence_ids") or []:
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)

    lines = [
        f"# Home Intelligence Journal - {date}",
        "",
        "## Evidence Counts",
        f"- observations: {len(observations)}",
        f"- override events: {kind_counts.get('override_event', 0)}",
        f"- pending preferences: {kind_counts.get('pending_preference', 0)}",
        f"- M4 pending records: {m4_count}",
        f"- duplicate captures suppressed: {suppressed_total}",
        (
            "- episodes: "
            f"{len(episodes)} total, "
            f"{link_counts.get('related_override_context_id', 0)} direct, "
            f"{link_counts.get('heuristic', 0)} heuristic, "
            f"{link_counts.get('unlinked', 0)} unlinked"
        ),
        "",
        "## Zone Notes",
    ]
    if not zones:
        lines.append("- no lighting preference evidence ingested")
    for zone, rows in sorted(zones.items(), key=lambda item: (-len(item[1]), item[0])):
        pending = [row for row in rows if row["kind"] == "pending_preference"]
        overrides = [row for row in rows if row["kind"] == "override_event"]
        latest_pending = max(pending, key=lambda row: row["ts"] or "") if pending else None
        note = (
            f"- {_zone_label(zone)}: {len(rows)} observations, "
            f"{len(overrides)} touches, {len(pending)} final captures"
        )
        if latest_pending:
            note += (
                f"; latest {_fmt_pct(latest_pending['actual_brightness_pct'])} actual vs "
                f"{_fmt_pct(latest_pending['predicted_brightness_pct'])} predicted "
                f"({latest_pending['profile'] or '-'}, {latest_pending['state'] or '-'})"
            )
            if latest_pending["dedupe_key"]:
                note += "; M4 evidence present"
        lines.append(note)

    lines.extend(["", "## Candidate State"])
    if not candidates:
        lines.append("- no preference candidates yet")
    for row in candidates[:12]:
        blockers = _candidate_blockers(row)
        evidence_ids.extend(eid for eid in _candidate_evidence_ids(row) if eid not in evidence_ids)
        lines.append(
            "- "
            f"{_zone_label(row['zone'])}: {row['status']} "
            f"target {_target_text(row)} in {_context_label(_candidate_context(row))}; "
            f"evidence {int(row['evidence_count'] or 0)}, "
            f"days {int(row['days_seen'] or 0)}, "
            f"linked touches {int(row['linked_override_count'] or 0)}"
            + (f"; blockers: {', '.join(blockers[:3])}" if blockers else "")
        )

    lines.extend(["", "## Open Questions"])
    open_questions = []
    for row in candidates:
        issue = _issue_claim(row)
        if issue:
            open_questions.append(issue["claim"])
    if open_questions:
        for claim in sorted(set(open_questions))[:8]:
            lines.append(f"- {claim}")
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n", sorted(set(evidence_ids))


def _load_candidates_for_date(
    conn: sqlite3.Connection,
    date: str,
    evidence_ids: set[str],
) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT *
        FROM preference_candidates
        ORDER BY
          CASE status WHEN 'candidate' THEN 0 ELSE 1 END,
          evidence_count DESC,
          updated_at DESC
        """
    ).fetchall()
    out = []
    for row in rows:
        first_seen = _date_part(row["first_seen"])
        last_seen = _date_part(row["last_seen"])
        row_evidence = set(_candidate_evidence_ids(row))
        if (
            first_seen == date
            or last_seen == date
            or bool(row_evidence.intersection(evidence_ids))
        ):
            out.append(row)
    return out


def _upsert_claim(conn: sqlite3.Connection, claim: dict[str, Any], now: str) -> str:
    evidence_json = canonical_json(claim.get("evidence_ids") or [])
    existing = conn.execute(
        """
        SELECT *
        FROM wiki_claims
        WHERE id = ?
        """,
        (claim["id"],),
    ).fetchone()
    if existing:
        unchanged = (
            existing["claim"] == claim["claim"]
            and existing["claim_type"] == claim["claim_type"]
            and existing["page_path"] == claim["page_path"]
            and float(existing["confidence"] or 0.0) == float(claim["confidence"] or 0.0)
            and existing["review_state"] == claim["review_state"]
            and existing["evidence_ids_json"] == evidence_json
        )
        if unchanged:
            return "unchanged"
        conn.execute(
            """
            INSERT INTO wiki_updates (page_path, diff_json, created_at)
            VALUES (?, ?, ?)
            """,
            (
                claim["page_path"],
                canonical_json(
                    {
                        "claim_id": claim["id"],
                        "old": {
                            "claim": existing["claim"],
                            "confidence": existing["confidence"],
                            "review_state": existing["review_state"],
                            "evidence_ids": _json_loads(existing["evidence_ids_json"], []),
                        },
                        "new": {
                            "claim": claim["claim"],
                            "confidence": claim["confidence"],
                            "review_state": claim["review_state"],
                            "evidence_ids": claim.get("evidence_ids") or [],
                        },
                    }
                ),
                now,
            ),
        )
        status = "updated"
    else:
        status = "inserted"
    conn.execute(
        """
        INSERT INTO wiki_claims
            (id, page_path, claim_type, claim, confidence, review_state,
             evidence_ids_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            page_path=excluded.page_path,
            claim_type=excluded.claim_type,
            claim=excluded.claim,
            confidence=excluded.confidence,
            review_state=excluded.review_state,
            evidence_ids_json=excluded.evidence_ids_json,
            updated_at=excluded.updated_at
        """,
        (
            claim["id"],
            claim["page_path"],
            claim["claim_type"],
            claim["claim"],
            claim["confidence"],
            claim["review_state"],
            evidence_json,
            now,
        ),
    )
    return status


def build_daily_memory(
    conn: sqlite3.Connection,
    *,
    date: str | None = None,
) -> dict[str, Any]:
    if date is None:
        date = _latest_evidence_date(conn)
    date = _validate_date(date)
    observations = conn.execute(
        """
        SELECT *
        FROM preference_observations
        WHERE substr(ts, 1, 10) = ?
        ORDER BY ts
        """,
        (date,),
    ).fetchall()
    evidence_ids = {row["id"] for row in observations}
    candidates = _load_candidates_for_date(conn, date, evidence_ids)
    episodes = conn.execute(
        """
        SELECT *
        FROM episodes
        WHERE kind = 'lighting_preference'
          AND substr(end_ts, 1, 10) = ?
        ORDER BY end_ts
        """,
        (date,),
    ).fetchall()
    body, journal_evidence_ids = _journal_body(
        date=date,
        observations=observations,
        candidates=candidates,
        episodes=episodes,
    )
    now = utc_now()
    journal_id = f"journal:{date}"
    conn.execute(
        """
        INSERT INTO journals (id, date, body_md, evidence_ids_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            date=excluded.date,
            body_md=excluded.body_md,
            evidence_ids_json=excluded.evidence_ids_json
        """,
        (journal_id, date, body, canonical_json(journal_evidence_ids)),
    )

    claims = []
    for row in candidates:
        claims.append(_candidate_claim(row))
        issue = _issue_claim(row)
        if issue:
            claims.append(issue)
    claim_counts = Counter()
    for claim in claims:
        claim_counts[_upsert_claim(conn, claim, now)] += 1
    conn.commit()
    return {
        "ok": True,
        "date": date,
        "journal": {
            "id": journal_id,
            "date": date,
            "body_md": body,
            "evidence_ids": journal_evidence_ids,
        },
        "claims": {
            "considered": len(claims),
            "inserted": claim_counts.get("inserted", 0),
            "updated": claim_counts.get("updated", 0),
            "unchanged": claim_counts.get("unchanged", 0),
        },
    }


def list_journals(conn: sqlite3.Connection, limit: int = 7) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM journals
        ORDER BY date DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "id": row["id"],
                "date": row["date"],
                "body_md": row["body_md"],
                "evidence_ids": _json_loads(row["evidence_ids_json"], []),
            }
        )
    return out


def get_journal(conn: sqlite3.Connection, date: str) -> dict[str, Any] | None:
    date = _validate_date(date)
    row = conn.execute(
        """
        SELECT *
        FROM journals
        WHERE date = ?
        """,
        (date,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "date": row["date"],
        "body_md": row["body_md"],
        "evidence_ids": _json_loads(row["evidence_ids_json"], []),
    }


def _claim_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "page_path": row["page_path"],
        "claim_type": row["claim_type"],
        "claim": row["claim"],
        "confidence": row["confidence"],
        "review_state": row["review_state"],
        "evidence_ids": _json_loads(row["evidence_ids_json"], []),
        "updated_at": row["updated_at"],
    }


def _observation_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "ts": row["ts"],
        "zone": row["zone"],
        "primary_zone": row["primary_zone"],
        "context_id": row["context_id"],
        "evidence_id": row["evidence_id"],
        "dedupe_key": row["dedupe_key"],
        "dedupe_window_s": row["dedupe_window_s"],
        "capture_suppressed_count": row["capture_suppressed_count"],
        "related_override_context_id": row["related_override_context_id"],
        "light_entity": row["light_entity"],
        "light_state": row["light_state"],
        "actual_brightness_pct": row["actual_brightness_pct"],
        "predicted_brightness_pct": row["predicted_brightness_pct"],
        "delta_pct": row["delta_pct"],
        "profile": row["profile"],
        "state": row["state"],
        "tv_playing": row["tv_playing"],
        "asleep": row["asleep"],
        "night_safe": row["night_safe"],
        "user_at_home": row["user_at_home"],
        "shadow_mode": bool(row["shadow_mode"]),
        "weight": row["weight"],
        "predictions_by_zone": _json_loads(row["predictions_by_zone_json"], {}),
        "capture_provenance": _json_loads(row["capture_provenance_json"], {}),
        "prediction_consistency": _json_loads(row["prediction_consistency_json"], {}),
        "occupancy": _json_loads(row["occupancy_json"], {}),
        "occupancy_flicker_count_5min": row["occupancy_flicker_count_5min"],
        "context": _json_loads(row["context_json"], {}),
    }


def _candidate_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "zone": row["zone"],
        "context": _json_loads(row["context_json"], {}),
        "status": row["status"],
        "evidence_count": row["evidence_count"],
        "supporting_evidence_count": row["supporting_evidence_count"],
        "linked_override_count": row["linked_override_count"],
        "linked_pending_count": row["linked_pending_count"],
        "capture_suppressed_total": row["capture_suppressed_total"],
        "dedupe_key_count": row["dedupe_key_count"],
        "non_shadow_count": row["non_shadow_count"],
        "days_seen": row["days_seen"],
        "median_actual_brightness_pct": row["median_actual_brightness_pct"],
        "median_predicted_brightness_pct": row["median_predicted_brightness_pct"],
        "median_delta_pct": row["median_delta_pct"],
        "delta_variance": row["delta_variance"],
        "shadow_mode_ratio": row["shadow_mode_ratio"],
        "opposite_direction_rate": row["opposite_direction_rate"],
        "duplicate_pending_ratio": row["duplicate_pending_ratio"],
        "suggested_target": _json_loads(row["suggested_target_json"], {}),
        "blockers": _json_loads(row["blockers_json"], []),
        "quality_warnings": _json_loads(row["quality_warnings_json"], []),
        "evidence_ids": _candidate_evidence_ids(row),
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "updated_at": row["updated_at"],
    }


def _episode_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "start_ts": row["start_ts"],
        "end_ts": row["end_ts"],
        "zone": row["zone"],
        "kind": row["kind"],
        "summary": _json_loads(row["summary_json"], {}),
    }


def list_wiki_claims(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    review_state: str | None = None,
    claim_type: str | None = None,
    zone: str | None = None,
    min_confidence: float | None = None,
) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if review_state:
        where.append("review_state = ?")
        params.append(review_state)
    if claim_type:
        where.append("claim_type = ?")
        params.append(claim_type)
    if zone:
        where.append("(page_path LIKE ? OR page_path LIKE ?)")
        params.extend([f"%/{zone}.md", f"%/{zone}-%"])
    if min_confidence is not None:
        where.append("confidence >= ?")
        params.append(min_confidence)
    sql = "SELECT * FROM wiki_claims"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC, confidence DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for row in rows:
        out.append(_claim_from_row(row))
    return out


def get_wiki_claim_detail(
    conn: sqlite3.Connection,
    claim_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM wiki_claims
        WHERE id = ?
        """,
        (claim_id,),
    ).fetchone()
    if not row:
        return None
    claim = _claim_from_row(row)
    evidence_ids = set(claim.get("evidence_ids") or [])
    evidence: list[dict[str, Any]] = []
    if evidence_ids:
        placeholders = ",".join("?" for _ in evidence_ids)
        rows = conn.execute(
            f"""
            SELECT *
            FROM preference_observations
            WHERE id IN ({placeholders})
            ORDER BY ts
            """,
            list(evidence_ids),
        ).fetchall()
        evidence = [_observation_from_row(item) for item in rows]

    candidates: list[dict[str, Any]] = []
    candidate_rows = conn.execute(
        """
        SELECT *
        FROM preference_candidates
        ORDER BY evidence_count DESC, updated_at DESC
        """
    ).fetchall()
    for candidate in candidate_rows:
        if evidence_ids.intersection(_candidate_evidence_ids(candidate)):
            candidates.append(_candidate_from_row(candidate))

    episodes: list[dict[str, Any]] = []
    episode_rows = conn.execute(
        """
        SELECT *
        FROM episodes
        WHERE kind = 'lighting_preference'
        ORDER BY end_ts DESC
        LIMIT 500
        """
    ).fetchall()
    for episode in episode_rows:
        decoded = _episode_from_row(episode)
        if evidence_ids.intersection(decoded["summary"].get("evidence_ids") or []):
            episodes.append(decoded)

    return {
        "claim": claim,
        "evidence": evidence,
        "candidates": candidates[:20],
        "episodes": episodes[:50],
    }
