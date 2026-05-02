"""Harvest coordinator.

Runs all enabled discovery channels in parallel, dedupes results by
TikTok user ID, and writes discovery_events rows tracking which channels
surfaced each candidate creator. Multi-channel discovery is itself a soft
positive signal (a creator surfaced through three channels is a stronger
candidate than one surfaced through one).
"""
