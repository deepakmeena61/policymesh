# Shared asyncpg connection pool — one pool per process, reused across all requests.
import os

import asyncpg

# Module-level singleton; None until first call to get_pool().
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    # Lazy init: pool is created on first request, not at import time.
    # This keeps tests that don't need a DB from requiring DATABASE_URL.
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    return _pool


async def close_pool() -> None:
    # Reset to None so the next get_pool() call re-initialises cleanly (e.g. in tests).
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
