"""Cron-scheduled job entry points.

Each module is a script invoked on a schedule by the host's cron daemon.
Jobs are idempotent — re-running a job that partially completed should
either resume from where it left off or no-op on already-processed items.
"""
