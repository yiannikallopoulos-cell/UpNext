"""LLM categorization classifier.

Takes a creator's bio and recent caption texts, returns a category,
sub-archetype, confidence score (0-1), and reasoning. Uses Claude via
the Anthropic API.

Outputs are written to the categorizations table append-only.
"""
