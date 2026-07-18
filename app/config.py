"""Central configuration.

Loads static defaults from ``config.toml`` and overlays them with environment
variables (``.env`` supported via python-dotenv). Exposes a single ``settings``
object plus a per-role ``LLMRoleConfig`` resolver.

Design: everything is read once at import time into a frozen-ish dataclass so the
rest of the app never touches ``os.environ`` directly.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

with (ROOT / "config.toml").open("rb") as fh:
    _TOML = tomllib.load(fh)


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


@dataclass(frozen=True)
class LLMRoleConfig:
    """Resolved LLM configuration for one agent role."""

    role: str
    model: str
    base_url: str
    api_key: str
    temperature: float

    @property
    def available(self) -> bool:
        """True when a real provider is configured (has a key)."""
        return bool(self.api_key)


@dataclass(frozen=True)
class Settings:
    title: str
    base_path: str
    db_path: Path
    task_llm_budget: int
    timezone: str

    slot_granularity: str
    collecting_timeout_hours: int
    max_messages_per_round: int
    candidate_count: int
    max_reschedule_iterations: int

    request_timeout_s: int
    max_retries: int

    search_provider: str
    tavily_api_key: str
    searxng_base_url: str

    admin_password: str  # empty -> /admin disabled

    _llm_defaults: dict = field(default_factory=dict)

    def llm_role(self, role: str) -> LLMRoleConfig:
        """Resolve the LLM config for ``role`` in {'person','admin','search'}."""
        d = self._llm_defaults
        prefix = {"person": "LLM_PERSON", "admin": "LLM_ADMIN", "search": "LLM_SEARCH"}[role]
        model = _env(f"{prefix}_MODEL", d["model"])
        base_url = _env(f"{prefix}_BASE_URL", d["base_url"])
        api_key = _env(f"{prefix}_API_KEY", d["api_key"])
        temperature = float(_env(f"{prefix}_TEMPERATURE", str(d["temperature"][role])))
        return LLMRoleConfig(role, model, base_url, api_key, temperature)


def _load() -> Settings:
    app = _TOML["app"]
    llm = _TOML["llm"]
    db_path = Path(_env("DB_PATH", "data/agenttable.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    defaults = {
        "model": _env("LLM_MODEL", "deepseek-chat"),
        "base_url": _env("LLM_BASE_URL", "https://api.deepseek.com"),
        "api_key": _env("LLM_API_KEY", ""),
        "temperature": {
            "person": llm["person_temperature"],
            "admin": llm["admin_temperature"],
            "search": llm["search_temperature"],
        },
    }
    return Settings(
        title=app["title"],
        base_path=_env("BASE_PATH", "").rstrip("/"),
        db_path=db_path,
        task_llm_budget=int(_env("TASK_LLM_BUDGET", "200")),
        timezone=_env("APP_TIMEZONE", "Europe/Berlin"),
        slot_granularity=app["slot_granularity"],
        collecting_timeout_hours=int(app["collecting_timeout_hours"]),
        max_messages_per_round=int(app["max_messages_per_round"]),
        candidate_count=int(app["candidate_count"]),
        max_reschedule_iterations=int(app["max_reschedule_iterations"]),
        request_timeout_s=int(llm["request_timeout_s"]),
        max_retries=int(llm["max_retries"]),
        search_provider=_env("SEARCH_PROVIDER", "none"),
        tavily_api_key=_env("TAVILY_API_KEY", ""),
        searxng_base_url=_env("SEARXNG_BASE_URL", ""),
        admin_password=_env("ADMIN_PASSWORD", ""),
        _llm_defaults=defaults,
    )


settings = _load()
