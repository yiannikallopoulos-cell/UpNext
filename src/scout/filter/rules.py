"""Filter rules.

Pure logic that decides whether a candidate creator should be kept or
rejected based on objective criteria. No I/O — these functions take data
and return decisions. Easy to test in isolation.

Two layers of filtering:

1. Pre-scrape filter: applied to the minimal data we have from the search
   harvester (handle, display name, bio). Drops obvious spam/bot patterns
   before we spend Apify credits on a deep scrape.

2. Post-scrape filter: applied to the full TikTokProfile after enrichment.
   This is where the band check, activity check, and post-count check live.

Filter functions follow a consistent contract: they return (passed, reason).
The reason is None when passed=True and a short human-readable string
explaining the rejection when passed=False.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from scout.config import get_settings
from scout.enrich.apify_client import TikTokProfile


class FilterResult(NamedTuple):
    """Outcome of running filter rules against a candidate.

    A creator passes only if every rule passes. The first failing rule
    determines the rejection reason — we don't aggregate multiple failures
    because one is enough to reject.
    """

    passed: bool
    reason: str | None  # None when passed=True


# -----------------------------------------------------------------------------
# Pre-scrape filter
# -----------------------------------------------------------------------------
# Applied to the lightweight data we have from search results. Goal: drop
# obvious junk without spending the deep-scrape budget on it.

# Patterns that suggest auto-generated or junk accounts. These aren't
# perfect — some real creators have weird handles — but the false positive
# rate is low and the savings on Apify spend are meaningful.
_PURE_NUMERIC_HANDLE_RE = re.compile(r"^\d{6,}$")
_USER_PREFIX_NUMERIC_HANDLE_RE = re.compile(r"^user\d{4,}$", re.IGNORECASE)

# Bio strings that strongly correlate with spam/sales accounts rather than
# content creators. Match is substring, case-insensitive.
_SPAM_BIO_MARKERS = (
    "dm for promo",
    "100% real followers",
    "buy followers",
    "follower service",
    "trade for trade",
    "f4f only",
    "telegram:",
    "whatsapp:",
)


def pre_scrape_filter(
    handle: str,
    display_name: str | None,
    bio: str | None,
) -> FilterResult:
    """Reject obvious junk before spending an Apify credit on enrichment.

    Conservative on purpose: false positives here mean missing a real
    creator entirely, since we won't deep-scrape them. Better to let
    borderline cases through and reject post-scrape if needed.
    """
    if not handle or not handle.strip():
        return FilterResult(False, "empty_handle")

    if _PURE_NUMERIC_HANDLE_RE.match(handle):
        return FilterResult(False, "numeric_handle_likely_bot")

    if _USER_PREFIX_NUMERIC_HANDLE_RE.match(handle):
        return FilterResult(False, "user_prefix_handle_likely_default")

    if bio:
        bio_lower = bio.lower()
        for marker in _SPAM_BIO_MARKERS:
            if marker in bio_lower:
                return FilterResult(False, f"spam_bio_marker:{marker}")

    return FilterResult(True, None)


# -----------------------------------------------------------------------------
# Post-scrape filter
# -----------------------------------------------------------------------------
# Applied to the full TikTokProfile after enrichment. This is where the
# real money rules live — band check, activity, posting history.

# Activity threshold: creators inactive longer than this are considered
# dormant for the purpose of initial filtering. They can be re-discovered
# later via opportunistic harvest if they come back.
DORMANT_DAYS_THRESHOLD = 30

# Minimum total posts. Brand-new accounts don't have enough history for
# scoring to mean anything, and they pollute the dataset with noise.
MIN_TOTAL_POSTS = 10

# Bot heuristic: ratio of following / followers. Real creators tend to follow
# far fewer accounts than follow them. When following >> followers it usually
# indicates an auto-follower account or a fresh bot. The threshold is loose
# because some new creators legitimately follow more than they have followers.
BOT_FOLLOWING_RATIO_THRESHOLD = 5.0


def post_scrape_filter(profile: TikTokProfile) -> FilterResult:
    """Decide whether a fully enriched profile passes our criteria.

    Runs the rules in order of selectivity — the most common rejection
    reasons (band, inactivity) come first so the typical case short-circuits
    fast. Order doesn't affect correctness, just clarity in logs.
    """
    settings = get_settings()

    # Band check — the primary filter. The whole project's thesis is creators
    # in a specific size range, so this is non-negotiable.
    if profile.follower_count < settings.follower_band_min:
        return FilterResult(
            False, f"below_band:{profile.follower_count}"
        )
    if profile.follower_count > settings.follower_band_max:
        return FilterResult(
            False, f"above_band:{profile.follower_count}"
        )

    # Total post count check — need enough history for trend analysis.
    if profile.total_posts < MIN_TOTAL_POSTS:
        return FilterResult(
            False, f"insufficient_posts:{profile.total_posts}"
        )

    # Activity check — has the creator posted recently? Computed from the
    # most recent post's timestamp, not from total_posts.
    if profile.posts:
        most_recent = max(p.published_at for p in profile.posts)
        days_since = (datetime.now(timezone.utc) - most_recent).days
        if days_since > DORMANT_DAYS_THRESHOLD:
            return FilterResult(False, f"dormant:{days_since}_days")
    else:
        # Profile claims posts exist (total_posts > 0) but none returned.
        # Defensive — treat as dormant rather than crashing.
        return FilterResult(False, "no_recent_posts_returned")

    # Bot heuristic.
    if profile.follower_count > 0:
        ratio = profile.following_count / profile.follower_count
        if ratio > BOT_FOLLOWING_RATIO_THRESHOLD:
            return FilterResult(
                False, f"high_following_ratio:{ratio:.1f}"
            )

    return FilterResult(True, None)
