# Data exploration: schema, sample rows, and stats for a table.
# Applies the same role-based masking as the MCP tools so governance is consistent
# across every code path that returns data.
from typing import Any

from app.db import get_pool
from app.policy import ROLE_POLICY, mask_rows


async def explore_table(table: str, caller_role: str) -> dict:
    role_policy = ROLE_POLICY.get(caller_role)
    if role_policy is None:
        raise ValueError(f"Unknown role: {caller_role!r}")

    # Table name is validated against ROLE_POLICY before it ever reaches a query,
    # so the f-string interpolation below is safe — no untrusted input reaches the DB.
    if table not in role_policy:
        raise PermissionError(
            f"Role '{caller_role}' is not permitted to access table '{table}'."
        )

    allowed_cols = role_policy[table]

    pool = await get_pool()
    async with pool.acquire() as conn:
        col_rows = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM   information_schema.columns
            WHERE  table_schema = 'public' AND table_name = $1
            ORDER  BY ordinal_position
            """,
            table,
        )

        columns = [
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "nullable": r["is_nullable"] == "YES",
                "access": "allowed" if r["column_name"] in allowed_cols else "restricted",
            }
            for r in col_rows
        ]

        # COUNT(*) here (not pg_class.reltuples) because the Explore UI shows exact
        # row counts that users compare against query results — approximations would confuse.
        row_count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")  # noqa: S608

        sample_raw = await conn.fetch(f"SELECT * FROM {table} LIMIT 8")  # noqa: S608
        sample = mask_rows(caller_role, {table}, [dict(r) for r in sample_raw])

        stats: dict[str, Any] = {}
        for col in columns:
            cname = col["name"]
            if col["access"] == "restricted":
                stats[cname] = {"note": "restricted"}
                continue

            dtype = col["type"]
            if dtype in ("integer", "bigint", "numeric", "real", "double precision"):
                agg = await conn.fetchrow(
                    f'SELECT MIN("{cname}") as mn, MAX("{cname}") as mx, '  # noqa: S608
                    f'ROUND(AVG("{cname}")::numeric, 2) as avg FROM {table}'
                )
                stats[cname] = {
                    "min": str(agg["mn"]) if agg["mn"] is not None else None,
                    "max": str(agg["mx"]) if agg["mx"] is not None else None,
                    "avg": str(agg["avg"]) if agg["avg"] is not None else None,
                }
            elif dtype == "text":
                distinct = await conn.fetchval(
                    f'SELECT COUNT(DISTINCT "{cname}") FROM {table}'  # noqa: S608
                )
                # ≤10 distinct values → show the full distribution (e.g. tier, status).
                # >10 → just the cardinality, to avoid bloating the response.
                if distinct <= 10:
                    vals = await conn.fetch(
                        f'SELECT "{cname}", COUNT(*) as n FROM {table} '  # noqa: S608
                        f'GROUP BY "{cname}" ORDER BY n DESC'
                    )
                    stats[cname] = {
                        "distinct": distinct,
                        "values": [{"value": r[cname], "count": r["n"]} for r in vals],
                    }
                else:
                    stats[cname] = {"distinct": distinct}
            elif "timestamp" in dtype or "date" in dtype:
                agg = await conn.fetchrow(
                    f'SELECT MIN("{cname}") as mn, MAX("{cname}") as mx FROM {table}'  # noqa: S608
                )
                stats[cname] = {
                    "earliest": agg["mn"].isoformat() if agg["mn"] else None,
                    "latest":   agg["mx"].isoformat() if agg["mx"] else None,
                }

    return {
        "table": table,
        "role": caller_role,
        "row_count": row_count,
        "columns": columns,
        # Serialise all values to str so timestamps and Decimals don't break JSON.
        "sample": [{k: (str(v) if v is not None else None) for k, v in row.items()} for row in sample],
        "stats": stats,
    }


async def explore_all(caller_role: str) -> dict:
    # Overview only — no stats or sample rows, so it returns fast for the tab initial load.
    role_policy = ROLE_POLICY.get(caller_role, {})
    tables = list(role_policy.keys())

    pool = await get_pool()
    async with pool.acquire() as conn:
        counts = {t: await conn.fetchval(f"SELECT COUNT(*) FROM {t}") for t in tables}  # noqa: S608

    return {
        "role": caller_role,
        "tables": [
            {"name": t, "row_count": counts[t], "accessible_columns": len(role_policy[t])}
            for t in tables
        ],
    }
