"""Manual categorization review CLI.

Pulls creators with LLM-assigned categorizations and presents them for
operator review. Each creator displays their handle, follower count,
bio, recent caption sample, current LLM categorization, and prompts for
acceptance or override. Overrides are written to category_corrections
append-only.

Designed for the periodic ~3-5 hour review sessions that calibrate the
system's categorization accuracy and accumulate the few-shot examples
that will power phase 2.

Usage:
    python scripts/manual_categorize.py
    python scripts/manual_categorize.py --category sports_commentary
    python scripts/manual_categorize.py --low-confidence-only
"""
