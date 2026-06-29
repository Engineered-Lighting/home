-- 0002_embeddings_clip_idx.sql (M4) -- embeddings rows are per-CLIP: every
-- analysis window stores 3 pooled_clip vectors (one per 16-frame clip) plus
-- 1 pooled_window mean; clip_idx disambiguates the pooled_clip rows. The
-- unique index is what makes the embed job's INSERT OR REPLACE idempotent
-- per (window, model, kind, clip). Table is empty before M4 (the schema
-- shipped at M0, no writer existed), so the ALTER is safe on the live db.

ALTER TABLE embeddings ADD COLUMN clip_idx INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS uq_embeddings_row
  ON embeddings (analysis_window_id, model_id, kind, clip_idx);
