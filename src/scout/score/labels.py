"""Pattern labels (v3).

Rules over computed signals that assign interpretable archetype labels.
A creator can carry multiple labels.

v3 changes from v2:
  - Added stable_performer label for creators with healthy engagement
    trend and posting consistency who don't qualify for any other label.
    Catches the "holding their own" middle tier that previously came back
    label-less, leaving the dashboard with nothing meaningful to say.

v2 changes from v1:
  - engagement_magnet threshold raised from 70 to 80.
  - steady_climber loosened to 3+ signals at >= 60 (was 4+).
  - plateau_risk requires BOTH low acceleration AND low engagement trend.
  - Three signals (view_acceleration, posting_consistency,
    best_post_percentile_rank) had bug fixes in signals.py v2.

Labels are intentionally conservative: they require both a strong underlying
signal AND sufficient confidence (enough datapoints).
"""

from __future__ import annotations

from scout.score.signals import CompositeScore, SignalResult

# -----------------------------------------------------------------------------
# Label thresholds
# -----------------------------------------------------------------------------

SIGNAL_CONFIDENCE_FLOOR = 0.6
COMPOSITE_CONFIDENCE_FLOOR = 0.5


def _signal_qualifies(result: SignalResult, min_contribution: float) -> bool:
    """A signal counts toward a label only if it's strong AND confident."""
    return (
        result.confidence >= SIGNAL_CONFIDENCE_FLOOR
        and result.contribution >= min_contribution
    )


# -----------------------------------------------------------------------------
# Individual label rules
# -----------------------------------------------------------------------------


def is_steady_climber(composite: CompositeScore) -> bool:
    """Positive contribution across most signals, no major weakness.

    v2 thresholds:
    - 3+ signals showing contribution >= 60 with confidence >= floor
    - No confident signal contributing < 25 (allows one mediocre signal)
    - Composite confidence >= floor
    """
    if composite.confidence < COMPOSITE_CONFIDENCE_FLOOR:
        return False

    confident_signals = [
        r for r in composite.by_signal.values()
        if r.confidence >= SIGNAL_CONFIDENCE_FLOOR
    ]
    strong = [r for r in confident_signals if r.contribution >= 60]
    if len(strong) < 3:
        return False

    if any(r.contribution < 25 for r in confident_signals):
        return False

    return True


def is_breakout_candidate(composite: CompositeScore) -> bool:
    """Top view-acceleration AND high view-to-follower ratio.

    The "next up" archetype — algorithm is starting to push them harder
    than their follower count alone would predict, AND that push is
    accelerating.

    Requires:
    - view_acceleration >= 70 with confidence >= floor
    - view_to_follower_ratio >= 55 with confidence >= floor
    - Composite confidence >= floor
    """
    if composite.confidence < COMPOSITE_CONFIDENCE_FLOOR:
        return False

    accel = composite.by_signal["view_acceleration"]
    vfr = composite.by_signal["view_to_follower_ratio"]

    return _signal_qualifies(accel, 70) and _signal_qualifies(vfr, 55)


def is_algorithm_darling(composite: CompositeScore) -> bool:
    """High amplification but follower conversion is lagging.

    TikTok is pushing the creator but they're not converting reach into
    followers efficiently. Often a content/CTA optimization opportunity.

    Requires:
    - view_to_follower_ratio >= 70
    - view_acceleration < 55 (followers not yet responding)
    - Both confident
    """
    if composite.confidence < COMPOSITE_CONFIDENCE_FLOOR:
        return False

    vfr = composite.by_signal["view_to_follower_ratio"]
    accel = composite.by_signal["view_acceleration"]

    if not _signal_qualifies(vfr, 70):
        return False
    if accel.confidence < SIGNAL_CONFIDENCE_FLOOR:
        return False
    return accel.contribution < 55


def is_engagement_magnet(composite: CompositeScore) -> bool:
    """Top-decile comment-to-like ratio.

    Community-driven creator. Lower viral ceiling, higher floor, brand-friendly.

    v2: threshold raised to 80 (corresponds to ~3.6% C/L ratio).
    v1 used 70, which fired for nearly everyone because the ceiling was 3%.
    """
    if composite.confidence < COMPOSITE_CONFIDENCE_FLOOR:
        return False

    ctl = composite.by_signal["comment_to_like_ratio"]
    return _signal_qualifies(ctl, 80)


def is_plateau_risk(composite: CompositeScore) -> bool:
    """Was growing, now flat or declining.

    Negative label — surfaces creators who looked promising but stalled.

    v2: requires BOTH low acceleration AND low engagement trend, not just
    acceleration alone. Reduces false positives.

    Requires:
    - view_acceleration <= 35 with confidence >= floor
    - engagement_rate_trend <= 40 with confidence >= floor
    - Posting consistency confidence >= 0.4 (don't flag brand-new accounts)
    """
    accel = composite.by_signal["view_acceleration"]
    trend = composite.by_signal["engagement_rate_trend"]
    consistency = composite.by_signal["posting_consistency"]

    if accel.confidence < SIGNAL_CONFIDENCE_FLOOR:
        return False
    if accel.contribution > 35:
        return False
    if trend.confidence < SIGNAL_CONFIDENCE_FLOOR:
        return False
    if trend.contribution > 40:
        return False
    if consistency.confidence < 0.4:
        return False

    return True


def is_stable_performer(composite: CompositeScore) -> bool:
    """Holding steady — not breaking out, not plateauing.

    The middle tier: creators whose engagement quality is healthy and
    whose posting cadence is consistent, even if they're not currently
    breaking out. These are watch-list candidates: nothing wrong, no
    clear upside signal yet either.

    Requires:
    - engagement_rate_trend >= 40 with confidence >= floor
    - posting_consistency >= 50 with confidence >= floor
    - NOT a plateau risk (mutually exclusive — if they're plateauing,
      they're not stable, they're declining)
    - Composite confidence >= floor
    """
    if composite.confidence < COMPOSITE_CONFIDENCE_FLOOR:
        return False

    trend = composite.by_signal["engagement_rate_trend"]
    consistency = composite.by_signal["posting_consistency"]

    if not _signal_qualifies(trend, 40):
        return False
    if not _signal_qualifies(consistency, 50):
        return False

    # Mutually exclusive with plateau_risk: a declining creator isn't stable.
    if is_plateau_risk(composite):
        return False

    return True


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


LABEL_RULES = [
    ("steady_climber", is_steady_climber),
    ("breakout_candidate", is_breakout_candidate),
    ("algorithm_darling", is_algorithm_darling),
    ("engagement_magnet", is_engagement_magnet),
    ("stable_performer", is_stable_performer),
    ("plateau_risk", is_plateau_risk),
]


def compute_labels(composite: CompositeScore) -> list[str]:
    """Return all labels that apply to a creator. May be empty."""
    return [name for name, rule in LABEL_RULES if rule(composite)]
