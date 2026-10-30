"""
WebSocket connection manager.
Maintains a registry of active WebSocket connections per run_id.
When a build produces a log line, it is broadcast to all connected clients.

Pattern: fan-out pub/sub per run_id.
"""
import asyncio
import json
from datetime import datetime
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # run_id → list of active WebSocket connections
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, run_id: str, ws: WebSocket):
        await ws.accept()
        if run_id not in self._connections:
            self._connections[run_id] = []
        self._connections[run_id].append(ws)

    def disconnect(self, run_id: str, ws: WebSocket):
        if run_id in self._connections:
            self._connections[run_id] = [
                c for c in self._connections[run_id] if c is not ws
            ]
            if not self._connections[run_id]:
                del self._connections[run_id]

    async def broadcast(self, run_id: str, message: dict):
        """Send a log line to all clients watching this run."""
        if run_id not in self._connections:
            return
        dead = []
        payload = json.dumps(message, default=str)
        for ws in self._connections[run_id]:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for d in dead:
            self.disconnect(run_id, d)

    def active_viewers(self, run_id: str) -> int:
        return len(self._connections.get(run_id, []))


# Global singleton — imported by both routes and tasks
ws_manager = ConnectionManager()
