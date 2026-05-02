"""Database connection management.

Thin wrapper over psycopg providing a connection pool and context-managed
session helpers. Kept minimal — we don't need an ORM at this scale.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from scout.config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Return the singleton connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row},
        )
    return _pool


@contextmanager
def get_connection() -> Iterator[psycopg.Connection[Any]]:
    """Context manager yielding a pooled database connection.

    Connection is automatically returned to the pool on exit. Transactions
    are committed if the block exits cleanly, rolled back if an exception
    propagates.
    """
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


@contextmanager
def get_cursor() -> Iterator[psycopg.Cursor[Any]]:
    """Context manager yielding a cursor with auto-commit semantics on the pool connection."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            yield cur


def close_pool() -> None:
    """Close the connection pool. Call at process shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
