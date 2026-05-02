"""Run the enrichment + filter stage on new candidates.

Pulls candidates with lifecycle='new' from the database, applies the
pre-scrape filter to drop obvious junk, samples a configurable number,
deep-scrapes each one via Apify, applies the post-scrape filter, and
updates lifecycle to 'active' (kept) or 'rejected' (with reason).

Cost note: this is the most expensive stage in the pipeline. Each candidate
deep-scraped costs ~$0.05-0.10 in Apify credits. With sample_size=150 the
expected cost is $7.50-$15.

Usage:
    # Stratified sample of 150 (recommended first run)
    python scripts/run_enrichment.py

    # Custom sample size
    python scripts/run_enrichment.py --sample-size 75

    # Single category for testing
    python scripts/run_enrichment.py --category grwm --sample-size 30

    # Dry run — show what would be scraped without spending credits
    python scripts/run_enrichment.py --dry-run

Verification:
    After running, check Supabase:
      - creators table: candidates updated to lifecycle='active' or 'rejected'
      - rejection_reason populated on rejected rows
      - creator_snapshots: new rows for accepted creators
      - posts and post_metrics: rows for accepted creators
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).parent.parent / "src"))

import argparse
import logging
import random
import sys
from dataclasses import dataclass

import structlog

from scout.db import get_connection
from scout.enrich.apify_client import (
    ActorRunFailed,
    CreatorNotFound,
    TikTokApifyClient,
    UnexpectedResponseShape,
)
from scout.enrich.persist import save_profile
from scout.filter.rules import post_scrape_filter, pre_scrape_filter

logger = structlog.get_logger(__name__)


# -----------------------------------------------------------------------------
# Counters for per-run reporting
# -----------------------------------------------------------------------------


@dataclass
class EnrichmentRunStats:
    candidates_loaded: int = 0
    pre_filtered_out: int = 0  # rejected before deep scrape
    sampled: int = 0
    scrape_failed: int = 0
    post_filtered_out: int = 0  # rejected after deep scrape
    accepted: int = 0
    rejection_reasons: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.rejection_reasons is None:
            self.rejection_reasons = {}


# -----------------------------------------------------------------------------
# Database helpers
# -----------------------------------------------------------------------------


def load_new_candidates(
    category: str | None = None,
) -> list[dict]:
    """Load all creators with lifecycle='new' that haven't been enriched yet.

    Optionally filter to a single category.
    """
    sql = """
        SELECT id, tiktok_user_id, handle, display_name, bio, current_category
          FROM creators
         WHERE lifecycle = 'new'
    """
    params: list = []
    if category is not None:
        sql += " AND current_category = %s"
        params.append(category)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return list(cur.fetchall())


def mark_rejected(creator_id: int, reason: str) -> None:
    """Move a creator to lifecycle='rejected' with a reason."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE creators
                   SET lifecycle = 'rejected',
                       rejected_at = NOW(),
                       rejection_reason = %s,
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (reason, creator_id),
            )


