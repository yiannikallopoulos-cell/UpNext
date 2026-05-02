"""Filter stage: applies follower-band, activity, and quality filters.

Drops candidates that fail any rule. Failed candidates are logged for
diagnostics but not stored in the creators table.
"""
