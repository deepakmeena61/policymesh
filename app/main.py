# FastAPI application entry point — routes, lifespan, and startup validation.
import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

import app.audit as audit
from app.agent import run_agent
from app.db import close_pool, get_pool
from app.explore import explore_all, explore_table
from app.mcp_server import mcp
from app.policy import ROLE_POLICY

_STATIC = Path(__file__).parent / "static"

load_dotenv()

# Fail loudly at import time rather than with a cryptic AttributeError mid-request.
_REQUIRED_ENV = ["DATABASE_URL", "GROQ_API_KEY", "GOOGLE_API_KEY"]
_missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
if _missing:
    raise RuntimeError(
        f"Missing required environment variables: {_missing}\n"
        f"Copy .env.example to .env and fill in the values."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the pool on startup so the first request doesn't pay connection overhead.
    # ensure_table is idempotent — safe to call on every deploy.
    await get_pool()
    await audit.ensure_table()
    yield
    await close_pool()


app = FastAPI(title="PolicyMesh — Governed Data Intelligence", lifespan=lifespan)

# mcp.sse_app() returns a Starlette ASGI app mounted at /mcp.
# GET  /mcp/sse       — client opens SSE stream, receives endpoint URL
# POST /mcp/messages/ — client posts JSON-RPC requests here
app.mount("/mcp", mcp.sse_app())


class AskRequest(BaseModel):
    question: str
    caller_role: str


# 60s default covers multi-step agent loops; override via ASK_TIMEOUT_SECONDS.
# Without this, a hung Groq connection blocks a FastAPI worker indefinitely.
_ASK_TIMEOUT = int(os.getenv("ASK_TIMEOUT_SECONDS", "60"))


@app.post("/ask")
async def ask(req: AskRequest):
    # Reject an unknown role before spending a single LLM call: without this, an
    # invalid caller_role silently burns the full MAX_STEPS budget on lookup_metadata
    # calls that return nothing useful (empty tables), then produces a garbled answer.
    if req.caller_role not in ROLE_POLICY:
        return {
            "answer": f"Unknown role {req.caller_role!r}. Valid roles: {', '.join(sorted(ROLE_POLICY))}.",
            "steps": [],
            "stopped_reason": "error:unknown_role",
            "step_count": 0,
        }
    try:
        return await asyncio.wait_for(
            run_agent(req.question, req.caller_role),
            timeout=_ASK_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "answer": f"Request timed out after {_ASK_TIMEOUT}s. Try a simpler question.",
            "steps": [],
            "stopped_reason": "error:timeout",
            "step_count": 0,
        }


@app.get("/")
async def index():
    return FileResponse(_STATIC / "index.html")


@app.get("/explore")
async def explore_overview(role: str = "analyst"):
    """All tables visible to role with row counts."""
    return await explore_all(role)


@app.get("/explore/{table}")
async def explore_one(table: str, role: str = "analyst"):
    """Schema, sample rows, and stats for a single table — same masking as MCP tools."""
    start = time.monotonic()
    try:
        result = await explore_table(table, role)
        await audit.record(
            caller_role=role, tool="explore",
            input_data={"table": table},
            row_count=result["row_count"], error=None,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        return result
    except PermissionError as e:
        await audit.record(
            caller_role=role, tool="explore",
            input_data={"table": table},
            row_count=None, error=str(e),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        return {"error": str(e)}


@app.get("/audit")
async def audit_log(limit: int = Query(default=50, ge=1, le=500)):
    """Recent tool calls from the audit log — newest first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, ts, caller_role, tool, input_json, row_count, error, duration_ms
            FROM   audit_log
            ORDER  BY ts DESC
            LIMIT  $1
            """,
            limit,
        )
    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat(),
            "caller_role": r["caller_role"],
            "tool": r["tool"],
            "input": r["input_json"],
            "row_count": r["row_count"],
            "error": r["error"],
            "duration_ms": r["duration_ms"],
        }
        for r in rows
    ]


@app.get("/health")
async def health():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok", "db": "connected"}
