"""Admin/moderator agent + task state machine (spec §5.2, §6).

State transitions are deterministic Python; the admin *LLM* only writes prose
(announcement, summaries) and makes two structured decisions: speaker-selection
and the final pick. Hard guards live in code:

* budget cap per task (§10),
* max messages per round,
* no agent twice in a row,
* two consecutive "no progress" judgements end the round.

Everything that reaches the room is persisted first, then broadcast, so a restart
can replay and the state machine can resume (§10 robustness).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta

from app import repo, serialize
from app.agents import context, person
from app.agents import search as search_agent
from app.config import settings
from app.llm import get_client
from app.llm.parsing import call_and_log, parse_json
from app.prompts import render
from app.realtime import hub
from app.scheduling import (
    Slot,
    compute_candidates,
    find_candidate,
    generate_slots,
    has_full_intersection,
    render_candidate_table,
)

log = logging.getLogger("agenttable.moderator")


# --- broadcast helpers ----------------------------------------------------
async def post_room(group_id: int, content: str, agent_id: int | None = None, kind: str = "agent") -> int:
    mid = repo.add_room_message(group_id, kind, content, agent_id=agent_id)
    row = repo.get_room_message(mid)
    await hub.to_room(group_id, {"type": "room_message", "message": serialize.room_message(row)})
    return mid


async def broadcast_task(task) -> None:
    await hub.to_room(task["group_id"], {"type": "task_update", "task": serialize.task_public(task)})


async def notify_user_private(user_id: int, text: str) -> None:
    repo.add_private_message(user_id, "system", text)
    row = repo.list_private_messages(user_id, limit=1)[-1]
    await hub.to_private(user_id, {"type": "private_message", "message": serialize.private_message(row)})


# --- budget guard ---------------------------------------------------------
# The budget guards the AUTONOMOUS negotiation loop (spec §10), not the
# user-driven collecting phase (onboarding, ask_admin/ask_agent/ask_search).
# So we count only the calls made since negotiation started for this attempt.
def _neg_baseline(task) -> int:
    admin = repo.get_agent_by_kind(task["group_id"], "admin")
    return int(repo.get_agent_state(admin["id"], f"neg_base:{task['id']}", 0))


def budget_ok(task) -> bool:
    used = repo.count_task_calls(task["id"]) - _neg_baseline(task)
    return used < settings.task_llm_budget


async def _pause_over_budget(task) -> None:
    await post_room(
        task["group_id"],
        f"⏸️ Budget-Limit erreicht ({settings.task_llm_budget} LLM-Calls). "
        "Terminfindung pausiert.",
        kind="system",
    )
    log.warning("task %s paused: budget exceeded", task["id"])


# --- task lifecycle -------------------------------------------------------
async def start_task(group_id: int, params: dict) -> dict:
    """Create a schedule_meeting task, announce it, and prompt every user."""
    task_id = repo.create_task(group_id, params)
    task = repo.get_task(task_id)
    admin = repo.get_agent_by_kind(group_id, "admin")

    system = render(
        "admin_announce",
        range_start=params.get("range_start"),
        range_end=params.get("range_end"),
        granularity=params.get("granularity", "evening"),
        description=params.get("description", "Gemeinsames Treffen"),
    )
    resp = await call_and_log(
        get_client("admin"), [{"role": "system", "content": system},
                              {"role": "user", "content": "Formuliere die Startansage."}],
        group_id=group_id, task_id=task_id,
    )
    announce = resp.content.strip() or "Wir suchen einen gemeinsamen Termin. Bitte Verfügbarkeiten klären."
    await post_room(group_id, announce, agent_id=admin["id"])

    for u in repo.list_person_agents(group_id):
        await notify_user_private(
            u["user_id"],
            "📅 Eine Terminfindung wurde gestartet. Erzähl mir, wann du im Zeitraum "
            f"{params.get('range_start')}–{params.get('range_end')} kannst!",
        )
    await broadcast_task(task)
    log.info("task %s started for group %s params=%s", task_id, group_id, params)
    return task


async def handle_admin_request(user, task, request_text: str) -> str:
    """A person-agent nudges the organizer on the user's behalf (spec follow-up).

    The person's request is voiced in the room; the admin answers with the real
    status (who is still missing) and, if everyone is ready, kicks off the
    negotiation. Returns a short German text the person-agent relays to the user.
    """
    group_id = user["group_id"]
    admin = repo.get_agent_by_kind(group_id, "admin")
    person_agent = repo.get_person_agent(user["id"])
    if request_text:
        await post_room(group_id, request_text, agent_id=person_agent["id"])

    if task is None:
        msg = ("Aktuell läuft keine Terminfindung. Eine Person kann oben über den "
               "Button 'Terminfindung' eine starten.")
        await post_room(group_id, msg, agent_id=admin["id"])
        return msg

    task = repo.get_task(task["id"])
    status = task["status"]
    if status == "collecting":
        persons = repo.list_person_agents(group_id)
        missing = [repo.get_user(a["user_id"])["display_name"]
                   for a in persons if not person.is_ready(a, task)]
        if missing:
            msg = (f"Ich kann noch nicht starten — ich warte noch auf: "
                   f"{', '.join(missing)}. Sobald alle fertig sind, geht's los.")
            await post_room(group_id, msg, agent_id=admin["id"])
            return msg
        msg = "Alle sind bereit — ich starte jetzt die Terminplanung! 🗓️"
        await post_room(group_id, msg, agent_id=admin["id"])
        asyncio.create_task(check_and_advance(task["id"]))
        return msg
    if status == "negotiating":
        msg = "Die Verhandlung läuft bereits — ich melde mich mit dem Ergebnis."
        await post_room(group_id, msg, agent_id=admin["id"])
        return msg
    msg = "Die Terminfindung ist bereits abgeschlossen. Schau ins Ergebnis oben. 🙂"
    await post_room(group_id, msg, agent_id=admin["id"])
    return msg


# --- small talk (gimmick) -------------------------------------------------
_smalltalk_active: set[int] = set()
_ST_MIN_PER_AGENT = 2  # everyone should speak at least this often before ending
_ST_PAUSE = (2.0, 5.5)  # random seconds between messages (feels more natural)


async def start_smalltalk(user, topic: str) -> str:
    """Kick off a background small-talk session in the room (user-requested)."""
    group_id = user["group_id"]
    if group_id in _smalltalk_active:
        return "Im Raum läuft gerade schon ein Smalltalk. 😄"
    _smalltalk_active.add(group_id)
    asyncio.create_task(_run_smalltalk(group_id, topic.strip(), user["display_name"]))
    extra = f" (Thema: {topic.strip()})" if topic.strip() else ""
    return f"Alles klar — ich hab den Smalltalk im Gruppenraum angestoßen{extra}. Schau mal rein! 🎉"


def _pick_smalltalk_speaker(persons, searcher, last_name, counts, turn):
    """Random next speaker: never twice in a row, bias to those below the minimum,
    occasionally the Rechercheur for a fun fact."""
    if searcher and turn > 0 and last_name != searcher["name"] and random.random() < 0.22:
        return searcher
    pool = [a for a in persons if a["name"] != last_name] or list(persons)
    below = [a for a in pool if counts.get(a["name"], 0) < _ST_MIN_PER_AGENT]
    return random.choice(below or pool)


async def _smalltalk_say(agent, topic: str, group_id: int) -> str:
    user = repo.get_user(agent["user_id"])
    topic_line = f"Thema (Vorschlag): {topic}" if topic else "Es gibt kein festes Thema — lass dir was einfallen."
    system = render(
        "smalltalk_person",
        display_name=user["display_name"],
        persona=user["persona"] or "(keine Persona)",
        topic_line=topic_line,
        recent_messages=context.recent_room_text(group_id),
    )
    resp = await call_and_log(
        get_client("person"),
        [{"role": "system", "content": system},
         {"role": "user", "content": "Du bist dran. Sag locker etwas (1–2 Sätze)."}],
        group_id=group_id,
    )
    return resp.content.strip()


async def _smalltalk_should_end(group_id: int) -> bool:
    admin = repo.get_agent_by_kind(group_id, "admin")
    system = render("smalltalk_end", recent_messages=context.recent_room_text(group_id, k=14))
    resp = await call_and_log(
        get_client("admin"),
        [{"role": "system", "content": system},
         {"role": "user", "content": "Beenden? Antworte als JSON."}],
        response_format={"type": "json_object"}, group_id=group_id,
    )
    return bool(parse_json(resp.content).get("end", False))


async def _run_smalltalk(group_id: int, topic: str, initiator: str) -> None:
    try:
        admin = repo.get_agent_by_kind(group_id, "admin")
        persons = repo.list_person_agents(group_id)
        searcher = repo.get_agent_by_kind(group_id, "search")
        if not persons:
            return
        intro = (f"{initiator} hat Lust auf Smalltalk! "
                 + (f"Thema: {topic}. " if topic else "Kein festes Thema — quatscht einfach. ")
                 + "Legt los. 😄")
        await post_room(group_id, intro, agent_id=admin["id"])

        counts: dict[str, int] = {}
        last_name = None
        max_turns = min(28, 6 + len(persons) * 4)
        last_content = topic

        for turn in range(max_turns):
            everyone_spoke = all(counts.get(a["name"], 0) >= _ST_MIN_PER_AGENT for a in persons)
            if everyone_spoke and await _smalltalk_should_end(group_id):
                break
            if turn > 0 and _ST_PAUSE[1] > 0:  # natural pause between messages
                await asyncio.sleep(random.uniform(*_ST_PAUSE))
            speaker = _pick_smalltalk_speaker(persons, searcher, last_name, counts, turn)
            if speaker["kind"] == "search":
                seed = (last_content or topic or "Alltag")[:80]
                content = await search_agent.speak_in_room(
                    speaker, f"überraschender oder lustiger Fakt zu: {seed}", group_id
                )
            else:
                content = await _smalltalk_say(speaker, topic, group_id)
                last_content = content or last_content
            if not content:
                content = "…"
            await post_room(group_id, content, agent_id=speaker["id"])
            counts[speaker["name"]] = counts.get(speaker["name"], 0) + 1
            last_name = speaker["name"]

        closing = await call_and_log(
            get_client("admin"),
            [{"role": "system", "content": render("smalltalk_close",
                                                  recent_messages=context.recent_room_text(group_id, k=14))},
             {"role": "user", "content": "Beende den Smalltalk charmant."}],
            group_id=group_id,
        )
        await post_room(group_id, closing.content.strip() or "So, genug geplaudert! 😄", agent_id=admin["id"])
        log.info("smalltalk in group %s ended after %d turns", group_id, sum(counts.values()))
    except Exception:  # noqa: BLE001
        log.exception("smalltalk failed")
    finally:
        _smalltalk_active.discard(group_id)


def _collecting_timed_out(task) -> bool:
    created = datetime.fromisoformat(task["created_at"])
    return datetime.utcnow() >= created + timedelta(hours=settings.collecting_timeout_hours)


def _participant_ids(group_id: int) -> list[int]:
    return [a["user_id"] for a in repo.list_person_agents(group_id)]


def _mark_missing_flexible(task, missing_user_ids: list[int]) -> None:
    """Fill in 'maybe' availability across the whole range for users who never
    responded, so they no longer block the intersection (spec §6, timeout)."""
    p = json.loads(task["params_json"])
    slots = generate_slots(
        datetime.fromisoformat(p["range_start"]).date()
        if "T" in p["range_start"] else _date(p["range_start"]),
        _date(p["range_end"]),
        p.get("granularity", "evening"),
    )
    for uid in missing_user_ids:
        repo.replace_availability(
            uid, [{"start": s.start, "end": s.end, "preference": "maybe"} for s in slots]
        )


def _date(value: str):
    from datetime import date

    return date.fromisoformat(value[:10])


async def check_and_advance(task_id: int) -> None:
    """Called on every mark_ready and periodically: advance collecting->negotiating."""
    task = repo.get_task(task_id)
    if task is None or task["status"] != "collecting":
        return
    persons = repo.list_person_agents(task["group_id"])
    if not persons:
        return
    ready = [a for a in persons if person.is_ready(a, task)]
    timed_out = _collecting_timed_out(task)

    if len(ready) == len(persons) or timed_out:
        if timed_out and len(ready) < len(persons):
            missing = [a["user_id"] for a in persons if a not in ready]
            _mark_missing_flexible(task, missing)
            await post_room(
                task["group_id"],
                "⏱️ Zeitfenster abgelaufen — fehlende Rückmeldungen werden als „flexibel“ gewertet.",
                kind="system",
            )
        repo.update_task_status(task_id, "negotiating")
        task = repo.get_task(task_id)
        await broadcast_task(task)
        log.info("task %s -> negotiating (ready=%d/%d, timeout=%s)",
                 task_id, len(ready), len(persons), timed_out)
        await run_negotiation(task_id)


async def run_negotiation(task_id: int) -> None:
    """The negotiating phase: candidates -> discussion -> decision (spec §6)."""
    task = repo.get_task(task_id)
    if task is None or task["status"] != "negotiating":
        return
    group_id = task["group_id"]
    admin = repo.get_agent_by_kind(group_id, "admin")
    # anchor the budget window to the start of THIS negotiation attempt
    repo.set_agent_state(admin["id"], f"neg_base:{task['id']}", repo.count_task_calls(task["id"]))

    availability = repo.list_group_availability(group_id)
    candidates = compute_candidates(
        [dict(r) for r in availability], _participant_ids(group_id), top_n=settings.candidate_count
    )
    table = render_candidate_table(candidates)
    await post_room(group_id, "Hier die möglichen Termine:\n\n" + table, agent_id=admin["id"])

    if not has_full_intersection(candidates):
        await _handle_no_intersection(task, candidates)
        return

    await _run_room_round(task, candidates, table)
    await _decide_and_finalize(repo.get_task(task_id), candidates, table)


# --- room round with guards ----------------------------------------------
def _pick_fallback(speakers: dict, last_name: str | None, counts: dict[str, int]) -> str | None:
    """Choose an eligible speaker (not the last one) with the fewest turns."""
    eligible = [n for n in speakers if n != last_name]
    if not eligible:
        return None
    return min(eligible, key=lambda n: counts.get(n, 0))


async def _select_speaker(task, speakers: dict, table: str) -> tuple[str, bool, str]:
    admin = repo.get_agent_by_kind(task["group_id"], "admin")
    system = render(
        "admin_select",
        speakers=", ".join(speakers.keys()),
        candidates=table,
        running_summary=context.running_summary(task["id"]),
        recent_messages=context.recent_room_text(task["group_id"]),
    )
    resp = await call_and_log(
        get_client("admin"),
        [{"role": "system", "content": system},
         {"role": "user", "content": "Wer spricht als Nächstes? Antworte als JSON."}],
        response_format={"type": "json_object"},
        group_id=task["group_id"], task_id=task["id"],
    )
    data = parse_json(resp.content)
    next_speaker = str(data.get("next_speaker", "END_ROUND")).strip()
    progress = bool(data.get("progress", False))
    reason = str(data.get("reason", ""))
    return next_speaker, progress, reason


async def _run_room_round(task, candidates, table: str) -> None:
    group_id = task["group_id"]
    persons = repo.list_person_agents(group_id)
    speakers = {a["name"]: a for a in persons}
    search = repo.get_agent_by_kind(group_id, "search")
    if search:
        speakers[search["name"]] = search

    last_name: str | None = None
    counts: dict[str, int] = {}
    false_streak = 0

    for turn in range(settings.max_messages_per_round):
        task = repo.get_task(task["id"])
        if not budget_ok(task):
            await _pause_over_budget(task)
            return

        next_name, progress, reason = await _select_speaker(task, speakers, table)
        log.info("moderator select turn=%d next=%s progress=%s reason=%s",
                 turn, next_name, progress, reason)

        if next_name.upper() == "END_ROUND":
            break
        false_streak = false_streak + 1 if not progress else 0
        if false_streak >= 2:
            await post_room(group_id, "Keine neuen Argumente mehr — ich entscheide.", kind="system")
            break

        agent = speakers.get(next_name)
        if agent is None or agent["name"] == last_name:
            fallback = _pick_fallback(speakers, last_name, counts)
            if fallback is None:
                break
            agent = speakers[fallback]

        if agent["kind"] == "search":
            fav = candidates[0].label() if candidates else ""
            content = await search_agent.speak_in_room(
                agent, f"Location-Vorschläge für ein Gruppentreffen am {fav}",
                task["group_id"], task["id"],
            )
        else:
            content = await person.speak_in_room(agent, task, table)

        if not content:
            content = "(keine Äußerung)"
        await post_room(group_id, content, agent_id=agent["id"])
        _append_summary(task, agent["name"], content)
        counts[agent["name"]] = counts.get(agent["name"], 0) + 1
        last_name = agent["name"]


def _append_summary(task, name: str, content: str) -> None:
    admin = repo.get_agent_by_kind(task["group_id"], "admin")
    key = f"summary:{task['id']}"
    prev = repo.get_agent_state(admin["id"], key, "")
    snippet = content.strip().replace("\n", " ")[:160]
    lines = (prev + f"\n- {name}: {snippet}").strip().splitlines()
    repo.set_agent_state(admin["id"], key, "\n".join(lines[-12:]))


# --- decision -------------------------------------------------------------
async def _decide_and_finalize(task, candidates, table: str) -> None:
    group_id = task["group_id"]
    admin = repo.get_agent_by_kind(group_id, "admin")

    if not budget_ok(task):
        await _pause_over_budget(task)
        return

    system = render(
        "admin_decide",
        candidates=table,
        running_summary=context.running_summary(task["id"]),
        recent_messages=context.recent_room_text(group_id),
        location_hint="(siehe Diskussion)",
    )
    resp = await call_and_log(
        get_client("admin"),
        [{"role": "system", "content": system},
         {"role": "user", "content": "Triff die Entscheidung als JSON."}],
        response_format={"type": "json_object"},
        group_id=group_id, task_id=task["id"],
    )
    data = parse_json(resp.content)
    idx = data.get("candidate_index")
    chosen = None
    if isinstance(idx, int) and 1 <= idx <= len(candidates):
        c = candidates[idx - 1]
        if c.full:
            chosen = c
    if chosen is None:  # invalid or non-full pick -> deterministic fallback
        full = [c for c in candidates if c.full]
        chosen = full[0] if full else None

    if chosen is None:
        await _handle_no_intersection(task, candidates)
        return

    # validate the chosen slot really exists in the candidate set (spec §6.8)
    assert find_candidate(candidates, chosen.start, chosen.end) is not None

    location = data.get("location") or None
    summary = str(data.get("summary", "")).strip()
    result = {
        "slot": {"start": chosen.start, "end": chosen.end, "label": chosen.label()},
        "location": location,
        "summary": summary,
    }
    repo.update_task_status(task["id"], "decided", result=result)
    task = repo.get_task(task["id"])
    log.info("task %s decided: %s @ %s", task["id"], chosen.label(), location)

    result_msg = render(
        "admin_result",
        slot_label=chosen.label(),
        location=location or "wird noch geklärt",
        summary=summary or "Bester gemeinsamer Termin.",
    )
    resp2 = await call_and_log(
        get_client("admin"), [{"role": "system", "content": result_msg},
                              {"role": "user", "content": "Poste die Abschlussnachricht."}],
        group_id=group_id, task_id=task["id"],
    )
    await post_room(group_id, resp2.content.strip() or result_msg, agent_id=admin["id"])
    await broadcast_task(task)

    # persistent notification into every user's private chat (spec §6.9)
    for a in repo.list_person_agents(group_id):
        await notify_user_private(
            a["user_id"],
            f"✅ Termin steht: {chosen.label()}"
            + (f" @ {location}" if location else "")
            + (f" — {summary}" if summary else ""),
        )


async def _handle_no_intersection(task, candidates) -> None:
    group_id = task["group_id"]
    admin = repo.get_agent_by_kind(group_id, "admin")
    if task["iteration"] >= settings.max_reschedule_iterations:
        repo.update_task_status(
            task["id"], "failed",
            result={"reason": "Kein gemeinsamer Termin nach mehreren Anläufen gefunden."},
        )
        task = repo.get_task(task["id"])
        await post_room(
            group_id,
            "❌ Leider kein gemeinsamer Termin gefunden — auch nach mehreren Anläufen. "
            "Bitte klärt es diesmal direkt untereinander.",
            agent_id=admin["id"],
        )
        await broadcast_task(task)
        log.info("task %s failed: no intersection after %d iterations",
                 task["id"], task["iteration"])
        return

    repo.bump_task_iteration(task["id"])
    repo.update_task_status(task["id"], "collecting")
    # reset ready flags so users are asked again
    for a in repo.list_person_agents(group_id):
        repo.set_agent_state(a["id"], f"ready:{task['id']}", False)
    await post_room(
        group_id,
        "Kein Termin passt für alle. Bitte klärt mit euren Usern Alternativen — "
        "ich frage neu ab.",
        agent_id=admin["id"],
    )
    task = repo.get_task(task["id"])
    await broadcast_task(task)
    for a in repo.list_person_agents(group_id):
        await notify_user_private(
            a["user_id"],
            "🔄 Es gab keinen gemeinsamen Termin. Hast du noch weitere Möglichkeiten?",
        )
    log.info("task %s back to collecting (iteration %d)", task["id"], task["iteration"] + 1)
