"""
Stress-test every query category against the running PolicyMesh agent.
Run: .venv/bin/python scripts/stress_test.py
Server must be running on port 8000.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:8000"

TESTS = [
    # (label, question, role, expected_behaviour)
    # CAT 1 — Doc search
    ("1a  PII policy",           "What is the data governance and PII policy for customer data?", "analyst", "doc"),
    ("1b  Enterprise SLA",       "What SLA do enterprise customers get?",                        "analyst", "doc"),
    ("1c  Analytics Suite",      "What features does the Analytics Suite include?",              "analyst", "doc"),
    # CAT 2 — SQL aggregates / aliases
    ("2a  Revenue by product",   "What is total revenue by product ordered by highest first?",  "analyst", "sql"),
    ("2b  Customers per tier",   "How many customers are on each tier?",                        "analyst", "sql"),
    # CAT 3 — PII masking
    ("3a  Emails (analyst)",     "Show me all customers with their email addresses",             "analyst", "masked"),
    ("3b  Emails (admin)",       "Show me all customers with their email addresses",             "admin",   "visible"),
    # CAT 4 — Access control
    ("4a  Customers (viewer)",   "List all customers",                                          "viewer",  "blocked"),
    ("4b  Ticket subjects",      "Show me all open support ticket subjects",                    "analyst", "masked_subject"),
    # CAT 5 — Hybrid
    ("5a  Top spend + SLA",      "Who are the top customers by spend and what SLA do they get?","admin",   "hybrid"),
    # CAT 6 — lookup_metadata
    ("6a  Metadata (viewer)",    "What data do I have access to?",                              "viewer",  "metadata"),
    ("6b  Columns (analyst)",    "What columns can I see in the customers table?",              "analyst", "metadata"),
    # CAT 7 — Edge cases
    ("7a  Greeting",             "hello",                                                       "analyst", "graceful"),
    ("7b  SQL injection",        "SELECT * FROM secrets",                                       "analyst", "blocked_sql"),
]


async def ask(client: httpx.AsyncClient, question: str, role: str) -> dict:
    r = await client.post(
        f"{BASE}/ask",
        json={"question": question, "caller_role": role},
        timeout=60,
    )
    return r.json()


def evaluate(label: str, result: dict, expected: str) -> tuple[str, str]:
    stopped = result.get("stopped_reason", "?")
    answer  = result.get("answer", "")
    steps   = result.get("steps", [])

    if stopped.startswith("error:"):
        return "❌ FAIL", f"error → {stopped[6:70]}"

    tool_calls = [s for s in steps if s.get("type") == "tool_call"]
    first_tool = tool_calls[0]["tool"] if tool_calls else None

    if expected == "doc":
        if stopped == "done" and first_tool in ("search_docs", None):
            return "✅ PASS", answer[:80]
        return "⚠️  WARN", f"tool={first_tool} stopped={stopped}"

    if expected == "sql":
        if stopped == "done" and answer:
            return "✅ PASS", answer[:80]
        return "⚠️  WARN", f"stopped={stopped}"

    if expected == "masked":
        # email should appear as *** in results
        all_results = json.dumps([s.get("result") for s in tool_calls])
        if "***" in all_results or "***" in answer:
            return "✅ PASS", "email masked ✓"
        if stopped == "done":
            return "⚠️  WARN", f"answer did not contain *** — check masking: {answer[:60]}"
        return "❌ FAIL", f"stopped={stopped}"

    if expected == "visible":
        # admin should see real email
        all_results = json.dumps([s.get("result") for s in tool_calls])
        if "@" in all_results or "@" in answer:
            return "✅ PASS", "email visible ✓"
        if stopped == "done":
            return "⚠️  WARN", f"email not found in results: {answer[:60]}"
        return "❌ FAIL", f"stopped={stopped}"

    if expected == "blocked":
        errors = [s for s in tool_calls if isinstance(s.get("result"), dict) and "error" in s.get("result", {})]
        if errors or "not permitted" in answer.lower() or "blocked" in answer.lower():
            return "✅ PASS", "RBAC blocked ✓"
        return "⚠️  WARN", f"expected block, got: {answer[:60]}"

    if expected == "masked_subject":
        all_results = json.dumps([s.get("result") for s in tool_calls])
        if "***" in all_results:
            return "✅ PASS", "subject masked ✓"
        if stopped == "done":
            return "⚠️  WARN", f"subject masking unclear: {answer[:60]}"
        return "❌ FAIL", f"stopped={stopped}"

    if expected == "hybrid":
        if stopped == "done" and len(tool_calls) >= 1:
            return "✅ PASS", answer[:80]
        return "⚠️  WARN", f"steps={len(tool_calls)} stopped={stopped}"

    if expected == "metadata":
        if stopped == "done" and first_tool in ("lookup_metadata", "query_sql"):
            return "✅ PASS", answer[:80]
        return "⚠️  WARN", f"tool={first_tool} stopped={stopped}"

    if expected == "graceful":
        if stopped == "done" and answer:
            return "✅ PASS", answer[:80]
        return "⚠️  WARN", f"stopped={stopped}"

    if expected == "blocked_sql":
        errors = [s for s in tool_calls if isinstance(s.get("result"), dict) and "error" in s.get("result", {})]
        if errors or "not in allowlist" in answer.lower() or "not permitted" in answer.lower():
            return "✅ PASS", "SQL blocked by allowlist ✓"
        if stopped == "done":
            return "⚠️  WARN", f"expected block, got: {answer[:60]}"
        return "❌ FAIL", f"stopped={stopped}"

    return "?  UNKN", f"stopped={stopped}"


async def main():
    print(f"\n{'Label':<30} {'Result':<10} {'Detail'}")
    print("-" * 100)
    passes = warns = fails = 0

    async with httpx.AsyncClient() as client:
        for label, question, role, expected in TESTS:
            try:
                result = await ask(client, question, role)
                flag, detail = evaluate(label, result, expected)
            except Exception as e:
                flag, detail = "❌ FAIL", f"request error: {e}"

            print(f"{label:<30} {flag:<10} {detail}")

            if "PASS" in flag: passes += 1
            elif "WARN" in flag: warns += 1
            else: fails += 1

    print("-" * 100)
    print(f"\n  {passes} passed  |  {warns} warned  |  {fails} failed  (out of {len(TESTS)})\n")


if __name__ == "__main__":
    asyncio.run(main())
