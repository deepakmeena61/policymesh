import pytest
from app.policy import AccessDenied, check_access, mask_rows
from app.sql_validator import parse

CUSTOMER_ROW = {"id": 1, "name": "Alice", "email": "alice@example.com", "tier": "pro"}
ORDER_ROW    = {"id": 10, "customer_id": 1, "product": "Analytics Suite",
                "amount": "4200.00", "created_at": "2024-01-01"}


# ── check_access ──────────────────────────────────────────────────────────────

def test_admin_can_read_customers():
    check_access("admin", parse("SELECT * FROM customers"))


def test_admin_can_read_orders():
    check_access("admin", parse("SELECT * FROM orders"))


def test_analyst_can_read_customers():
    check_access("analyst", parse("SELECT id, name FROM customers"))


def test_analyst_can_read_orders():
    check_access("analyst", parse("SELECT * FROM orders"))


def test_analyst_can_join():
    check_access("analyst", parse(
        "SELECT c.name, o.amount FROM customers c JOIN orders o ON c.id = o.customer_id"
    ))


def test_viewer_can_read_orders():
    check_access("viewer", parse("SELECT product, amount FROM orders"))


def test_viewer_cannot_read_customers():
    with pytest.raises(AccessDenied, match="not permitted"):
        check_access("viewer", parse("SELECT id FROM customers"))


def test_unknown_role_denied():
    with pytest.raises(AccessDenied, match="Unknown role"):
        check_access("hacker", parse("SELECT * FROM customers"))


def test_cte_alias_not_treated_as_real_table():
    # 'recent' is a CTE alias — must not trigger an allowlist failure
    check_access("analyst", parse(
        "WITH recent AS (SELECT id FROM orders) SELECT id FROM recent"
    ))


# ── mask_rows ─────────────────────────────────────────────────────────────────

def test_admin_sees_email():
    masked = mask_rows("admin", {"customers"}, [CUSTOMER_ROW])
    assert masked[0]["email"] == "alice@example.com"


def test_analyst_email_is_masked():
    masked = mask_rows("analyst", {"customers"}, [CUSTOMER_ROW])
    assert masked[0]["email"] == "***"
    assert masked[0]["name"] == "Alice"     # non-PII field untouched
    assert masked[0]["tier"] == "pro"


def test_viewer_order_fields_visible():
    masked = mask_rows("viewer", {"orders"}, [ORDER_ROW])
    assert masked[0]["product"] == "Analytics Suite"
    assert masked[0]["amount"] == "4200.00"


def test_viewer_customer_id_masked_in_order():
    # viewer policy for orders excludes customer_id (no linkage)
    masked = mask_rows("viewer", {"orders"}, [ORDER_ROW])
    assert masked[0]["customer_id"] == "***"


def test_mask_empty_rows():
    assert mask_rows("analyst", {"customers"}, []) == []


def test_analyst_join_masks_email_only():
    join_row = {**CUSTOMER_ROW, **ORDER_ROW}
    masked = mask_rows("analyst", {"customers", "orders"}, [join_row])
    assert masked[0]["email"] == "***"
    assert masked[0]["amount"] == "4200.00"   # orders column, visible to analyst


def test_computed_alias_not_masked():
    # 'total_revenue' is not a schema column — it's a SUM(...) alias.
    # It must pass through unmasked regardless of role.
    row = {"product": "Analytics Suite", "total_revenue": "12999.00"}
    masked = mask_rows("analyst", {"orders"}, [row])
    assert masked[0]["total_revenue"] == "12999.00"
    assert masked[0]["product"] == "Analytics Suite"
