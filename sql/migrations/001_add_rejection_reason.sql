-- Migration 001: Add rejection_reason column to creators table.
--
-- When the filter stage rejects a candidate, we want to know why so we can
-- analyze rejection patterns and tune filter thresholds. Without this, all
-- rejected creators look the same — useless for diagnostics.
--
-- Idempotent via IF NOT EXISTS so re-running is safe.

ALTER TABLE creators
    ADD COLUMN IF NOT EXISTS rejection_reason TEXT;

-- Index on rejection_reason isn't necessary; the column will be NULL for
-- the vast majority of rows and we won't query it for ranking purposes.
-- Aggregations like "count rejections by reason" don't need an index at
-- this scale.
