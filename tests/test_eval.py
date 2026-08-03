"""
Two-axis eval as specified in CLAUDE.md:
  1. tool_selection_accuracy — did the agent pick the right tool first?
  2. groundedness             — does the answer stay within what the tools returned?

Both use LLM-as-judge (Groq) for groundedness; tool-selection is deterministic.

Run:
  pytest tests/test_eval.py -v -m eval -s

Requires GROQ_API_KEY and GOOGLE_API_KEY in .env.
"""

import json
import os

import pytest
from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv()

from app.agent import run_agent

# ── Axis 1: Tool-selection accuracy ──────────────────────────────────────────
# (question, expected_first_tool)
TOOL_CASES = [
    ("What is the total revenue across all orders?",       "query_sql"),
    ("How many customers are on each tier?",                "query_sql"),
    ("Which product generates the most revenue?",           "query_sql"),
    ("What features does the Analytics Suite include?",     "search_docs"),
    ("What SLA do pro customers get?",                      "search_docs"),
    ("What is the data governance policy for PII?",         "search_docs"),
]

# Allow one miss out of six — accounts for occasional LLM non-determinism.
TOOL_THRESHOLD = 5 / 6


@pytest.mark.eval
async def test_tool_selection_accuracy():
    results = []
    for question, expected in TOOL_CASES:
        outcome = await run_agent(question, caller_role="analyst")
        first_tool = next(
            (s["tool"] for s in outcome["steps"] if s["type"] == "tool_call"),
            None,
        )
        passed = first_tool == expected
        results.append((question, expected, first_tool, passed))

    correct = sum(1 for *_, ok in results if ok)
    accuracy = correct / len(TOOL_CASES)

    print(f"\n  Tool-selection accuracy: {correct}/{len(TOOL_CASES)} = {accuracy:.0%}")
    for q, exp, got, ok in results:
        print(f"  {'✓' if ok else '✗'} expected={exp:12s} got={str(got):12s}  {q[:55]}")

    assert accuracy >= TOOL_THRESHOLD, f"Accuracy {accuracy:.0%} below {TOOL_THRESHOLD:.0%}"


# ── Axis 2: Groundedness ─────────────────────────────────────────────────────
# Groundedness = the answer doesn't assert facts that aren't in the retrieved context.
# We use the LLM as a judge: give it (question, retrieved context, answer) and ask
# whether every factual claim in the answer is supported by the context.
# This catches hallucination — the most dangerous failure mode in a governed system.

GROUND_CASES = [
    "What is total revenue by product?",
    "Who are the top 3 customers by total spend?",
    "How many customers are on the pro tier?",
]

GROUND_THRESHOLD = 0.8  # 80% of claims must be grounded

JUDGE_PROMPT = """\
You are evaluating whether an AI answer is grounded in its retrieved context.

QUESTION: {question}

RETRIEVED CONTEXT (what the tools returned):
{context}

ANSWER TO EVALUATE:
{answer}

Task: Identify every factual claim in the answer (numbers, percentages, product names,
policy details, customer counts, etc.). For each claim, determine if it is:
  - SUPPORTED: the claim is directly stated or clearly inferable from the retrieved context
  - UNSUPPORTED: the claim is not in the context (possible hallucination)
  - N/A: not a factual claim (e.g. hedging language, rephrasing the question)

Reply in JSON only:
{{
  "claims": [
    {{"claim": "...", "verdict": "SUPPORTED|UNSUPPORTED|N/A", "reason": "..."}}
  ],
  "groundedness_score": <fraction of SUPPORTED among non-N/A claims, 0.0-1.0>,
  "summary": "one sentence"
}}"""


async def _judge_groundedness(question: str, context: str, answer: str) -> dict:
    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    prompt = JUDGE_PROMPT.format(question=question, context=context, answer=answer)
    response = await client.chat.completions.create(
        model=os.getenv("AGENT_MODEL", "llama-3.3-70b-versatile"),
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _extract_context(steps: list[dict]) -> str:
    """Concatenate all tool results into a single context string for the judge."""
    parts = []
    for s in steps:
        if s["type"] != "tool_call":
            continue
        result = s["result"]
        if isinstance(result, list):
            parts.append(f"[{s['tool']}] {json.dumps(result, default=str)}")
        elif isinstance(result, str):
            parts.append(f"[{s['tool']}] {result[:800]}")
        elif isinstance(result, dict) and not result.get("error"):
            parts.append(f"[{s['tool']}] {json.dumps(result, default=str)}")
    return "\n\n".join(parts) or "(no context retrieved)"


@pytest.mark.eval
async def test_groundedness():
    scores = []

    for question in GROUND_CASES:
        outcome = await run_agent(question, caller_role="analyst")
        context = _extract_context(outcome["steps"])
        answer = outcome["answer"]

        if not answer or answer == "(no answer)" or outcome["stopped_reason"].startswith("error:"):
            print(f"\n  ⚠ Skipped (agent error): {question[:60]}")
            continue  # don't penalise agent API errors in the groundedness score

        verdict = await _judge_groundedness(question, context, answer)
        score = float(verdict.get("groundedness_score", 0))
        scores.append(score)

        print(f"\n  Q: {question[:60]}")
        print(f"  Groundedness: {score:.0%} — {verdict.get('summary','')}")
        for c in verdict.get("claims", []):
            mark = "✓" if c["verdict"] == "SUPPORTED" else ("~" if c["verdict"] == "N/A" else "✗")
            print(f"    {mark} [{c['verdict']:11s}] {c['claim'][:80]}")

    avg = sum(scores) / len(scores) if scores else 0.0
    print(f"\n  Average groundedness: {avg:.0%} (threshold {GROUND_THRESHOLD:.0%})")

    assert avg >= GROUND_THRESHOLD, (
        f"Groundedness {avg:.0%} below {GROUND_THRESHOLD:.0%} threshold"
    )