def mark_active(creator_id: int) -> None:
    """Move a creator to lifecycle='active' (passed all filters, in tracking)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE creators
                   SET lifecycle = 'active',
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (creator_id,),
            )


# -----------------------------------------------------------------------------
# Sampling
# -----------------------------------------------------------------------------


def stratified_sample(
    candidates: list[dict],
    sample_size: int,
    seed: int = 42,
) -> list[dict]:
    """Sample candidates evenly across categories.

    Splits the requested sample size between sports_commentary and grwm so
    one category dominating discovery doesn't starve the other from being
    tested. If a category has fewer candidates than its share, takes all
    of them and gives the leftover slots to the other category.
    """
    rng = random.Random(seed)
    by_category: dict[str, list[dict]] = {}
    for cand in candidates:
        cat = cand.get("current_category") or "uncategorized"
        by_category.setdefault(cat, []).append(cand)

    # Target half-per-category split.
    target_per_category = sample_size // len(by_category)
    sampled: list[dict] = []
    leftover: list[dict] = []

    for cat, group in by_category.items():
        rng.shuffle(group)
        take = min(target_per_category, len(group))
        sampled.extend(group[:take])
        leftover.extend(group[take:])

    # If we're short of the target (because some category had fewer
    # candidates than its share), top up from leftover.
    deficit = sample_size - len(sampled)
    if deficit > 0 and leftover:
        rng.shuffle(leftover)
        sampled.extend(leftover[:deficit])

    rng.shuffle(sampled)  # randomize processing order
    return sampled


# -----------------------------------------------------------------------------
# The orchestration
# -----------------------------------------------------------------------------


def run_enrichment(
    sample_size: int,
    category: str | None,
    dry_run: bool,
) -> EnrichmentRunStats:
    """Main pipeline: load candidates, pre-filter, sample, scrape, post-filter."""
    stats = EnrichmentRunStats()
    log = logger.bind(stage="enrichment")

    # 1. Load candidates from database.
    candidates = load_new_candidates(category=category)
    stats.candidates_loaded = len(candidates)
    log.info("candidates_loaded", count=len(candidates), category=category)

    if not candidates:
        log.warning("no_candidates_to_enrich")
        return stats

    # 2. Pre-scrape filter — drop obvious junk before sampling.
    survivors_after_prefilter: list[dict] = []
    for cand in candidates:
        result = pre_scrape_filter(
            handle=cand["handle"],
            display_name=cand.get("display_name"),
            bio=cand.get("bio"),
        )
        if result.passed:
            survivors_after_prefilter.append(cand)
        else:
            stats.pre_filtered_out += 1
            stats.rejection_reasons[result.reason] = (
                stats.rejection_reasons.get(result.reason, 0) + 1
            )
            if not dry_run:
                mark_rejected(cand["id"], result.reason)

    log.info(
        "pre_filter_complete",
        survivors=len(survivors_after_prefilter),
        rejected=stats.pre_filtered_out,
    )

    # 3. Sample down to target size.
    sampled = stratified_sample(survivors_after_prefilter, sample_size)
    stats.sampled = len(sampled)
    log.info("sampled", count=len(sampled), target=sample_size)

    if dry_run:
        log.info("dry_run_complete", would_scrape=len(sampled))
        return stats

    # 4. Deep scrape + post-filter each sampled creator.
    apify = TikTokApifyClient()

    for i, cand in enumerate(sampled, start=1):
        cand_log = log.bind(handle=cand["handle"], creator_id=cand["id"])
        cand_log.info("enriching", progress=f"{i}/{len(sampled)}")

        try:
            profile = apify.fetch_creator_profile(cand["handle"], posts_limit=30)
        except CreatorNotFound:
            cand_log.warning("creator_not_found_during_enrichment")
            mark_rejected(cand["id"], "creator_not_found_at_enrichment")
            stats.scrape_failed += 1
            stats.rejection_reasons["creator_not_found_at_enrichment"] = (
                stats.rejection_reasons.get("creator_not_found_at_enrichment", 0) + 1
            )
            continue
        except (ActorRunFailed, UnexpectedResponseShape) as e:
            cand_log.error("scrape_failed", error=str(e))
            # Don't mark as rejected — leave as 'new' so we can retry later.
            stats.scrape_failed += 1
            continue

        # Apply post-scrape filter.
        result = post_scrape_filter(profile)
        if not result.passed:
            cand_log.info("post_filter_rejected", reason=result.reason)
            mark_rejected(cand["id"], result.reason)
            stats.post_filtered_out += 1
            stats.rejection_reasons[result.reason] = (
                stats.rejection_reasons.get(result.reason, 0) + 1
            )
            continue

        # Accepted — persist full profile data + mark active.
        try:
            save_profile(profile, discovery_channel="search_terms")
            mark_active(cand["id"])
            stats.accepted += 1
            cand_log.info(
                "accepted",
                followers=profile.follower_count,
                posts=len(profile.posts),
            )
        except Exception as e:
            cand_log.error("persist_failed", error=str(e))
            stats.scrape_failed += 1

    return stats


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run enrichment + filter on new candidates.")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=150,
        help="How many candidates to deep-scrape this run. Default: 150.",
    )
    parser.add_argument(
        "--category",
        choices=["sports_commentary", "grwm"],
        default=None,
        help="Restrict to a single category.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pre-filter and sampling but skip the deep scrape (no Apify cost).",
    )
    args = parser.parse_args()

    setup_logging()

    print("=" * 70)
    print("ENRICHMENT + FILTER")
    print("=" * 70)
    print(f"  Sample size:      {args.sample_size}")
    print(f"  Category filter:  {args.category or 'all'}")
    print(f"  Dry run:          {args.dry_run}")
    print()
    if not args.dry_run:
        est_cost_low = args.sample_size * 0.05
        est_cost_high = args.sample_size * 0.10
        print(
            f"  Estimated Apify cost: ${est_cost_low:.2f} - ${est_cost_high:.2f}"
        )
        print()
        print("  This will take 30-90 minutes depending on Apify load.")
        print("  Each creator scrape takes 30-90 seconds.")
        print()

    try:
        stats = run_enrichment(
            sample_size=args.sample_size,
            category=args.category,
            dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"\nERROR during enrichment: {e}")
        return 1

    print()
    print("=" * 70)
    print("ENRICHMENT RESULT")
    print("=" * 70)
    print(f"  Candidates loaded:        {stats.candidates_loaded}")
    print(f"  Pre-filter rejected:      {stats.pre_filtered_out}")
    print(f"  Sampled for scrape:       {stats.sampled}")
    print(f"  Scrape failed:            {stats.scrape_failed}")
    print(f"  Post-filter rejected:     {stats.post_filtered_out}")
    print(f"  Accepted (lifecycle=active): {stats.accepted}")
    print()
    print("Rejection reasons:")
    if stats.rejection_reasons:
        for reason, count in sorted(
            stats.rejection_reasons.items(), key=lambda x: -x[1]
        ):
            print(f"    {count:>4}  {reason}")
    else:
        print("    (none — nothing was rejected)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
