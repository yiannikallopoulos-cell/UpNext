"""Discovery run: scheduled every ~3 weeks.

Runs the harvest coordinator across all enabled channels, applies the filter
stage to surviving candidates, calls the categorization stage on filtered
candidates, and writes new creators to the database. Then triggers an
initial enrichment scrape on the new arrivals.
"""
