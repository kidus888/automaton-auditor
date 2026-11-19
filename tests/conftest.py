import pytest


@pytest.fixture(autouse=True)
def _no_live_llms(monkeypatch):
    """Tests exercise the deterministic paths; never call paid LLM / tracing APIs."""
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"):
        monkeypatch.delenv(key, raising=False)
