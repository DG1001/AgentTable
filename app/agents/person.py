"""Person-agent behaviour (spec §5.1): onboarding, persona, private chat, room.

Private mode gathers availability via tool-calls; room mode argues the user's
soft preferences over the fixed candidate list. The agent never does slot math.
"""
from __future__ import annotations

from app import repo
from app.agents import context
from app.llm import get_client
from app.llm.parsing import call_and_log
from app.prompts import render
from app.tools import ASK_TOOL_SCHEMAS, TOOL_SCHEMAS, apply_tool_call

# how many user turns the onboarding lasts before we synthesise a persona
ONBOARDING_TURNS = 3

# The model frequently *claims* a room action ("ich hab den Rechercheur gefragt")
# without emitting the tool call. When the text is about search/organizer intent
# and no tool ran this turn, we force exactly that tool on a follow-up round.
_SEARCH_HINTS = (
    "rechercheur", "recherche", "such", "gesucht", "location", "locations", "bar",
    "restaurant", "kneipe", "lokal", "biergarten", "café", "cafe",
)
_ADMIN_HINTS = (
    "organisator", "orga ", "anstoß", "angestoß", "losgehen", "loslegen",
    "vorschlag machen", "stand der", "wer noch fehlt", "starten kann",
)
def _forced_tool_for(text: str) -> str | None:
    """Map an action-announcing reply to the room tool it should have called.

    Deliberately does NOT cover start_smalltalk: forcing that from loose word
    matches spuriously kicked off small talk (e.g. mid-scheduling). Small talk is
    only started when the model explicitly calls the tool on the user's request."""
    t = text.lower()
    if any(h in t for h in _SEARCH_HINTS):
        return "ask_search"
    if any(h in t for h in _ADMIN_HINTS):
        return "ask_admin"
    return None


async def _run_onboarding(user, agent) -> str:
    """Ask the next onboarding question; after enough turns, build the persona."""
    history = repo.list_private_messages(user["id"])
    user_turns = sum(1 for m in history if m["role"] == "user")

    if user_turns >= ONBOARDING_TURNS:
        persona = await _generate_persona(user)
        repo.set_persona(user["id"], persona)
        repo.set_agent_state(agent["id"], "onboarding_done", True)
        return (
            "Danke, ich hab ein Bild von dir! Hier meine Kurz-Persona für dich:\n\n"
            f"_{persona}_\n\nPasst das so? Dann können wir loslegen — sobald eine "
            "Terminfindung startet, melde ich mich."
        )

    sys = render("onboarding", display_name=user["display_name"])
    messages = [{"role": "system", "content": sys}]
    for m in history:
        role = "assistant" if m["role"] == "agent" else "user"
        if m["role"] == "system":
            continue
        messages.append({"role": role, "content": m["content"]})
    resp = await call_and_log(get_client("person"), messages, group_id=user["group_id"])
    return resp.content.strip() or "Erzähl mir ein bisschen von dir!"


async def _generate_persona(user) -> str:
    history = repo.list_private_messages(user["id"])
    transcript = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Agent'}: {m['content']}"
        for m in history
        if m["role"] in ("user", "agent")
    )
    prompt = render("persona_gen", display_name=user["display_name"], transcript=transcript)
    resp = await call_and_log(
        get_client("person"), [{"role": "user", "content": prompt}], group_id=user["group_id"]
    )
    return resp.content.strip() or f"Du bist der Agent von {user['display_name']}."


def _private_system_prompt(user, agent, task) -> str:
    import json

    params = json.loads(task["params_json"]) if task else {}
    return render(
        "person_private",
        display_name=user["display_name"],
        persona=user["persona"] or "(noch keine Persona)",
        task_context=context.task_context_text(task),
        range_start=params.get("range_start", "-"),
        range_end=params.get("range_end", "-"),
        granularity=params.get("granularity", "evening"),
    )


