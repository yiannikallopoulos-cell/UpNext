"""Versioned prompt templates for categorization.

Prompts are versioned (v1, v2, ...) and the version is stored alongside
each categorization in the database. This lets us reason about category
drift across prompt revisions and detect whether a prompt change improved
or degraded classification quality.

The current version is exposed via CURRENT_PROMPT_VERSION. When the prompt
changes meaningfully, bump that constant and the database will record
which version produced each categorization.
"""

from __future__ import annotations

CURRENT_PROMPT_VERSION = "v1"


# -----------------------------------------------------------------------------
# Category and sub-archetype definitions
# -----------------------------------------------------------------------------
# These definitions are the authoritative description of what each category
# means for this project. Edit carefully — changes shift what creators get
# bucketed where.

CATEGORY_DEFINITIONS = """
Categories:

1. sports_commentary
   Creators who DISCUSS, ANALYZE, REACT TO, or COMMENT ON sports — including
   game reactions, team news, player takes, fan culture, fantasy sports, and
   sports media commentary. The creator is talking ABOUT sports, not playing
   them.

   IMPORTANT: This category does NOT include creators who primarily film
   themselves doing athletic activities, working out, or playing sports.
   Those are athlete/fitness creators and should be classified as 'other'.

   Sub-archetypes (pick one):
   - analyst: produces breakdowns, predictions, structured analysis
   - fan_creator: posts as a fan of a specific team or player
   - aspirant_pro: trying to break into sports media as a career (host, reporter)
   - edit_creator: makes highlight edits, compilations, montage content
   - meme_creator: humor-driven sports content, memes, jokes

2. grwm
   Creators producing get-ready-with-me, day-in-the-life, morning/night routine,
   what-I-eat-in-a-day, lifestyle aesthetic content. The creator is showing
   their daily life, beauty routine, fashion, or aesthetic for an audience
   that comes for the personality and aesthetic.

   IMPORTANT: This category does NOT include businesses (cleaning services,
   product sellers, etc.) even if they post videos formatted like routines.
   Those are 'other'.

   Sub-archetypes (pick one):
   - aesthetic_grwm: visuals-first, voiceover-light, beauty-focused
   - voiceover_grwm: personality-driven, talking through the routine, storytelling
   - day_in_life_professional: full work day in life, professional context
   - study_lifestyle: student-focused, study vlogs, academic life
   - niche_lifestyle: specific lifestyle niche (fitness routine, mom life, etc.)

3. other
   Anything that doesn't clearly fit the above. Includes:
   - Athletes performing their sport
   - Businesses and brands
   - News aggregators or auto-repost accounts
   - Creators whose content is too varied to categorize
   - Languages we cannot evaluate from the provided text
   - Spam, bots, or accounts that don't appear to be content creators

   For 'other', the sub-archetype field can describe what they actually do
   (e.g., 'cleaning_business', 'news_aggregator', 'athlete_performance')
   or be left as 'unknown'.
"""


# -----------------------------------------------------------------------------
# The classification prompt
# -----------------------------------------------------------------------------


def build_classification_prompt(
    handle: str,
    bio: str | None,
    recent_captions: list[str],
) -> str:
    """Build the user message for a single creator categorization.

    Inputs are the minimal information the LLM needs: who the creator is,
    how they describe themselves, and a sample of what they actually post.
    """
    bio_text = bio.strip() if bio else "(empty)"

    if recent_captions:
        # Filter out empty captions and limit to 10. Truncate each to keep
        # the prompt compact — full captions can be long and we don't need
        # every word to classify.
        cleaned = [c.strip()[:300] for c in recent_captions if c and c.strip()]
        cleaned = cleaned[:10]
        if cleaned:
            captions_section = "\n".join(f"- {c}" for c in cleaned)
        else:
            captions_section = "(no caption text available)"
    else:
        captions_section = "(no recent captions provided)"

    return f"""You are classifying a TikTok creator into one of three categories for a talent scouting system.

{CATEGORY_DEFINITIONS}

Creator to classify:

Handle: @{handle}

Bio: {bio_text}

Recent post captions:
{captions_section}

Classify this creator. Return ONLY a JSON object with these exact fields:
- "category": one of "sports_commentary", "grwm", "other"
- "sub_archetype": one of the sub-archetypes listed for the chosen category, OR "unknown" if you cannot determine
- "confidence": a number between 0.0 and 1.0 representing your confidence in the category assignment (NOT the sub-archetype)
- "reasoning": a one-sentence explanation of your decision

Confidence guidance:
- 0.9-1.0: clearly fits the category, no ambiguity
- 0.7-0.9: fits the category but with some signals pointing elsewhere
- 0.5-0.7: borderline — multiple categories plausible
- below 0.5: uncertain, but provide your best guess

Return ONLY the JSON object. No markdown, no code fences, no preamble."""
