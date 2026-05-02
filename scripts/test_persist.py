"""Test script for the full scrape-and-persist pipeline.

Scrapes a TikTok creator via Apify and writes the result to the database.
Run this to verify your database connection is working and that data
flows correctly into all four tables (creators, creator_snapshots, posts,
post_metrics).

Usage:
    python scripts/test_persist.py <handle>
    python scripts/test_persist.py charlidamelio

Verification:
    After running, check your Supabase Table Editor:
      - creators: 1 row for the handle you scraped
      - creator_snapshots: 1 row with current follower count
      - posts: ~30 rows (one per recent post)
      - post_metrics: ~30 rows (one per post)

    Run this script TWICE on the same handle. Second run should:
      - Show is_new_creator=False
      - Skip the snapshot insert if metrics didn't change (snapshot_inserted=False)
      - Insert 0 new posts (posts_inserted=0)
      - Insert ~30 new metrics rows (metrics always append)
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).parent.parent / "src"))

import logging
import sys

import structlog

from scout.enrich.apify_client import (
    ActorRunFailed,
    CreatorNotFound,
    TikTokApifyClient,
    UnexpectedResponseShape,
)
from scout.enrich.persist import save_profile


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


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1

    handle = sys.argv[1]
    setup_logging()

    print(f"Scraping and persisting: {handle}\n")

    client = TikTokApifyClient()

    # 1. Scrape.
    try:
        profile = client.fetch_creator_profile(handle, posts_limit=30)
    except CreatorNotFound as e:
        print(f"\nERROR: Creator not found: {e}")
        return 2
    except UnexpectedResponseShape as e:
        print(f"\nERROR: Response shape changed: {e}")
        return 3
    except ActorRunFailed as e:
        print(f"\nERROR: Actor run failed: {e}")
        return 4

    print(f"\nScraped @{profile.handle}: {profile.follower_count:,} followers, "
          f"{len(profile.posts)} posts returned\n")

    # 2. Persist. Use 'manual' as discovery channel since this is a manual test.
    try:
        result = save_profile(profile, discovery_channel="manual")
    except Exception as e:
        print(f"\nERROR persisting to database: {e}")
        print("Check that your DATABASE_URL is correct and the schema is applied.")
        return 5

    print("=" * 70)
    print("PERSIST RESULT")
    print("=" * 70)
    print(f"  Creator DB id:          {result.creator_id}")
    print(f"  New creator?            {result.is_new_creator}")
    print(f"  Snapshot inserted?      {result.snapshot_inserted}")
    print(f"  New posts inserted:     {result.posts_inserted}")
    print(f"  Post metrics inserted:  {result.post_metrics_inserted}")
    print()
    print("Verify in Supabase Table Editor:")
    print(f"  creators           — should have a row with id = {result.creator_id}")
    print(f"  creator_snapshots  — should have at least 1 row for this creator")
    print(f"  posts              — should have rows for this creator")
    print(f"  post_metrics       — should have rows linked to those posts")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
