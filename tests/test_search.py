"""Tests for the search provider chain (FallbackProvider)."""
import pytest

from app.search_provider import FallbackProvider, SearchResult, SearchUnavailable


class FakeProvider:
    available = True

    def __init__(self, results=None, exc=None):
        self._results = results or []
        self._exc = exc
        self.calls = 0

    async def search(self, query, max_results=5, engines=None):
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._results


async def test_primary_results_used():
    primary = FakeProvider([SearchResult("a", "u", "s")])
    secondary = FakeProvider([SearchResult("b", "u", "s")])
    out = await FallbackProvider(primary, secondary).search("x")
    assert out[0].title == "a"
    assert secondary.calls == 0


async def test_fallback_on_empty():
    primary = FakeProvider([])
    secondary = FakeProvider([SearchResult("b", "u", "s")])
    out = await FallbackProvider(primary, secondary).search("x")
    assert out[0].title == "b"
    assert secondary.calls == 1


async def test_fallback_on_error():
    primary = FakeProvider(exc=RuntimeError("quota exceeded"))
    secondary = FakeProvider([SearchResult("b", "u", "s")])
    out = await FallbackProvider(primary, secondary).search("x")
    assert out[0].title == "b"


async def test_secondary_unavailable_propagates():
    primary = FakeProvider(exc=RuntimeError("quota"))
    secondary = FakeProvider(exc=SearchUnavailable("blocked"))
    with pytest.raises(SearchUnavailable):
        await FallbackProvider(primary, secondary).search("x")


async def test_engines_passed_to_secondary():
    primary = FakeProvider([])
    secondary = FakeProvider([SearchResult("b", "u", "s")])
    fb = FallbackProvider(primary, secondary)

    captured = {}
    orig = secondary.search

    async def spy(query, max_results=5, engines=None):
        captured["engines"] = engines
        return await orig(query, max_results, engines)

    secondary.search = spy
    await fb.search("x", engines="openstreetmap")
    assert captured["engines"] == "openstreetmap"
