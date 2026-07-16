"""FastAPI application: token login, REST, and the two WebSocket channels.

Bound to 0.0.0.0 in the runner (proxy requirement). All app routes live under an
optional ``BASE_PATH`` so it can sit behind an nginx sub-path with WS upgrade.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import repo, serialize
from app.agents import person
from app.config import settings
from app.moderator import check_and_advance, start_task
from app.realtime import hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("agenttable.api")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title=settings.title)
api = FastAPI(title=settings.title + " API")


# --- auth helpers ---------------------------------------------------------
def _user_from_token(token: str | None):
    if not token:
        raise HTTPException(status_code=401, detail="Kein Token")
    user = repo.get_user_by_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Unbekanntes Token")
    return user


def _require_user(request: Request):
    token = request.cookies.get("at_token") or request.query_params.get("t")
    return _user_from_token(token)


# --- REST -----------------------------------------------------------------
@api.get("/me")
def me(request: Request):
    user = _require_user(request)
    repo.touch_user(user["id"])
    agent = repo.get_person_agent(user["id"])
    group = repo.get_group(user["group_id"])
    task = repo.latest_task(user["group_id"])
    active = repo.get_active_task(user["group_id"])
    ready = bool(repo.get_agent_state(agent["id"], f"ready:{active['id']}", False)) if active else False
    return {
        "user": {"id": user["id"], "display_name": user["display_name"],
                 "persona": user["persona"], "group_id": user["group_id"]},
        "agent": serialize.agent_public(agent),
        "group": {"id": group["id"], "name": group["name"]},
        "onboarding_done": bool(repo.get_agent_state(agent["id"], "onboarding_done", False)),
        "agents": [serialize.agent_public(a) for a in repo.list_agents(user["group_id"])],
        "task": serialize.task_public(task),
        "ready": ready,
    }


@api.post("/ready")
async def set_ready(request: Request):
    """Deterministically mark the user ready (no LLM in the loop — LLMs skip the
    mark_ready tool too often). Guard: requires at least one availability slot."""
    user = _require_user(request)
    task = repo.get_active_task(user["group_id"])
    if task is None:
        raise HTTPException(status_code=409, detail="Keine aktive Terminfindung.")
    if not repo.list_availability(user["id"]):
        return {"ok": False, "message": "Bitte nenne deinem Agenten zuerst mindestens einen möglichen Termin."}
    agent = repo.get_person_agent(user["id"])
    repo.set_agent_state(agent["id"], f"ready:{task['id']}", True)
    repo.add_private_message(user["id"], "system", "✓ Du bist als bereit markiert.")
    last = repo.list_private_messages(user["id"], limit=1)[-1]
    await hub.to_private(user["id"], {"type": "private_message", "message": serialize.private_message(last)})
    asyncio.create_task(check_and_advance(task["id"]))
    return {"ok": True, "ready": True}


def _since(request: Request) -> int | None:
    """Parse an optional ?since=<id> for reconnect resync."""
    raw = request.query_params.get("since")
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


@api.get("/private/history")
def private_history(request: Request):
    user = _require_user(request)
    rows = repo.list_private_messages(user["id"], since=_since(request))
    return {"messages": [serialize.private_message(r) for r in rows]}


@api.get("/room/history")
def room_history(request: Request):
    user = _require_user(request)
    rows = repo.list_room_messages(user["group_id"], since=_since(request))
    return {"messages": [serialize.room_message(r) for r in rows]}


@api.get("/task")
def task_state(request: Request):
    user = _require_user(request)
    task = repo.latest_task(user["group_id"])
    return {"task": serialize.task_public(task)}


@api.post("/task/start")
async def task_start(request: Request):
    user = _require_user(request)
    active = repo.get_active_task(user["group_id"])
    if active is not None:
        raise HTTPException(status_code=409, detail="Es läuft bereits eine Terminfindung.")
    body = await request.json()
    params = {
        "description": (body.get("description") or "Gemeinsames Treffen").strip(),
        "range_start": body["range_start"],
        "range_end": body["range_end"],
        "granularity": body.get("granularity", settings.slot_granularity),
    }
    task = await start_task(user["group_id"], params)
    return {"task": serialize.task_public(task)}


# --- WebSockets -----------------------------------------------------------
def _ws_user(websocket: WebSocket):
    token = websocket.query_params.get("t") or websocket.cookies.get("at_token")
    if not token:
        return None
    return repo.get_user_by_token(token)


@app.websocket("/ws/private")
async def ws_private(websocket: WebSocket):
    user = _ws_user(websocket)
    if user is None:
        await websocket.close(code=4401)
        return
    user_id = user["id"]
    await hub.connect_private(user_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            text = json.loads(raw).get("text", "").strip() if raw.startswith("{") else raw.strip()
            if not text:
                continue
            # echo the user's own message immediately
            row = repo.list_private_messages(user_id, limit=1)
            await hub.to_private(user_id, {"type": "private_message",
                                           "message": {"role": "user", "content": text,
                                                       "created_at": ""}})
            replies = await person.handle_private_message(user_id, text)
            for r in replies:
                last = repo.list_private_messages(user_id, limit=1)[-1]
                await hub.to_private(user_id, {"type": "private_message",
                                               "message": serialize.private_message(last)})
            # a mark_ready during the chat may advance the task
            task = repo.get_active_task(user["group_id"])
            if task:
                asyncio.create_task(check_and_advance(task["id"]))
    except WebSocketDisconnect:
        await hub.disconnect_private(user_id, websocket)
    except Exception:  # noqa: BLE001
        log.exception("private ws error")
        await hub.disconnect_private(user_id, websocket)


@app.websocket("/ws/room")
async def ws_room(websocket: WebSocket):
    user = _ws_user(websocket)
    if user is None:
        await websocket.close(code=4401)
        return
    group_id = user["group_id"]
    await hub.connect_room(group_id, websocket)
    try:
        while True:
            await websocket.receive_text()  # room is read-only for humans
    except WebSocketDisconnect:
        await hub.disconnect_room(group_id, websocket)
    except Exception:  # noqa: BLE001
        await hub.disconnect_room(group_id, websocket)


# --- static frontend + token login ---------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    token = request.query_params.get("t")
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    resp = HTMLResponse(html)
    if token and repo.get_user_by_token(token):
        resp.set_cookie("at_token", token, httponly=False, samesite="lax")
    elif token:
        return HTMLResponse(
            "<h1>Unbekanntes Token</h1><p>Dieser Zugangslink ist ungültig. "
            "Bitte prüfe deinen Magic-Link.</p>",
            status_code=404,
        )
    return resp


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/api", api)
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


# optional base-path wrapper: if BASE_PATH set, expose the whole app beneath it
if settings.base_path:
    root = FastAPI()
    root.mount(settings.base_path, app)

    @root.get("/")
    def _redirect():
        return JSONResponse({"app": settings.base_path})

    application = root
else:
    application = app
