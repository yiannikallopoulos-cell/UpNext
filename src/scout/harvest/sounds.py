"""Sound-based discovery channel.

Surfaces creators using trending audio in target categories. Implementation
will call Apify's TikTok-by-sound actor for each target sound ID and return
unique creator handles with their post-context metadata.

Sound IDs are seeded manually for run 1, then derived from prior harvest data
for subsequent runs (sounds appearing frequently in high-performing posts of
known creators become seeds for the next harvest).
"""