async def handle_private_message(user_id: int, text: str) -> list[dict]:
    """Process one user message; return the agent messages to persist+broadcast.

    Each returned dict is ``{"role": "agent"|"system", "content": str}``.
    """
    user = repo.get_user(user_id)
    agent = repo.get_person_agent(user_id)
    repo.add_private_message(user_id, "user", text)
    repo.touch_user(user_id)

    onboarding_done = repo.get_agent_state(agent["id"], "onboarding_done", False)
    if not onboarding_done:
        reply = await _run_onboarding(user, agent)
        repo.add_private_message(user_id, "agent", reply)
        return [{"role": "agent", "content": reply}]

    task = repo.get_active_task(user["group_id"])
    system = _private_system_prompt(user, agent, task)
    history = repo.list_private_messages(user_id, limit=20)
    messages = [{"role": "system", "content": system}]
    for m in history:
        if m["role"] == "system":
            continue
        role = "assistant" if m["role"] == "agent" else "user"
        messages.append({"role": role, "content": m["content"]})

    # scheduling tools only during an active task; the "ask" tools always
    tools = TOOL_SCHEMAS if task else ASK_TOOL_SCHEMAS
    import json as _json

    params = _json.loads(task["params_json"]) if task else {}
    emitted: list[dict] = []
    ready_triggered = False
    nudged = False
    called_tools: set[str] = set()
    forced_choice: dict | None = None
    # Intent from the USER's message is far more stable than the model's varying
    # reply wording — use it as the primary signal for forcing a room tool.
    user_intent = _forced_tool_for(text)

    # allow a few tool-call rounds so the agent can act then confirm
    for _round in range(4):
        resp = await call_and_log(
            get_client("person"), messages, tools=tools,
            tool_choice=forced_choice,
            group_id=user["group_id"], task_id=task["id"] if task else None,
        )
        forced_choice = None
        if resp.tool_calls:
            called_tools.update(tc.name for tc in resp.tool_calls)
            messages.append({
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": _json.dumps(tc.arguments)}}
                    for tc in resp.tool_calls
                ],
            })
            for tc in resp.tool_calls:
                if tc.name in ("ask_admin", "ask_search", "ask_agent", "start_smalltalk"):
                    tool_msg = await _handle_room_tool(user, task, tc.name, tc.arguments)
                else:
                    result = apply_tool_call(user_id, tc.name, tc.arguments, params)
                    if result.triggered_ready:
                        ready_triggered = True
                    tool_msg = result.message
                messages.append({
                    "role": "tool", "tool_call_id": tc.id, "content": tool_msg,
                })
            continue

        text = resp.content.strip()
        # Anti-phantom-action guard: the user asked for a room action (or the
        # model claims one) but the matching tool never ran this turn — force it.
        forced_tool = None
        if tools and not nudged:
            forced_tool = user_intent or _forced_tool_for(text)
            if forced_tool in called_tools:
                forced_tool = None
        if forced_tool:
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "system", "content": (
                f"Du hast eine Aktion angekündigt, aber KEINEN Tool-Call gemacht — es "
                f"ist also nichts passiert. Führe sie JETZT per {forced_tool} aus.")})
            nudged = True
            forced_choice = {"type": "function", "function": {"name": forced_tool}}
            continue

        if text:
            repo.add_private_message(user_id, "agent", text)
            emitted.append({"role": "agent", "content": text})
        break

    if ready_triggered and task:
        repo.set_agent_state(agent["id"], f"ready:{task['id']}", True)

    return emitted


def _resolve_person_agent(group_id: int, needle: str, exclude_agent_id: int | None = None, db=None):
    """Find a person-agent by its name or its user's display name (fuzzy)."""
    needle = needle.strip().lower()
    persons = [a for a in repo.list_person_agents(group_id, db=db) if a["id"] != exclude_agent_id]
    if not needle:
        return None
    for a in persons:  # exact agent-name match first
        if a["name"].lower() == needle:
            return a
    for a in persons:  # then substring on agent name or user display name
        u = repo.get_user(a["user_id"], db=db)
        if needle in a["name"].lower() or needle in u["display_name"].lower() \
                or u["display_name"].lower() in needle:
            return a
    return None


