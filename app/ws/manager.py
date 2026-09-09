"""Connection bookkeeping: who is connected and under which anonymous ID."""

import uuid

from fastapi import WebSocket


def generate_anonymous_id() -> str:
    """Generate an anonymous ID based on uuid4, e.g. 'anonymous-3f9a2c1e'."""
    return f"anonymous-{uuid.uuid4().hex[:8]}"


class Manager:
    """Tracks active WebSocket connections and their anonymous IDs."""

    def __init__(self) -> None:
        self.active_connections: dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        # uuid4 is random and collision-proof for practical purposes, so no
        # need to check against existing IDs.
        anon_id = generate_anonymous_id()
        self.active_connections[websocket] = anon_id
        return anon_id

    def disconnect(self, websocket: WebSocket) -> str | None:
        return self.active_connections.pop(websocket, None)

    def user_list(self) -> list[str]:
        return list(self.active_connections.values())

    async def broadcast(self, message: dict) -> None:
        stale = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


mgr = Manager()
