"""Search agent (spec §5.3): a normal room participant with a web_search tool."""
from __future__ import annotations

from app import repo
from app.agents import context
from app.llm import get_client
from app.llm.parsing import call_and_log
from app.prompts import render
from app.search_provider import get_provider


async def speak_in_room(agent, task, query_context: str, db=None) -> str:
    """Run a web search for ``query_context`` and summarise it into the room."""
    provider = get_provider()
    if not provider.available:
        return ("Ich kann gerade nicht im Web suchen — es ist kein Such-Provider "
                "konfiguriert. (SEARCH_PROVIDER in der .env setzen.)")
    try:
        results = await provider.search(query_context, max_results=5)
    except Exception as exc:  # noqa: BLE001
        return f"Die Web-Suche ist fehlgeschlagen ({exc.__class__.__name__})."

    if not results:
        return "Ich habe zu dieser Anfrage leider nichts Brauchbares gefunden."

    results_text = "\n".join(f"- {r.title} ({r.url}): {r.snippet[:300]}" for r in results)
    system = render("search_room", query_context=query_context, results=results_text)
    resp = await call_and_log(
        get_client("search"),
        [{"role": "system", "content": system},
         {"role": "user", "content": "Fasse die Ergebnisse kompakt zusammen."}],
        group_id=task["group_id"], task_id=task["id"],
    )
    return resp.content.strip()
