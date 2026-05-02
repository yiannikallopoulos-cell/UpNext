"""LLM categorization classifier.

Takes a creator's bio and recent caption texts, calls Claude to classify
them, and writes the result to the categorizations table.

Key design choices:
- Output is strict JSON. Malformed responses are treated as failures, not
  guessed at. Better to have a creator marked as failed than misclassified.
- Categorizations are append-only — each call adds a row to the database.
  History is preserved for analyzing prompt drift over time.
- The classifier never updates the creator's current_category field directly.
  That update happens via a database trigger or post-categorization step,
  honoring category_locked when manual corrections exist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog
from anthropic import Anthropic

from scout.categorize.prompts import (
    CURRENT_PROMPT_VERSION,
    build_classification_prompt,
)
from scout.config import get_settings
from scout.db import get_connection

logger = structlog.get_logger(__name__)


# Model configuration — Haiku is fast, cheap, and accurate for structured
# classification tasks like this. Cost is fractions of a cent per call.
MODEL_NAME = "claude-haiku-4-5"
MAX_OUTPUT_TOKENS = 500


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------


@dataclass
class CategorizationResult:
    """Result of categorizing a single creator."""

    creator_id: int
    success: bool
    category: str | None = None
    sub_archetype: str | None = None
    confidence: float | None = None
    reasoning: str | None = None
    error: str | None = None  # populated when success=False


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------


class CategorizationError(Exception):
    """Base for categorization-related errors."""


class MalformedResponse(CategorizationError):
    """Claude returned output we can't parse as the expected schema."""


# -----------------------------------------------------------------------------
# Classifier
# -----------------------------------------------------------------------------


class Classifier:
    """Claude-backed creator categorizer.

    One instance per process. Reuses an Anthropic client across calls so
    we benefit from connection pooling and don't pay setup overhead repeatedly.
    """

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or get_settings().anthropic_api_key
        self._client = Anthropic(api_key=key)

    def classify(
        self,
        creator_id: int,
        handle: str,
        bio: str | None,
        recent_captions: list[str],
    ) -> CategorizationResult:
        """Classify a single creator. Persists result to database on success.

        Failures (malformed response, API errors) return a result with
        success=False and an error message. They do NOT raise — callers can
        choose to skip and move on rather than aborting a batch run.
        """
        log = logger.bind(creator_id=creator_id, handle=handle)
        prompt = build_classification_prompt(handle, bio, recent_captions)

        try:
            response = self._client.messages.create(
                model=MODEL_NAME,
                max_tokens=MAX_OUTPUT_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            log.error("anthropic_call_failed", error=str(e))
            return CategorizationResult(
                creator_id=creator_id, success=False, error=f"api_error: {e}"
            )

        # Extract the text content from Claude's response.
        if not response.content:
            log.error("empty_response")
            return CategorizationResult(
                creator_id=creator_id, success=False, error="empty_response"
            )

        raw_text = response.content[0].text.strip()

        # Parse the JSON response.
        try:
            parsed = _parse_response(raw_text)
        except MalformedResponse as e:
            log.warning("malformed_response", raw=raw_text[:200], error=str(e))
            return CategorizationResult(
                creator_id=creator_id, success=False, error=f"malformed: {e}"
            )

        # Persist to database.
        try:
            _persist_categorization(
                creator_id=creator_id,
                category=parsed["category"],
                sub_archetype=parsed["sub_archetype"],
                confidence=parsed["confidence"],
                reasoning=parsed["reasoning"],
                input_bio=bio,
                input_captions=recent_captions,
            )
        except Exception as e:
            log.error("persist_failed", error=str(e))
            return CategorizationResult(
                creator_id=creator_id, success=False, error=f"persist_failed: {e}"
            )

        log.info(
            "classified",
            category=parsed["category"],
            sub_archetype=parsed["sub_archetype"],
            confidence=parsed["confidence"],
        )
        return CategorizationResult(
            creator_id=creator_id,
            success=True,
            category=parsed["category"],
            sub_archetype=parsed["sub_archetype"],
            confidence=parsed["confidence"],
            reasoning=parsed["reasoning"],
        )


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------


_VALID_CATEGORIES = {"sports_commentary", "grwm", "other"}


def _parse_response(raw: str) -> dict:
    """Parse Claude's JSON response and validate the schema.

    Tolerates a few common LLM quirks:
    - Markdown code fences around JSON (we strip them)
    - Leading/trailing whitespace
    - Single-line vs. multi-line JSON
    """
    # Strip markdown code fences if present. Claude is asked not to use them
    # but occasionally does anyway.
    text = raw.strip()
    if text.startswith("```"):
        # Remove the opening fence (with or without language tag).
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise MalformedResponse(f"not valid JSON: {e}") from e

    # Schema validation.
    required_fields = ("category", "sub_archetype", "confidence", "reasoning")
    for field in required_fields:
        if field not in parsed:
            raise MalformedResponse(f"missing field: {field}")

    if parsed["category"] not in _VALID_CATEGORIES:
        raise MalformedResponse(
            f"invalid category: {parsed['category']}"
        )

    try:
        confidence = float(parsed["confidence"])
    except (TypeError, ValueError) as e:
        raise MalformedResponse(f"confidence not numeric: {e}") from e

    if not 0.0 <= confidence <= 1.0:
        raise MalformedResponse(
            f"confidence out of range: {confidence}"
        )
    parsed["confidence"] = confidence

    return parsed


def _persist_categorization(
    creator_id: int,
    category: str,
    sub_archetype: str | None,
    confidence: float,
    reasoning: str,
    input_bio: str | None,
    input_captions: list[str],
) -> None:
    """Insert a row into the categorizations table.

    Append-only — never updates an existing row. Each call adds history.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO categorizations (
                    creator_id, category, sub_archetype, confidence,
                    reasoning, model_name, prompt_version,
                    input_bio, input_captions
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    creator_id,
                    category,
                    sub_archetype,
                    confidence,
                    reasoning,
                    MODEL_NAME,
                    CURRENT_PROMPT_VERSION,
                    input_bio,
                    json.dumps(input_captions),
                ),
            )

            # Also update creators.current_category — but only if NOT locked
            # by a prior manual correction. category_locked is the gate that
            # protects manual work from being overwritten by automated runs.
            cur.execute(
                """
                UPDATE creators
                   SET current_category = %s,
                       current_sub_archetype = %s,
                       updated_at = NOW()
                 WHERE id = %s AND category_locked = FALSE
                """,
                (category, sub_archetype, creator_id),
            )
