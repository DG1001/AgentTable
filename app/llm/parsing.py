"""Robust helpers for structured LLM output and usage accounting."""
from __future__ import annotations

import json
import re
from typing import Any

from app import repo
from app.llm.client import LLMClient, LLMResponse

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> dict[str, Any]:
    """Parse a JSON object out of an LLM response, tolerating code fences and
    surrounding prose. Returns ``{}`` if nothing parseable is found."""
    if not text:
        return {}
    cleaned = _FENCE.sub("", text).strip()
    try:
        val = json.loads(cleaned)
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        pass
    # last resort: grab the outermost {...}
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            val = json.loads(cleaned[start : end + 1])
            return val if isinstance(val, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def call_and_log(
    client: LLMClient,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    tool_choice: str | dict | None = None,
    group_id: int | None = None,
    task_id: int | None = None,
) -> LLMResponse:
    """Run ``client.chat`` and record token usage in ``llm_usage``."""
    resp = await client.chat(
        messages, tools=tools, response_format=response_format, tool_choice=tool_choice
    )
    repo.log_usage(
        role=client.role_config.role,
        model=client.role_config.model,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        group_id=group_id,
        task_id=task_id,
    )
    return resp
