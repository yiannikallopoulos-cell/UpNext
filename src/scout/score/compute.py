"""Scoring orchestrator.

For each creator: loads their enriched data from the database, runs the
signals, computes the weighted composite score, applies label rules,
and writes a row to the scores table.

The scores table is append-only — every run produces fresh rows. This
preserves history of how scores evolved as the dataset matured and as
the algorithm itself improved (algo_version field).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog

from scout.db import get_connection
from scout.score.labels import compute_labels
from scout.score.signals import (
    SIGNAL_WEIGHTS,
    CompositeScore,
    compute_composite,
)

logger = structlog.get_logger(__name__)

# Version stamp written to every score row. Bumped when signal logic changes
# meaningfully so we can distinguish scores across algorithm generations.
#
# v1: Initial implementation. Three signals broken (view_acceleration always 50,
#     posting_consistency frequently 0, best_post_percentile_rank always 0).
# v2: Fixed positional splits for view_acceleration and best_post_percentile_rank.
#     Recalibrated posting_consistency CV thresholds. Raised comment_to_like
#     ceiling. Adjusted label thresholds accordingly.
# v3: Added stable_performer label to catch the middle tier (healthy engagement
#     trend + consistent posting, but not breaking out). No signal changes.
ALGO_VERSION = "v3"


# -----------------------------------------------------------------------------
# Result type
# -----------------------------------------------------------------------------


@dataclass
class ScoringResult:
    creator_id: int
    handle: str
    score: float
    confidence: float
    labels: list[str]
    composite: CompositeScore


# -----------------------------------------------------------------------------
# Data loading
# -----------------------------------------------------------------------------


def _load_creators_for_scoring(
    lifecycle_filter: list[str] | None = None,
    creator_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Pull creators to score plus their associated post data."""
    where_clauses: list[str] = []
    params: list = []

    if lifecycle_filter:
        placeholders = ",".join(["%s"] * len(lifecycle_filter))
        where_clauses.append(f"lifecycle IN ({placeholders})")
        params.extend(lifecycle_filter)
    if creator_ids:
        placeholders = ",".join(["%s"] * len(creator_ids))
        where_clauses.append(f"id IN ({placeholders})")
        params.extend(creator_ids)

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"
    sql_creators = f"""
        SELECT c.id, c.handle, c.current_category, c.current_sub_archetype,
               c.lifecycle, s.follower_count
          FROM creators c
          LEFT JOIN v_latest_snapshot s ON s.creator_id = c.id
         WHERE {where_sql}
         ORDER BY c.id
    """

    out: list[dict[str, Any]] = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_creators, tuple(params))
            base_rows = cur.fetchall()

            for row in base_rows:
                cur.execute(
                    """
                    SELECT p.id, p.published_at,
                           m.view_count, m.like_count, m.comment_count
                      FROM posts p
                      JOIN LATERAL (
                          SELECT * FROM post_metrics
                           WHERE post_id = p.id
                           ORDER BY scraped_at DESC
                           LIMIT 1
                      ) m ON TRUE
                     WHERE p.creator_id = %s
                     ORDER BY p.published_at DESC
                    """,
                    (row["id"],),
                )
                posts = [dict(r) for r in cur.fetchall()]
                out.append(
                    {
                        "id": row["id"],
                        "handle": row["handle"],
                        "follower_count": row["follower_count"] or 0,
                        "lifecycle": row["lifecycle"],
                        "category": row["current_category"],
                        "sub_archetype": row["current_sub_archetype"],
                        "posts": posts,
                    }
                )

    return out


# -----------------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------------


def _persist_score(result: ScoringResult) -> None:
    """Insert one row into the scores table for a creator."""
    breakdown = {
        name: round(sig.contribution, 2)
        for name, sig in result.composite.by_signal.items()
    }
    datapoint_counts = {
        name: sig.datapoints
        for name, sig in result.composite.by_signal.items()
    }

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scores (
                    creator_id, score, confidence, labels,
                    breakdown, datapoint_counts, algo_version
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    result.creator_id,
                    round(result.score, 2),
                    round(result.confidence, 3),
                    result.labels,
                    json.dumps(breakdown),
                    json.dumps(datapoint_counts),
                    ALGO_VERSION,
                ),
            )


# -----------------------------------------------------------------------------
# Main scoring entry point
# -----------------------------------------------------------------------------


def score_creators(
    lifecycle_filter: list[str] | None = None,
    creator_ids: list[int] | None = None,
) -> list[ScoringResult]:
    """Score all matching creators and persist results.

    Args:
        lifecycle_filter: Only score creators in these lifecycle states.
            Defaults to ['active', 'graduated'].
        creator_ids: Restrict to specific creator ids (overrides lifecycle).

    Returns:
        List of ScoringResult for each scored creator.
    """
    if lifecycle_filter is None and creator_ids is None:
        lifecycle_filter = ["active", "graduated"]

    creators = _load_creators_for_scoring(lifecycle_filter, creator_ids)
    logger.info("scoring_starting", count=len(creators), algo_version=ALGO_VERSION)

    results: list[ScoringResult] = []
    for creator in creators:
        log = logger.bind(handle=creator["handle"], creator_id=creator["id"])

        if not creator["posts"]:
            log.warning("no_posts_skipping")
            continue

        composite = compute_composite(
            posts=creator["posts"],
            follower_count=creator["follower_count"],
        )
        labels = compute_labels(composite)

        result = ScoringResult(
            creator_id=creator["id"],
            handle=creator["handle"],
            score=composite.score,
            confidence=composite.confidence,
            labels=labels,
            composite=composite,
        )

        try:
            _persist_score(result)
            log.info(
                "scored",
                score=round(result.score, 1),
                confidence=round(result.confidence, 2),
                labels=labels,
            )
        except Exception as e:
            log.error("persist_failed", error=str(e))
            continue

        results.append(result)

    logger.info("scoring_complete", scored=len(results))
    return results
