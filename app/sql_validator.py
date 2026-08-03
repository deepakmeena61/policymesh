# Four-guard SQL pipeline: parse → allowlist → dry-run → read-only execute.
# Each guard is a hard stop — raising ValidationError aborts the chain.
# Governance is enforced here in code, not in the prompt.
import sqlglot
from sqlglot import exp


class ValidationError(ValueError):
    pass


# Only tables/columns in these dicts can appear in any query.
# A new data source must be explicitly added — accidental access is impossible.
ALLOWED_TABLES: frozenset[str] = frozenset(
    {"customers", "orders", "products", "events", "tickets"}
)

ALLOWED_COLUMNS: dict[str, frozenset[str]] = {
    "customers": frozenset({"id", "name", "email", "tier"}),
    "orders":    frozenset({"id", "customer_id", "product", "amount", "created_at"}),
    "products":  frozenset({"id", "name", "category", "price", "description"}),
    "events":    frozenset({"id", "customer_id", "event_type", "occurred_at"}),
    "tickets":   frozenset({"id", "customer_id", "subject", "status", "priority", "created_at", "resolved_at"}),
}

# Flat union used to validate unqualified column references (e.g. `SELECT amount FROM ...`).
_ALL_COLUMNS: frozenset[str] = frozenset().union(*ALLOWED_COLUMNS.values())


def parse(sql: str) -> exp.Select:
    # Guard 1 — cheapest gate, no network. SQLGlot builds a typed AST so we can
    # walk nodes reliably; regex cannot handle subqueries, aliases, or quoted identifiers.
    # Rejects DELETE/UPDATE/INSERT/DDL before anything touches the DB.
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.ParseError as exc:
        raise ValidationError(f"SQL parse error: {exc}") from exc

    if len(statements) != 1:
        raise ValidationError("Exactly one statement is required.")

    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        raise ValidationError(
            f"Only SELECT statements are allowed; got {type(stmt).__name__}."
        )
    return stmt


def _alias_map(stmt: exp.Select) -> dict[str, str]:
    # Maps table alias → real name so `SELECT c.id FROM customers c` resolves correctly.
    return {
        table.alias.lower(): table.name.lower()
        for table in stmt.find_all(exp.Table)
        if table.alias
    }


def _scope_aliases(stmt: exp.Select) -> frozenset[str]:
    """
    Names that are safe to use as unqualified column references because they are
    defined *within* this query's own scope — not looked up in any real table.

    Two sources:
    1. SELECT-clause aliases: `SUM(amount) AS revenue` → 'revenue' is valid in
       ORDER BY, GROUP BY, HAVING, and window ORDER BY clauses.
    2. CTE output columns: `WITH x AS (SELECT product, SUM(amount) AS rev …)`
       → 'rev' (alias) and 'product' (bare column) are both valid in the outer SELECT.

    Why this matters: find_all(exp.Column) is recursive and returns Column nodes
    from ORDER BY, HAVING, etc. Without this set, we block valid queries like
    `ORDER BY total_revenue` where total_revenue is a SELECT alias, not a schema column.

    Security note: this does NOT fully prevent alias-based PII leakage
    (e.g. `SELECT email AS contact` bypasses mask_rows because the result column
    is named 'contact', not 'email'). Production-grade mitigation is a read-only
    DB role with column-level GRANT restrictions, so the DB itself refuses the query.
    That is independent of application-layer masking.
    """
    aliases: set[str] = set()

    # SELECT clause of the outermost query — ONLY computed aliases (expr AS name).
    # Bare column refs (SELECT product) are already validated against _ALL_COLUMNS;
    # adding them here would let unknown column names bypass the schema check.
    for expr in stmt.expressions:
        if isinstance(expr, exp.Alias):
            aliases.add(expr.alias.lower())

    # CTE bodies — only immediate WITH clause, not nested subquery CTEs.
    # Note: SQLGlot stores the WITH clause under "with_" (not "with") because
    # "with" is a Python reserved keyword. Same rule: only alias names.
    with_clause = stmt.args.get("with_")
    if with_clause:
        for cte in with_clause.expressions:
            inner = cte.this
            if isinstance(inner, exp.Select):
                for expr in inner.expressions:
                    if isinstance(expr, exp.Alias):
                        aliases.add(expr.alias.lower())

    return frozenset(aliases)


def check_allowlist(stmt: exp.Select) -> None:
    # Guard 2 — walks the full AST including subqueries (find_all is recursive).
    # CTE aliases are excluded because they are virtual; their bodies are already walked.
    cte_names  = {cte.alias.lower() for cte in stmt.find_all(exp.CTE)}
    alias_map  = _alias_map(stmt)
    scope_safe = _scope_aliases(stmt)

    bad = {t.name.lower() for t in stmt.find_all(exp.Table)} - cte_names - ALLOWED_TABLES
    if bad:
        raise ValidationError(f"Table(s) not in allowlist: {sorted(bad)}")

    for col in stmt.find_all(exp.Column):
        col_name   = col.name.lower()
        table_ref  = col.table.lower() if col.table else None
        # Resolve alias to the real table name before the allowlist lookup.
        real_table = alias_map.get(table_ref, table_ref) if table_ref else None

        if real_table and real_table in ALLOWED_COLUMNS:
            # Qualified reference to a known table: check the column exists there.
            if col_name not in ALLOWED_COLUMNS[real_table]:
                raise ValidationError(
                    f"Column '{col_name}' not in allowlist for table '{real_table}'."
                )
        elif real_table is None and col_name not in _ALL_COLUMNS:
            # Unqualified reference: must be a known schema column OR a scope alias.
            # Scope aliases (SELECT/CTE aliases) are validated at their definition
            # site — blocking them here would reject valid SQL like ORDER BY alias.
            if col_name not in scope_safe:
                raise ValidationError(f"Column '{col_name}' not in allowlist.")
        # else: real_table is set but not a known table (e.g. a subquery alias like 's'
        # in `SELECT s.rev FROM (SELECT ...) s`). The subquery's inner columns are
        # validated by the recursive find_all, so this case is already covered.


async def dry_run(conn, sql: str, params: list) -> None:
    # Guard 3 — one network round-trip, but runs after the free in-process guards.
    # EXPLAIN catches things SQLGlot misses: type mismatches, ambiguous references,
    # Postgres syntax the parser accepted but the planner rejects. No data moves.
    try:
        await conn.execute(f"EXPLAIN {sql}", *params)
    except Exception as exc:
        raise ValidationError(f"Query dry-run failed: {exc}") from exc


async def execute_readonly(conn, sql: str, params: list) -> list[dict]:
    # Guard 4 — SET TRANSACTION READ ONLY blocks writes at the engine level,
    # including data-modifying CTEs (INSERT … RETURNING) that are SELECT at the top.
    async with conn.transaction():
        await conn.execute("SET TRANSACTION READ ONLY")
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


async def validate_and_run(
    conn,
    sql: str,
    params: list[str | int | float] | None = None,
    *,
    parsed: exp.Select | None = None,
) -> list[dict]:
    # Accept a pre-parsed AST to avoid double-parsing when the caller already ran parse()
    # (e.g. to pass the same stmt to check_access and validate_and_run).
    params = params or []
    stmt = parsed if parsed is not None else parse(sql)
    check_allowlist(stmt)
    await dry_run(conn, sql, params)
    return await execute_readonly(conn, sql, params)
