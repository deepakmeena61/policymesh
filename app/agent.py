# Agentic loop: question → tool calls → grounded answer.
# Uses Groq (free tier, llama-3.3-70b-versatile) via the OpenAI-compatible API.
#
# Termination conditions (checked in order each iteration):
#   1. finish_reason == "stop"      — LLM produced text, no tool call pending → done
#   2. tool_calls_made >= MAX_STEPS — step cap hit → one forced synthesis call → done
#   3. finish_reason unexpected     — exit with error label
#
# State between steps is the `messages` list. Each tool-call turn appends:
#   {"role": "assistant", "tool_calls": [...]}  ← LLM's tool requests
#   {"role": "tool", "tool_call_id": ..., ...}  ← one message per result
import json
import os

from groq import AsyncGroq

import app.audit as audit
from app.db import get_pool
from app.docs import build_context, search_raw
from app.metadata import get_metadata
from app.policy import AccessDenied, check_access, mask_rows, real_tables
from app.sql_validator import ValidationError, parse, validate_and_run

# Hard cap on tool calls per request — bounds cost and latency regardless of LLM behaviour.
MAX_STEPS = 5

# Groq/LLaMA occasionally emits malformed tool-call XML on complex multi-tool questions.
# Retrying the same request (same messages, no state change) usually succeeds.
MAX_LLM_RETRIES = 2

# The schema hint is included in the prompt to reduce lookup_metadata round-trips
# for common queries. It is a convenience, not a source of truth — lookup_metadata
# always returns the authoritative, role-filtered schema. If the schema changes,
# update both this hint AND the allowlists in sql_validator.py and policy.py.
SYSTEM_PROMPT = """\
You are a data analyst assistant with three tools:
  - lookup_metadata : discover what tables and columns exist and which you can access
  - query_sql       : run a SELECT query for quantitative data (revenue, counts, etc.)
  - search_docs     : search knowledge-base docs (products, policies, SLAs, how-tos)

Rules:
1. Answer ONLY from what tools return — never invent numbers or facts.
2. If you are unsure what tables or columns exist, call lookup_metadata first.
3. For search_docs results, cite sources using the [N] markers: "99.99% uptime [1]."
4. For query_sql results, reference the specific rows or aggregates returned.
5. Make tool calls one at a time.

Available SQL schema (use lookup_metadata to confirm columns for your role):
  customers(id, name, email, tier)
  orders(id, customer_id, product, amount, created_at)
  products(id, name, category, price, description)
  events(id, customer_id, event_type, occurred_at)
  tickets(id, customer_id, subject, status, priority, created_at, resolved_at)\
"""

# OpenAI/Groq tool format — "parameters" key, not Anthropic's "input_schema".
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_metadata",
            "description": (
                "Discover the data warehouse schema: tables, columns, types, row counts, "
                "and which columns are restricted by your access role. "
                "Call this first if you are unsure what data is available."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_sql",
            "description": (
                "Run a read-only SELECT query against the data warehouse. "
                "Returns rows as a JSON array. Only SELECT statements are accepted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "A SQL SELECT statement."},
                    "params": {
                        "type": "array",
                        "items": {"type": ["string", "number"]},
                        "description": "Optional positional bind parameters ($1, $2, …).",
                    },
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": (
                "Search the documentation knowledge base. Use for questions about "
                "products, policies, SLAs, tier benefits, or how things work. "
                "Returns numbered chunks with source citations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query."},
                },
                "required": ["query"],
            },
        },
    },
]


