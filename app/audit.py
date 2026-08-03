# Append-only audit trail: every tool call is recorded regardless of outcome.
# Logs metadata only (row_count, error) — never result content, which may contain PII.
# Two outputs: JSON to stdout for log aggregators, and a durable DB INSERT for compliance.
import json
import logging
import time
from contextlib import asynccontextmanager

from app.db import get_pool

logger = logging.getLogger("audit")

_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL    PRIMARY KEY,
    ts          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    caller_role TEXT         NOT NULL,
    tool        TEXT         NOT NULL,
    input_json  JSONB,
    row_count   INTEGER,
    error       TEXT,
    duration_ms INTEGER      NOT NULL
);

-- Supports ORDER BY ts DESC on /audit and time-range compliance queries.
CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log (ts DESC);
"""


async def ensure_table() -> None:
    # Called once at app startup; idempotent due to IF NOT EXISTS.
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_DDL)


async def record(
    *,
    caller_role: str,
    tool: str,
    input_data: dict,
    row_count: int | None,
    error: str | None,
    duration_ms: int,
) -> None:
    # row_count (not result content) is enough to detect anomalous bulk-access patterns
    # without persisting PII that belongs only in the caller's response.
    logger.info(json.dumps({
        "event": "tool_call",
        "caller_role": caller_role,
        "tool": tool,
        "row_count": row_count,
        "error": error,
        "duration_ms": duration_ms,
    }))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO audit_log (caller_role, tool, input_json, row_count, error, duration_ms)
            VALUES ($1, $2, $3::jsonb, $4, $5, $6)
            """,
            caller_role, tool, json.dumps(input_data), row_count, error, duration_ms,
        )


@asynccontextmanager
async def log_call(*, caller_role: str, tool: str, input_data: dict):
    # Context manager so audit fires in finally — even when the tool raises.
    # Caller sets ctx["row_count"] after a successful result; ctx["error"] on failure.
    ctx: dict = {"row_count": None, "error": None}
    start = time.monotonic()
    try:
        yield ctx
    except Exception as exc:
        ctx["error"] = str(exc)
        raise
    finally:
        await record(
            caller_role=caller_role,
            tool=tool,
            input_data=input_data,
            row_count=ctx["row_count"],
            error=ctx["error"],
            duration_ms=int((time.monotonic() - start) * 1000),
        )
