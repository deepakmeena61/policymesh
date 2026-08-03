# Unit tests for guards 1 (parse) and 2 (allowlist) — pure and sync, no DB needed.
# Guards 3 (dry_run) and 4 (execute_readonly) require a live connection and are
# covered by the integration tests in test_audit.py.

import pytest
from app.sql_validator import ValidationError, check_allowlist, parse


# ── Guard 1: parse ────────────────────────────────────────────────────────────

def test_parse_valid_select():
    stmt = parse("SELECT id, name FROM customers")
    assert stmt is not None


def test_parse_rejects_delete():
    with pytest.raises(ValidationError, match="Only SELECT"):
        parse("DELETE FROM customers WHERE id = 1")


def test_parse_rejects_update():
    with pytest.raises(ValidationError, match="Only SELECT"):
        parse("UPDATE customers SET tier = 'free'")


def test_parse_rejects_insert():
    with pytest.raises(ValidationError, match="Only SELECT"):
        parse("INSERT INTO customers (name) VALUES ('x')")


def test_parse_rejects_multiple_statements():
    with pytest.raises(ValidationError, match="Exactly one"):
        parse("SELECT 1; SELECT 2")


def test_parse_rejects_garbage():
    with pytest.raises(ValidationError, match="parse error"):
        parse("THIS IS NOT SQL !!!")


# ── Guard 2: allowlist ────────────────────────────────────────────────────────

def test_allowlist_simple_select():
    check_allowlist(parse("SELECT id, name FROM customers"))


def test_allowlist_star():
    # SELECT * produces no Column AST nodes, so allowlist passes.
    # mask_rows() in policy.py handles column-level redaction at result time.
    check_allowlist(parse("SELECT * FROM customers"))


def test_allowlist_join():
    check_allowlist(parse(
        "SELECT c.name, o.amount FROM customers c JOIN orders o ON c.id = o.customer_id"
    ))


def test_allowlist_cte():
    # CTE alias 'recent' is virtual — must not be checked against ALLOWED_TABLES.
    check_allowlist(parse(
        "WITH recent AS (SELECT id, amount FROM orders) SELECT id, amount FROM recent"
    ))


def test_allowlist_aggregate():
    check_allowlist(parse("SELECT tier, COUNT(*) FROM customers GROUP BY tier"))


def test_allowlist_rejects_unknown_table():
    with pytest.raises(ValidationError, match="not in allowlist"):
        check_allowlist(parse("SELECT id FROM secrets"))


def test_allowlist_rejects_unknown_column():
    with pytest.raises(ValidationError, match="not in allowlist"):
        check_allowlist(parse("SELECT password FROM customers"))


def test_allowlist_rejects_qualified_unknown_column():
    with pytest.raises(ValidationError, match="not in allowlist"):
        check_allowlist(parse("SELECT customers.ssn FROM customers"))


def test_allowlist_resolves_alias():
    # 'c.name' — alias 'c' must resolve to 'customers' before column check.
    check_allowlist(parse("SELECT c.name FROM customers c"))


def test_allowlist_rejects_alias_bad_column():
    with pytest.raises(ValidationError, match="not in allowlist"):
        check_allowlist(parse("SELECT c.password FROM customers c"))


# ── Scope-alias tests (the ORDER BY / GROUP BY / HAVING / CTE cases) ─────────

def test_allowlist_order_by_select_alias():
    # SELECT alias used in ORDER BY must not be blocked.
    # This was the live bug: ORDER BY total_revenue failed because 'total_revenue'
    # is not in _ALL_COLUMNS even though it's defined in the same SELECT clause.
    check_allowlist(parse(
        "SELECT product, SUM(amount) AS total_revenue "
        "FROM orders GROUP BY product ORDER BY total_revenue DESC"
    ))


def test_allowlist_having_select_alias():
    # HAVING on a SELECT alias — same root cause as ORDER BY alias.
    check_allowlist(parse(
        "SELECT tier, COUNT(*) AS customer_count "
        "FROM customers GROUP BY tier HAVING customer_count > 2"
    ))


def test_allowlist_group_by_select_alias():
    # GROUP BY using an alias defined in the SELECT list.
    check_allowlist(parse(
        "SELECT LOWER(tier) AS tier_lower, COUNT(*) "
        "FROM customers GROUP BY tier_lower"
    ))


def test_allowlist_cte_alias_in_outer_query():
    # CTE column alias referenced in the outer SELECT and ORDER BY must not be blocked.
    check_allowlist(parse(
        "WITH revenue AS ("
        "  SELECT product, SUM(amount) AS total FROM orders GROUP BY product"
        ") SELECT product, total FROM revenue ORDER BY total DESC"
    ))


def test_allowlist_cte_bare_column_in_outer_query():
    # Bare column name from a CTE body is also a scope alias.
    check_allowlist(parse(
        "WITH recent AS (SELECT id, amount FROM orders WHERE amount > 1000) "
        "SELECT id, amount FROM recent ORDER BY amount DESC"
    ))


def test_allowlist_subquery_alias_is_not_blocked():
    # Column referenced via a subquery alias ('s.total') — the inner query is
    # already validated by find_all recursion; the outer qualified ref passes through.
    check_allowlist(parse(
        "SELECT s.product, s.total FROM "
        "(SELECT product, SUM(amount) AS total FROM orders GROUP BY product) s"
    ))


def test_allowlist_still_rejects_truly_unknown_column():
    # A column that is neither in the schema nor a scope alias must still be blocked.
    with pytest.raises(ValidationError, match="not in allowlist"):
        check_allowlist(parse("SELECT secret_col FROM orders"))
