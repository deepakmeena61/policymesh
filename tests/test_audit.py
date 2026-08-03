"""
Integration tests for audit logging.
Hits the real Neon DB — requires DATABASE_URL in .env.
All tests share one event loop (session scope) so the asyncpg pool is reused.
"""

import json

import pytest
import pytest_asyncio
from dotenv import load_dotenv

load_dotenv()

import app.audit as audit
from app.agent import _execute_tool
from app.db import get_pool


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _db_setup():
    """Create audit_log once; record a pre-test timestamp; clean up after."""
    await audit.ensure_table()

    # Snapshot the clock so teardown can delete only rows this test created.
    pool = await get_pool()
    async with pool.acquire() as conn:
        cutoff = await conn.fetchval("SELECT now()")

    yield

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM audit_log WHERE ts >= $1", cutoff)


async def test_audit_record_written_on_success():
    """A successful query_sql call writes one audit row with no error."""
    await _execute_tool(
        "query_sql",
        '{"sql": "SELECT id FROM customers LIMIT 2"}',
        "analyst",
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE tool = 'query_sql' ORDER BY ts DESC LIMIT 1"
        )

    assert row is not None
    assert row["tool"] == "query_sql"
    assert row["caller_role"] == "analyst"
    assert row["error"] is None
    assert row["row_count"] == 2
    assert row["duration_ms"] >= 0


async def test_audit_record_written_on_error():
    """An access-denied error still writes an audit row recording the failure."""
    await _execute_tool(
        "query_sql",
        '{"sql": "SELECT id FROM customers"}',
        "viewer",   # viewer cannot read customers
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE caller_role = 'viewer' ORDER BY ts DESC LIMIT 1"
        )

    assert row is not None
    assert row["error"] is not None
    assert "not permitted" in row["error"]
    assert row["row_count"] is None  # error fired before any rows were fetched


async def test_audit_does_not_store_result_content():
    """
    Result rows — which may contain PII — must NOT appear in the audit log.
    Only metadata (row_count, error, input SQL) is persisted.
    """
    await _execute_tool(
        "query_sql",
        '{"sql": "SELECT name, email FROM customers LIMIT 1"}',
        "admin",
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE caller_role = 'admin' ORDER BY ts DESC LIMIT 1"
        )

    assert row is not None
    raw = str(dict(row))  # str handles datetime columns; json.dumps doesn't
    # input_json has the SQL (fine) but must not contain returned values
    assert "alice@example.com" not in raw
    assert "Alice Martin" not in raw


async def test_audit_search_docs_recorded():
    """search_docs calls are audited the same way as query_sql."""
    await _execute_tool(
        "search_docs",
        '{"query": "enterprise SLA"}',
        "analyst",
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE tool = 'search_docs' ORDER BY ts DESC LIMIT 1"
        )

    assert row is not None
    assert row["tool"] == "search_docs"
    assert row["row_count"] is not None and row["row_count"] > 0
    assert row["error"] is None
