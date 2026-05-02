"""Run LLM categorization on creators in the database.

By default, classifies all creators with lifecycle='active' that don't yet
have a categorization. Use flags to scope: limit count, single creator,
or force re-categorization.

Cost is very low — Claude Haiku at this volume costs fractions of a cent
per creator. 31 creators ≈ $0.10-0.50 total.

Usage:
    # Categorize all active creators that don't have a categorization yet
    python scripts/run_categorization.py

    # Categorize only the first 5 (cheap test)
    python scripts/run_categorization.py --limit 5

    # Force re-categorization of creators that already have one
    python scripts/run_categorization.py --force

    # Dry run: show what would be classified, no API calls
    python scripts/run_categorization.py --dry-run

Verification:
    After running, check Supabase:
      - categorizations: new rows for each creator processed
      - creators: current_category and current_sub_archetype populated
        (unless category_locked=true, in which case they're left alone)
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).parent.parent / "src"))

import argparse
import logging
import sys
from collections import Counter

import structlog

from scout.categorize.classifier import Classifier
from scout.db import get_connection

logger = structlog.get_logger(__name__)


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )


def load_creators_to_categorize(
    limit: int | None,
    force: bool,
) -> list[dict]:
    """Load active creators that need categorization.

    Without --force, skips creators that already have a categorization row.
    With --force, returns all active creators regardless.

    For each creator, also pulls up to 10 recent caption texts to use as
    classification input.
    """
    if force:
        sql = """
            SELECT c.id, c.handle, c.bio
              FROM creators c
             WHERE c.lifecycle = 'active'
             ORDER BY c.id
        """
    else:
        # Skip creators who already have at least one categorization row.
        sql = """
            SELECT c.id, c.handle, c.bio
              FROM creators c
              LEFT JOIN categorizations cat ON cat.creator_id = c.id
             WHERE c.lifecycle = 'active'
               AND cat.id IS NULL
             ORDER BY c.id
        """

    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    creators: list[dict] = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            base_rows = cur.fetchall()

            # For each creator, pull recent captions in a separate query.
            # Could be done as a single query with array_agg, but this is
            # clearer and the volume is small.
            for row in base_rows:
                cur.execute(
                    """
                    SELECT caption FROM posts
                     WHERE creator_id = %s AND caption IS NOT NULL AND caption <> ''
                     ORDER BY published_at DESC
                     LIMIT 10
                    """,
                    (row["id"],),
                )
                captions = [r["caption"] for r in cur.fetchall()]
                creators.append(
                    {
                        "id": row["id"],
                        "handle": row["handle"],
                        "bio": row["bio"],
                        "captions": captions,
                    }
                )

    return creators


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LLM categorization on active creators.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max creators to categorize this run.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-categorize creators that already have a categorization.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without calling the API.",
    )
    args = parser.parse_args()

    setup_logging()

    print("=" * 70)
    print("LLM CATEGORIZATION")
    print("=" * 70)

    creators = load_creators_to_categorize(limit=args.limit, force=args.force)
    print(f"  Creators to process: {len(creators)}")
    print(f"  Force mode:          {args.force}")
    print(f"  Dry run:             {args.dry_run}")
    print()

    if not creators:
        print("Nothing to do. Use --force to re-categorize already-classified creators.")
        return 0

    if args.dry_run:
        print("Dry run — listing creators that would be classified:")
        for c in creators:
            n_captions = len(c["captions"])
            bio_preview = (c["bio"] or "")[:40]
            print(
                f"  id={c['id']:>4}  @{c['handle']:25}  "
                f"captions={n_captions:>2}  bio={bio_preview!r}"
            )
        print()
        return 0

    # Real run.
    classifier = Classifier()
    successes: list = []
    failures: list = []
    category_counts: Counter = Counter()
    sub_archetype_counts: Counter = Counter()
    low_confidence: list = []

    for i, creator in enumerate(creators, start=1):
        print(
            f"[{i:>3}/{len(creators)}] @{creator['handle']:25} ...",
            end=" ",
            flush=True,
        )
        result = classifier.classify(
            creator_id=creator["id"],
            handle=creator["handle"],
            bio=creator["bio"],
            recent_captions=creator["captions"],
        )

        if result.success:
            successes.append(result)
            category_counts[result.category] += 1
            sub_archetype_counts[result.sub_archetype or "unknown"] += 1
            print(
                f"{result.category:20} / {result.sub_archetype or 'unknown':25} "
                f"(conf={result.confidence:.2f})"
            )
            if result.confidence < 0.7:
                low_confidence.append(result)
        else:
            failures.append(result)
            print(f"FAILED: {result.error}")

    # Summary.
    print()
    print("=" * 70)
    print("CATEGORIZATION RESULT")
    print("=" * 70)
    print(f"  Successful:  {len(successes)}")
    print(f"  Failed:      {len(failures)}")
    print()
    print("Categories:")
    for cat, count in category_counts.most_common():
        print(f"  {count:>3}  {cat}")
    print()
    print("Sub-archetypes:")
    for sub, count in sub_archetype_counts.most_common():
        print(f"  {count:>3}  {sub}")
    print()
    print(f"Low-confidence (<0.7): {len(low_confidence)}")
    if low_confidence:
        for r in low_confidence:
            print(
                f"  creator_id={r.creator_id:>4} "
                f"category={r.category} "
                f"conf={r.confidence:.2f}"
            )
    print()
    if failures:
        print("Failures:")
        for r in failures:
            print(f"  creator_id={r.creator_id} error={r.error}")
        print()

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
