"""Search-term discovery channel.

Surfaces creators via TikTok's content-recognition search, hitting transcribed
audio, on-screen text, and caption content. Better than hashtag search at
catching creators with sparse captions.

The harvester:
  1. Iterates through the configured search terms (per category)
  2. Calls the Apify search actor for each term
  3. Extracts unique creator handles from results
  4. Writes each new candidate to the `creators` table with lifecycle='new'
  5. Records a discovery_event row for every surfacing (including duplicates
     across terms — multi-channel surfacing is itself a useful signal)

Cost-conscious: this stage does NOT do a full profile scrape on each
candidate. It just records that the creator exists and was surfaced.
A separate enrichment step does the deep scrape later, only on candidates
that pass initial quality filters.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from scout.db import get_connection
from scout.enrich.apify_client import (
    ActorRunFailed,
    SearchHit,
    TikTokApifyClient,
)
from scout.harvest.search_terms import all_search_terms

logger = structlog.get_logger(__name__)


@dataclass
class HarvestResult:
    """Summary of a search-term harvest run.

    Tracks volume at each stage so we can reason about channel yield over
    time and tune search terms based on what's actually working.
    """

    terms_processed: int
    terms_failed: int
    total_hits: int  # before deduplication across terms
    unique_candidates: int  # after deduplication
    new_creators_inserted: int  # candidates we'd never seen before
    discovery_events_logged: int  # always equals total_hits


def run_search_harvest(
    client: TikTokApifyClient | None = None,
    posts_per_search: int = 30,
    category_filter: str | None = None,
) -> HarvestResult:
    """Run the full search-term harvest across all configured categories.

    Args:
        client: Optional pre-initialized Apify client. Useful for testing.
        posts_per_search: How many posts to pull per search term. Lower = cheaper.
        category_filter: If provided, only run terms for this category.
            Useful for incremental testing. None means run all categories.

    Returns:
        HarvestResult summarizing the run.
    """
    apify = client or TikTokApifyClient()
    terms_by_category = all_search_terms()

    if category_filter is not None:
        if category_filter not in terms_by_category:
            raise ValueError(
                f"Unknown category: {category_filter}. "
                f"Valid: {list(terms_by_category)}"
            )
        terms_by_category = {category_filter: terms_by_category[category_filter]}

    log = logger.bind(channel="search_terms")
    log.info(
        "harvest_starting",
        categories=list(terms_by_category.keys()),
        total_terms=sum(len(t) for t in terms_by_category.values()),
    )

    # Track per-run state.
    terms_processed = 0
    terms_failed = 0
    total_hits = 0
    all_hits: list[tuple[str, str, SearchHit]] = []  # (category, query, hit)

    # 1. Pull search results for every term, every category.
    for category, terms in terms_by_category.items():
        for query in terms:
            term_log = log.bind(category=category, query=query)
            try:
                hits = apify.search_posts_by_keyword(
                    query=query, posts_limit=posts_per_search
                )
                terms_processed += 1
                total_hits += len(hits)
                term_log.info("term_complete", hits=len(hits))
                for hit in hits:
                    all_hits.append((category, query, hit))
            except ActorRunFailed as e:
                terms_failed += 1
                term_log.error("term_failed", error=str(e))
                # Don't bail — keep going with remaining terms.

    # 2. Deduplicate by tiktok_user_id, keeping all surfacing contexts so
    #    we can log multiple discovery events per creator.
    by_user_id: dict[str, list[tuple[str, str, SearchHit]]] = {}
    for category, query, hit in all_hits:
        by_user_id.setdefault(hit.tiktok_user_id, []).append((category, query, hit))

    log.info(
        "harvest_dedup",
        total_hits=total_hits,
        unique_candidates=len(by_user_id),
    )

    # 3. Persist each unique candidate + log all surfacing events.
    new_creators = 0
    events_logged = 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            for user_id, surfacings in by_user_id.items():
                # The first surfacing carries the creator data we'll insert with.
                first_category, first_query, first_hit = surfacings[0]

                # Try to insert the creator; if they exist, just get their id.
                creator_id, was_new = _ensure_creator(cur, first_hit, first_category)
                if was_new:
                    new_creators += 1

                # Log a discovery event for EVERY surfacing, not just the first.
                # If a creator was surfaced by 3 terms, we log 3 events.
                for category, query, _hit in surfacings:
                    _log_discovery_event(cur, creator_id, category, query)
                    events_logged += 1

    result = HarvestResult(
        terms_processed=terms_processed,
        terms_failed=terms_failed,
        total_hits=total_hits,
        unique_candidates=len(by_user_id),
        new_creators_inserted=new_creators,
        discovery_events_logged=events_logged,
    )
    log.info("harvest_complete", **result.__dict__)
    return result


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------


def _ensure_creator(
    cur,  # noqa: ANN001
    hit: SearchHit,
    category: str,
) -> tuple[int, bool]:
    """Insert a new creator from a search hit, or return existing id.

    The category here is provisional — it reflects which category's search
    surfaced this creator. The LLM categorizer may override this later, and
    the database is the source of truth via the categorizations table.
    For now we just stamp it into current_category as an initial guess.

    Returns (creator_id, was_new_insertion).
    """
    cur.execute(
        "SELECT id FROM creators WHERE tiktok_user_id = %s",
        (hit.tiktok_user_id,),
    )
    row = cur.fetchone()
    if row is not None:
        return row["id"], False

    cur.execute(
        """
        INSERT INTO creators (
            tiktok_user_id, handle, display_name, bio,
            lifecycle, first_discovery_channel, current_category
        )
        VALUES (%s, %s, %s, %s, 'new', 'search_terms', %s)
        RETURNING id
        """,
        (
            hit.tiktok_user_id,
            hit.handle,
            hit.display_name,
            hit.bio,
            category,
        ),
    )
    creator_id = cur.fetchone()["id"]
    return creator_id, True


def _log_discovery_event(
    cur,  # noqa: ANN001
    creator_id: int,
    category: str,
    query: str,
) -> None:
    """Record that a creator was surfaced by a specific search term.

    Stored in JSONB context so we can later analyze which queries are
    yielding which creators — useful for tuning the term list over time.
    """
    cur.execute(
        """
        INSERT INTO discovery_events (creator_id, channel, context)
        VALUES (%s, 'search_terms', %s::jsonb)
        """,
        (
            creator_id,
            f'{{"category": "{category}", "query": "{query}"}}',
        ),
    )
