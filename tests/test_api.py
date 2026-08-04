# HTTP-level regression tests for app/main.py endpoint edge cases.
# Uses FastAPI's TestClient (sync, in-process) against the real app + real Neon DB,
# same convention as test_audit.py and test_explore.py — no live LLM calls needed
# for these cases since the bugs being guarded against are caught before run_agent().
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


def test_ask_unknown_role_rejected_without_calling_agent():
    with patch("app.main.run_agent", new_callable=AsyncMock) as mock_run_agent:
        with TestClient(app) as client:
            resp = client.post("/ask", json={"question": "hi", "caller_role": "hacker"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["stopped_reason"] == "error:unknown_role"
    assert body["steps"] == []
    mock_run_agent.assert_not_called()


def test_ask_empty_string_role_rejected():
    with patch("app.main.run_agent", new_callable=AsyncMock) as mock_run_agent:
        with TestClient(app) as client:
            resp = client.post("/ask", json={"question": "hi", "caller_role": ""})

    assert resp.json()["stopped_reason"] == "error:unknown_role"
    mock_run_agent.assert_not_called()


def test_audit_limit_rejects_negative():
    with TestClient(app) as client:
        resp = client.get("/audit", params={"limit": -1})
    assert resp.status_code == 422


def test_audit_limit_rejects_zero():
    with TestClient(app) as client:
        resp = client.get("/audit", params={"limit": 0})
    assert resp.status_code == 422


def test_audit_limit_rejects_over_max():
    with TestClient(app) as client:
        resp = client.get("/audit", params={"limit": 501})
    assert resp.status_code == 422


def test_audit_limit_accepts_valid_value():
    with TestClient(app) as client:
        resp = client.get("/audit", params={"limit": 5})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_explore_unknown_role_returns_typed_error_not_500():
    with TestClient(app) as client:
        resp = client.get("/explore/customers", params={"role": "hacker"})
    assert resp.status_code == 200
    assert "Unknown role" in resp.json()["error"]
