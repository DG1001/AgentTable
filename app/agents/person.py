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
from app.tools import TOOL_SCHEMAS, apply_tool_call

# how many user turns the onboarding lasts before we synthesise a persona
ONBOARDING_TURNS = 3


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

    tools = TOOL_SCHEMAS if task else None
    import json as _json

    params = _json.loads(task["params_json"]) if task else {}
    emitted: list[dict] = []
    ready_triggered = False

    # allow up to 2 tool-call rounds so the agent can act then confirm
    for _round in range(3):
        resp = await call_and_log(
            get_client("person"), messages, tools=tools, group_id=user["group_id"],
            task_id=task["id"] if task else None,
        )
        if resp.tool_calls:
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
                result = apply_tool_call(user_id, tc.name, tc.arguments, params)
                if result.triggered_ready:
                    ready_triggered = True
                messages.append({
                    "role": "tool", "tool_call_id": tc.id, "content": result.message,
                })
            continue
        if resp.content.strip():
            repo.add_private_message(user_id, "agent", resp.content.strip())
            emitted.append({"role": "agent", "content": resp.content.strip()})
        break

    if ready_triggered and task:
        repo.set_agent_state(agent["id"], f"ready:{task['id']}", True)

    return emitted


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
