-- ============================================================
-- TikTok Creator Scouting System - Database Schema v1
-- ============================================================
-- Postgres 14+ assumed. Uses native timestamp with time zone,
-- JSONB for flexible blobs, generated columns where helpful.
--
-- Design principles:
--   1. Time-series tables are append-only. We never overwrite history.
--   2. Identity is anchored on TikTok's stable user ID, not handle
--      (handles can change).
--   3. Lifecycle states are explicit enums, not derived flags, so
--      tier transitions are auditable.
--   4. Correction history is preserved separately from LLM history
--      so phase 2 few-shot prompting has clean training material.
-- ============================================================

-- Enums kept narrow and explicit.
CREATE TYPE category_t AS ENUM ('sports_commentary', 'grwm', 'other');

CREATE TYPE lifecycle_t AS ENUM (
    'new',          -- just discovered, not yet categorized or scored
    'watchlist',    -- top scorers + trend-confirmation candidates; weekly tracking
    'active',       -- mid-tier, in band, biweekly tracking
    'long_tail',    -- below scoring threshold but in band, monthly tracking
    'graduated',    -- crossed 50K, alumni follower-only tracking
    'dormant',      -- inactive 60+ days, scraping paused, history retained
    'rejected'      -- failed quality filter (bot, spam, etc.), excluded permanently
);

CREATE TYPE discovery_channel_t AS ENUM (
    'sounds',
    'search_terms',
    'hashtags',
    'neighbors',
    'manual'        -- reserved for any creator you add by hand
);

CREATE TYPE pattern_label_t AS ENUM (
    'steady_climber',
    'breakout_candidate',
    'algorithm_darling',
    'engagement_magnet',
    'plateau_risk'
);

-- ============================================================
-- creators
-- ============================================================
-- One row per TikTok creator. The anchor table.
-- tiktok_user_id is the stable identifier; handle can change.
CREATE TABLE creators (
    id              BIGSERIAL PRIMARY KEY,
    tiktok_user_id  TEXT NOT NULL UNIQUE,
    handle          TEXT NOT NULL,
    display_name    TEXT,
    bio             TEXT,

    -- Lifecycle and category. category_locked tracks whether your
    -- manual correction has overridden the LLM. If true, future
    -- LLM runs do not change category without explicit re-review.
    lifecycle           lifecycle_t NOT NULL DEFAULT 'new',
    current_category    category_t,
    current_sub_archetype TEXT,         -- free text, e.g. 'voiceover_grwm', 'nba_analyst'
    category_locked     BOOLEAN NOT NULL DEFAULT FALSE,

    -- Discovery provenance.
    first_discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    first_discovery_channel discovery_channel_t NOT NULL,

    -- Lifecycle audit timestamps. Explicit columns rather than derived
    -- so we can index on them and query "who graduated this month".
    graduated_at        TIMESTAMPTZ,
    dormant_at          TIMESTAMPTZ,
    rejected_at         TIMESTAMPTZ,

    last_scraped_at     TIMESTAMPTZ,    -- last successful tracking scrape
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_creators_lifecycle ON creators(lifecycle);
CREATE INDEX idx_creators_category ON creators(current_category);
CREATE INDEX idx_creators_last_scraped ON creators(last_scraped_at);
CREATE INDEX idx_creators_handle ON creators(handle);

-- ============================================================
-- creator_snapshots
-- ============================================================
-- Append-only time series of creator-level metrics.
-- One row per (creator, scrape time). Powers the forward-built
-- follower trajectory for the alumni track and trend analysis.
CREATE TABLE creator_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    creator_id      BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    follower_count  INTEGER NOT NULL,
    following_count INTEGER,
    total_posts     INTEGER,
    total_hearts    BIGINT,             -- cumulative likes across all posts

    -- Snapshot of bio at this time, since bio changes are themselves
    -- a signal (rebrand events, new niche pivot, etc.).
    bio_at_snapshot TEXT,

    UNIQUE (creator_id, snapshot_at)
);

CREATE INDEX idx_snapshots_creator_time ON creator_snapshots(creator_id, snapshot_at DESC);
CREATE INDEX idx_snapshots_time ON creator_snapshots(snapshot_at DESC);

