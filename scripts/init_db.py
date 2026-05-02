"""Initialize the database schema.

Applies sql/schema.sql to the configured database. Safe to run on an empty
database; will produce errors on a populated one if the schema has drifted
(those errors are informative — they tell you what's already there).

Usage:
    python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from scout.db import get_connection


def main() -> int:
    schema_path = Path(__file__).parent.parent / "sql" / "schema.sql"
    if not schema_path.exists():
        print(f"ERROR: Schema file not found at {schema_path}")
        return 1

    schema_sql = schema_path.read_text()
    print(f"Applying schema from {schema_path} ({len(schema_sql):,} chars)")

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
            conn.commit()
    except Exception as e:
        print(f"ERROR applying schema: {e}")
        return 2

    print("Schema applied successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
