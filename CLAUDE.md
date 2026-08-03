# CLAUDE.md — Governed Multi-Tool MCP Agent

Project context for Claude Code. Read this before making changes.

## What we're building
A **governed, multi-tool AI agent** that answers a user's question over data. One connected flow:

1. User question hits a FastAPI endpoint.
2. An **agent loop** decides which **tool** to call.
3. Tools are exposed through an **MCP server**: `query_sql`, `search_docs` (vector), `lookup_metadata`.
4. Every tool call passes an **access-control + policy check at the boundary** (role/attribute check, row/column filters, PII masking, read-only).
5. SQL passes a **validation layer**: parse (allowlist tables/columns) → dry-run → read-only execute.
6. The agent may take **multiple recursive steps** (with a hard step cap) before answering.
7. It **constructs context** from what was retrieved and returns a **grounded, cited answer**.
8. **Every tool call is audit-logged** (who, what accessed, what returned).
9. **Tests + observability**: a small eval for tool-selection accuracy and groundedness; structured logs/traces.

## Why this design (defend these — they're the interview)
- **Governance lives at the MCP/tool boundary, never in the prompt.** Prompt rules are a backstop, not the control. All auth/policy/PII/audit is enforced server-side.
- **Recursive loop is engineering, not magic:** explicit termination conditions + max-step cap + state passed between steps + cost budget.
- **Query safety:** parse + dry-run + read-only + parameterized queries prevent unsafe/invalid SQL touching data.
- **Two eval axes:** tool-selection accuracy (did it pick/query right?) and groundedness (did the answer stick to retrieved data?).
- **Ship a thin vertical slice first, then widen** — one source end-to-end with tests + audit before adding the next.

## Tech stack
- **Python 3.11+**, **FastAPI** (async), **Postgres** (Neon serverless), **pgvector** (built-in on Neon free tier).
- **MCP 2.0** (SSE transport) for the tool/endpoint layer.
- **Groq** (LLaMA 3.3 70B) for the agent LLM — free tier, OpenAI-compatible API.
- **Google Gemini** (`gemini-embedding-001`, 3072-dim) for vector embeddings — free tier.
- **SQLGlot** for SQL AST parsing and allowlist enforcement.
- **pytest + pytest-asyncio** for tests.
- **Hosting**: Neon (free Postgres) + Render (free FastAPI web service).

## Build order (do NOT skip ahead — get each piece working + understood first)
1. ✅ FastAPI service + Postgres connection + health check + seed schema/data (5 tables, 25 customers).
2. ✅ MCP server + `query_sql` tool — handshake, SSE transport, 3 tools total.
3. ✅ SQL validation layer — SQLGlot parse → allowlist (with scope-alias fix) → EXPLAIN dry-run → read-only.
4. ✅ Access-control check — RBAC in policy.py, column masking, PII sentinel.
5. ✅ Agent loop — tool selection, recursion, MAX_STEPS cap, state in messages list, LLM retry.
6. ✅ `search_docs` (pgvector) + `lookup_metadata` + context construction + grounded cited answers.
7. ✅ Audit logging — append-only DB table + stdout JSON, log_call context manager.
8. ✅ Tests (43 passing) + two-axis eval (tool-selection 100%, groundedness 80%).

## Conventions
- Keep governance (auth, policy, PII, audit) in a **single boundary module** so it's obviously enforced in one place — this is the story.
- Read-only DB role for query execution.
- After each step, explain what was built and why (I need to defend every line).
- No secrets in code; use env vars.

## Context
This is both interview prep for a **Senior AI Platform Engineer contract (Nextdata, data-mesh)** and a **portfolio project** showcasing MCP, governed data access, agentic retrieval, and grounding/eval — the most in-demand skills across current GenAI-engineer JDs.
