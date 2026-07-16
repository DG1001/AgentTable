"""Integration tests for the task state machine + moderator loop (spec §6).

No network: the LLM is a scripted mock, availability is written directly, and we
assert the deterministic transitions collecting -> negotiating -> decided and the
no-intersection fallback collecting <- negotiating.
"""
from app import repo
from app.llm.client import LLMResponse
from app.moderator import check_and_advance, start_task
from app.service import bootstrap_group

PARAMS = {"description": "Spieleabend", "range_start": "2026-07-20",
          "range_end": "2026-07-27", "granularity": "evening"}


def router(role, messages, tools, response_format):
    system = messages[0]["content"] if messages else ""
    if response_format and response_format.get("type") == "json_object":
        if "next_speaker" in system:
            return LLMResponse(content='{"next_speaker": "END_ROUND", "progress": false, "reason": "fertig"}')
        if "candidate_index" in system:
            return LLMResponse(content='{"candidate_index": 1, "location": "Café Central", "summary": "passt allen"}')
    return LLMResponse(content="Alles klar, los geht's.")


def _set_ready(group_id, task_id, slot):
    for a in repo.list_person_agents(group_id):
        repo.replace_availability(a["user_id"], [
            {"start": slot[0], "end": slot[1], "preference": "yes"}
        ])
        repo.set_agent_state(a["id"], f"ready:{task_id}", True)


async def test_full_flow_reaches_decided(db, mock_llm):
    mock_llm.router = router
    group_id, _ = bootstrap_group("Runde", ["Alex", "Bea", "Chris"])

    task = await start_task(group_id, PARAMS)
    assert task["status"] == "collecting"

    _set_ready(group_id, task["id"], ("2026-07-21T18:00", "2026-07-21T22:00"))
    await check_and_advance(task["id"])

    final = repo.get_task(task["id"])
    assert final["status"] == "decided"
    import json
    result = json.loads(final["result_json"])
    assert result["slot"]["start"] == "2026-07-21T18:00"
    assert result["location"] == "Café Central"

    # announcement + candidate table + result all landed in the room
    room = repo.list_room_messages(group_id)
    assert len(room) >= 3


async def test_admin_request_reports_missing(db, mock_llm):
    mock_llm.router = router
    from app.moderator import handle_admin_request
    group_id, members = bootstrap_group("Runde", ["Alex", "Bea", "Chris"])
    task = await start_task(group_id, PARAMS)

    # only Bea is ready
    persons = repo.list_person_agents(group_id)
    repo.replace_availability(persons[1]["user_id"],
                              [{"start": "2026-07-21T18:00", "end": "2026-07-21T22:00", "preference": "yes"}])
    repo.set_agent_state(persons[1]["id"], f"ready:{task['id']}", True)

    alex = repo.get_user(members[0]["user_id"])
    msg = await handle_admin_request(alex, task, "Kann's losgehen?")
    assert "warte noch auf" in msg
    assert "Alex" in msg and "Chris" in msg and "Bea" not in msg
    # the person's request and the admin reply both landed in the room
    contents = [r["content"] for r in repo.list_room_messages(group_id)]
    assert "Kann's losgehen?" in contents


async def test_phantom_action_forces_search_tool(db, mock_llm):
    """If the model claims a room action but emits no tool call, the intended
    tool is forced on a follow-up round (spec robustness)."""
    from app.llm.client import LLMResponse, ToolCall
    from app.search_provider import NullProvider, set_provider
    from app.agents import person
    set_provider(NullProvider())
    group_id, members = bootstrap_group("Runde", ["Alex"])
    agent = repo.get_person_agent(members[0]["user_id"])
    repo.set_agent_state(agent["id"], "onboarding_done", True)
    await start_task(group_id, PARAMS)

    state = {"n": 0}

    def r(role, messages, tools, rf):
        if role != "person":
            return LLMResponse(content="ok")
        state["n"] += 1
        if state["n"] == 1:
            return LLMResponse(content="Klar, ich frag den Rechercheur!")  # phantom, no tool
        if state["n"] == 2:
            return LLMResponse(tool_calls=[ToolCall("t1", "ask_search", {"query": "bars"})])
        return LLMResponse(content="Ist raus.")
    mock_llm.router = r

    before = len(repo.list_room_messages(group_id))
    await person.handle_private_message(members[0]["user_id"], "frag den rechercheur nach bars in stuttgart")
    new = repo.list_room_messages(group_id)[before:]
    assert any("Zwischenfrage an den Rechercheur" in m["content"] for m in new)
    set_provider(None)


async def test_smalltalk_runs_and_ends(db, mock_llm):
    """Small talk: each agent speaks >= the minimum, admin frames it, loop ends."""
    import random as _r
    import app.moderator as moderator
    from app.llm.client import LLMResponse
    from app.moderator import _ST_MIN_PER_AGENT, _run_smalltalk
    from app.search_provider import NullProvider, set_provider
    set_provider(NullProvider())
    moderator._ST_PAUSE = (0, 0)  # no real sleeping in tests
    _r.seed(1)
    group_id, _ = bootstrap_group("Runde", ["Alex", "Bea"])

    def router(role, messages, tools, rf):
        if rf and rf.get("type") == "json_object":
            return LLMResponse(content='{"end": true, "reason": "rund"}')
        return LLMResponse(content="Lockeres Geplauder hier. 😄")
    mock_llm.router = router

    await _run_smalltalk(group_id, "Urlaub", "Alex")

    msgs = repo.list_room_messages(group_id)
    names = [repo.get_agent(m["agent_id"])["name"] if m["agent_id"] else "System" for m in msgs]
    assert names.count("Alexs Agent") >= _ST_MIN_PER_AGENT
    assert names.count("Beas Agent") >= _ST_MIN_PER_AGENT
    assert names.count("Organisator") >= 2  # intro + closing
    set_provider(None)


