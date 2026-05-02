# Architecture Decisions

A running log of significant design decisions, the alternatives considered, and the reasoning. Decisions are dated and may be revisited; revisions are appended rather than overwriting prior reasoning.

---

## ADR-001: Drop third-party data providers in favor of Apify-only

**Date**: Project inception
**Status**: Accepted

**Context**: The conventional approach to creator analytics platforms is to build on a third-party data provider (Phyllo, Modash, HypeAuditor) that handles ingestion and provides structured creator data via API.

**Decision**: Use Apify directly for all TikTok data acquisition. Skip third-party providers entirely.

**Reasoning**:
- Third-party providers are optimized for brand-side discovery of mid-to-large creators. Their data quality and coverage degrade significantly below 50K followers, which is exactly UpNext's target band.
- Provider costs ($100–300/month minimum) are not justified by the marginal data quality improvement at this scale.
- Apify provides everything needed: profile data, post archives, sound-based search, hashtag-based search, search-term scraping. One vendor relationship instead of two.
- Building directly on Apify avoids vendor lock-in to a provider's specific data model.

**Tradeoffs accepted**: Higher implementation effort vs. consuming a provider's pre-structured API. More resilience burden when TikTok changes site structure (mitigated by Apify maintaining the actors).

---

## ADR-002: Post-level scoring instead of follower-level

**Date**: Project inception
**Status**: Accepted

**Context**: The natural instinct is to score creator growth on follower-count trajectory. Industry tooling typically does this.

**Decision**: Score primarily on post-level signals (view trajectory, engagement quality, posting consistency). Use follower-count history only as a supplementary signal, built forward from project start.

**Reasoning**:
- Reliable historical follower-count data does not exist for sub-50K creators through any accessible provider.
- Per-post view counts are a *leading* indicator of follower growth — TikTok algorithmic amplification shows up in views before followers respond.
- Scoring on leading indicators catches breakouts 1–4 weeks earlier than follower-based scoring.

**Tradeoffs accepted**: More complex signal computation. Requires post-archive scraping in addition to profile scraping.

---

## ADR-003: Labels-primary, score-secondary

**Date**: Project inception
**Status**: Accepted

**Context**: A composite 0–100 score is the natural interface for ranked output. But the underlying signal weights are reasoned defaults, not empirically tuned.

**Decision**: Treat pattern labels (Steady Climber, Breakout Candidate, etc.) as the primary output. The numeric score is a tiebreaker within label tiers, not a precise ranking signal.

**Reasoning**: Producing precise rankings on un-tuned weights implies a confidence the underlying model does not have. Labels are interpretable, defensible, and degrade gracefully when individual signals are noisy. Scores can be added confidently after backtest validation.

**Revisit when**: Backtest data is available to empirically tune weights.

---

## ADR-004: 14-day post-age cutoff for engagement signals

**Date**: Project inception
**Status**: Accepted

**Context**: TikTok view counts continue accumulating after publish, distorting engagement-rate signals computed on recent posts.

**Decision**: Engagement-rate-based signals are computed only on posts at least 14 days old.

**Reasoning**: At 14 days, ~90% of final views are typically accumulated. Earlier cutoffs (7 days at ~75%) systematically underestimate engagement on viral posts (which continue accumulating views longer than non-viral posts), which would bias scoring against the very breakouts the system is designed to catch.

**Tradeoffs accepted**: 14-day lag in scoring recency for any given creator. Acceptable for a trend-detection system.

---

## ADR-005: Append-only time-series tables

**Date**: Schema design
**Status**: Accepted

**Context**: Snapshots, post metrics, categorizations, corrections, and scores could be modeled as either updatable rows or append-only history.

**Decision**: All time-series and audit-relevant tables are append-only. Latest values are surfaced through views.

**Reasoning**:
- Storage cost is trivial at this scale.
- Full historical replay is invaluable for debugging and for interview demonstrations ("here's how this creator's score evolved").
- Append-only correction history is the asset that powers phase 2 few-shot prompting.

---

## ADR-006: Tiered tracking cadence

**Date**: Project inception
**Status**: Accepted

**Context**: Uniform tracking cadence across all creators wastes budget on low-priority creators or under-samples high-priority ones.

**Decision**:
- Watchlist (~150–200): weekly
- Active set (~250–300): biweekly
- Long tail: monthly
- Graduated (>50K): weekly follower-only
- Dormant (60+ days inactive): paused

**Reasoning**: Concentrates observation budget on creators where additional datapoints have the highest marginal value. The watchlist serves dual purpose as both "best creators" and "highest-value-to-track" set, since trend-confirmation candidates get promoted there.

---

## ADR-007: Human-in-the-loop categorization with phased rollout

**Date**: Project inception
**Status**: Accepted

**Context**: LLM categorization at run 1 has no examples to learn from. A pure-automation approach would either over-rely on a generic prompt or attempt fine-tuning that's overkill at this scale.

**Decision**:
- **Phase 1**: LLM categorizes with a static prompt. Operator corrections are stored append-only. Corrections do not yet feed back into the LLM.
- **Phase 2**: Once ~30+ corrections exist, accumulated corrections feed into the categorization prompt as few-shot examples.
- **Phase 3 (deferred)**: Fine-tuning, only if the few-shot approach proves insufficient.

**Reasoning**: Treats domain expertise as a compounding asset. Avoids premature fine-tuning. The same prompt-construction code handles all phases — only the example-selection logic changes.

---

## ADR-008: Single-operator design at v1

**Date**: Project inception
**Status**: Accepted

**Context**: The eventual customer is a talent agency (multi-user). The current developer is a single operator.

**Decision**: Build for single-operator use at v1. No users table, no permission model, no notes/outreach/team features.

**Reasoning**: Premature multi-tenancy is one of the most common ways personal projects collapse under their own complexity. The system is more useful single-user-and-shipped than multi-user-and-half-built.

**Revisit when**: The system has demonstrated value to a target agency and there is a concrete path to multi-user adoption.
