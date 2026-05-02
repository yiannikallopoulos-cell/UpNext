"""Versioned prompt templates for categorization.

Prompts are versioned (v1, v2, ...) and the version is stored alongside
each categorization in the database. This lets us reason about category
drift across prompt revisions and detect whether a prompt change improved
or degraded classification quality.

Category definitions used in prompts:
- sports_commentary: creators who discuss, analyze, react to, or comment
  on professional or competitive sports. Includes game reactions, team
  news, player takes, fan culture, fantasy sports, sports media commentary.
  Does NOT include creators primarily filming themselves playing sports.
- grwm: get-ready-with-me, morning/night routine, day-in-the-life,
  what-I-eat-in-a-day, lifestyle aesthetic content. Sub-archetypes:
  aesthetic_grwm, voiceover_grwm, day_in_the_life.
- other: anything not clearly fitting the above categories.
"""
