-- Migration 003: Add stable_performer to pattern_label_t enum.
--
-- Required for scoring algo v3, which introduces the stable_performer label
-- for creators with healthy engagement trend and posting consistency who
-- don't qualify for any other archetype.
--
-- Postgres requires enum modifications as separate ALTER TYPE statements,
-- which cannot run inside a transaction block in older PG versions. Modern
-- Supabase Postgres handles this fine, but we keep it as a standalone
-- statement to be safe.
--
-- Idempotent via IF NOT EXISTS — safe to re-run.

ALTER TYPE pattern_label_t ADD VALUE IF NOT EXISTS 'stable_performer';
