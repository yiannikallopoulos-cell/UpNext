"""Run the scoring engine on creators in the database.

By default, scores all active and graduated creators. Each run produces
new rows in the scores table (append-only) — historical scores are preserved.

Usage:
    # Score all active + graduated creators
    python scripts/run_scoring.py

    # Score a specific creator
    python scripts/run_scoring.py --creator-id 210

    # Score only sports commentary
    python scripts/run_scoring.py --category sports_commentary

    # Show detailed breakdown for each creator
    python scripts/run_scoring.py --verbose

Verification:
    After running, check Supabase:
      - scores table: new rows, one per creator scored
      - Each row contains breakdown (signal contributions) and
        datapoint_counts (how many posts fed each signal)
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

from scout.db import get_connection
from scout.score.compute import score_creators

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


def filter_creators_by_category(category: str) -> list[int]:
    """Return creator IDs matching a category filter."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM creators
                 WHERE current_category = %s
                   AND lifecycle IN ('active', 'graduated')
                 ORDER BY id
                """,
                (category,),
            )
            return [r["id"] for r in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scoring engine on creators.")
    parser.add_argument(
        "--creator-id", type=int, default=None, help="Score one specific creator."
    )
    parser.add_argument(
        "--category",
        choices=["sports_commentary", "grwm"],
        default=None,
        help="Restrict to a single category.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-signal breakdown for each creator.",
    )
    args = parser.parse_args()

    setup_logging()

    print("=" * 76)
    print("SCORING ENGINE")
    print("=" * 76)

    # Build creator_ids list if filters were given.
    creator_ids: list[int] | None = None
    if args.creator_id is not None:
        creator_ids = [args.creator_id]
    elif args.category is not None:
        creator_ids = filter_creators_by_category(args.category)
        print(f"  Category filter: {args.category} ({len(creator_ids)} creators)")

    results = score_creators(creator_ids=creator_ids)

    if not results:
        print("\nNo creators were scored.")
        return 0

    # Sort by score descending for display.
    results.sort(key=lambda r: r.score, reverse=True)

    # Per-creator summary lines.
    print()
    print(f"{'Handle':25} {'Score':>6}  {'Conf':>5}  Labels")
    print("-" * 76)
    for r in results:
        labels_str = ", ".join(r.labels) if r.labels else "(none)"
        print(
            f"@{r.handle:24} "
            f"{r.score:>6.1f}  "
            f"{r.confidence:>5.2f}  "
            f"{labels_str}"
        )

    if args.verbose:
        print()
        print("=" * 76)
        print("PER-SIGNAL BREAKDOWN")
        print("=" * 76)
        for r in results:
            print()
            print(f"  @{r.handle} — score={r.score:.1f}, confidence={r.confidence:.2f}")
            for name, sig in r.composite.by_signal.items():
                print(
                    f"    {name:30} contribution={sig.contribution:>5.1f}  "
                    f"datapoints={sig.datapoints:>3}  confidence={sig.confidence:.2f}"
                )

    # Label distribution summary.
    label_counts: Counter = Counter()
    for r in results:
        for label in r.labels:
            label_counts[label] += 1
    no_label_count = sum(1 for r in results if not r.labels)

    print()
    print("=" * 76)
    print("LABEL DISTRIBUTION")
    print("=" * 76)
    for label, count in label_counts.most_common():
        print(f"  {count:>3}  {label}")
    print(f"  {no_label_count:>3}  (no labels)")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
