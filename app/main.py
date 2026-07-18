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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import ics, repo, serialize
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


def _member_names(group_id: int) -> list[str]:
    return [u["display_name"] for u in repo.list_users(group_id)]


def _ics_response(task) -> Response:
    group = repo.get_group(task["group_id"])
    body = ics.build_ics(task, group, _member_names(task["group_id"]))
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="treffen.ics"'})


@api.get("/task/{task_id}/ics")
def task_ics(task_id: int, request: Request):
    user = _require_user(request)
    task = repo.get_task(task_id)
    if task is None or task["group_id"] != user["group_id"]:
        raise HTTPException(status_code=404, detail="Unbekannter Task")
    if task["status"] != "decided":
        raise HTTPException(status_code=409, detail="Noch kein Termin entschieden")
    repo.ensure_share_token(task_id)
    return _ics_response(repo.get_task(task_id))


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


def _share_card_html(task, request: Request) -> str:
    """Self-contained, public, read-only result card with OG meta for previews."""
    import html as _html

    group = repo.get_group(task["group_id"])
    result = json.loads(task["result_json"]) if task["result_json"] else {}
    params = json.loads(task["params_json"]) if task["params_json"] else {}
    slot = result.get("slot", {})
    title = params.get("description") or "Treffen"
    when = slot.get("label", "")
    location = result.get("location") or ""
    summary = result.get("summary") or ""
    people = ", ".join(_member_names(task["group_id"]))
    e = _html.escape
    og_desc = e(f"{when}" + (f" · {location}" if location else ""))
    ics_url = f"share/{task['share_token']}.ics"
    return f"""<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} — {e(when)}</title>
<meta property="og:title" content="{e(title)} · {e(group['name'])}">
<meta property="og:description" content="{og_desc}">
<meta property="og:type" content="website">
<style>
  body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f1ec;
    font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#2d3142;padding:20px}}
  .card{{background:#fff;border:1px solid #e3ddd2;border-radius:16px;max-width:440px;width:100%;
    padding:26px 28px;box-shadow:0 8px 30px rgba(0,0,0,.06)}}
  .badge{{display:inline-block;background:#d7f0d7;border-radius:12px;padding:2px 10px;font-size:.8rem;font-weight:600}}
  h1{{font-size:1.5rem;margin:14px 0 4px}}
  .when{{font-size:1.15rem;font-weight:600;margin:10px 0}}
  .row{{margin:6px 0;color:#4b4f5c}} .label{{color:#7a7f8c;font-size:.85rem}}
  .btn{{display:inline-block;margin-top:18px;background:#3d8bdb;color:#fff;text-decoration:none;
    padding:10px 16px;border-radius:9px;font-weight:600}}
  .foot{{margin-top:16px;color:#9a9fac;font-size:.78rem}}
</style></head><body><div class="card">
  <span class="badge">✅ Termin steht</span>
  <h1>🗓️ {e(title)}</h1>
  <div class="when">{e(when)}</div>
  {f'<div class="row"><span class="label">Ort:</span> {e(location)}</div>' if location else ''}
  <div class="row"><span class="label">Mit:</span> {e(people)}</div>
  {f'<div class="row">{e(summary)}</div>' if summary else ''}
  <a class="btn" href="{ics_url}">📅 Zum Kalender (.ics)</a>
  <div class="foot">Gruppe „{e(group['name'])}" · erstellt mit AgentTable</div>
</div></body></html>"""


@app.get("/share/{token}.ics")
def share_ics(token: str):
    task = repo.get_task_by_share_token(token)
    if task is None or task["status"] != "decided":
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return _ics_response(task)


@app.get("/share/{token}", response_class=HTMLResponse)
def share_card(token: str, request: Request):
    task = repo.get_task_by_share_token(token)
    if task is None or task["status"] != "decided":
        return HTMLResponse(
            "<h1>Nicht gefunden</h1><p>Dieser Ergebnis-Link ist ungültig oder der "
            "Termin steht noch nicht fest.</p>", status_code=404)
    return HTMLResponse(_share_card_html(task, request))


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
