# Unit test for the LLM failover chain in app/agent.py: Groq key 1 -> Groq key 2 -> Gemini.
# Mocks both SDK clients so it runs without real API keys or network calls.
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.agent as agent


def _response(content: str):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=msg)])


class _FakeClient:
    def __init__(self, label: str, fail: bool):
        self.label = label
        self.calls = 0
        self.fail = fail
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise Exception(f"rate_limit_exceeded: 429 on {self.label}")
        return _response(f"answered by {self.label}")


@pytest.mark.asyncio
async def test_falls_back_to_gemini_when_all_groq_keys_rate_limited(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake1")
    monkeypatch.setenv("GROQ_API_KEY_2", "gsk_fake2")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzafake")

    groq1 = _FakeClient("groq-key1", fail=True)
    groq2 = _FakeClient("groq-key2", fail=True)
    gemini = _FakeClient("gemini", fail=False)

    def fake_groq(api_key):
        return groq1 if api_key == "gsk_fake1" else groq2

    def fake_openai(api_key, base_url):
        assert base_url == agent.GEMINI_BASE_URL
        return gemini

    with patch.object(agent, "AsyncGroq", side_effect=fake_groq), \
         patch.object(agent, "AsyncOpenAI", side_effect=fake_openai), \
         patch("random.randrange", return_value=0):
        result = await agent.run_agent("hello", "analyst")

    assert groq1.calls == 1
    assert groq2.calls == 1
    assert gemini.calls == 1
    assert result["answer"] == "answered by gemini"
    assert result["stopped_reason"] == "done"


@pytest.mark.asyncio
async def test_second_groq_key_used_when_first_is_rate_limited(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake1")
    monkeypatch.setenv("GROQ_API_KEY_2", "gsk_fake2")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    groq1 = _FakeClient("groq-key1", fail=True)
    groq2 = _FakeClient("groq-key2", fail=False)

    def fake_groq(api_key):
        return groq1 if api_key == "gsk_fake1" else groq2

    with patch.object(agent, "AsyncGroq", side_effect=fake_groq), \
         patch("random.randrange", return_value=0):
        result = await agent.run_agent("hello", "analyst")

    assert groq1.calls == 1
    assert groq2.calls == 1
    assert result["answer"] == "answered by groq-key2"
