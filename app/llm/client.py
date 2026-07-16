"""LLM client abstraction (spec §7).

A ``LLMClient`` is role-bound (``person`` / ``admin`` / ``search``) and exposes a
single async ``chat`` method. Two implementations:

* ``OpenAICompatibleClient`` — wraps the ``openai`` SDK with ``base_url``, so it
  targets DeepSeek/OpenRouter/Ollama/etc. Adds retry+backoff, timeout and token
  usage logging into ``llm_usage``.
* ``MockLLM`` — deterministic, no network. Used automatically when no API key is
  configured, and injected directly in tests. Behaviour is fully scriptable via
  a ``handler`` callable.

Structured output: ``response_format={"type": "json_object"}`` requests JSON;
callers still parse defensively via ``app.llm.parsing``.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from app.config import LLMRoleConfig, settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: Any = None


class LLMClient(Protocol):
    role_config: LLMRoleConfig

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse: ...


# --- real provider --------------------------------------------------------
class OpenAICompatibleClient:
    """OpenAI-SDK-compatible client (DeepSeek by default)."""

    def __init__(self, role_config: LLMRoleConfig) -> None:
        from openai import AsyncOpenAI

        self.role_config = role_config
        self._client = AsyncOpenAI(
            api_key=role_config.api_key,
            base_url=role_config.base_url,
            timeout=settings.request_timeout_s,
        )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.role_config.model,
            "messages": messages,
            "temperature": self.role_config.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        if response_format:
            kwargs["response_format"] = response_format

        last_exc: Exception | None = None
        for attempt in range(settings.max_retries):
            try:
                resp = await self._client.chat.completions.create(**kwargs)
                return self._parse(resp)
            except Exception as exc:  # noqa: BLE001 — retry any transient provider error
                last_exc = exc
                await asyncio.sleep(min(2**attempt, 8))
        raise RuntimeError(f"LLM call failed after {settings.max_retries} attempts: {last_exc}")

    @staticmethod
    def _parse(resp: Any) -> LLMResponse:
        choice = resp.choices[0]
        msg = choice.message
        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            raw=resp,
        )


# --- mock provider --------------------------------------------------------
Handler = Callable[[list[dict], list[dict] | None, dict | None], LLMResponse | Awaitable[LLMResponse]]


class MockLLM:
    """Deterministic offline client.

    Without a ``handler`` it produces safe, minimal responses:
    * a JSON request -> ``{}`` (moderator selection reads this as END_ROUND),
    * otherwise a short German placeholder line, never calling tools.

    Tests pass a ``handler`` (or a ``queue`` of pre-baked ``LLMResponse``s) to
    drive exact behaviour.
    """

    def __init__(
        self,
        role_config: LLMRoleConfig | None = None,
        handler: Handler | None = None,
        queue: list[LLMResponse] | None = None,
    ) -> None:
        self.role_config = role_config or LLMRoleConfig("person", "mock", "", "", 0.0)
        self.handler = handler
        self.queue = list(queue) if queue else None
        self.calls: list[dict] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, "response_format": response_format})
        if self.queue:
            return self.queue.pop(0)
        if self.handler is not None:
            result = self.handler(messages, tools, response_format)
            if asyncio.iscoroutine(result):
                return await result
            return result  # type: ignore[return-value]
        if response_format and response_format.get("type") == "json_object":
            return LLMResponse(content="{}")
        return LLMResponse(content="(Mock-Agent ohne LLM-Key — hier stünde eine echte Antwort.)")


# --- factory --------------------------------------------------------------
_factory: Callable[[str], LLMClient] | None = None


def _default_factory(role: str) -> LLMClient:
    cfg = settings.llm_role(role)
    if cfg.available:
        return OpenAICompatibleClient(cfg)
    return MockLLM(cfg)


def get_client(role: str) -> LLMClient:
    """Return an LLM client for a role in {'person','admin','search'}."""
    return (_factory or _default_factory)(role)


def set_client_factory(factory: Callable[[str], LLMClient] | None) -> None:
    """Override the client factory (tests inject a mock factory)."""
    global _factory
    _factory = factory
