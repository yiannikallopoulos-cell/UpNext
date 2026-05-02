"""Test script for the Apify client.

Run this once to confirm the Apify integration works and that the response
parser handles real TikTok data correctly. NOT part of the production
pipeline — this is exploratory.

Usage:
    python scripts/test_apify.py <handle>
    python scripts/test_apify.py mrbeast
    python scripts/test_apify.py @charlidamelio

What it does:
    1. Loads config from .env
    2. Calls the Apify client to fetch the handle's profile + recent posts
    3. Pretty-prints the result so you can verify the data shape
    4. Prints summary stats: post count, view-to-follower ratio, etc.

If parsing fails, the error message will tell you which field tripped it up.
That's the signal to update _parse_profile_response or _parse_post in
apify_client.py.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

import structlog

from scout.enrich.apify_client import (
    ActorRunFailed,
    CreatorNotFound,
    TikTokApifyClient,
    UnexpectedResponseShape,
)


def setup_logging() -> None:
    """Configure structlog for human-readable console output during testing."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )


def print_profile_summary(profile) -> None:  # noqa: ANN001 — local script type
    """Pretty-print a TikTokProfile for human eyeballing."""
    print("\n" + "=" * 70)
    print(f"PROFILE: @{profile.handle}")
    print("=" * 70)
    print(f"  Display name:    {profile.display_name}")
    print(f"  TikTok user ID:  {profile.tiktok_user_id}")
    print(f"  Bio:             {profile.bio!r}")
    print(f"  Followers:       {profile.follower_count:,}")
    print(f"  Following:       {profile.following_count:,}")
    print(f"  Total posts:     {profile.total_posts:,}")
    print(f"  Total hearts:    {profile.total_hearts:,}")
    print(f"  Posts returned:  {len(profile.posts)}")
    print(f"  Fetched at:      {profile.fetched_at.isoformat()}")

    if not profile.posts:
        print("\n  No posts returned.")
        return

    # Compute some quick stats — this is what scoring will eventually do.
    total_views = sum(p.view_count for p in profile.posts)
    total_likes = sum(p.like_count for p in profile.posts)
    total_comments = sum(p.comment_count for p in profile.posts)
    avg_views = total_views / len(profile.posts)
    view_to_follower = avg_views / max(profile.follower_count, 1)
    eng_rate = (total_likes + total_comments) / max(total_views, 1)

    print("\n  Quick stats across returned posts:")
    print(f"    Avg views per post:       {avg_views:,.0f}")
    print(f"    View-to-follower ratio:   {view_to_follower:.2f}")
    print(f"    Engagement rate:          {eng_rate:.2%}")

    now = datetime.now(timezone.utc)
    settled_count = sum(
        1 for p in profile.posts if (now - p.published_at).days >= 14
    )
    print(f"    Settled posts (14d+):     {settled_count}/{len(profile.posts)}")

    # Show the most recent few posts.
    print("\n  Most recent 3 posts:")
    for post in sorted(profile.posts, key=lambda p: p.published_at, reverse=True)[:3]:
        age_days = (now - post.published_at).days
        caption_preview = (post.caption or "")[:60].replace("\n", " ")
        print(f"    [{age_days:>3}d ago] views={post.view_count:>8,} "
              f"likes={post.like_count:>6,} comments={post.comment_count:>5,}")
        print(f"             caption: {caption_preview!r}")
        if post.sound_title:
            print(f"             sound:   {post.sound_title!r}")

    print()


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1

    handle = sys.argv[1]
    setup_logging()

    print(f"Fetching profile for: {handle}")
    print("(This will take 30-90 seconds — Apify actors take time to spin up)\n")

    client = TikTokApifyClient()
    try:
        profile = client.fetch_creator_profile(handle, posts_limit=30)
    except CreatorNotFound as e:
        print(f"\nERROR: Creator not found: {e}")
        print("Possible reasons: handle misspelled, account private/deleted/suspended.")
        return 2
    except UnexpectedResponseShape as e:
        print(f"\nERROR: Response shape changed: {e}")
        print("The Apify actor's output format has changed.")
        print("Update _parse_profile_response or _parse_post in apify_client.py.")
        return 3
    except ActorRunFailed as e:
        print(f"\nERROR: Actor run failed: {e}")
        print("Could be transient (Apify load) or persistent (TikTok blocking).")
        print("Check the Apify console for run details.")
        return 4

    print_profile_summary(profile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
