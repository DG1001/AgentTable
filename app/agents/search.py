"""Search agent (spec §5.3): a normal room participant with a web_search tool."""
from __future__ import annotations

import logging

from app.llm import get_client
from app.llm.parsing import call_and_log
from app.prompts import render
from app.search_provider import SearchUnavailable, get_provider

log = logging.getLogger("agenttable.search")


async def _knowledge_fallback(query_context: str, reason: str, group_id: int, task_id: int | None) -> str:
    """No live results: answer from the model's own knowledge, clearly flagged."""
    system = render("search_fallback", query_context=query_context, reason=reason)
    resp = await call_and_log(
        get_client("search"),
        [{"role": "system", "content": system},
         {"role": "user", "content": "Gib hilfreiche Vorschläge mit klarem Hinweis."}],
        group_id=group_id, task_id=task_id,
    )
    return resp.content.strip()


async def speak_in_room(agent, query_context: str, group_id: int, task_id: int | None = None, db=None) -> str:
    """Search the web for ``query_context`` and summarise it into the room.

    If the provider is unconfigured, blocked (rate-limit/CAPTCHA), or returns
    nothing, fall back to a clearly-flagged knowledge-based answer so the
    Rechercheur stays useful. Usable with or without an active task."""
    # OpenStreetMap + Wikipedia don't CAPTCHA and are ideal for venue/location
    # queries — used as a fallback when the general engines are blocked.
    CAPTCHA_FREE = "openstreetmap,wikipedia"

    provider = get_provider()
    reason = ""
    results = []
    if not provider.available:
        reason = "kein Such-Provider konfiguriert"
    else:
        try:
            results = await provider.search(query_context, max_results=5)
        except SearchUnavailable as exc:
            reason = f"Suchmaschinen temporär blockiert ({exc})"
            log.warning("search unavailable, retrying with CAPTCHA-free engines: %s", exc)
        except Exception as exc:  # noqa: BLE001
            reason = f"Web-Suche fehlgeschlagen ({exc.__class__.__name__})"
            log.warning("search failed: %s", exc)
        if not results and reason:  # retry on engines that don't rate-limit
            try:
                results = await provider.search(query_context, max_results=5, engines=CAPTCHA_FREE)
                if results:
                    reason = ""
            except Exception as exc:  # noqa: BLE001
                log.warning("captcha-free retry failed: %s", exc)

    if not results:
        return await _knowledge_fallback(query_context, reason or "keine Web-Treffer",
                                         group_id, task_id)

    results_text = "\n".join(f"- {r.title} ({r.url}): {r.snippet[:300]}" for r in results)
    system = render("search_room", query_context=query_context, results=results_text)
    resp = await call_and_log(
        get_client("search"),
        [{"role": "system", "content": system},
         {"role": "user", "content": "Fasse die Ergebnisse kompakt zusammen."}],
        group_id=group_id, task_id=task_id,
    )
    return resp.content.strip()
