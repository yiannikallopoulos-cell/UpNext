"""Filter rules.

Each rule is a pure function taking creator candidate data and returning
a (passed, reason) tuple. The filter pipeline runs all rules and keeps
only candidates passing every rule.

Rules:
- in_follower_band: 5K <= followers <= 50K
- recently_active: posted in last 30 days
- has_post_history: at least 10 posts
- not_obvious_bot: heuristic check on follower/following ratio,
  rounded follower counts at suspicious values, missing profile data
"""