-- ============================================================
-- posts
-- ============================================================
-- One row per TikTok post we've seen. Posts are immutable metadata;
-- their metrics live in post_metrics and accumulate over the 14-day
-- window before being marked settled.
CREATE TABLE posts (
    id              BIGSERIAL PRIMARY KEY,
    tiktok_post_id  TEXT NOT NULL UNIQUE,
    creator_id      BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,

    published_at    TIMESTAMPTZ NOT NULL,
    caption         TEXT,
    sound_id        TEXT,
    sound_title     TEXT,
    hashtags        TEXT[],             -- parsed from caption, can be empty

    -- 14-day settlement: once true, no further metric scrapes needed.
    metrics_settled BOOLEAN NOT NULL DEFAULT FALSE,
    settled_at      TIMESTAMPTZ,

    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_posts_creator ON posts(creator_id, published_at DESC);
CREATE INDEX idx_posts_unsettled ON posts(metrics_settled, published_at)
    WHERE metrics_settled = FALSE;
CREATE INDEX idx_posts_sound ON posts(sound_id) WHERE sound_id IS NOT NULL;

-- ============================================================
-- post_metrics
-- ============================================================
-- Append-only time series of per-post metrics. A post gets multiple
-- rows during its first 14 days (typically: at first scrape, at ~7
-- days, at 14 days for final settlement). After settlement, no
-- further rows are added.
CREATE TABLE post_metrics (
    id              BIGSERIAL PRIMARY KEY,
    post_id         BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    post_age_hours  INTEGER NOT NULL,   -- hours since published_at, for trend analysis

    view_count      BIGINT NOT NULL,
    like_count      INTEGER NOT NULL,
    comment_count   INTEGER NOT NULL,
    share_count     INTEGER NOT NULL,
    save_count      INTEGER,            -- not always available, tolerate null

    UNIQUE (post_id, scraped_at)
);

CREATE INDEX idx_post_metrics_post_time ON post_metrics(post_id, scraped_at DESC);

-- ============================================================
-- categorizations
-- ============================================================
-- Append-only history of LLM category assignments. Each scoring run
-- that re-categorizes a creator adds a new row. The most recent row
-- is the current LLM opinion (which may be overridden by a correction).
CREATE TABLE categorizations (
    id                  BIGSERIAL PRIMARY KEY,
    creator_id          BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    categorized_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    category            category_t NOT NULL,
    sub_archetype       TEXT,
    confidence          NUMERIC(4,3) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    reasoning           TEXT,           -- LLM's explanation, used for debugging

    -- Identifies which prompt version/model produced this. Critical
    -- for understanding category drift when prompts evolve.
    model_name          TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,

    -- Snapshot of the inputs used, so we can replay if needed.
    input_bio           TEXT,
    input_captions      JSONB           -- array of caption strings used as input
);

CREATE INDEX idx_categorizations_creator ON categorizations(creator_id, categorized_at DESC);

-- ============================================================
-- category_corrections
-- ============================================================
-- Your manual overrides. This table is the long-term IP of the
-- system. Phase 2 few-shot prompting will pull from here.
-- Append-only: revisions are added as new rows, not updates.
CREATE TABLE category_corrections (
    id                  BIGSERIAL PRIMARY KEY,
    creator_id          BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    corrected_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    corrected_category      category_t NOT NULL,
    corrected_sub_archetype TEXT,

    -- Your reasoning. Optional but high-value for phase 2 prompting,
    -- since few-shot examples with reasoning beat bare label examples.
    reasoning           TEXT,

    -- Tracks what you were correcting against. Helps phase 2 learn
    -- where the LLM was systematically wrong.
    overrides_categorization_id BIGINT REFERENCES categorizations(id)
);

CREATE INDEX idx_corrections_creator ON category_corrections(creator_id, corrected_at DESC);
CREATE INDEX idx_corrections_time ON category_corrections(corrected_at DESC);

-- ============================================================
-- scores
-- ============================================================
-- Append-only history of computed scores per creator.
-- Each scoring run produces one row per scored creator.
-- breakdown stores per-signal contributions for transparency/debugging.
CREATE TABLE scores (
    id                  BIGSERIAL PRIMARY KEY,
    creator_id          BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Composite score, 0-100. Treated as tiebreaker within label tier,
    -- not as a primary ranking signal (per our methodology decision).
    score               NUMERIC(5,2) NOT NULL,

    -- Confidence in the score, factoring in datapoint count.
    -- Low confidence = subtle/tentative trends, high confidence = robust.
    confidence          NUMERIC(4,3) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),

    -- Pattern labels. A creator can have multiple labels.
    labels              pattern_label_t[] NOT NULL DEFAULT '{}',

    -- Per-signal contribution breakdown. Stored as JSONB rather than
    -- columns because the signal set will evolve. Example shape:
    -- { "view_acceleration": 18.4, "view_to_follower": 15.2, ... }
    breakdown           JSONB NOT NULL,

    -- How many datapoints fed each signal. Critical for confidence.
    datapoint_counts    JSONB NOT NULL,

    -- Version of the scoring algorithm that produced this score,
    -- so we can reason about score drift across algo updates.
    algo_version        TEXT NOT NULL
);

