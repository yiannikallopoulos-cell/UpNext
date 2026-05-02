# Methodology

This document explains the reasoning behind UpNext's design choices. It is intentionally honest about what is empirically grounded and what is reasoned defaults — the latter pending backtest validation.

## The discovery thesis

Most creator discovery tools surface creators who are *already trying to be discovered* — they search by hashtag, by self-categorization, by submitted profiles. This selection mechanism systematically under-represents the creators most worth finding: those whose content carries itself algorithmically without explicit discovery signaling.

UpNext's discovery layer runs four channels with different bias profiles:

- **Sounds**: surfaces creators participating in trending audio, including those with sparse captions
- **Search terms**: hits TikTok's content-recognition layer, catching transcribed and on-screen text regardless of caption hashtags
- **Hashtags**: included as a baseline channel but expected to under-represent organic creators
- **Neighbors of known**: graph traversal through commenters and duet partners of creators already in the system, capturing network-adjacent talent

The neighbors channel is expected to become the highest-yield channel in steady state because creator breakouts on TikTok follow social-graph patterns more than topical-tag patterns. A seed-diversity guard limits any single seed creator to ≤5% of harvest output to prevent echo-chamber effects.

## The scoring philosophy

The scoring engine is **labels-primary, score-secondary**. Pattern labels (Steady Climber, Breakout Candidate, Algorithm Darling, Engagement Magnet, Plateau Risk) are interpretable and defensible. The composite 0–100 numeric score is a tiebreaker within label tiers, not a precise ranking signal — its weight assignments are reasoned defaults, not empirically tuned.

This honesty is deliberate. A scoring system that confidently produces precise rankings without empirical grounding is more harmful than helpful. Treating labels as the primary output and scores as secondary respects the limits of the underlying data.

## Why post-level signals, not follower-level

The scoring engine operates on post-level trajectory data rather than follower-count history. This is partly necessity (reliable historical follower data does not exist for sub-50K creators through any accessible provider) and partly design preference — post-level view trajectory is a *leading* indicator of follower growth, not a lagging one.

When TikTok's algorithm begins amplifying a creator, the sequence of observable signals is approximately:

1. Per-post view counts spike
2. View-to-follower ratios climb
3. Engagement patterns shift (comments per view rise as new audiences engage)
4. Follower count begins responding (with several days to weeks of lag)
5. Follower count reflects the change in steady-state metrics

Scoring on signals 1–3 catches creators 1–4 weeks earlier than scoring on signal 4 or 5. For a system whose value proposition is *finding talent before competitors*, this is a meaningful edge.

## The 14-day post-age cutoff

TikTok view counts continue accumulating after publish, faster early then trailing off. Empirical industry estimates of view-accumulation curves:

- ~40–50% of final views in the first 48 hours
- ~70–80% in the first 7 days
- ~90% in the first 14 days
- ~95–98% in the first 30 days

Engagement-rate signals are computed only on posts at least 14 days old. This trades a 14-day lag in scoring recency for ~90% accuracy on settled engagement, which is the right tradeoff for a system focused on trend detection rather than real-time monitoring. Earlier cutoffs systematically underestimate engagement on viral posts (which continue accumulating views longer), which would bias against the very breakouts the system is meant to catch.

## Forward-built history

Sub-50K TikTok creators do not have reliable historical follower-count data available through any provider. UpNext addresses this by:

1. **Computing scoring from post-level data on day one** — every signal except absolute follower-growth rate is computable from a single deep scrape, which Apify provides.
2. **Building follower-count history forward** — weekly snapshots accumulate into a multi-week trajectory that becomes a supplementary signal as the dataset matures.
3. **Tracking graduates** — when creators cross 50K and exit the active band, lightweight follower-only snapshots continue indefinitely. This produces ground-truth validation data for the system's predictions over time and is the single most powerful interview asset the system generates.

## Categorization with human-in-the-loop

Initial categorization is performed by Claude with structured prompts. Manual corrections are stored append-only with reasoning. In phase 2, accumulated corrections feed into few-shot examples in the categorization prompt, gradually aligning the LLM with the operator's domain judgment. No model fine-tuning is performed; the alignment happens at prompt-construction time.

This architecture treats domain expertise as an asset that compounds in the data, not as labor to be eliminated. The system is more accurate after a hundred reviews than after one, and the operator's expertise is captured rather than burned.

## Tiered tracking cadence

Not every creator merits the same observation frequency. UpNext's tiered cadence:

- **Watchlist (~150–200 creators)**: weekly tracking. Top scorers plus creators flagged for trend confirmation.
- **Active set (~250–300 creators)**: biweekly tracking. In-band creators with passing scores.
- **Long tail**: monthly tracking. New entries and below-threshold creators pending more data.
- **Graduated (>50K)**: weekly follower-only snapshots. Alumni track for outcome validation.
- **Dormant (60+ days inactive)**: scraping paused, history retained, eligible for re-entry on opportunistic re-discovery.

Creators on the watchlist with provisional trend signals receive higher-resolution tracking specifically to confirm or reject subtle trends. This makes the watchlist a dual-purpose structure: best creators *and* highest-value-per-additional-datapoint creators.

## Known limitations

The scoring weights are reasoned defaults. A planned backtest against creators who crossed 100K in the prior year will tune weights against ground truth.

The 14-day cutoff lags the platform's real-time signal by two weeks for engagement-rate computation. This is acceptable for trend detection but would not be acceptable for, say, real-time brand-safety monitoring.

Apify-based scraping is subject to occasional disruption when TikTok changes its site structure. Apify maintains the scrapers, but isolated days of degraded harvest are expected.

The system has no view into private or protected accounts. Creators with locked profiles are invisible to the discovery layer.
