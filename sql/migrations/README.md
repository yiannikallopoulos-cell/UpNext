# Schema migrations

Future schema changes go here as numbered files: `001_add_notes_table.sql`,
`002_add_creator_tags.sql`, etc.

The initial schema lives in `../schema.sql` and is applied as the baseline.
Migrations apply incremental changes on top.

Each migration should be:
- Idempotent where possible (use `IF NOT EXISTS`, `IF EXISTS`)
- Reversible via a paired `_down.sql` file
- Tested against a copy of production-shaped data before deployment
