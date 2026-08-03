# All access-control decisions live here and nowhere else.
# Tool handlers call check_access() before the DB, then mask_rows() after.
from sqlglot import exp


class AccessDenied(PermissionError):
    pass


# Every permission grant is an explicit line in this dict — omissions are governance decisions.
# Adding a role or column requires editing here, making changes auditable in version control.
ROLE_POLICY: dict[str, dict[str, frozenset[str]]] = {
    "admin": {
        "customers": frozenset({"id", "name", "email", "tier"}),
        "orders":    frozenset({"id", "customer_id", "product", "amount", "created_at"}),
        "products":  frozenset({"id", "name", "category", "price", "description"}),
        "events":    frozenset({"id", "customer_id", "event_type", "occurred_at"}),
        "tickets":   frozenset({"id", "customer_id", "subject", "status", "priority", "created_at", "resolved_at"}),
    },
    "analyst": {
        "customers": frozenset({"id", "name", "tier"}),           # email excluded — PII
        "orders":    frozenset({"id", "customer_id", "product", "amount", "created_at"}),
        "products":  frozenset({"id", "name", "category", "price", "description"}),
        "events":    frozenset({"id", "customer_id", "event_type", "occurred_at"}),
        "tickets":   frozenset({"id", "customer_id", "status", "priority", "created_at", "resolved_at"}),
        # tickets.subject excluded — free-text field may contain customer-identifying detail
    },
    "viewer": {
        "orders":   frozenset({"product", "amount", "created_at"}),   # no customer linkage
        "products": frozenset({"id", "name", "category", "price"}),   # public catalog, no description
        "events":   frozenset({"event_type", "occurred_at"}),         # aggregate-safe, no customer id
    },
}

# Union of all columns that exist in the real schema (from admin, which has full access).
# Used by mask_rows to tell real columns apart from computed aliases like 'total_revenue'.
_SCHEMA_COLUMNS: frozenset[str] = frozenset().union(*ROLE_POLICY["admin"].values())


def real_tables(stmt: exp.Select) -> set[str]:
    # CTE aliases are virtual — exclude them so WITH clauses don't fail the allowlist check.
    cte_names = {cte.alias.lower() for cte in stmt.find_all(exp.CTE)}
    return {t.name.lower() for t in stmt.find_all(exp.Table)} - cte_names


def _allowed_columns(role: str, tables: set[str]) -> frozenset[str]:
    # Union of allowed columns across all queried tables for this role.
    policy = ROLE_POLICY[role]
    return frozenset().union(*(policy.get(t, frozenset()) for t in tables))


def check_access(role: str, stmt: exp.Select) -> None:
    # Table-level check runs before column-level: cheaper and gives a clearer error message.
    # In production, `role` would come from a verified bearer token at the transport layer;
    # it is an explicit parameter here so the policy logic can be tested in isolation.
    policy = ROLE_POLICY.get(role)
    if policy is None:
        raise AccessDenied(f"Unknown role: {role!r}. No policy defined.")

    denied = real_tables(stmt) - set(policy.keys())
    if denied:
        raise AccessDenied(
            f"Role {role!r} is not permitted to access table(s): {sorted(denied)}."
        )


def mask_rows(role: str, tables: set[str], rows: list[dict]) -> list[dict]:
    # Only mask columns that are in _SCHEMA_COLUMNS (raw DB columns).
    # Computed aliases like 'total_revenue' are NOT in the schema, so they pass through —
    # the allowlist guard already prevented any denied raw column from being queried.
    # Sentinel '***' signals redaction rather than silently dropping the field.
    # Known limitation: `SELECT email AS contact` renames the column so mask_rows
    # won't catch it (it sees 'contact', not 'email'). Production mitigation is a
    # read-only Postgres role with column-level GRANT restrictions — the DB refuses
    # the query entirely, regardless of aliasing at the application layer.
    allowed = _allowed_columns(role, tables)
    return [
        {k: (v if k in allowed or k not in _SCHEMA_COLUMNS else "***") for k, v in row.items()}
        for row in rows
    ]


# ── ABAC extension sketch ──────────────────────────────────────────────────────
# The current model is RBAC: role → allowed tables/columns.
# To extend to ABAC (attribute-based access control), you would:
#
#   1. Replace the ROLE_POLICY dict with a DB table:
#        CREATE TABLE access_policies (
#            subject_role TEXT,          -- e.g. 'analyst'
#            subject_dept TEXT,          -- e.g. 'finance'
#            resource_table TEXT,
#            resource_sensitivity TEXT,  -- e.g. 'PII', 'INTERNAL'
#            allowed_columns TEXT[],
#            effect TEXT                 -- 'allow' | 'deny'
#        );
#
#   2. Make check_access() async and query the policy table at request time:
#        async def check_access(role: str, dept: str, stmt: exp.Select) -> None:
#            rows = await conn.fetch(
#                "SELECT * FROM access_policies WHERE subject_role=$1 AND subject_dept=$2",
#                role, dept
#            )
#            ... evaluate against tables in stmt ...
#
#   3. Pass subject attributes from the JWT claim (role + department + sensitivity clearance)
#      into check_access() instead of just role.
#
# The enforcement call sites in mcp_server.py and agent.py don't change —
# only the signature of check_access() and the ROLE_POLICY data source.

