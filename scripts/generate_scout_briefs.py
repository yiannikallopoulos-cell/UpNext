"""Generate scout briefs for creators.

For each creator, produces two pieces of writing using the LLM:
  - scout_brief_short: 1-2 sentences for discovery cards
  - scout_brief_full:  3-5 sentence paragraph for the detail page

The LLM has access to all signal data, labels, follower count, category,
sub-archetype, and existing categorization reasoning. It writes in a scout
voice — direct, professional, willing to call out weaknesses.

Briefs are persisted to creators.scout_brief_short and scout_brief_full.
The dashboard reads from these columns; it does NOT call the LLM at request
time. This is intentional: it caps cost, removes latency, and makes the
dashboard work offline.

Usage:
    # Generate briefs for all creators that don't have them yet
    python scripts/generate_scout_briefs.py

    # Force regeneration for everyone (including those who already have briefs)
    python scripts/generate_scout_briefs.py --force

    # Limit to N creators for testing
    python scripts/generate_scout_briefs.py --limit 3

    # Dry run — print prompts and what would be generated, no API calls
    python scripts/generate_scout_briefs.py --dry-run

Cost: ~$0.02 per creator with Haiku. 15 creators ≈ $0.30 total.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).parent.parent / "src"))

import argparse
import json
import logging
import sys
from dataclasses import dataclass

import structlog
from anthropic import Anthropic

from scout.config import get_settings
from scout.db import get_connection

logger = structlog.get_logger(__name__)

MODEL_NAME = "claude-haiku-4-5"
MAX_OUTPUT_TOKENS = 600


# -----------------------------------------------------------------------------
# Prompt
# -----------------------------------------------------------------------------


SYSTEM_PROMPT = """You are a senior talent scout at a creator agency, writing internal scouting briefs about TikTok creators. Your briefs go to other scouts and agents who need to decide whether a creator is worth pursuing.

Voice and tone:
- Direct and professional. No marketing language. No hype.
- Willing to call out weaknesses honestly. "Engagement is solid but posting cadence is erratic — won't grow without consistency" is the right register.
- Avoid sentimental language. Avoid filler ("interesting creator", "promising potential").
- Use specifics from the data provided. Reference signal patterns, not just labels.
- Talk about the account and its trajectory, NOT the person's bio or self-description.
- Brand-deal viability is the underlying concern. Frame around: can they grow, do they convert reach to followers, is engagement quality real or vanity.

Format: Return ONLY valid JSON. No markdown fences, no preamble. Exact schema:
{
  "short": "1-2 sentences. Under 200 characters. Card-display friendly. NO numeric values inline — state the read, not the data. End with a verdict, not a hedge.",
  "full": "3-5 sentences. Paragraph-style. Reads as a paragraph in a detail page. This is where numbers go, with interpretation."
}
The "short" version goes on a creator card in a list view. The "full" version goes at the top of a detail page where someone has clicked in for more information."""


def build_user_prompt(creator: dict) -> str:
    """Pack everything the LLM should consider into a single user message."""
    labels_str = ", ".join(creator["labels"]) if creator["labels"] else "no labels assigned"

    # Format the signal breakdown so the LLM can reason from it.
    signal_lines = []
    for name, value in (creator["breakdown"] or {}).items():
        signal_lines.append(f"  - {name}: {value}/100")
    signal_section = "\n".join(signal_lines) if signal_lines else "  (no signal data)"

    cat_reasoning = creator.get("categorization_reasoning") or "(none recorded)"
    correction_reasoning = creator.get("correction_reasoning")

    notes_section = f"LLM categorization reasoning: {cat_reasoning}"
    if correction_reasoning:
        notes_section += f"\nManual review override: {correction_reasoning}"

    return f"""Creator to brief:

Handle: @{creator['handle']}
Category: {creator['current_category']} ({creator['current_sub_archetype'] or 'no sub-archetype'})
Lifecycle: {creator['lifecycle']}
Follower count: {creator['follower_count']:,}

Scoring (algo v3):
  Composite score: {creator['score']}/100
  Confidence: {creator['confidence']}
  Labels: {labels_str}

Signal contributions:
{signal_section}

{notes_section}

Signal interpretation guide:
- view_acceleration: are newer posts outperforming older ones? > 50 = growing, < 30 = declining
- view_to_follower_ratio: > 50 means TikTok is pushing them beyond their follower base (algorithmic amplification). < 20 means weak organic reach.
- engagement_rate_trend: are engagement rates holding as views grow? > 50 = quality scaling, < 40 = quality declining
- comment_to_like_ratio: > 70 = strong community, < 30 = passive consumption
- posting_consistency: > 60 = reliable cadence, < 30 = erratic posting (a growth blocker)
- best_post_percentile_rank: > 50 = best posts are recent (breakout pattern), < 30 = best posts predate them