async def answer_question(agent, task, question: str, asker_name: str, db=None) -> str:
    """Have a person-agent answer a direct question from another agent, grounded
    in its user's stored availability/persona (one bounded response — spec §10)."""
    user = repo.get_user(agent["user_id"], db=db)
    system = render(
        "person_answer",
        display_name=user["display_name"],
        persona=user["persona"] or "(keine Persona)",
        task_context=context.task_context_text(task),
        user_summary=context.user_availability_summary(user["id"], db=db),
        asker=asker_name,
        question=question,
    )
    resp = await call_and_log(
        get_client("person"),
        [{"role": "system", "content": system}, {"role": "user", "content": question}],
        group_id=user["group_id"], task_id=task["id"] if task else None,
    )
    return resp.content.strip() or "(keine Antwort)"


async def _handle_room_tool(user, task, name: str, args: dict) -> str:
    """Handle the two room-facing person tools (ask_admin / ask_search).

    Both post into the group room (so everyone sees it and the sprite speaks) and
    return a short text the person-agent relays to its user in the private chat.
    Imported lazily to avoid a person<->moderator import cycle.
    """
    from app import moderator
    from app.agents import search as search_agent

    if name == "ask_admin":
        return await moderator.handle_admin_request(user, task, (args.get("request") or "").strip())

    if name == "start_smalltalk":
        return await moderator.start_smalltalk(user, args.get("topic") or "")

    if name == "ask_agent":
        group_id = user["group_id"]
        asker = repo.get_person_agent(user["id"])
        question = (args.get("query") or args.get("question") or "").strip()
        target = _resolve_person_agent(group_id, args.get("agent_name") or "", exclude_agent_id=asker["id"])
        if not question:
            return "Leere Frage — was genau soll ich fragen?"
        if target is None:
            others = ", ".join(a["name"] for a in repo.list_person_agents(group_id) if a["id"] != asker["id"])
            return f"Diesen Agenten finde ich nicht. Verfügbar sind: {others}."
        await moderator.post_room(group_id, f"@{target['name']}: {question}", agent_id=asker["id"])
        answer = await answer_question(target, task, question, user["display_name"])
        await moderator.post_room(group_id, answer, agent_id=target["id"])
        return f"{target['name']} antwortet: {answer}"

    # ask_search
    query = (args.get("query") or "").strip()
    if not query:
        return "Leere Suchanfrage — ich brauche eine konkrete Frage."
    group_id = user["group_id"]
    search = repo.get_agent_by_kind(group_id, "search")
    person_agent = repo.get_person_agent(user["id"])
    await moderator.post_room(
        group_id, f"Zwischenfrage an den Rechercheur: {query}", agent_id=person_agent["id"]
    )
    answer = await search_agent.speak_in_room(
        search, query, group_id, task["id"] if task else None
    )
    await moderator.post_room(group_id, answer, agent_id=search["id"])
    return answer


async def speak_in_room(agent, task, candidates_md: str, db=None) -> str:
    """Produce the person-agent's room contribution when called by the moderator."""
    user = repo.get_user(agent["user_id"], db=db)
    system = render(
        "person_room",
        display_name=user["display_name"],
        persona=user["persona"] or "(keine Persona)",
        task_context=context.task_context_text(task),
        user_summary=context.user_availability_summary(user["id"], db=db),
        candidates=candidates_md,
        running_summary=context.running_summary(task["id"], db=db),
        recent_messages=context.recent_room_text(task["group_id"], db=db),
    )
    resp = await call_and_log(
        get_client("person"), [{"role": "system", "content": system},
                               {"role": "user", "content": "Du bist an der Reihe. Äußere dich knapp."}],
        group_id=task["group_id"], task_id=task["id"],
    )
    return resp.content.strip()


def is_ready(agent, task, db=None) -> bool:
    return bool(repo.get_agent_state(agent["id"], f"ready:{task['id']}", False, db=db))
