"""WebSocket connection registry + broadcast (spec §3 realtime).

Two logical channels:
* private chat — keyed by ``user_id`` (a user may have several tabs open).
* group room  — keyed by ``group_id`` (all members read-only, live).

Messages are plain JSON dicts. The registry only fans out; persistence happens
in the repo layer before broadcasting so a reconnect can replay from the DB.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket


class ConnectionHub:
    def __init__(self) -> None:
        self._private: dict[int, set[WebSocket]] = defaultdict(set)
        self._room: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect_private(self, user_id: int, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._private[user_id].add(ws)

    async def connect_room(self, group_id: int, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._room[group_id].add(ws)

    async def disconnect_private(self, user_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self._private[user_id].discard(ws)

    async def disconnect_room(self, group_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self._room[group_id].discard(ws)

    async def _send(self, targets: set[WebSocket], payload: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(targets):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001 — client vanished mid-send
                dead.append(ws)
        for ws in dead:
            targets.discard(ws)

    async def to_private(self, user_id: int, payload: dict) -> None:
        await self._send(self._private.get(user_id, set()), payload)

    async def to_room(self, group_id: int, payload: dict) -> None:
        await self._send(self._room.get(group_id, set()), payload)


hub = ConnectionHub()