async def test_smalltalk_blocked_only_during_negotiation(db, mock_llm):
    """Small talk is blocked only during active negotiation, not while collecting."""
    from app.moderator import smalltalk_block_reason
    group_id, _ = bootstrap_group("Runde", ["Alex"])
    task = await start_task(group_id, PARAMS)  # collecting

    assert smalltalk_block_reason(group_id) is None  # collecting -> allowed

    repo.update_task_status(task["id"], "negotiating")
    assert "verhandeln" in (smalltalk_block_reason(group_id) or "")  # negotiating -> blocked


async def test_budget_scoped_to_negotiation(db, mock_llm):
    """Heavy collecting-phase usage (ask_* chatter) must NOT block negotiation —
    the budget only counts calls made after negotiation starts."""
    mock_llm.router = router
    from app.config import settings
    group_id, _ = bootstrap_group("Runde", ["Alex", "Bea"])
    task = await start_task(group_id, PARAMS)
    _set_ready(group_id, task["id"], ("2026-07-21T18:00", "2026-07-21T22:00"))

    # simulate lots of collecting-phase calls (over the whole budget)
    for _ in range(settings.task_llm_budget + 20):
        repo.log_usage("person", "mock", 1, 1, group_id=group_id, task_id=task["id"])

    await check_and_advance(task["id"])
    assert repo.get_task(task["id"])["status"] == "decided"


async def test_ask_agent_targets_other_and_posts_qa(db, mock_llm):
    mock_llm.router = lambda role, m, t, rf: LLMResponse(content="Dienstag passt bei mir gut.")
    from app.agents.person import _handle_room_tool
    group_id, members = bootstrap_group("Runde", ["Alex", "Bea", "Chris"])
    task = await start_task(group_id, PARAMS)
    alex = repo.get_user(members[0]["user_id"])

    # Alex's agent asks "Bea" (resolves by display name) a question
    result = await _handle_room_tool(alex, task, "ask_agent",
                                     {"agent_name": "Bea", "question": "Kannst du Dienstag?"})
    assert "Beas Agent" in result and "Dienstag" in result

    contents = [r["content"] for r in repo.list_room_messages(group_id)]
    assert any("@Beas Agent: Kannst du Dienstag?" in c for c in contents)  # the question
    assert any("Dienstag passt bei mir gut." in c for c in contents)       # the answer


async def test_ask_agent_unknown_target(db, mock_llm):
    from app.agents.person import _handle_room_tool
    group_id, members = bootstrap_group("Runde", ["Alex", "Bea"])
    task = await start_task(group_id, PARAMS)
    alex = repo.get_user(members[0]["user_id"])
    result = await _handle_room_tool(alex, task, "ask_agent",
                                     {"agent_name": "Zaphod", "question": "hi?"})
    assert "finde ich nicht" in result and "Beas Agent" in result


async def test_no_intersection_goes_back_to_collecting(db, mock_llm):
    mock_llm.router = router
    group_id, _ = bootstrap_group("Runde", ["Alex", "Bea"])
    task = await start_task(group_id, PARAMS)

    persons = repo.list_person_agents(group_id)
    # disjoint availability -> no common slot
    repo.replace_availability(persons[0]["user_id"],
                              [{"start": "2026-07-21T18:00", "end": "2026-07-21T22:00", "preference": "yes"}])
    repo.replace_availability(persons[1]["user_id"],
                              [{"start": "2026-07-23T18:00", "end": "2026-07-23T22:00", "preference": "yes"}])
    for a in persons:
        repo.set_agent_state(a["id"], f"ready:{task['id']}", True)

    await check_and_advance(task["id"])

    back = repo.get_task(task["id"])
    assert back["status"] == "collecting"
    assert back["iteration"] == 1


async def test_second_no_intersection_fails(db, mock_llm):
    mock_llm.router = router
    group_id, _ = bootstrap_group("Runde", ["Alex", "Bea"])
    task = await start_task(group_id, PARAMS)
    persons = repo.list_person_agents(group_id)

    def disjoint():
        repo.replace_availability(persons[0]["user_id"],
                                  [{"start": "2026-07-21T18:00", "end": "2026-07-21T22:00", "preference": "yes"}])
        repo.replace_availability(persons[1]["user_id"],
                                  [{"start": "2026-07-23T18:00", "end": "2026-07-23T22:00", "preference": "yes"}])
        for a in persons:
            repo.set_agent_state(a["id"], f"ready:{task['id']}", True)

    disjoint()
    await check_and_advance(task["id"])  # iteration 1, back to collecting
    disjoint()
    await check_and_advance(task["id"])  # iteration 2, back to collecting
    disjoint()
    await check_and_advance(task["id"])  # exceeds max -> failed

    assert repo.get_task(task["id"])["status"] == "failed"
