# Regression test: the LLM can emit its tool-call XML as literal text even on a
# call where no `tools` are offered (e.g. the forced max-steps synthesis call in
# app/agent.py), especially once the message history is full of prior tool-call
# turns. That raw "<function=name{...}>" text must never reach the user-facing
# answer — it should be replaced with a clean fallback message instead.
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.agent as agent


def _tool_call_response():
    tc = SimpleNamespace(id="call_1", function=SimpleNamespace(name="lookup_metadata", arguments="{}"))
    msg = SimpleNamespace(content=None, tool_calls=[tc])
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=msg)])


def _leaked_xml_response():
    msg = SimpleNamespace(
        content="I haven't retrieved useful data.\n\n<function=lookup_metadata></function>",
        tool_calls=None,
    )
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=msg)])


class _FakeClient:
    """Every call that offers `tools` returns a tool_call; the forced synthesis
    call (no `tools` kwarg) returns text containing leaked function-call XML."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        if "tools" in kwargs:
            return _tool_call_response()
        return _leaked_xml_response()


@pytest.mark.asyncio
async def test_forced_synthesis_answer_never_leaks_raw_function_call_xml(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake1")
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    fake = _FakeClient()
    with patch.object(agent, "AsyncGroq", return_value=fake), \
         patch("random.randrange", return_value=0), \
         patch.object(agent, "_execute_tool", return_value='{"tables": []}'):
        result = await agent.run_agent("a complex multi-step question", "analyst")

    assert result["stopped_reason"] == "max_steps"
    assert "<function=" not in result["answer"]
    assert result["answer"] == (
        "Step cap reached; unable to synthesise a final answer from the "
        "retrieved data. See steps for partial results."
    )


def test_clean_answer_text_passes_through_normal_content():
    assert agent._clean_answer_text("Total revenue is $12,999.", "fallback") == "Total revenue is $12,999."


def test_clean_answer_text_falls_back_on_leaked_xml():
    leaked = "Here you go: <function=search_docs{\"query\": \"x\"}></function>"
    assert agent._clean_answer_text(leaked, "fallback") == "fallback"


def test_clean_answer_text_handles_none_content():
    assert agent._clean_answer_text(None, "fallback") == "fallback"


def test_clean_answer_text_handles_empty_string_content():
    assert agent._clean_answer_text("", "fallback") == "fallback"
