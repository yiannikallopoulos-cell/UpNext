"""Persistence layer for scraped TikTok data.

Takes structured TikTokProfile objects from the Apify client and writes them
to the database in a single transaction. Handles upserts for creators (so
re-scraping doesn't duplicate), deduplicates snapshots when nothing has
materially changed, and appends post metrics on every scrape.

This module is intentionally narrow: it persists data, nothing else.
Scoring, categorization, and discovery-event recording happen elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from scout.db import get_connection
from scout.enrich.apify_client import TikTokPost, TikTokProfile

logger = structlog.get_logger(__name__)


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------


@dataclass
class PersistResult:
    """Summary of what happened when a profile was persisted.

    Useful for logging and for tests. Tells the caller whether this was a
    new creator or an existing one, and how many rows ended up being written
    (which won't always match the input — snapshots get deduped, posts get
    upserted).
    """

    creator_id: int
    is_new_creator: bool
    snapshot_inserted: bool
    posts_inserted: int  # net new posts (not previously in the database)
    post_metrics_inserted: int  # always equals total posts in the profile


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def save_profile(
    profile: TikTokProfile,
    discovery_channel: str | None = None,
) -> PersistResult:
    """Persist a scraped TikTok profile to the database.

    All writes happen in a single transaction. If any step fails, nothing
    is committed.

    Args:
        profile: A TikTokProfile from the Apify client.
        discovery_channel: If this is a brand-new creator, used to populate
            first_discovery_channel. Required when is_new_creator becomes
            True; ignored otherwise. Pass one of the values from the
            discovery_channel_t enum: 'sounds', 'search_terms', 'hashtags',
            'neighbors', 'manual'.

    Returns:
        PersistResult summarizing what was written.

    Raises:
        ValueError: If a new creator is being inserted but no
            discovery_channel was provided.
    """
    log = logger.bind(handle=profile.handle, tiktok_user_id=profile.tiktok_user_id)
    log.info("persisting_profile", post_count=len(profile.posts))

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Upsert creator. Returns (id, is_new).
            creator_id, is_new = _upsert_creator(cur, profile, discovery_channel)

            # 2. Conditionally insert snapshot if metrics changed.
            snapshot_inserted = _maybe_insert_snapshot(cur, creator_id, profile)

            # 3. Upsert posts (insert if new, ignore if seen before).
            new_post_count, post_id_map = _upsert_posts(cur, creator_id, profile.posts)

            # 4. Always insert metrics for every post in the profile.
            metrics_count = _insert_post_metrics(
                cur, post_id_map, profile.posts, profile.fetched_at
            )

            # 5. Update last_scraped_at on the creator row.
            cur.execute(
                "UPDATE creators SET last_scraped_at = %s, updated_at = NOW() "
                "WHERE id = %s",
                (profile.fetched_at, creator_id),
            )

        # Commit happens automatically when the `with conn` block exits cleanly.

    result = PersistResult(
        creator_id=creator_id,
        is_new_creator=is_new,
        snapshot_inserted=snapshot_inserted,
        posts_inserted=new_post_count,
        post_metrics_inserted=metrics_count,
    )
    log.info(
        "persist_complete",
        creator_id=result.creator_id,
        is_new=result.is_new_creator,
        snapshot_inserted=result.snapshot_inserted,
        new_posts=result.posts_inserted,
        metrics_written=result.post_metrics_inserted,
    )
    return result


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------


def _upsert_creator(
    cur,  # noqa: ANN001 — psycopg cursor type is awkward to import
    profile: TikTokProfile,
    discovery_channel: str | None,
) -> tuple[int, bool]:
    """Insert a new creator or update an existing one.

    Identity is anchored on tiktok_user_id (handles change; user IDs don't).
    For existing creators we refresh handle, display_name, and bio because
    those can change between scrapes.

    Returns (creator_id, is_new_creator).
    """
    # Check if this creator already exists.
    cur.execute(
        "SELECT id FROM creators WHERE tiktok_user_id = %s",
        (profile.tiktok_user_id,),
    )
    row = cur.fetchone()

    if row is not None:
        creator_id = row["id"]
        # Refresh mutable profile fields. Do not touch lifecycle, category,
        # discovery channel, or any of the audit timestamps.
        cur.execute(
            """
            UPDATE creators
               SET handle = %s,
                   display_name = %s,
                   bio = %s,
                   updated_at = NOW()
             WHERE id = %s
            """,
            (profile.handle, profile.display_name, profile.bio, creator_id),
        )
        return creator_id, False

    # New creator. Discovery channel is required at insert time.
    if discovery_channel is None:
        raise ValueError(
            "discovery_channel is required when inserting a new creator. "
            "Pass one of: 'sounds', 'search_terms', 'hashtags', 'neighbors', 'manual'."
        )

    cur.execute(
        """
        INSERT INTO creators (
            tiktok_user_id, handle, display_name, bio,
            lifecycle, first_discovery_channel
        )
        VALUES (%s, %s, %s, %s, 'new', %s)
        RETURNING id
        """,
        (
            profile.tiktok_user_id,
            profile.handle,
            profile.display_name,
            profile.bio,
            discovery_channel,
        ),
    )
    creator_id = cur.fetchone()["id"]
    return creator_id, True


def _maybe_insert_snapshot(
    cur,  # noqa: ANN001
    creator_id: int,
    profile: TikTokProfile,
) -> bool:
    """Insert a creator_snapshots row only if metrics have changed.

    Compares against the most recent existing snapshot for this creator.
    Returns True if a row was inserted, False if the snapshot was deduped.

    "Changed" is defined narrowly: follower_count or total_posts differs
    from the previous snapshot. Following count and total hearts can drift
    by tiny amounts due to platform recounts; we don't treat those as
    meaningful changes.
    """
    cur.execute(
        """
        SELECT follower_count, total_posts
          FROM creator_snapshots
         WHERE creator_id = %s
         ORDER BY snapshot_at DESC
         LIMIT 1
        """,
        (creator_id,),
    )
    last = cur.fetchone()

    if last is not None:
        # Skip insert if neither follower count nor post count has moved.
        unchanged = (
            last["follower_count"] == profile.follower_count
            and last["total_posts"] == profile.total_posts
        )
        if unchanged:
            return False

    cur.execute(
        """
        INSERT INTO creator_snapshots (
            creator_id, snapshot_at,
            follower_count, following_count,
            total_posts, total_hearts,
            bio_at_snapshot
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            creator_id,
            profile.fetched_at,
            profile.follower_count,
            profile.following_count,
            profile.total_posts,
            profile.total_hearts,
            profile.bio,
        ),
    )
    return True


