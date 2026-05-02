"""Test script for the search-term harvester.

Runs the search-term discovery channel end-to-end. Calls Apify for each
configured search term, dedupes results, and writes new candidates to
the database.

Cost note: a full run with all categories costs roughly $5-8 in Apify credits.
For initial testing, use --category to limit scope:

    # Cheap test: run only GRWM terms (~$1)
    python scripts/test_search_harvest.py --category grwm

    # Full run (all categories)
    python scripts/test_search_harvest.py

    # Use fewer posts per search to save more (default 30)
    python scripts/test_search_harvest.py --category grwm --posts-per-search 15

Verification:
    After running, check Supabase:
      - creators: many new rows with lifecycle='new', first_discovery_channel='search_terms'
      - discovery_events: one row per surfacing (creators surfaced by multiple
        terms will have multiple events)
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).parent.parent / "src"))

import argparse
import logging
import sys

import structlog

from scout.harvest.search import run_search_harvest


def setup_logging() -> None:
    """Human-readable console output."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the search-term harvester.")
    parser.add_argument(
        "--category",
        choices=["sports_commentary", "grwm"],
        default=None,
        help="Run only a single category (default: run all).",
    )
    parser.add_argument(
        "--posts-per-search",
        type=int,
        default=30,
        help="Posts to fetch per search term. Lower = cheaper. Default: 30.",
    )
    args = parser.parse_args()

    setup_logging()

    print("=" * 70)
    print("SEARCH-TERM HARVEST")
    print("=" * 70)
    print(f"  Category filter:    {args.category or 'all'}")
    print(f"  Posts per search:   {args.posts_per_search}")
    print()
    print("This will take several minutes — each search term spins up an")
    print("Apify actor that runs for 30-60 seconds.")
    print()

    try:
        result = run_search_harvest(
            posts_per_search=args.posts_per_search,
            category_filter=args.category,
        )
    except Exception as e:
        print(f"\nERROR during harvest: {e}")
        return 1

    print()
    print("=" * 70)
    print("HARVEST RESULT")
    print("=" * 70)
    print(f"  Terms processed:        {result.terms_processed}")
    print(f"  Terms failed:           {result.terms_failed}")
    print(f"  Total hits:             {result.total_hits}")
    print(f"  Unique candidates:      {result.unique_candidates}")
    print(f"  New creators inserted:  {result.new_creators_inserted}")
    print(f"  Discovery events:       {result.discovery_events_logged}")
    print()
    print("Verify in Supabase Table Editor:")
    print("  creators           — new rows with lifecycle='new'")
    print("  discovery_events   — one row per surfacing")
    print()
    print("Next: enrich these candidates with full profile scrapes,")
    print("then apply the filter stage to drop those outside the band.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
