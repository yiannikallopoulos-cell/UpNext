"""Scoring signals.

Each signal is a pure function taking a creator's enriched data
(profile snapshots + settled post metrics) and returning a normalized
contribution to the composite score.

Signals (with weights — reasoned defaults pending backtest):
- view_acceleration (25%): recent vs. prior view-count trajectory
- view_to_follower_ratio (20%): algorithmic amplification indicator
- engagement_rate_trend (20%): are quality and reach scaling together
- comment_to_like_ratio (15%): community vs. passive consumption
- posting_consistency (10%): inverse variance in post cadence
- best_post_percentile_rank (10%): concentration of top posts in recent window

Each function also returns the number of datapoints used, which feeds
into the overall confidence calculation.
"""
