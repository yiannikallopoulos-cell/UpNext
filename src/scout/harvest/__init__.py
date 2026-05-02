"""Discovery harvest stage.

Surfaces candidate TikTok creators via four channels — sounds, search terms,
hashtags, and neighbors-of-known. Each channel is implemented as a separate
module with a uniform interface; the coordinator unions and dedupes results.
"""
