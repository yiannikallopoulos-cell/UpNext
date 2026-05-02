"""Settle posts: scheduled daily.

Finds posts that have crossed the 14-day post-age threshold and have not
yet been marked settled. Pulls a final metric scrape for each, writes a
final post_metrics row, and marks the post as settled. After settlement,
post metrics are immutable and engagement-rate signals can incorporate them.

Triggers re-scoring on creators whose posts settled in this run.
"""
