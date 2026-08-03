# Schema discovery for the lookup_metadata MCP tool.
# Returns role-filtered table/column metadata — the data-mesh "what exists" layer.
from collections import defaultdict

from app.db import get_pool
from app.policy import ROLE_POLICY


async def get_metadata(caller_role: str) -> dict:
    # Returns only tables the role can access; columns are labelled allowed/restricted
    # so the LLM knows a field exists even if it cannot see the values.
    role_policy = ROLE_POLICY.get(caller_role, {})
    visible_tables = list(role_policy.keys())

    if not visible_tables:
        return {"tables": [], "role": caller_role}

    pool = await get_pool()
    async with pool.acquire() as conn:
        col_rows = await conn.fetch(
            """
            SELECT table_name, column_name, data_type
            FROM   information_schema.columns
            WHERE  table_schema = 'public'
              AND  table_name   = ANY($1::text[])
            ORDER BY table_name, ordinal_position
            """,
            visible_tables,
        )

        # pg_class.reltuples is a fast approximate count updated by ANALYZE —
        # no table scan, unlike COUNT(*), so safe to call on large tables.
        count_rows = await conn.fetch(
            """
            SELECT relname AS table_name,
                   greatest(reltuples::bigint, 0) AS approx_rows
            FROM   pg_class
            WHERE  relname = ANY($1::text[])
              AND  relkind = 'r'
            """,
            visible_tables,
        )

    row_counts = {r["table_name"]: r["approx_rows"] for r in count_rows}

    cols_by_table: dict[str, list] = defaultdict(list)
    for r in col_rows:
        tname = r["table_name"]
        cname = r["column_name"]
        allowed_cols = role_policy.get(tname, frozenset())
        cols_by_table[tname].append({
            "name": cname,
            "type": r["data_type"],
            "access": "allowed" if cname in allowed_cols else "restricted",
        })

    return {
        "role": caller_role,
        "tables": [
            {
                "table": t,
                "approx_rows": row_counts.get(t, 0),
                "columns": cols_by_table.get(t, []),
            }
            for t in visible_tables
        ],
    }
