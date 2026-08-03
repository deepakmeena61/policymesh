# MCP 2.0 server — exposes three governed tools over an SSE transport.
# External MCP clients connect at GET /mcp/sse and POST requests to /mcp/messages/.
import json

from mcp.server.mcpserver.server import MCPServer

import app.audit as audit
from app.db import get_pool
from app.docs import build_context, search_raw
from app.metadata import get_metadata
from app.policy import AccessDenied, check_access, mask_rows, real_tables
from app.sql_validator import ValidationError, parse, validate_and_run

mcp = MCPServer("policymesh-mcp", version="0.1.0")


@mcp.tool(
    description=(
        "Run a read-only SELECT query against the data warehouse. "
        "Returns rows as a JSON array. "
        "Caller must supply their role; the server enforces table/column access "
        "and masks PII before returning results. "
        "Only SELECT statements are accepted."
    )
)
async def query_sql(
    sql: str,
    caller_role: str,
    params: list[str | int | float] | None = None,
) -> str:
    args = {"sql": sql, "params": params}
    async with audit.log_call(caller_role=caller_role, tool="query_sql", input_data=args) as ctx:
        try:
            stmt = parse(sql)
            check_access(caller_role, stmt)
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await validate_and_run(conn, sql, params, parsed=stmt)
            rows = mask_rows(caller_role, real_tables(stmt), rows)
            ctx["row_count"] = len(rows)
        except (ValidationError, AccessDenied) as exc:
            ctx["error"] = str(exc)
            return json.dumps({"error": str(exc)})
    return json.dumps(rows, default=str)


@mcp.tool(
    description=(
        "Search the documentation knowledge base for context about products, "
        "policies, SLAs, and processes. Returns the most relevant chunks with "
        "numbered source citations. Use for 'how does X work' or 'what is the "
        "policy on Y' questions rather than quantitative data questions."
    )
)
async def search_docs(query: str, k: int = 4) -> str:
    args = {"query": query, "k": k}
    # caller_role is unknown at the MCP transport level without bearer-token auth;
    # "mcp_client" is a placeholder so the audit row is always populated.
    async with audit.log_call(caller_role="mcp_client", tool="search_docs", input_data=args) as ctx:
        chunks = await search_raw(query, k)
        ctx["row_count"] = len(chunks)
    return build_context(chunks)


@mcp.tool(
    description=(
        "Return the schema of the data warehouse visible to the caller's role: "
        "tables, columns with types, approximate row counts, and which columns "
        "are restricted by access policy. Call this before writing SQL to know "
        "exactly what data is available. Columns marked 'restricted' exist but "
        "their values are masked for your role."
    )
)
async def lookup_metadata(caller_role: str) -> str:
    async with audit.log_call(caller_role=caller_role, tool="lookup_metadata", input_data={}) as ctx:
        meta = await get_metadata(caller_role)
        ctx["row_count"] = len(meta.get("tables", []))
    return json.dumps(meta, default=str)
