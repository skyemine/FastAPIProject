from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._presence_counts: dict[int, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def connect(self, room_slug: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[room_slug].add(websocket)

    async def disconnect(self, room_slug: str, websocket: WebSocket) -> None:
        async with self._lock:
            connections = self._connections.get(room_slug)
            if not connections:
                return
            connections.discard(websocket)
            if not connections:
                self._connections.pop(room_slug, None)

    async def broadcast(self, room_slug: str, payload: dict) -> None:
        async with self._lock:
            recipients = list(self._connections.get(room_slug, ()))

        stale_connections: list[WebSocket] = []
        for connection in recipients:
            try:
                await connection.send_json(payload)
            except Exception:
                stale_connections.append(connection)

        for connection in stale_connections:
            await self.disconnect(room_slug, connection)

    def connection_count(self, room_slug: str) -> int:
        return len(self._connections.get(room_slug, ()))

    async def mark_online(self, user_id: int) -> bool:
        async with self._lock:
            was_online = self._presence_counts[user_id] > 0
            self._presence_counts[user_id] += 1
            return not was_online

    async def mark_offline(self, user_id: int) -> bool:
        async with self._lock:
            count = self._presence_counts.get(user_id, 0)
            if count <= 1:
                self._presence_counts.pop(user_id, None)
                return count > 0
            self._presence_counts[user_id] = count - 1
            return False

    def is_online(self, user_id: int) -> bool:
        return self._presence_counts.get(user_id, 0) > 0
