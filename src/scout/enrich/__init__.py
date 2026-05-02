"""Enrichment stage: fetches detailed profile and post-level data.

Runs after filtering and categorization on the surviving qualified set.
Pulls from Apify and writes to creator_snapshots, posts, and post_metrics tables.
"""
