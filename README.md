# UpNext

**An automated TikTok creator scouting system for identifying emerging talent before agency representation.**

## What this is

UpNext is a data pipeline and scoring system that continuously surfaces TikTok creators in the 5K–50K follower range showing measurable growth signals across two content categories: **sports commentary** and **GRWM/day-in-the-life**. The system identifies creators agencies should be looking at *before* they break out, not after.

It runs autonomously on a schedule, harvests new creators through multiple discovery channels, computes a composite "Next Up" score from post-level trajectory data, and surfaces ranked candidates with interpretable archetype labels (Steady Climber, Breakout Candidate, Algorithm Darling, Engagement Magnet, Plateau Risk).

## Why it exists

The talent agency industry is structurally bad at finding small creators early. Existing tooling (CreatorIQ, Modash, HypeAuditor) is built for brand-side discovery of mid-to-large creators with established metrics. The pre-agency, sub-50K-follower window — where representation actually adds the most career value — is poorly served by available data products.

UpNext is built around a specific thesis: **the most predictive growth signal in the sub-50K range isn't follower count, it's per-post view trajectory and engagement quality**. When TikTok's algorithm starts amplifying a creator, post views spike before follower counts respond. Scoring on post-level data catches breakouts earlier than any follower-count-based system can.

This project exists as both a working system and a portfolio piece for talent agency interviews.

## How it works

The pipeline runs in four stages:

**1. Discovery harvest** — Surfaces candidate creators via four channels: trending sounds, semantic search terms, hashtags, and "neighbors of known" graph traversal (commenters and duet partners of creators already in the system). Channels run in parallel and dedupe into a unified candidate pool.

**2. Filter** — Applies follower-band, activity, and quality filters. Surviving candidates have between 5K–50K followers, posted within the last 30 days, and pass anti-bot heuristics.

**3. Categorize** — An LLM classifies each surviving candidate into sports commentary, GRWM, or other, with a sub-archetype tag (e.g., voiceover-grwm, single-sport-analyst). Manual corrections are stored and feed back into the prompting layer over time.

**4. Enrich + score** — Per-post metrics are scraped for the qualified set. The scoring engine computes six post-level signals, applies pattern labels, and writes a composite score. Engagement-rate signals are computed only on posts older than 14 days to avoid the cumulative-view-count distortion.

The system tracks creators on a tiered cadence (weekly for the watchlist, biweekly for the active set, monthly for the long tail) and follows graduates past 50K with lightweight follower-only snapshots to validate the system's predictive accuracy over time.

For deeper detail see [`docs/methodology.md`](docs/methodology.md), [`docs/scoring.md`](docs/scoring.md), and [`docs/architecture.md`](docs/architecture.md).

## Tech stack

- **Python 3.14** for the pipeline
- **Apify** for TikTok data acquisition (sounds, search, hashtag, profile, post scraping)
- **Anthropic Claude API** for categorization
- **Supabase Postgres** for storage
- **Next.js + Tailwind** for the dashboard *(planned)*
- **Cron on a small VM** for scheduling

## Current status

This is an active build. Ship status as of latest commit:

- [x] Database schema
- [x] Project scaffolding
- [ ] Discovery harvest channels
- [ ] Filter and categorization stages
- [ ] Enrichment and scoring engine
- [ ] Manual review CLI
- [ ] Dashboard
- [ ] First end-to-end run

## Setup

```bash
git clone https://github.com/yiannikallopoulos-cell/UpNext.git
cd UpNext
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env
# Fill in API keys and database connection string

# Apply the schema to your Supabase database
psql "$DATABASE_URL" < sql/schema.sql
```

## Methodology

The scoring engine is intentionally **labels-primary, score-secondary**. The composite numeric score is treated as a tiebreaker within label tiers rather than a precise ranking, because the underlying signal weights are reasoned defaults, not empirically tuned values. A planned backtest against creators who crossed 100K in the last year will tune weights against ground truth.

Detailed methodology, signal definitions, and weight rationale are in [`docs/methodology.md`](docs/methodology.md). Architectural decisions and tradeoffs are recorded in [`docs/decisions.md`](docs/decisions.md).

## Author

Built by Yianni Kallopoulos as both a working scouting system and a demonstration of applied data engineering for the talent representation industry.