CREATE INDEX idx_scores_creator_time ON scores(creator_id, computed_at DESC);
CREATE INDEX idx_scores_time_score ON scores(computed_at DESC, score DESC);

-- ============================================================
-- discovery_events
-- ============================================================
-- Records every time a creator was surfaced by any discovery channel.
-- Used for: (a) multi-channel bonus in scoring, (b) channel yield
-- analysis to tune the harvest mix over time.
CREATE TABLE discovery_events (
    id              BIGSERIAL PRIMARY KEY,
    creator_id      BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    discovered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    channel         discovery_channel_t NOT NULL,

    -- Channel-specific context. For sounds: sound_id. For search:
    -- the query string. For neighbors: the seed creator's id.
    -- JSONB keeps this flexible.
    context         JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- For neighbors channel specifically, FK to the seed creator.
    -- Null for other channels. Lets us enforce the seed-diversity
    -- guard (no single seed contributes >5% of harvest).
    seed_creator_id BIGINT REFERENCES creators(id) ON DELETE SET NULL
);

CREATE INDEX idx_discovery_creator ON discovery_events(creator_id, discovered_at DESC);
CREATE INDEX idx_discovery_channel_time ON discovery_events(channel, discovered_at DESC);
CREATE INDEX idx_discovery_seed ON discovery_events(seed_creator_id)
    WHERE seed_creator_id IS NOT NULL;

-- ============================================================
-- Convenience views (read-only abstractions over the raw tables)
-- ============================================================

-- Latest snapshot per creator. Common query, worth a view.
CREATE VIEW v_latest_snapshot AS
SELECT DISTINCT ON (creator_id)
    creator_id, snapshot_at, follower_count, following_count,
    total_posts, total_hearts
FROM creator_snapshots
ORDER BY creator_id, snapshot_at DESC;

-- Latest score per creator.
CREATE VIEW v_latest_score AS
SELECT DISTINCT ON (creator_id)
    creator_id, computed_at, score, confidence, labels, breakdown
FROM scores
ORDER BY creator_id, computed_at DESC;

-- Effective category: correction wins over LLM categorization.
CREATE VIEW v_effective_category AS
SELECT
    c.id AS creator_id,
    COALESCE(corr.corrected_category, cat.category) AS category,
    COALESCE(corr.corrected_sub_archetype, cat.sub_archetype) AS sub_archetype,
    CASE WHEN corr.id IS NOT NULL THEN 'corrected' ELSE 'llm' END AS source
FROM creators c
LEFT JOIN LATERAL (
    SELECT * FROM categorizations
    WHERE creator_id = c.id
    ORDER BY categorized_at DESC LIMIT 1
) cat ON TRUE
LEFT JOIN LATERAL (
    SELECT * FROM category_corrections
    WHERE creator_id = c.id
    ORDER BY corrected_at DESC LIMIT 1
) corr ON TRUE;
