# Scoring

This document defines the signals, weights, and pattern labels used by UpNext's scoring engine. All weights are reasoned defaults pending empirical validation via backtest.

## Signals

### View-count acceleration (25%)

The single most predictive signal. Compares the rolling-average view count of a creator's recent posts (last ~4 weeks) against their prior baseline (preceding 4–8 weeks). Acceleration captures *change in trajectory*, not absolute level — a creator going from 5K average views to 25K average views is more interesting than a creator steadily averaging 50K.

Computed from `post_metrics` rows on posts older than 14 days (settled engagement window).

### View-to-follower ratio (20%)

Current per-post view count divided by current follower count, averaged over recent settled posts. A ratio significantly above 1.0 indicates TikTok is serving the creator's content beyond their follower base — the algorithmic amplification signal that precedes follower-count growth.

A creator with 8K followers averaging 200K views per post is being amplified. This signal fires earlier than follower-acceleration.

### Engagement-rate trend (20%)

Engagement rate computed as `(likes + comments) / views` per post, then trended over time. Most creators see engagement dilute as they grow. Creators who *maintain or grow* engagement rate as their reach expands are unusually sticky, which is the property agencies and brands actually pay for.

Computed only on posts older than 14 days. Flat or rising trend = positive contribution; sharply declining trend = negative contribution.

### Comment-to-like ratio (15%)

Mean comment-to-like ratio across recent settled posts. Likes are cheap. Comments mean a viewer stopped scrolling, formed an opinion, and typed. This separates creators inspiring genuine community from those with passively-consumed content.

Industry benchmarks: above 1% is strong, above 2% is exceptional. Especially predictive in GRWM, where parasocial connection drives growth.

### Posting consistency (10%)

Inverse of variance in days-between-posts over the last 90 days. Lower weight because consistency is more of a hygiene signal than a growth signal — but creators failing this badly (irregular long gaps) are filtered before scoring rather than penalized within scoring.

### Best-post percentile rank (10%)

Fraction of a creator's top-decile-performing posts (by views) that fell in the most recent ~4 weeks. Surging creators concentrate their best posts in recent weeks; plateaued creators have their best posts scattered across history. This signal is only computable from full post archives and is one of the system's distinctive scoring features.

## Composite score

A weighted sum of normalized signals produces a 0–100 composite score. **The numeric score is a tiebreaker within pattern label tiers, not a primary ranking signal.** Treat the labels as the output that matters.

## Pattern labels

A creator can carry multiple labels. Labels are computed from rules over the underlying signals.

### Steady Climber

Positive contribution across most signals, with low variance. View-count acceleration positive but moderate, engagement holding, posting consistent. The safest profile — predictable growth, low risk.

### Breakout Candidate

View-count acceleration in the top decile of the dataset, view-to-follower ratio spiking, often paired with high best-post percentile rank. The highest-upside, highest-variance profile. This is the "next up" archetype the system is named for.

### Algorithm Darling

View-to-follower ratio extremely high but follower-count trajectory lagging. Indicates TikTok is pushing the creator but they're not converting reach into followers efficiently — usually a content/CTA optimization opportunity. Often a coaching candidate rather than a sign-and-go.

### Engagement Magnet

Comment-to-like ratio in the top decile, even if growth is moderate. These creators have communities, not audiences. Lower viral ceiling, higher floor, brand-friendly.

### Plateau Risk

Was growing, now flat for 4+ weeks. Surfaces creators sliding from previously interesting trajectories. Useful as a negative filter — these are creators to *not* prioritize even if their absolute scores are high.

## Confidence

Each score carries a confidence value (0–1) reflecting the number of datapoints available. Creators with fewer than ~6 settled posts and fewer than ~4 weeks of follower-count history receive lower confidence. This protects against false signals from sparse data and makes "subtle but reliable" trends distinguishable from "subtle and possibly noise."

The dashboard separates high-confidence picks from low-confidence ones rather than blending them.
