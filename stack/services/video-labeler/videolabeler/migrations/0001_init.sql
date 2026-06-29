-- 0001_init.sql — FULL v1 schema, shipped at M0 so later milestones do not
-- churn migrations. created_at/updated_at are ISO-8601 UTC TEXT set by code
-- (one fixed format; lexicographic comparison is chronologically correct).

CREATE TABLE IF NOT EXISTS videos (
  id            TEXT PRIMARY KEY,             -- vid_<sha256[:16]>
  drive_file_id TEXT,
  filename      TEXT NOT NULL,
  size_bytes    INTEGER,
  mime          TEXT,
  checksum      TEXT NOT NULL UNIQUE,         -- sha256 hex of the original file
  local_path    TEXT,                         -- relative to DATA_DIR, forward slashes
  duration_s    REAL,
  fps           REAL,
  codec         TEXT,
  width         INTEGER,
  height        INTEGER,
  rotation_deg  INTEGER NOT NULL DEFAULT 0,
  source_batch  TEXT,
  is_holdout    INTEGER NOT NULL DEFAULT 0 CHECK (is_holdout IN (0, 1)),
                -- set at import, immutable via API
  import_status TEXT NOT NULL DEFAULT 'imported'
                CHECK (import_status IN ('imported','probed','ready','failed','deleted')),
  error         TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos (import_status);
CREATE INDEX IF NOT EXISTS idx_videos_batch  ON videos (source_batch);

CREATE TABLE IF NOT EXISTS video_artifacts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id   TEXT NOT NULL REFERENCES videos (id),
  kind       TEXT NOT NULL CHECK (kind IN ('proxy480','sprite','keyframes')),
  path       TEXT NOT NULL,                   -- relative to DATA_DIR
  meta_json  TEXT,
  status     TEXT NOT NULL DEFAULT 'ready'
             CHECK (status IN ('pending','ready','failed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (video_id, kind)
);

-- Per-axis SEGMENTS are the canonical human-labeled record (M1 populates).
CREATE TABLE IF NOT EXISTS segments (
  id            TEXT PRIMARY KEY,             -- seg_<hex>
  video_id      TEXT NOT NULL REFERENCES videos (id),
  axis          TEXT NOT NULL CHECK (axis IN ('activity','posture','quality','custom')),
  start_s       REAL NOT NULL,
  end_s         REAL NOT NULL,
  value         TEXT NOT NULL,                -- scalar, or JSON array on the quality axis
  review_state  TEXT NOT NULL DEFAULT 'needs_review'
                CHECK (review_state IN ('prelabel','needs_review','reviewed',
                                        'accepted','rejected','excluded_from_export')),
  source        TEXT NOT NULL DEFAULT 'human'
                CHECK (source IN ('vlm','human','cluster','rule')),
  confidence    REAL,
  aux_json      TEXT,                         -- activity axis only: verb/object_noun/room_zone/
                                              -- attention_target/motion_intensity/activity_phase/notes
  person_slot   TEXT,                         -- NULLABLE: v1 is single-primary-occupant;
                                              -- forward-compat for per-track labels
  evidence_json TEXT,
  reviewed_by   TEXT,
  reviewed_at   TEXT,
  is_current    INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
  superseded_by TEXT REFERENCES segments (id),-- split/merge/nudge history
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_video_axis ON segments (video_id, axis, is_current);
CREATE INDEX IF NOT EXISTS idx_segments_review     ON segments (review_state);

CREATE TABLE IF NOT EXISTS video_label_state (
  video_id   TEXT PRIMARY KEY REFERENCES videos (id),
  revision   INTEGER NOT NULL DEFAULT 0,      -- optimistic concurrency: bumped ONLY by
                                              -- human/geometry writes, never by VLM suggestions
  updated_at TEXT NOT NULL
);

-- Immutable machine analysis grid (8s windows, 4s stride); M2 populates.
CREATE TABLE IF NOT EXISTS analysis_windows (
  id                TEXT PRIMARY KEY,         -- aw_<video>_<start_ms>
  video_id          TEXT NOT NULL REFERENCES videos (id),
  start_s           REAL NOT NULL,
  end_s             REAL NOT NULL,
  person_presence   REAL,
  motion_energy     REAL,
  pose_summary_json TEXT,                     -- incl. person bboxes + count
  prelabel_status   TEXT NOT NULL DEFAULT 'pending',
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aw_video ON analysis_windows (video_id, start_s);

CREATE TABLE IF NOT EXISTS custom_labels (
  slug                   TEXT PRIMARY KEY,    -- custom:<slug>
  axis                   TEXT NOT NULL,
  name                   TEXT NOT NULL,
  description            TEXT,
  aliases_json           TEXT,
  color                  TEXT,
  status                 TEXT NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','hidden','merged','promoted')),
  merged_into            TEXT,
  canonical_mapping      TEXT,
  mapping_approved       INTEGER NOT NULL DEFAULT 0,
  example_segments_json  TEXT,
  negative_segments_json TEXT,
  created_at             TEXT NOT NULL,
  updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id               TEXT PRIMARY KEY,          -- job_<hex>
  type             TEXT NOT NULL,             -- import|probe|proxy|sprite|segment|prelabel|
                                              -- embed|cluster|export|train|evaluate|promote
  state            TEXT NOT NULL DEFAULT 'queued'
                   CHECK (state IN ('queued','running','succeeded','failed','cancelled')),
  lane             TEXT NOT NULL CHECK (lane IN ('cpu','gpu')),
  payload_json     TEXT,
  idempotency_key  TEXT UNIQUE,
  progress         REAL NOT NULL DEFAULT 0,
  progress_msg     TEXT,
  checkpoint_json  TEXT,                      -- resume point, preserved across requeues
  error            TEXT,
  attempts         INTEGER NOT NULL DEFAULT 0,
  max_attempts     INTEGER NOT NULL DEFAULT 3,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
  heartbeat_at     TEXT,
  created_at       TEXT NOT NULL,
  started_at       TEXT,
  finished_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_state_lane ON jobs (state, lane);
CREATE INDEX IF NOT EXISTS idx_jobs_created    ON jobs (created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_type       ON jobs (type);

-- GPU-eviction deadman ledger (M2): record what the labeler stopped/evicted
-- BEFORE doing it; startup recovery + hard-TTL restore read this.
CREATE TABLE IF NOT EXISTS exclusive_state (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  holder_job_id TEXT REFERENCES jobs (id),
  units_json    TEXT NOT NULL,                -- evicted/stopped units + per-unit restore method
  deadline_at   TEXT NOT NULL,                -- hard TTL: API force-restores past this
  state         TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','restored')),
  created_at    TEXT NOT NULL,
  restored_at   TEXT
);

CREATE TABLE IF NOT EXISTS embeddings (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_window_id TEXT NOT NULL REFERENCES analysis_windows (id),
  model_id           TEXT NOT NULL,
  kind               TEXT NOT NULL CHECK (kind IN ('pooled_clip','pooled_window')),
  shard_path         TEXT NOT NULL,           -- fp16 npy shard, relative to DATA_DIR
  row_index          INTEGER NOT NULL,
  dim                INTEGER NOT NULL,
  frames_json        TEXT,                    -- exact frame indices + timestamps + crop box
  cache_key          TEXT UNIQUE,
  created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emb_window ON embeddings (analysis_window_id, model_id, kind);

CREATE TABLE IF NOT EXISTS cluster_runs (
  id          TEXT PRIMARY KEY,
  model_id    TEXT,
  k           INTEGER,
  params_json TEXT,
  status      TEXT NOT NULL DEFAULT 'pending',
  created_at  TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS cluster_members (
  cluster_run_id     TEXT NOT NULL REFERENCES cluster_runs (id),
  analysis_window_id TEXT NOT NULL REFERENCES analysis_windows (id),
  cluster_idx        INTEGER NOT NULL,
  distance           REAL,
  is_outlier         INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (cluster_run_id, analysis_window_id)
);

CREATE TABLE IF NOT EXISTS exports (
  id                 TEXT PRIMARY KEY,
  dataset_version    TEXT NOT NULL UNIQUE,
  head               TEXT NOT NULL,
  filters_json       TEXT,
  counts_json        TEXT,
  splits_json        TEXT,
  content_hash       TEXT,
  holdout_evals_used INTEGER NOT NULL DEFAULT 0,  -- ONE holdout eval per (export, head)
  status             TEXT NOT NULL DEFAULT 'pending',
  created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
  id                   TEXT PRIMARY KEY,
  kind                 TEXT NOT NULL,         -- baseline|attentive_probe|...
  head                 TEXT NOT NULL,         -- activity_primary|posture|quality_flags
  export_id            TEXT REFERENCES exports (id),
  backbone             TEXT,
  temporal_config_json TEXT,
  metrics_json         TEXT,
  holdout_metrics_json TEXT,
  calibration_json     TEXT,
  confusion_json       TEXT,
  status               TEXT NOT NULL DEFAULT 'trained',
  refusal_reasons_json TEXT,
  shadow_only          INTEGER NOT NULL DEFAULT 1,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration_stats (
  axis         TEXT NOT NULL,
  value        TEXT NOT NULL,
  conf_decile  INTEGER NOT NULL,
  n_prelabeled INTEGER NOT NULL DEFAULT 0,
  n_accepted   INTEGER NOT NULL DEFAULT 0,
  n_rejected   INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (axis, value, conf_decile)
);
