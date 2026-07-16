"""Shared test fixtures: isolated SQLite DB + injectable mock LLM."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database, set_db  # noqa: E402
from app.llm import set_client_factory  # noqa: E402
from app.llm.client import LLMResponse, MockLLM  # noqa: E402
from app.config import LLMRoleConfig  # noqa: E402


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    set_db(database)
    yield database
    database.close()
    set_db(None)  # type: ignore[arg-type]


class ScriptedLLM(MockLLM):
    """MockLLM whose response is chosen by inspecting the prompt."""

    def __init__(self, role: str, router):
        super().__init__(LLMRoleConfig(role, "mock", "", "", 0.0))
        self._router = router
        self.role = role

    async def chat(self, messages, tools=None, response_format=None, tool_choice=None):
        self.calls.append({"messages": messages, "tools": tools, "response_format": response_format})
        return self._router(self.role, messages, tools, response_format)


@pytest.fixture
def mock_llm():
    """Install a scriptable LLM factory. Returns a controller with a settable
    ``router`` callable (role, messages, tools, response_format) -> LLMResponse."""

    class Controller:
        def __init__(self):
            self.router = lambda role, m, t, rf: LLMResponse(content="ok")

        def __call__(self, role):
            return ScriptedLLM(role, lambda *a: self.router(*a))

    ctrl = Controller()
    set_client_factory(ctrl)
    yield ctrl
    set_client_factory(None)