Write the short and full briefs now."""


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------


@dataclass
class BriefResult:
    creator_id: int
    handle: str
    success: bool
    short: str | None = None
    full: str | None = None
    error: str | None = None


# -----------------------------------------------------------------------------
# Data loading
# -----------------------------------------------------------------------------


def load_creators_needing_briefs(force: bool, limit: int | None) -> list[dict]:
    """Load creators that should get briefs in this run.

    Without --force, skips creators that already have a scout_brief_short.
    Pulls latest categorization, latest correction reasoning, and latest
    score breakdown to give the LLM the richest possible input.

    Returns active and graduated creators only — rejected creators aren't
    surfaced in the dashboard so they don't need briefs.
    """
    where = "c.lifecycle IN ('active', 'graduated')"
    if not force:
        where += " AND c.scout_brief_short IS NULL"

    sql = f"""
        SELECT
            c.id, c.handle, c.lifecycle,
            c.current_category, c.current_sub_archetype,
            s.follower_count,
            cat.reasoning AS categorization_reasoning,
            cc.reasoning  AS correction_reasoning,
            sc.score, sc.confidence, sc.labels, sc.breakdown
          FROM creators c
          JOIN v_latest_snapshot s ON s.creator_id = c.id
          LEFT JOIN LATERAL (
              SELECT * FROM categorizations
               WHERE creator_id = c.id
               ORDER BY categorized_at DESC LIMIT 1
          ) cat ON TRUE
          LEFT JOIN LATERAL (
              SELECT * FROM category_corrections
               WHERE creator_id = c.id
               ORDER BY corrected_at DESC LIMIT 1
          ) cc ON TRUE
          LEFT JOIN LATERAL (
              SELECT * FROM scores
               WHERE creator_id = c.id
               ORDER BY computed_at DESC LIMIT 1
          ) sc ON TRUE
         WHERE {where}
         ORDER BY c.id
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]


# -----------------------------------------------------------------------------
# LLM call + parsing
# -----------------------------------------------------------------------------


def _parse_response(raw: str) -> dict:
    """Parse Claude's JSON response. Tolerates markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("response was not a JSON object")
    if "short" not in parsed or "full" not in parsed:
        raise ValueError("missing required fields: short, full")
    if not isinstance(parsed["short"], str) or not isinstance(parsed["full"], str):
        raise ValueError("short and full must be strings")
    return parsed


def generate_brief(
    client: Anthropic, creator: dict, dry_run: bool
) -> BriefResult:
    """Call the LLM once for a creator and return the parsed brief."""
    prompt = build_user_prompt(creator)

    if dry_run:
        print("\n" + "=" * 70)
        print(f"DRY RUN — would prompt for @{creator['handle']}:")
        print("-" * 70)
        print(prompt)
        return BriefResult(
            creator_id=creator["id"], handle=creator["handle"], success=True
        )

    try:
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return BriefResult(
            creator_id=creator["id"], handle=creator["handle"],
            success=False, error=f"api_error: {e}",
        )

    if not response.content:
        return BriefResult(
            creator_id=creator["id"], handle=creator["handle"],
            success=False, error="empty_response",
        )

    raw_text = response.content[0].text
    try:
        parsed = _parse_response(raw_text)
    except (json.JSONDecodeError, ValueError) as e:
        return BriefResult(
            creator_id=creator["id"], handle=creator["handle"],
            success=False, error=f"parse_error: {e}",
        )

    return BriefResult(
        creator_id=creator["id"], handle=creator["handle"],
        success=True, short=parsed["short"], full=parsed["full"],
    )


def persist_brief(creator_id: int, short: str, full: str) -> None:
    """Write the two briefs to the creators table."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE creators
                   SET scout_brief_short = %s,
                       scout_brief_full  = %s,
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (short, full, creator_id),
            )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate scout briefs for creators.")
    parser.add_argument("--limit", type=int, default=None, help="Cap creators processed.")
    parser.add_argument(
        "--force", action="store_true",
        help="Regenerate briefs for creators that already have them.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print prompts without calling the API.",
    )
    args = parser.parse_args()

    setup_logging()

    print("=" * 72)
    print("SCOUT BRIEF GENERATOR")
    print("=" * 72)

    creators = load_creators_needing_briefs(force=args.force, limit=args.limit)
    print(f"  Creators to process: {len(creators)}")
    print(f"  Force mode:          {args.force}")
    print(f"  Dry run:             {args.dry_run}")
    if not args.dry_run:
        est_cost = len(creators) * 0.02
        print(f"  Estimated cost:      ~${est_cost:.2f}")
    print()

    if not creators:
        print("Nothing to do. Use --force to regenerate existing briefs.")
        return 0

    client = Anthropic(api_key=get_settings().anthropic_api_key)
    successes: list[BriefResult] = []
    failures: list[BriefResult] = []

    for i, creator in enumerate(creators, start=1):
        print(f"[{i:>2}/{len(creators)}] @{creator['handle']:25} ...", end=" ", flush=True)
        result = generate_brief(client, creator, dry_run=args.dry_run)

        if result.success:
            if not args.dry_run:
                try:
                    persist_brief(result.creator_id, result.short, result.full)
                except Exception as e:
                    failures.append(BriefResult(
                        creator_id=result.creator_id, handle=result.handle,
                        success=False, error=f"persist_failed: {e}",
                    ))
                    print(f"PERSIST FAILED: {e}")
                    continue
            successes.append(result)
            short_preview = (result.short or "")[:60]
            print(f"OK — {short_preview}{'...' if len(result.short or '') > 60 else ''}")
        else:
            failures.append(result)
            print(f"FAILED: {result.error}")

    print()
    print("=" * 72)
    print("RESULT")
    print("=" * 72)
    print(f"  Successful: {len(successes)}")
    print(f"  Failed:     {len(failures)}")
    if failures:
        print()
        print("Failures:")
        for f in failures:
            print(f"  @{f.handle}: {f.error}")
    print()

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
