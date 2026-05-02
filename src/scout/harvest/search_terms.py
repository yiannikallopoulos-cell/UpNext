"""Search term and team list configuration for the search harvest channel.

These lists define what UpNext considers "category-defining" content.
Edit freely — changes here flow into the next discovery run automatically.

A few principles applied to these lists:

- Sports terms favor commentary/reaction over performance, since we are
  scouting opinion creators rather than athletes.
- Team-specific phrases use "<team> reaction" and "<team> tonight" because
  those phrases catch fan-creator content around game nights, which is the
  archetype we want.
- GRWM terms are deliberately conversational — TikTok's content recognition
  hits transcribed audio and on-screen text, so caption-light creators are
  surfaced via what they actually say in the video.
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# Team lists
# -----------------------------------------------------------------------------
# Trimmed to the top-tier franchises in each league by social media volume.
# Expand later if discovery yield from neighboring teams looks promising.

NBA_TEAMS = [
    "lakers",
    "warriors",
    "celtics",
    "knicks",
    "heat",
    "mavericks",
]

NFL_TEAMS = [
    "cowboys",
    "eagles",
    "chiefs",
    "49ers",
    "steelers",
    "patriots",
]

EPL_AND_TOP_EUROPEAN_TEAMS = [
    "arsenal",
    "manchester united",
    "liverpool",
    "real madrid",
    "barcelona",
]

ALL_TEAMS = NBA_TEAMS + NFL_TEAMS + EPL_AND_TOP_EUROPEAN_TEAMS


# -----------------------------------------------------------------------------
# Generic sports commentary terms
# -----------------------------------------------------------------------------
# League-level and meta phrases that surface analyst-archetype creators.
# These complement the team-specific terms (which surface fan-archetype creators).

GENERIC_SPORTS_TERMS = [
    "nba reaction",
    "nba take",
    "nfl breakdown",
    "premier league reaction",
    "soccer take",
    "trade deadline reaction",
    "nfl draft analysis",
    "fantasy football",
    "sports debate",
    "sports take",
]


# -----------------------------------------------------------------------------
# GRWM and day-in-the-life terms
# -----------------------------------------------------------------------------
# Format-defining phrases that creators say (in voiceover, captions, or
# on-screen text). Catches creators with sparse or no caption hashtags.

GRWM_TERMS = [
    "get ready with me",
    "grwm",
    "morning routine",
    "night routine",
    "day in my life",
    "what i eat in a day",
    "5 to 9 routine",
    "outfit of the day",
    "skincare routine",
    "productive day",
]


# -----------------------------------------------------------------------------
# Term generation
# -----------------------------------------------------------------------------
# Functions to assemble the full search-term lists by category.
# Kept as functions (not module-level constants) so future logic — e.g.,
# rotating teams in/out by season — has somewhere to live.


def sports_search_terms() -> list[str]:
    """All sports search terms: team-specific + generic commentary."""
    team_terms = []
    for team in ALL_TEAMS:
        team_terms.append(f"{team} reaction")
        team_terms.append(f"{team} tonight")
    return team_terms + GENERIC_SPORTS_TERMS


def grwm_search_terms() -> list[str]:
    """All GRWM / day-in-the-life search terms."""
    return list(GRWM_TERMS)


def all_search_terms() -> dict[str, list[str]]:
    """All search terms grouped by category.

    Returns a dict mapping category name -> list of search terms.
    Category names match the category_t enum values in the database
    (sports_commentary, grwm), so harvest results can be tagged correctly.
    """
    return {
        "sports_commentary": sports_search_terms(),
        "grwm": grwm_search_terms(),
    }
