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
# The TikTok scraper actor we use. Swap here if we change vendors;
# call sites should not need to know which actor is in use.
TIKTOK_PROFILE_ACTOR = "clockworks/tiktok-scraper"

# Default cap on posts returned per profile fetch. Most scoring signals
# stabilize with 30-50 recent posts; pulling more is wasted spend.
DEFAULT_POSTS_PER_PROFILE = 30

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
    """Thin wrapper around the Apify SDK for TikTok scraping.

    Single-purpose at v1: fetch a creator's profile + recent posts by handle.
    More methods (sound search, hashtag search, etc.) will be added as the
    harvest channels are built out.
    """

    def __init__(self, api_token: str | None = None) -> None:
        token = api_token or get_settings().apify_api_token
        self._client = ApifyClient(token)

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
        # Normalize handle: actors typically expect a profile URL or username
        # without the @ prefix, but accept both forms for caller convenience.
        clean_handle = handle.lstrip("@").strip()
        if not clean_handle:
            raise ValueError("handle must be a non-empty string")

        log = logger.bind(handle=clean_handle, actor=TIKTOK_PROFILE_ACTOR)
        log.info("fetching_profile", posts_limit=posts_limit)

        # Actor input. The clockworks/tiktok-scraper actor accepts a list of
        # profile URLs and a results-per-page parameter. Configuration here
        # is intentionally minimal — we only set what we need.
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
            # Apify SDK raises a variety of exceptions; we normalize to ours.
            log.error("actor_call_failed", error=str(e))
            raise ActorRunFailed(f"Apify actor call failed: {e}") from e

        if not run or run.get("status") != "SUCCEEDED":
            status = run.get("status") if run else "UNKNOWN"
            log.error("actor_run_did_not_succeed", status=status)
            raise ActorRunFailed(f"Actor run status: {status}")

        # Pull results from the run's default dataset.
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
    # Parsing
    # -------------------------------------------------------------------------
    # Parsing is split out because it's the most likely thing to need updates
    # when actor responses change. Keep all field-name knowledge in here.

    def _parse_profile_response(
        self, requested_handle: str, items: list[dict[str, Any]]
    ) -> TikTokProfile:
        """Parse the actor's dataset items into a TikTokProfile.

        The clockworks actor returns one item per post, with the creator's
        profile data nested inside each item under 'authorMeta'. We extract
        profile data from the first item and treat all items as posts.
        """
        if not items:
            raise CreatorNotFound(f"No items for {requested_handle}")

        first = items[0]
        author = first.get("authorMeta") or {}

        # Profile-level fields. If any of these critical fields are missing,
        # the response shape has changed and we need to update parsing.
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

        # Parse each item as a post.
        for item in items:
            try:
                post = self._parse_post(item)
                profile.posts.append(post)
            except UnexpectedResponseShape as e:
                # Don't fail the whole fetch if a single post is malformed.
                # Log and skip; we'd rather have N-1 posts than zero.
                logger.warning(
                    "post_parse_failed",
                    handle=requested_handle,
                    error=str(e),
                )

        return profile

    @staticmethod
    def _parse_post(item: dict[str, Any]) -> TikTokPost:
        """Parse a single dataset item into a TikTokPost."""
        try:
            # Timestamp comes as either ISO string (createTimeISO) or
            # Unix seconds (createTime). Handle both.
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
