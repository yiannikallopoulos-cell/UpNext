# Dashboard

Next.js + Tailwind frontend for browsing creators, reviewing categorizations,
and inspecting score trajectories.

To be implemented after the backend pipeline produces real data. Will read
directly from the Supabase Postgres database.

Planned views:
- Creator list: ranked by score within label tiers, filterable by category
  and lifecycle state
- Creator detail: profile, score history chart, post-level metric trends,
  pattern label explanation, manual category override
- Alumni track: graduated creators with their post-graduation follower
  growth visualized — the proof-of-concept asset
- Channel yield: discovery channel performance over time, used to tune
  harvest cadence and weighting
