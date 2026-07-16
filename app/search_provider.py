"""Pluggable web-search providers (spec §3, §5.3).

Interface ``SearchProvider`` with two implementations (Tavily, SearXNG) plus a
``NullProvider`` used when no key/instance is configured — in that case the
search agent reports itself as unavailable rather than failing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import settings

log = logging.getLogger("agenttable.search")


class SearchUnavailable(Exception):
    """Raised when the provider is reachable but the upstream engines failed
    (rate-limited / CAPTCHA), so zero results is a block, not a genuine miss."""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    available: bool

    async def search(
        self, query: str, max_results: int = 5, engines: str | None = None
    ) -> list[SearchResult]: ...


class NullProvider:
    available = False

    async def search(self, query: str, max_results: int = 5, engines: str | None = None) -> list[SearchResult]:
        return []


class TavilyProvider:
    available = True

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    async def search(self, query: str, max_results: int = 5, engines: str | None = None) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": self._key, "query": query, "max_results": max_results},
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", ""))
            for r in data.get("results", [])
        ]


class SearxngProvider:
    available = True

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    async def search(self, query: str, max_results: int = 5, engines: str | None = None) -> list[SearchResult]:
        params = {"q": query, "format": "json"}
        if engines:  # target specific, CAPTCHA-free engines (e.g. openstreetmap)
            params["engines"] = engines
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{self._base}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results", [])[:max_results]
        if not results and data.get("unresponsive_engines"):
            reasons = ", ".join(f"{e[0]}: {e[1]}" for e in data["unresponsive_engines"][:4])
            raise SearchUnavailable(reasons)
        return [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", ""))
            for r in results
        ]


class FallbackProvider:
    """Try ``primary`` first; on error or empty result use ``secondary``.

    Used to run Tavily as primary with SearXNG as automatic fallback once the
    Tavily quota is hit (or any Tavily error occurs)."""

    available = True

    def __init__(self, primary: SearchProvider, secondary: SearchProvider) -> None:
        self.primary = primary
        self.secondary = secondary

    async def search(self, query: str, max_results: int = 5, engines: str | None = None) -> list[SearchResult]:
        try:
            results = await self.primary.search(query, max_results)
            if results:
                return results
            log.info("primary search empty, falling back to secondary")
        except Exception as exc:  # noqa: BLE001 — quota/limit/network -> fall back
            log.warning("primary search failed (%s), falling back to secondary", exc)
        # may raise SearchUnavailable (blocked engines) -> handled by the search agent
        return await self.secondary.search(query, max_results, engines=engines)


_provider: SearchProvider | None = None


def _build_provider() -> SearchProvider:
    kind = settings.search_provider
    searxng = SearxngProvider(settings.searxng_base_url) if settings.searxng_base_url else None

    if kind == "tavily" and settings.tavily_api_key:
        tavily = TavilyProvider(settings.tavily_api_key)
        # auto-fallback to SearXNG when Tavily's quota is reached
        return FallbackProvider(tavily, searxng) if searxng else tavily
    if kind == "tavily" and searxng:
        # Tavily requested but no key yet -> keep working via SearXNG
        log.warning("SEARCH_PROVIDER=tavily but TAVILY_API_KEY missing; using SearXNG")
        return searxng
    if kind == "searxng" and searxng:
        return searxng
    return NullProvider()


def get_provider() -> SearchProvider:
    global _provider
    if _provider is None:
        _provider = _build_provider()
    return _provider


def set_provider(provider: SearchProvider | None) -> None:
    """Override the provider (tests)."""
    global _provider
    _provider = provider
