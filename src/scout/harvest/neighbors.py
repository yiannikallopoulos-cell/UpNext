"""Neighbors-of-known discovery channel.

Graph traversal through the social neighborhoods of creators already in
the system. Pulls top commenters on recent posts, duet/stitch partners,
and TikTok's "similar creators" suggestions.

Disabled at run 1 (no seeds yet). Activated from run 2 onward.

Includes seed-diversity guard: no single seed creator contributes more than
~5% of harvest output, preventing echo-chamber drift into a single sub-niche.
"""
