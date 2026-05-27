from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable


def default_db_path() -> Path:
    raw = os.environ.get("INTELLIGENCE_DB")
    if raw:
        return Path(raw)
    data_dir = Path(os.environ.get("INTELLIGENCE_DATA_DIR", "/data"))
    return data_dir / "intelligence.sqlite"


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS raw_events (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_path TEXT,
            line_no INTEGER,
            source_ts TEXT,
            ingest_ts TEXT NOT NULL,
            source_hash TEXT NOT NULL UNIQUE,
            redacted_payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS override_events (
            id TEXT PRIMARY KEY,
            raw_event_id TEXT NOT NULL REFERENCES raw_events(id),
            ts TEXT,
            zone TEXT,
            light_entity TEXT,
            light_state TEXT,
            actual_brightness_pct REAL,
            predicted_brightness_pct REAL,
            delta_pct REAL,
            profile TEXT,
            state TEXT,
            tv_playing INTEGER,
            gaming_active INTEGER,
            asleep INTEGER,
            night_safe INTEGER,
            user_at_home INTEGER,
            shadow_mode INTEGER DEFAULT 0,
            predictions_by_zone_json TEXT NOT NULL DEFAULT '{}',
            context_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS preference_observations (
            id TEXT PRIMARY KEY,
            raw_event_id TEXT NOT NULL REFERENCES raw_events(id),
            kind TEXT NOT NULL,
            ts TEXT,
            zone TEXT NOT NULL,
            primary_zone TEXT,
            context_id TEXT,
            evidence_id TEXT,
            dedupe_key TEXT,
            dedupe_window_s INTEGER,
            capture_suppressed_count INTEGER NOT NULL DEFAULT 0,
            related_override_context_id TEXT,
            light_entity TEXT,
            light_state TEXT,
            actual_brightness_pct REAL,
            predicted_brightness_pct REAL,
            delta_pct REAL,
            profile TEXT,
            state TEXT,
            tv_playing INTEGER,
            gaming_active INTEGER,
            asleep INTEGER,
            night_safe INTEGER,
            user_at_home INTEGER,
            shadow_mode INTEGER DEFAULT 0,
            weight REAL NOT NULL DEFAULT 1.0,
            predictions_by_zone_json TEXT NOT NULL DEFAULT '{}',
            capture_provenance_json TEXT NOT NULL DEFAULT '{}',
            prediction_consistency_json TEXT NOT NULL DEFAULT '{}',
            occupancy_json TEXT NOT NULL DEFAULT '{}',
            occupancy_flicker_count_5min INTEGER,
            context_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS preference_candidates (
            id TEXT PRIMARY KEY,
            zone TEXT NOT NULL,
            context_json TEXT NOT NULL,
            status TEXT NOT NULL,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            raw_evidence_count INTEGER NOT NULL DEFAULT 0,
            raw_override_count INTEGER NOT NULL DEFAULT 0,
            override_session_count INTEGER NOT NULL DEFAULT 0,
            burst_event_count INTEGER NOT NULL DEFAULT 0,
            max_override_burst_size INTEGER NOT NULL DEFAULT 0,
            override_burst_ratio REAL,
            pending_evidence_count INTEGER NOT NULL DEFAULT 0,
            session_intent_count INTEGER NOT NULL DEFAULT 0,
            automation_tail_session_count INTEGER NOT NULL DEFAULT 0,
            evidence_tier TEXT,
            supporting_evidence_count INTEGER NOT NULL DEFAULT 0,
            linked_override_count INTEGER NOT NULL DEFAULT 0,
            linked_pending_count INTEGER NOT NULL DEFAULT 0,
            capture_suppressed_total INTEGER NOT NULL DEFAULT 0,
            dedupe_key_count INTEGER NOT NULL DEFAULT 0,
            non_shadow_count INTEGER NOT NULL DEFAULT 0,
            days_seen INTEGER NOT NULL DEFAULT 0,
            median_actual_brightness_pct REAL,
            median_predicted_brightness_pct REAL,
            median_delta_pct REAL,
            delta_variance REAL,
            shadow_mode_ratio REAL,
            opposite_direction_rate REAL,
            duplicate_pending_ratio REAL,
            suggested_target_json TEXT NOT NULL DEFAULT '{}',
            blockers_json TEXT NOT NULL DEFAULT '[]',
            quality_warnings_json TEXT NOT NULL DEFAULT '[]',
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            first_seen TEXT,
            last_seen TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS preference_candidate_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL REFERENCES preference_candidates(id),
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            blockers_json TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL,
            revision_hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS preference_confirmations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT,
            decision TEXT NOT NULL,
            created_at TEXT NOT NULL,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS preference_suppression_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT,
            zone TEXT,
            context_json TEXT NOT NULL DEFAULT '{}',
            reason TEXT,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS lighting_decisions (
            id TEXT PRIMARY KEY,
            raw_event_id TEXT NOT NULL REFERENCES raw_events(id),
            ts TEXT,
            entity_id TEXT,
            zone TEXT,
            from_state TEXT,
            to_state TEXT,
            predicted_brightness_pct REAL,
            predicted_color_temp_kelvin REAL,
            shadow_mode INTEGER DEFAULT 0,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id TEXT PRIMARY KEY,
            start_ts TEXT,
            end_ts TEXT,
            zone TEXT,
            kind TEXT,
            summary_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS journals (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            body_md TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS wiki_claims (
            id TEXT PRIMARY KEY,
            page_path TEXT NOT NULL,
            claim_type TEXT NOT NULL,
            claim TEXT NOT NULL,
            confidence REAL,
            review_state TEXT NOT NULL DEFAULT 'unreviewed',
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wiki_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_path TEXT NOT NULL,
            diff_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS proposals (
            id TEXT PRIMARY KEY,
            candidate_id TEXT,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            body_json TEXT NOT NULL,
            readiness_json TEXT NOT NULL DEFAULT '{}',
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id TEXT,
            kind TEXT NOT NULL,
            body_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS experiments (
            id TEXT PRIMARY KEY,
            proposal_id TEXT,
            candidate_id TEXT,
            status TEXT NOT NULL,
            body_json TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_type TEXT,
            subject_id TEXT,
            decision TEXT NOT NULL,
            actor TEXT,
            reason TEXT,
            note TEXT,
            prior_state TEXT,
            new_state TEXT,
            body_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            details_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS source_cursors (
            source TEXT PRIMARY KEY,
            transport TEXT NOT NULL,
            file_fingerprint TEXT,
            offset INTEGER NOT NULL DEFAULT 0,
            source_size INTEGER NOT NULL DEFAULT 0,
            source_mtime TEXT,
            latest_source_ts TEXT,
            latest_ingested_ts TEXT,
            lag_bytes INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            last_success_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS skill_versions (
            id TEXT PRIMARY KEY,
            skill_name TEXT NOT NULL,
            status TEXT NOT NULL,
            body_md TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS skill_trials (
            id TEXT PRIMARY KEY,
            skill_version_id TEXT NOT NULL,
            suite TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidate_edits (
            id TEXT PRIMARY KEY,
            skill_name TEXT NOT NULL,
            patch_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rejected_edits (
            id TEXT PRIMARY KEY,
            skill_name TEXT NOT NULL,
            reason TEXT NOT NULL,
            patch_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS observation_packets (
            id TEXT PRIMARY KEY,
            observation_id TEXT REFERENCES preference_observations(id),
            event_id TEXT,
            event_type TEXT NOT NULL,
            zone TEXT,
            camera TEXT,
            camera_entity TEXT,
            trigger TEXT,
            event_ts TEXT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            frame_count INTEGER NOT NULL DEFAULT 0,
            qwen_status TEXT NOT NULL DEFAULT 'not_requested',
            privacy_flags_json TEXT NOT NULL DEFAULT '[]',
            retention_policy_json TEXT NOT NULL DEFAULT '{}',
            ha_metadata_json TEXT NOT NULL DEFAULT '{}',
            source_ids_json TEXT NOT NULL DEFAULT '{}',
            model_versions_json TEXT NOT NULL DEFAULT '{}',
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS observation_frames (
            id TEXT PRIMARY KEY,
            packet_id TEXT NOT NULL REFERENCES observation_packets(id),
            frame_id TEXT NOT NULL,
            frame_role TEXT NOT NULL,
            relative_ts_s REAL,
            captured_at TEXT NOT NULL,
            camera TEXT,
            path TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            bytes INTEGER,
            source TEXT NOT NULL,
            promoted_from_ring INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT,
            privacy_flags_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS qwen_label_results (
            id TEXT PRIMARY KEY,
            packet_id TEXT NOT NULL REFERENCES observation_packets(id),
            status TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            model TEXT,
            model_base_url TEXT,
            request_json TEXT NOT NULL DEFAULT '{}',
            raw_output_text TEXT,
            labels_json TEXT NOT NULL DEFAULT '{}',
            validation_errors_json TEXT NOT NULL DEFAULT '[]',
            latency_ms INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS training_clip_manifests (
            id TEXT PRIMARY KEY,
            packet_id TEXT REFERENCES observation_packets(id),
            status TEXT NOT NULL,
            camera TEXT,
            zone TEXT,
            start_ts TEXT,
            end_ts TEXT,
            fps_plan_json TEXT NOT NULL DEFAULT '{}',
            frame_count_tier TEXT,
            linked_observation_ids_json TEXT NOT NULL DEFAULT '[]',
            privacy_flags_json TEXT NOT NULL DEFAULT '[]',
            quality_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS observation_audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            packet_id TEXT NOT NULL REFERENCES observation_packets(id),
            action TEXT NOT NULL,
            actor TEXT,
            note TEXT,
            body_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS label_eval_sets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            model TEXT,
            target_size INTEGER NOT NULL,
            selection_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS label_eval_items (
            id TEXT PRIMARY KEY,
            set_id TEXT NOT NULL REFERENCES label_eval_sets(id),
            packet_id TEXT NOT NULL REFERENCES observation_packets(id),
            qwen_result_id TEXT REFERENCES qwen_label_results(id),
            status TEXT NOT NULL,
            selection_reason TEXT,
            packet_snapshot_json TEXT NOT NULL DEFAULT '{}',
            qwen_labels_json TEXT NOT NULL DEFAULT '{}',
            capture_timing_json TEXT NOT NULL DEFAULT '{}',
            human_labels_json TEXT NOT NULL DEFAULT '{}',
            audit_note TEXT,
            audited_by TEXT,
            audited_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(set_id, packet_id)
        );

        CREATE TABLE IF NOT EXISTS qwen_prompt_trials (
            id TEXT PRIMARY KEY,
            eval_set_id TEXT REFERENCES label_eval_sets(id),
            candidate_name TEXT NOT NULL,
            status TEXT NOT NULL,
            gate_json TEXT NOT NULL DEFAULT '{}',
            candidate_prompt_json TEXT NOT NULL DEFAULT '{}',
            baseline_score_json TEXT NOT NULL DEFAULT '{}',
            candidate_score_json TEXT NOT NULL DEFAULT '{}',
            comparison_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_pref_obs_zone_ts
            ON preference_observations(zone, ts);
        CREATE INDEX IF NOT EXISTS idx_candidates_status
            ON preference_candidates(status);
        CREATE INDEX IF NOT EXISTS idx_lighting_decisions_zone_ts
            ON lighting_decisions(zone, ts);
        CREATE INDEX IF NOT EXISTS idx_proposals_status
            ON proposals(status);
        CREATE INDEX IF NOT EXISTS idx_proposals_candidate
            ON proposals(candidate_id);
        CREATE INDEX IF NOT EXISTS idx_observation_packets_zone_ts
            ON observation_packets(zone, event_ts);
        CREATE INDEX IF NOT EXISTS idx_observation_packets_status
            ON observation_packets(status, qwen_status);
        CREATE INDEX IF NOT EXISTS idx_observation_frames_packet
            ON observation_frames(packet_id);
        CREATE INDEX IF NOT EXISTS idx_qwen_label_results_packet
            ON qwen_label_results(packet_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_label_eval_items_set
            ON label_eval_items(set_id, status);
        CREATE INDEX IF NOT EXISTS idx_label_eval_items_packet
            ON label_eval_items(packet_id);
        CREATE INDEX IF NOT EXISTS idx_qwen_prompt_trials_set
            ON qwen_prompt_trials(eval_set_id, created_at);
        """
    )
    _ensure_column(
        conn,
        "proposals",
        "candidate_id",
        "candidate_id TEXT",
    )
    _ensure_column(
        conn,
        "proposals",
        "readiness_json",
        "readiness_json TEXT NOT NULL DEFAULT '{}'",
    )
    _ensure_column(
        conn,
        "proposals",
        "evidence_ids_json",
        "evidence_ids_json TEXT NOT NULL DEFAULT '[]'",
    )
    _ensure_column(
        conn,
        "proposals",
        "updated_at",
        "updated_at TEXT",
    )
    _ensure_column(
        conn,
        "decisions",
        "subject_type",
        "subject_type TEXT",
    )
    _ensure_column(
        conn,
        "decisions",
        "actor",
        "actor TEXT",
    )
    _ensure_column(
        conn,
        "decisions",
        "reason",
        "reason TEXT",
    )
    _ensure_column(
        conn,
        "decisions",
        "note",
        "note TEXT",
    )
    _ensure_column(
        conn,
        "decisions",
        "prior_state",
        "prior_state TEXT",
    )
    _ensure_column(
        conn,
        "decisions",
        "new_state",
        "new_state TEXT",
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_subject ON decisions(subject_type, subject_id)"
    )
    _ensure_column(
        conn,
        "experiments",
        "proposal_id",
        "proposal_id TEXT",
    )
    _ensure_column(
        conn,
        "experiments",
        "candidate_id",
        "candidate_id TEXT",
    )
    _ensure_column(
        conn,
        "experiments",
        "evidence_ids_json",
        "evidence_ids_json TEXT NOT NULL DEFAULT '[]'",
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_experiments_proposal ON experiments(proposal_id)"
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "quality_warnings_json",
        "quality_warnings_json TEXT NOT NULL DEFAULT '[]'",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "raw_evidence_count",
        "raw_evidence_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "raw_override_count",
        "raw_override_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "override_session_count",
        "override_session_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "burst_event_count",
        "burst_event_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "max_override_burst_size",
        "max_override_burst_size INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "override_burst_ratio",
        "override_burst_ratio REAL",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "pending_evidence_count",
        "pending_evidence_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "session_intent_count",
        "session_intent_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "automation_tail_session_count",
        "automation_tail_session_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "evidence_tier",
        "evidence_tier TEXT",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "supporting_evidence_count",
        "supporting_evidence_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "linked_override_count",
        "linked_override_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "linked_pending_count",
        "linked_pending_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "duplicate_pending_ratio",
        "duplicate_pending_ratio REAL",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "primary_zone",
        "primary_zone TEXT",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "context_id",
        "context_id TEXT",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "evidence_id",
        "evidence_id TEXT",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "dedupe_key",
        "dedupe_key TEXT",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "dedupe_window_s",
        "dedupe_window_s INTEGER",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "capture_suppressed_count",
        "capture_suppressed_count INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "related_override_context_id",
        "related_override_context_id TEXT",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "capture_provenance_json",
        "capture_provenance_json TEXT NOT NULL DEFAULT '{}'",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "prediction_consistency_json",
        "prediction_consistency_json TEXT NOT NULL DEFAULT '{}'",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "occupancy_json",
        "occupancy_json TEXT NOT NULL DEFAULT '{}'",
    )
    _ensure_column(
        conn,
        "preference_observations",
        "occupancy_flicker_count_5min",
        "occupancy_flicker_count_5min INTEGER",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "capture_suppressed_total",
        "capture_suppressed_total INTEGER NOT NULL DEFAULT 0",
    )
    _ensure_column(
        conn,
        "preference_candidates",
        "dedupe_key_count",
        "dedupe_key_count INTEGER NOT NULL DEFAULT 0",
    )
    # MC.1 — gaming_active context dimension (Steam-driven gaming-mode
    # lighting from the HA side). Additive: rows without the field land
    # as NULL, which groups together identically (None == None) so
    # historical sessions stay coherent. New rows with gaming_active=1
    # form their own group per session_key/policy_context.
    _ensure_column(
        conn,
        "preference_observations",
        "gaming_active",
        "gaming_active INTEGER",
    )
    _ensure_column(
        conn,
        "override_events",
        "gaming_active",
        "gaming_active INTEGER",
    )
    conn.commit()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
