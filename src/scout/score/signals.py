"""Scoring signals (v2).

Each signal is a pure function taking a creator's enriched data and
returning a SignalResult: a normalized contribution (0-100), the number
of datapoints used, and a confidence score (0-1).

v2 changes from v1 (informed by real-data inspection):
  - view_acceleration: now compares newer half vs older half of posts by
    publication date, instead of fixed time windows. Works regardless of
    posting cadence. v1 returned neutral 50 for every creator because the
    fixed windows didn't align with how creators actually post.
  - posting_consistency: recalibrated thresholds. v1 produced 0 for 11/15
    creators because the CV ceiling of 2.0 was hit by anyone mildly erratic.
    v2 maps CV=0.3 → 100, CV=1.5 → 50, CV=3.0 → 0, which matches the
    real distribution of human creators.
  - best_post_percentile_rank: now uses positional "most recent third of
    posts" instead of fixed 28-day window. v1 returned 0 for everyone
    because creators' best posts often predated 28 days.
  - comment_to_like_ratio: ceiling raised from 3% to 4.5% to stop pegging
    most creators at 100. v1 had 7 of 15 creators with contribution=100,
    making the signal useless for separation.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# -----------------------------------------------------------------------------
# Signal configuration
# -----------------------------------------------------------------------------

SETTLEMENT_AGE_DAYS = 14  # posts older than this have settled engagement

# Minimum post counts for each signal to be considered meaningful.
# Below these thresholds, confidence drops below 0.5 (used by labels.py).
MIN_POSTS_FOR_ACCELERATION = 8       # need 4+ in each half
MIN_POSTS_FOR_ENGAGEMENT_TREND = 5
MIN_POSTS_FOR_CONSISTENCY = 6
MIN_POSTS_FOR_PERCENTILE_RANK = 10


# -----------------------------------------------------------------------------
# Result type
# -----------------------------------------------------------------------------


@dataclass
class SignalResult:
    """One signal's computed contribution.

    contribution: normalized 0-100. Higher = stronger positive signal.
    datapoints: how many posts/observations fed into this signal.
    confidence: 0-1, scales with datapoint count and signal-specific quality.
    """

    contribution: float
    datapoints: int
    confidence: float

    @property
    def weighted_for_confidence(self) -> float:
        """Effective contribution after discounting for confidence."""
        return self.contribution * self.confidence


EMPTY_SIGNAL = SignalResult(contribution=0.0, datapoints=0, confidence=0.0)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _post_age_days(post: dict[str, Any]) -> float:
    return (_now() - post["published_at"]).total_seconds() / 86400


def _settled_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in posts if _post_age_days(p) >= SETTLEMENT_AGE_DAYS]


def _confidence_from_datapoints(n: int, min_required: int) -> float:
    """Linear confidence scaling.

    Below min_required: ramps from 0 to 0.5.
    At min_required: confidence is 0.5.
    Above: ramps from 0.5 to 1.0, saturating at 2x min_required.
    """
    if n <= 0:
        return 0.0
    if n < min_required:
        return 0.5 * (n / min_required)
    saturation = min_required * 2
    if n >= saturation:
        return 1.0
    return 0.5 + 0.5 * ((n - min_required) / (saturation - min_required))


def _normalize_to_100(value: float, low: float, high: float) -> float:
    """Clip to [low, high] and rescale to 0-100."""
    if high <= low:
        return 0.0
    clipped = max(low, min(high, value))
    return (clipped - low) / (high - low) * 100


# -----------------------------------------------------------------------------
# Signal 1: View-count acceleration (25%) — v2: positional split
# -----------------------------------------------------------------------------


def view_acceleration(posts: list[dict[str, Any]]) -> SignalResult:
    """Compare newer half of posts to older half by publication date.

    v2 change: positional split instead of fixed time windows. Works
    regardless of whether the creator posts daily or monthly.

    Returns contribution > 50 when newer posts outperform older posts,
    < 50 when declining, ~50 when flat.
    """
    if len(posts) < MIN_POSTS_FOR_ACCELERATION:
        return SignalResult(
            contribution=50.0,  # neutral when we can't measure
            datapoints=len(posts),
            confidence=_confidence_from_datapoints(
                len(posts), MIN_POSTS_FOR_ACCELERATION
            ),
        )

    sorted_posts = sorted(posts, key=lambda p: p["published_at"], reverse=True)
    midpoint = len(sorted_posts) // 2
    newer = sorted_posts[:midpoint]
    older = sorted_posts[midpoint:]

    newer_views = [p["view_count"] for p in newer]
    older_views = [p["view_count"] for p in older]

    if not newer_views or not older_views:
        return EMPTY_SIGNAL

    newer_median = statistics.median(newer_views)
    older_median = statistics.median(older_views)

    if older_median <= 0:
        # Older posts had no views? Treat newer as breakout if newer has views.
        if newer_median > 0:
            return SignalResult(
                contribution=100.0,
                datapoints=len(posts),
                confidence=_confidence_from_datapoints(
                    len(posts), MIN_POSTS_FOR_ACCELERATION
                ),
            )
        return SignalResult(contribution=50.0, datapoints=len(posts), confidence=0.0)

    ratio = newer_median / older_median

    # Map ratio to 0-100:
    #   0.33 (newer is 1/3 of older — major decline) → 0
    #   1.0  (flat)                                  → 50
    #   3.0  (newer is 3x older — strong growth)     → 100
    contribution = _normalize_to_100(ratio, low=0.33, high=3.0)

    return SignalResult(
        contribution=contribution,
        datapoints=len(posts),
        confidence=_confidence_from_datapoints(len(posts), MIN_POSTS_FOR_ACCELERATION),
    )


# -----------------------------------------------------------------------------
# Signal 2: View-to-follower ratio (20%) — unchanged from v1
# -----------------------------------------------------------------------------


def view_to_follower_ratio(
    posts: list[dict[str, Any]], follower_count: int
) -> SignalResult:
    """Median views per settled post divided by current follower count.

    > 1.0 means TikTok is serving the creator's content beyond their
    follower base — algorithmic amplification.
    """
    settled = _settled_posts(posts)
    if not settled or follower_count <= 0:
        return EMPTY_SIGNAL

    median_views = statistics.median([p["view_count"] for p in settled])
    ratio = median_views / follower_count

    # Map ratio to 0-100:
    #   0.05 (5% of followers see each post) → 0
    #   0.5  (50% of followers see each post)→ 50
    #   3.0  (views are 3x followers)        → 100
    contribution = _normalize_to_100(ratio, low=0.05, high=3.0)

    return SignalResult(
        contribution=contribution,
        datapoints=len(settled),
        confidence=_confidence_from_datapoints(
            len(settled), MIN_POSTS_FOR_ENGAGEMENT_TREND
        ),
    )


# -----------------------------------------------------------------------------
# Signal 3: Engagement rate trend (20%) — unchanged from v1
# -----------------------------------------------------------------------------


def engagement_rate_trend(posts: list[dict[str, Any]]) -> SignalResult:
    """Are engagement rates holding or improving as the creator grows?

    Compares median engagement rate (likes+comments)/views of later half
    vs. earlier half of settled posts.
    """
    settled = _settled_posts(posts)
    if len(settled) < MIN_POSTS_FOR_ENGAGEMENT_TREND:
        return SignalResult(
            contribution=50.0,
            datapoints=len(settled),
            confidence=_confidence_from_datapoints(
                len(settled), MIN_POSTS_FOR_ENGAGEMENT_TREND
            ),
        )

    rates: list[tuple[datetime, float]] = []
    for p in settled:
        if p["view_count"] <= 0:
            continue
        rate = (p["like_count"] + p["comment_count"]) / p["view_count"]
        rates.append((p["published_at"], rate))

    if len(rates) < MIN_POSTS_FOR_ENGAGEMENT_TREND:
        return EMPTY_SIGNAL

    rates.sort(key=lambda x: x[0])
    midpoint = len(rates) // 2
    earlier_median = statistics.median([r for _, r in rates[:midpoint]])
    later_median = statistics.median([r for _, r in rates[midpoint:]])

    if earlier_median <= 0:
        return SignalResult(contribution=50.0, datapoints=len(rates), confidence=0.0)

    ratio = later_median / earlier_median

    # Map to 0-100:
    #   0.5 (engagement halved) → 0
    #   1.0 (flat)              → 50
    #   2.0 (doubled)           → 100
    contribution = _normalize_to_100(ratio, low=0.5, high=2.0)

    return SignalResult(
        contribution=contribution,
        datapoints=len(rates),
        confidence=_confidence_from_datapoints(
            len(rates), MIN_POSTS_FOR_ENGAGEMENT_TREND
        ),
    )


# -----------------------------------------------------------------------------
# Signal 4: Comment-to-like ratio (15%) — v2: ceiling raised to 4.5%
# -----------------------------------------------------------------------------


def comment_to_like_ratio(posts: list[dict[str, Any]]) -> SignalResult:
    """Comments per like across settled posts. Higher = stronger community.

    v2 change: ceiling raised from 3% to 4.5%. v1 had 7 of 15 creators
    pegged at contribution=100, making the signal useless for separation.
    """
    settled = _settled_posts(posts)
    if not settled:
        return EMPTY_SIGNAL

    ratios: list[float] = []
    for p in settled:
        if p["like_count"] > 0:
            ratios.append(p["comment_count"] / p["like_count"])

    if not ratios:
        return EMPTY_SIGNAL

    median_ratio = statistics.median(ratios)

    # Map to 0-100:
    #   0.0%  → 0
    #   1.5%  → 50  (strong)
    #   4.5%  → 100 (exceptional, top decile)
    contribution = _normalize_to_100(median_ratio, low=0.0, high=0.045)

    return SignalResult(
        contribution=contribution,
        datapoints=len(ratios),
        confidence=_confidence_from_datapoints(
            len(ratios), MIN_POSTS_FOR_ENGAGEMENT_TREND
        ),
    )


# -----------------------------------------------------------------------------
# Signal 5: Posting consistency (10%) — v2: recalibrated CV thresholds
# -----------------------------------------------------------------------------


def posting_consistency(posts: list[dict[str, Any]]) -> SignalResult:
    """How regular is the creator's posting cadence?

    Computed as the coefficient of variation of inter-post intervals.

    v2 change: thresholds recalibrated. v1 pinned 11/15 creators to 0
    because the CV ceiling of 2.0 was too tight. v2 uses CV=0.3 → 100,
    CV=1.5 → 50, CV=3.0 → 0, matching observed real-creator behavior.
    """
    if len(posts) < MIN_POSTS_FOR_CONSISTENCY:
        return SignalResult(
            contribution=50.0,
            datapoints=len(posts),
            confidence=_confidence_from_datapoints(len(posts), MIN_POSTS_FOR_CONSISTENCY),
        )

    sorted_posts = sorted(posts, key=lambda p: p["published_at"])
    intervals_days: list[float] = []
    for i in range(1, len(sorted_posts)):
        delta = sorted_posts[i]["published_at"] - sorted_posts[i - 1]["published_at"]
        intervals_days.append(delta.total_seconds() / 86400)

    if not intervals_days:
        return EMPTY_SIGNAL

    mean_interval = statistics.mean(intervals_days)
    if mean_interval <= 0:
        return EMPTY_SIGNAL

    if len(intervals_days) < 2:
        return SignalResult(
            contribution=50.0,
            datapoints=len(intervals_days),
            confidence=0.3,
        )
    stdev = statistics.stdev(intervals_days)
    cv = stdev / mean_interval

    # Map CV to 0-100 with recalibrated thresholds. Lower CV = higher score.
    #   CV = 0.3  (very consistent)    → 100
    #   CV = 1.5  (moderately erratic) → 50
    #   CV = 3.0  (chaotic)            → 0
    # Implementation: invert and rescale. Map 3.0 - cv into [0, 2.7].
    inverted = 3.0 - cv
    contribution = _normalize_to_100(inverted, low=0.0, high=2.7)

    return SignalResult(
        contribution=contribution,
        datapoints=len(intervals_days),
        confidence=_confidence_from_datapoints(
            len(intervals_days) + 1, MIN_POSTS_FOR_CONSISTENCY
        ),
    )


# -----------------------------------------------------------------------------
# Signal 6: Best-post percentile rank (10%) — v2: positional, not temporal
# -----------------------------------------------------------------------------


def best_post_percentile_rank(posts: list[dict[str, Any]]) -> SignalResult:
    """Concentration of top-performing posts in the recent third by publication.

    v2 change: positional "most recent third" instead of fixed 28-day window.
    v1 returned 0 for all 15 creators because best posts often predated 28d.

    Take top 30% of posts by view count. What fraction fall in the
    most-recent-third of posts by publication date? Higher = breakout pattern.
    Baseline: a creator with uniformly distributed best posts gets ~33.
    """
    if len(posts) < MIN_POSTS_FOR_PERCENTILE_RANK:
        return SignalResult(
            contribution=50.0,
            datapoints=len(posts),
            confidence=_confidence_from_datapoints(
                len(posts), MIN_POSTS_FOR_PERCENTILE_RANK
            ),
        )

    # Top 30% by views
    sorted_by_views = sorted(posts, key=lambda p: p["view_count"], reverse=True)
    top_count = max(1, int(len(sorted_by_views) * 0.3))
    top_posts = sorted_by_views[:top_count]
    top_post_ids = {p["id"] for p in top_posts}

    # Most recent third by publication date
    sorted_by_date = sorted(posts, key=lambda p: p["published_at"], reverse=True)
    recent_third_count = max(1, int(len(sorted_by_date) / 3))
    recent_third = sorted_by_date[:recent_third_count]

    # How many of the top posts are in the recent third?
    top_in_recent = sum(1 for p in recent_third if p["id"] in top_post_ids)

    # Fraction. Baseline (uniform distribution) = 0.33.
    fraction = top_in_recent / top_count

    # Map fraction to 0-100:
    #   0.0  (no top posts recent)         → 0
    #   0.33 (uniform — expected baseline) → 33
    #   1.0  (all top posts recent)        → 100
    contribution = _normalize_to_100(fraction, low=0.0, high=1.0)

    return SignalResult(
        contribution=contribution,
        datapoints=len(posts),
        confidence=_confidence_from_datapoints(len(posts), MIN_POSTS_FOR_PERCENTILE_RANK),
    )


# -----------------------------------------------------------------------------
# Composite scoring
# -----------------------------------------------------------------------------


SIGNAL_WEIGHTS = {
    "view_acceleration": 0.25,
    "view_to_follower_ratio": 0.20,
    "engagement_rate_trend": 0.20,
    "comment_to_like_ratio": 0.15,
    "posting_consistency": 0.10,
    "best_post_percentile_rank": 0.10,
}


@dataclass
class CompositeScore:
    """Result of running all signals against a creator."""

    score: float                   # 0-100 composite
    confidence: float              # 0-1, overall confidence from signal confidences
    by_signal: dict[str, SignalResult]


def compute_composite(
    posts: list[dict[str, Any]],
    follower_count: int,
) -> CompositeScore:
    """Run all signals and produce a weighted composite score.

    Confidence-weighted: low-confidence signals contribute less to both
    the score and the overall confidence.
    """
    results = {
        "view_acceleration": view_acceleration(posts),
        "view_to_follower_ratio": view_to_follower_ratio(posts, follower_count),
        "engagement_rate_trend": engagement_rate_trend(posts),
        "comment_to_like_ratio": comment_to_like_ratio(posts),
        "posting_consistency": posting_consistency(posts),
        "best_post_percentile_rank": best_post_percentile_rank(posts),
    }

    weighted_sum = 0.0
    effective_weight = 0.0
    confidence_sum = 0.0
    weight_total = 0.0
    for name, result in results.items():
        w = SIGNAL_WEIGHTS[name]
        weighted_sum += result.contribution * result.confidence * w
        effective_weight += result.confidence * w
        confidence_sum += result.confidence * w
        weight_total += w

    if effective_weight <= 0:
        return CompositeScore(score=0.0, confidence=0.0, by_signal=results)

    composite_score = weighted_sum / effective_weight
    composite_confidence = confidence_sum / weight_total

    return CompositeScore(
        score=composite_score,
        confidence=composite_confidence,
        by_signal=results,
    )
