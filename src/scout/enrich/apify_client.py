"""Apify API client for TikTok data acquisition.

This module wraps the Apify SDK with the specific actor configurations and
data shapes that UpNext needs. Designed to be the single point of contact
between the rest of the system and Apify's API.

Design notes:
- Actor names are configurable constants at the top of the module so we can
  swap actors without touching call sites.
- Returns structured dataclasses, not raw dicts, so downstream code has a
  contract to work against. Apify response shapes change occasionally; this
  module absorbs that volatility.
- All Apify-specific error handling (rate limits, actor failures, timeouts)
  lives here. Callers see clean exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from apify_client import ApifyClient
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scout.config import get_settings

logger = structlog.get_logger(__name__)


# -----------------------------------------------------------------------------
# Actor configuration
# -----------------------------------------------------------------------------

# Profile + recent-posts actor. Used for full creator enrichment.
TIKTOK_PROFILE_ACTOR = "clockworks/tiktok-scraper"

# Search-by-keyword actor. Used for the search-term harvest channel.
# Returns posts matching a query string; we extract creator handles from those.
TIKTOK_SEARCH_ACTOR = "clockworks/tiktok-scraper"

# Default cap on posts returned per profile fetch. Most scoring signals
# stabilize with 30-50 recent posts; pulling more is wasted spend.
DEFAULT_POSTS_PER_PROFILE = 30

# Default cap on posts returned per search-term query. Tuned for cost vs.
# discovery yield: more posts surface more creators but linearly increase cost.
DEFAULT_POSTS_PER_SEARCH = 30

# Hard timeout for actor runs. If TikTok or Apify is having a bad day,
# we'd rather fail fast and let the job retry than block forever.
ACTOR_RUN_TIMEOUT_SECS = 300


# -----------------------------------------------------------------------------
# Data classes (the contract this module presents to the rest of the system)
# -----------------------------------------------------------------------------


@dataclass
class TikTokPost:
    """A single TikTok post with its metrics at scrape time.

    Mirrors the shape we'll write to the `posts` and `post_metrics` tables.
    Field naming aligns with our schema rather than Apify's response keys.
    """

    tiktok_post_id: str
    published_at: datetime
    caption: str | None
    sound_id: str | None
    sound_title: str | None
    hashtags: list[str]

    # Metrics — the time-varying part. These will change on subsequent
    # scrapes during the post's first 14 days.
    view_count: int
    like_count: int
    comment_count: int
    share_count: int
    save_count: int | None  # not always returned by the actor


@dataclass
class TikTokProfile:
    """A creator's profile snapshot plus recent posts.

    A single fetch returns both, which is the cost-efficient path: one
    actor run, one billable result, two tables' worth of data.
    """

    tiktok_user_id: str
    handle: str
    display_name: str | None
    bio: str | None

    follower_count: int
    following_count: int
    total_posts: int
    total_hearts: int  # cumulative likes across the creator's posts

    posts: list[TikTokPost] = field(default_factory=list)

    # When this snapshot was taken. Set by the client at fetch time so
    # downstream code doesn't have to remember to stamp it.
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SearchHit:
    """A single creator surfaced by a search-term query.

    Lightweight summary — just enough to decide whether to enrich this
    creator further. Full profile/post data is fetched separately during
    enrichment, only on candidates we actually want to track.
    """

    tiktok_user_id: str
    handle: str
    display_name: str | None
    follower_count: int | None  # not always provided by search results
    bio: str | None

    # The post that surfaced this creator. Useful for diagnostics —
    # e.g., looking at which posts a "reaction" search actually returned.
    surfacing_post_id: str
    surfacing_post_caption: str | None


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------


class ApifyError(Exception):
    """Base for all errors originating from the Apify integration."""


class ActorRunFailed(ApifyError):
    """The Apify actor run failed or timed out."""


class CreatorNotFound(ApifyError):
    """The requested handle returned no usable data.

    Could mean the account was deleted, made private, suspended, or the
    handle was misspelled. Caller decides how to respond.
    """


class UnexpectedResponseShape(ApifyError):
    """Actor returned data in a shape we don't know how to parse.

    Usually means the actor was updated and our parser needs to be too.
    """


# -----------------------------------------------------------------------------
# Client
# -----------------------------------------------------------------------------


class TikTokApifyClient:
    """Thin wrapper around the Apify SDK for TikTok scraping."""

    def __init__(self, api_token: str | None = None) -> None:
        token = api_token or get_settings().apify_api_token
        self._client = ApifyClient(token)

    # -------------------------------------------------------------------------
    # Profile fetching
    # -------------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(ActorRunFailed),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        reraise=True,
    )
    def fetch_creator_profile(
        self,
        handle: str,
        posts_limit: int = DEFAULT_POSTS_PER_PROFILE,
    ) -> TikTokProfile:
        """Fetch a TikTok creator's profile snapshot plus recent posts.

        Args:
            handle: TikTok username, with or without leading @.
            posts_limit: Maximum number of recent posts to return.

        Returns:
            TikTokProfile populated with current metrics and recent posts.

        Raises:
            CreatorNotFound: Handle resolved to no usable data.
            ActorRunFailed: Actor run failed (after retries).
            UnexpectedResponseShape: Response shape changed; parser needs updating.
        """
        clean_handle = handle.lstrip("@").strip()
        if not clean_handle:
            raise ValueError("handle must be a non-empty string")

        log = logger.bind(handle=clean_handle, actor=TIKTOK_PROFILE_ACTOR)
        log.info("fetching_profile", posts_limit=posts_limit)

        actor_input = {
            "profiles": [clean_handle],
            "resultsPerPage": posts_limit,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "shouldDownloadSlideshowImages": False,
        }

        try:
            run = self._client.actor(TIKTOK_PROFILE_ACTOR).call(
                run_input=actor_input,
                timeout_secs=ACTOR_RUN_TIMEOUT_SECS,
            )
        except Exception as e:
            log.error("actor_call_failed", error=str(e))
            raise ActorRunFailed(f"Apify actor call failed: {e}") from e

        if not run or run.get("status") != "SUCCEEDED":
            status = run.get("status") if run else "UNKNOWN"
            log.error("actor_run_did_not_succeed", status=status)
            raise ActorRunFailed(f"Actor run status: {status}")

        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise ActorRunFailed("Actor run produced no dataset")

        items = list(self._client.dataset(dataset_id).iterate_items())
        if not items:
            log.warning("no_items_returned")
            raise CreatorNotFound(f"No data returned for handle: {clean_handle}")

        log.info("actor_run_succeeded", item_count=len(items))
        return self._parse_profile_response(clean_handle, items)

    # -------------------------------------------------------------------------
    # Search-by-keyword
    # -------------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(ActorRunFailed),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        reraise=True,
    )
    def search_posts_by_keyword(
        self,
        query: str,
        posts_limit: int = DEFAULT_POSTS_PER_SEARCH,
    ) -> list[SearchHit]:
        """Search TikTok for posts matching a keyword query, return creators.

        Used by the search-term harvest channel. Each query returns up to
        `posts_limit` recent posts; we extract one SearchHit per unique
        creator that appears in the results.

        Args:
            query: Free-text search query (e.g., "lakers reaction", "grwm").
            posts_limit: Maximum number of posts to fetch for this query.

        Returns:
            List of SearchHit, one per unique creator surfaced by the query.
            Order is preserved from the response (most recent first).

        Raises:
            ActorRunFailed: Actor run failed (after retries).
        """
        if not query.strip():
            raise ValueError("query must be a non-empty string")

        log = logger.bind(query=query, actor=TIKTOK_SEARCH_ACTOR)
        log.info("searching_posts", posts_limit=posts_limit)

        # The clockworks scraper accepts a "searchQueries" list and returns
        # posts matching those queries. Configuration is parallel to profile
        # scraping but with a different input key.
        actor_input = {
            "searchQueries": [query],
            "resultsPerPage": posts_limit,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "shouldDownloadSlideshowImages": False,
        }

        try:
            run = self._client.actor(TIKTOK_SEARCH_ACTOR).call(
                run_input=actor_input,
                timeout_secs=ACTOR_RUN_TIMEOUT_SECS,
            )
        except Exception as e:
            log.error("actor_call_failed", error=str(e))
            raise ActorRunFailed(f"Apify actor call failed: {e}") from e

        if not run or run.get("status") != "SUCCEEDED":
            status = run.get("status") if run else "UNKNOWN"
            log.error("actor_run_did_not_succeed", status=status)
            raise ActorRunFailed(f"Actor run status: {status}")

        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            log.warning("no_dataset_returned")
            return []

        items = list(self._client.dataset(dataset_id).iterate_items())
        log.info("search_run_succeeded", item_count=len(items))

        return self._parse_search_response(items)

    # -------------------------------------------------------------------------
    # Parsing
    # -------------------------------------------------------------------------

    def _parse_profile_response(
        self, requested_handle: str, items: list[dict[str, Any]]
    ) -> TikTokProfile:
        """Parse the actor's dataset items into a TikTokProfile."""
        if not items:
            raise CreatorNotFound(f"No items for {requested_handle}")

        first = items[0]
        author = first.get("authorMeta") or {}

        try:
            profile = TikTokProfile(
                tiktok_user_id=str(author["id"]),
                handle=author.get("name") or requested_handle,
                display_name=author.get("nickName"),
                bio=author.get("signature"),
                follower_count=int(author.get("fans", 0)),
                following_count=int(author.get("following", 0)),
                total_posts=int(author.get("video", 0)),
                total_hearts=int(author.get("heart", 0)),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.error("profile_parse_failed", handle=requested_handle, error=str(e))
            raise UnexpectedResponseShape(
                f"Could not extract profile fields: {e}"
            ) from e

        for item in items:
            try:
                post = self._parse_post(item)
                profile.posts.append(post)
            except UnexpectedResponseShape as e:
                logger.warning(
                    "post_parse_failed", handle=requested_handle, error=str(e)
                )

        return profile

    @staticmethod
    def _parse_search_response(items: list[dict[str, Any]]) -> list[SearchHit]:
        """Parse search results into deduplicated SearchHits.

        Search results are post-shaped (same as profile responses), but each
        item represents a different creator. We dedupe by tiktok_user_id —
        if the same creator appears in multiple posts in the result set,
        we only emit one SearchHit, keeping the first (most recent) post
        as the surfacing context.
        """
        seen_user_ids: set[str] = set()
        hits: list[SearchHit] = []

        for item in items:
            author = item.get("authorMeta") or {}
            user_id_raw = author.get("id")
            if not user_id_raw:
                continue

            user_id = str(user_id_raw)
            if user_id in seen_user_ids:
                continue
            seen_user_ids.add(user_id)

            try:
                hit = SearchHit(
                    tiktok_user_id=user_id,
                    handle=author.get("name", ""),
                    display_name=author.get("nickName"),
                    # 'fans' is sometimes present in search responses, sometimes not.
                    # Tolerate absence — we'll get the real count during enrichment.
                    follower_count=(
                        int(author["fans"]) if "fans" in author else None
                    ),
                    bio=author.get("signature"),
                    surfacing_post_id=str(item.get("id", "")),
                    surfacing_post_caption=item.get("text"),
                )
                hits.append(hit)
            except (TypeError, ValueError) as e:
                logger.warning(
                    "search_hit_parse_failed",
                    user_id=user_id,
                    error=str(e),
                )

        return hits

    @staticmethod
    def _parse_post(item: dict[str, Any]) -> TikTokPost:
        """Parse a single dataset item into a TikTokPost."""
        try:
            published_ts = item.get("createTimeISO") or item.get("createTime")
            if isinstance(published_ts, str):
                published_at = datetime.fromisoformat(
                    published_ts.replace("Z", "+00:00")
                )
            else:
                published_at = datetime.fromtimestamp(
                    int(published_ts), tz=timezone.utc
                )

            music = item.get("musicMeta") or {}
            hashtags = [
                h.get("name") for h in (item.get("hashtags") or []) if h.get("name")
            ]

            return TikTokPost(
                tiktok_post_id=str(item["id"]),
                published_at=published_at,
                caption=item.get("text"),
                sound_id=str(music.get("musicId")) if music.get("musicId") else None,
                sound_title=music.get("musicName"),
                hashtags=hashtags,
                view_count=int(item.get("playCount", 0)),
                like_count=int(item.get("diggCount", 0)),
                comment_count=int(item.get("commentCount", 0)),
                share_count=int(item.get("shareCount", 0)),
                save_count=(
                    int(item["collectCount"]) if "collectCount" in item else None
                ),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise UnexpectedResponseShape(f"Post parse failed: {e}") from e
