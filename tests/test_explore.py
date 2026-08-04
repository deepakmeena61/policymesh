# Regression test: explore_table() with an unknown role must raise a typed
# AccessDenied (PermissionError subclass), not a bare ValueError — otherwise the
# /explore/{table} route in app/main.py (which only catches PermissionError)
# surfaces an unhandled 500 instead of a JSON error like every other governed path.
# Hits the real Neon DB, same as test_audit.py.
import pytest
from dotenv import load_dotenv

load_dotenv()

from app.explore import explore_table
from app.policy import AccessDenied


async def test_explore_table_unknown_role_raises_access_denied():
    with pytest.raises(AccessDenied, match="Unknown role"):
        await explore_table("customers", "hacker")


async def test_explore_table_known_role_forbidden_table_raises_permission_error():
    with pytest.raises(PermissionError, match="not permitted"):
        await explore_table("customers", "viewer")


async def test_explore_table_allowed_role_and_table_succeeds():
    result = await explore_table("customers", "admin")
    assert result["table"] == "customers"
    assert result["role"] == "admin"
    assert "columns" in result and "sample" in result
