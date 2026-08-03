import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "eval: live-LLM eval tests — requires GROQ_API_KEY and GOOGLE_API_KEY",
    )
