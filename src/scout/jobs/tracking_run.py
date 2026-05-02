"""Tracking run: scheduled daily.

Determines which creators are due for tracking based on their lifecycle
tier and last_scraped_at timestamp. Scrapes due creators, writes new
snapshots and post deltas, triggers re-scoring for affected creators.

Tier cadences (defined in config.py):
- watchlist: 7 days
- active: 14 days
- long_tail: 30 days
- graduated: 7 days (follower-only, lightweight)
"""
