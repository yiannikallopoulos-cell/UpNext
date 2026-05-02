"""Database connection management.

Thin wrapper over psycopg providing a connection pool and context-managed
session helpers. Kept minimal — we don't need an ORM at this scale.

The pool is created lazily on first use. An atexit handler ensures it's
closed cleanly when the process exits, preventing the PythonFinalizationError
that occurs when the pool's background thread is still running at interpreter
shutdown.
"""

from __future__ import annotations

import atexit
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from scout.config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Return the singleton connection pool, creating it on first call.

    On first call, also registers an atexit handler to close the pool
    cleanly when the process exits.
    """
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row},
        )
        # Register cleanup so the pool's background thread shuts down
        # before Python's interpreter finalizes. Without this, we get
        # PythonFinalizationError noise on every script exit.
        atexit.register(close_pool)
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
    """Close the connection pool. Called automatically at process exit.

    Safe to call multiple times — second and subsequent calls are no-ops.
    """
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            # If the pool is already partially shut down, swallow errors —
            # we're exiting anyway and there's nothing useful to do.
            pass
        _pool = None