# Architecture

## System overview

UpNext is a four-stage data pipeline that runs on a tiered cron schedule and writes to a single Postgres database. A separate Next.js dashboard reads from the same database for visualization and manual review workflows.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DISCOVERY HARVEST                           │
│   sounds  │  search terms  │  hashtags  │  neighbors-of-known       │
│                              ↓                                      │
│                       coordinator (dedup)                           │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│                              FILTER                                 │
│   follower band │ activity recency │ post count │ bot heuristics    │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│                            CATEGORIZE                               │
│       Claude API │ category + sub-archetype + confidence            │
│       (corrections from prior reviews used as few-shot in phase 2)  │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│                         ENRICH + SCORE                              │
│   profile snapshot │ post metrics │ signal computation │ labels     │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
                     ┌──────────────────────┐
                     │  Supabase Postgres   │
                     └──────────────────────┘
                                ↓
                     ┌──────────────────────┐
                     │   Dashboard (Next)   │
                     └──────────────────────┘
```

## Scheduled jobs

Three independent cron jobs handle different cadences:

**Discovery run** — every ~3 weeks. Runs the four harvest channels, applies filter and categorization, writes new creators to the database.

**Tracking run** — runs daily, but each creator is only scraped on their tier's cadence (weekly / biweekly / monthly / weekly-follower-only). The job determines which creators are due based on `last_scraped_at`.

**Settlement run** — runs daily. Finds posts that have crossed the 14-day post-age threshold and have not yet been marked settled, performs a final metric scrape, and marks them immutable. Triggers re-scoring for affected creators.

## Storage model

Eight tables, append-only where it matters (snapshots, post metrics, categorizations, corrections, scores, discovery events). Lifecycle state lives on the `creators` table as a single enum; transitions are explicit and auditable.

Convenience views surface the common queries: latest snapshot per creator, latest score per creator, effective category (correction-wins-over-LLM).

See [`sql/schema.sql`](../sql/schema.sql) for the complete schema.

## External dependencies

- **Apify** — TikTok data acquisition. The system uses lean profile-plus-recent-posts actors rather than full-archive actors, with differential scraping for repeat tracking. Estimated steady-state cost: ~$100–150/month.
- **Anthropic Claude API** — categorization. Estimated cost: under $20/month at this volume.
- **Supabase** — Postgres host. Free tier sufficient at v1 scale.
- **Cron host** — small VM running the scheduled jobs. Estimated cost: $5–10/month.

## Failure modes and resilience

- **Apify scraper outage**: harvest runs return less data than expected; the system continues with the data it has and logs the gap.
- **TikTok structural changes**: handled by Apify's actor maintenance, not the application layer.
- **Categorization API failure**: creator stays in `lifecycle = 'new'` and is retried on the next discovery run.
- **Database unreachable**: jobs fail loudly rather than silently, with alerting.

## What's deliberately not in this architecture

- **Real-time anything.** All processing is batch on cron. No streaming, no webhooks, no message queues.
- **Multi-user features.** Single operator at v1. Notes, outreach status, team workflows are deferred.
- **Production-grade observability.** Structured logging is in place; full metrics/tracing/alerting is overkill at this scale.
- **CI/CD pipeline.** Tests run locally. Adding GitHub Actions is on the roadmap but does not block functional work.