def _upsert_posts(
    cur,  # noqa: ANN001
    creator_id: int,
    posts: list[TikTokPost],
) -> tuple[int, dict[str, int]]:
    """Insert any posts we haven't seen before. Return mapping of TikTok post ID -> DB id.

    Posts are immutable — we never update a post row after insert. The metrics
    table tracks all the time-varying engagement data.

    Uses ON CONFLICT DO NOTHING so re-scraping the same posts is a no-op for
    the posts table itself (metrics still get appended separately).

    Returns (new_post_count, {tiktok_post_id: db_post_id}).
    """
    if not posts:
        return 0, {}

    # Build the values rows. Hashtags go in as a Postgres text[] array;
    # psycopg handles list-to-array conversion automatically.
    rows = [
        (
            post.tiktok_post_id,
            creator_id,
            post.published_at,
            post.caption,
            post.sound_id,
            post.sound_title,
            post.hashtags,
        )
        for post in posts
    ]

    # We want to know which posts were actually inserted vs. already existed.
    # Postgres's `xmax = 0` trick: on a fresh insert xmax is 0; on a conflict
    # update it's nonzero. Since we use DO NOTHING we get back only inserted
    # rows, which is fine — we'll do a separate lookup for the rest.
    cur.executemany(
        """
        INSERT INTO posts (
            tiktok_post_id, creator_id, published_at,
            caption, sound_id, sound_title, hashtags
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tiktok_post_id) DO NOTHING
        """,
        rows,
    )
    new_post_count = cur.rowcount  # rows actually inserted (not conflicted)

    # Look up the DB ids for all posts in this profile, including ones that
    # already existed before this scrape.
    tiktok_post_ids = [post.tiktok_post_id for post in posts]
    cur.execute(
        "SELECT id, tiktok_post_id FROM posts WHERE tiktok_post_id = ANY(%s)",
        (tiktok_post_ids,),
    )
    post_id_map = {row["tiktok_post_id"]: row["id"] for row in cur.fetchall()}

    return new_post_count, post_id_map


def _insert_post_metrics(
    cur,  # noqa: ANN001
    post_id_map: dict[str, int],
    posts: list[TikTokPost],
    fetched_at,  # noqa: ANN001 — datetime
) -> int:
    """Append a post_metrics row for every post in the profile.

    Always inserts — never deduplicates. Post metrics are time-series data;
    even unchanged values at a new timestamp are meaningful (they confirm the
    system observed the post and the metrics held steady).

    The post_age_hours field is computed at insert time so we can later filter
    on "metrics observed within first 7 days" etc. without recomputing.

    Returns the count of metrics rows inserted.
    """
    if not posts:
        return 0

    rows = []
    for post in posts:
        db_post_id = post_id_map.get(post.tiktok_post_id)
        if db_post_id is None:
            # Should be impossible after _upsert_posts, but defensive.
            logger.warning(
                "post_id_missing_in_map", tiktok_post_id=post.tiktok_post_id
            )
            continue

        age_hours = int((fetched_at - post.published_at).total_seconds() // 3600)
        rows.append(
            (
                db_post_id,
                fetched_at,
                age_hours,
                post.view_count,
                post.like_count,
                post.comment_count,
                post.share_count,
                post.save_count,
            )
        )

    if not rows:
        return 0

    cur.executemany(
        """
        INSERT INTO post_metrics (
            post_id, scraped_at, post_age_hours,
            view_count, like_count, comment_count, share_count, save_count
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        rows,
    )
    return len(rows)
