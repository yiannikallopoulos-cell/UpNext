"""Scoring orchestrator.

For each scored creator: pulls their enriched data, runs each signal
function, computes the weighted composite score, applies label rules,
calculates overall confidence, and writes a single row to the scores table.

Re-runs after settlement events (when previously-pending posts cross the
14-day threshold and get final metrics) so scores stay current.
"""