async def _execute_tool(name: str, arguments_json: str, caller_role: str) -> str:
    # The agent is an internal consumer — we call enforcement code directly rather than
    # round-tripping through the MCP transport, which would add a pointless network hop.
    # lookup_metadata: role-filtered schema from information_schema + pg_class
    # query_sql:       parse → check_access → validate_and_run → mask_rows
    # search_docs:     embed → pgvector cosine → build_context with [N] markers
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid tool arguments JSON: {exc}"})

    if name == "lookup_metadata":
        async with audit.log_call(caller_role=caller_role, tool=name, input_data={}) as ctx:
            meta = await get_metadata(caller_role)
            ctx["row_count"] = len(meta.get("tables", []))
        return json.dumps(meta, default=str)

    if name == "query_sql":
        sql = args.get("sql", "")
        params = args.get("params")
        async with audit.log_call(caller_role=caller_role, tool=name, input_data=args) as ctx:
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

    if name == "search_docs":
        query = args.get("query", "")
        k = int(args.get("k", 4))
        async with audit.log_call(caller_role=caller_role, tool=name, input_data=args) as ctx:
            try:
                chunks = await search_raw(query, k)
                ctx["row_count"] = len(chunks)
            except Exception as exc:
                ctx["error"] = str(exc)
                return json.dumps({"error": f"search_docs failed: {exc}"})
        return build_context(chunks)

    return json.dumps({"error": f"Unknown tool: {name!r}"})


async def run_agent(question: str, caller_role: str) -> dict:
    # Returns: answer, steps (ordered tool_call + answer records), stopped_reason, step_count.
    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    model = os.getenv("AGENT_MODEL", "llama-3.3-70b-versatile")

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    steps: list[dict] = []
    tool_calls_made = 0
    stopped_reason = "done"
    llm_retries = 0

    while True:
        try:
            response = await client.chat.completions.create(
                model=model,
                max_tokens=1024,
                tools=TOOLS,
                tool_choice="auto",
                messages=messages,
            )
            llm_retries = 0
        except Exception as exc:
            if "tool_use_failed" in str(exc) and llm_retries < MAX_LLM_RETRIES:
                # Malformed tool-call XML from Groq — retry without state change.
                llm_retries += 1
                continue
            stopped_reason = f"error:llm_api:{exc}"
            break

        choice = response.choices[0]

        # Termination 1: LLM has enough data and produced a text answer.
        if choice.finish_reason == "stop":
            steps.append({"step": len(steps) + 1, "type": "answer", "content": choice.message.content or ""})
            break

        if choice.finish_reason != "tool_calls":
            stopped_reason = f"error:unexpected_finish_reason:{choice.finish_reason}"
            break

        # The assistant message must be appended before tool results (API requirement).
        messages.append(choice.message)

        for tc in (choice.message.tool_calls or []):
            tool_calls_made += 1
            result_str = await _execute_tool(tc.function.name, tc.function.arguments, caller_role)

            # search_docs returns plain text (not JSON), so fall back gracefully.
            try:
                result_display = json.loads(result_str)
            except json.JSONDecodeError:
                result_display = result_str

            steps.append({
                "step": len(steps) + 1,
                "type": "tool_call",
                "tool": tc.function.name,
                "input": json.loads(tc.function.arguments),
                "result": result_display,
            })
            # Each tool result is a separate "tool" role message (OpenAI/Groq format).
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})

        # Termination 2: step cap — force a synthesis call with no tools offered.
        if tool_calls_made >= MAX_STEPS:
            stopped_reason = "max_steps"
            messages.append({
                "role": "user",
                "content": f"You have reached the {MAX_STEPS}-step tool-call limit. Summarise your findings from the data retrieved so far.",
            })
            final = await client.chat.completions.create(
                model=model, max_tokens=1024, messages=messages
            )
            steps.append({
                "step": len(steps) + 1,
                "type": "forced_answer",
                "content": final.choices[0].message.content or "Step cap reached; see steps for partial results.",
            })
            break

    final_answer = next(
        (s["content"] for s in reversed(steps) if s["type"] in ("answer", "forced_answer")),
        "",
    )
    return {"answer": final_answer, "steps": steps, "stopped_reason": stopped_reason, "step_count": tool_calls_made}
