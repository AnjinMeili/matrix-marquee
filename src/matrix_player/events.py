"""The events channel: one WebSocket per open page, carrying small JSON events.

The sign tells every open page about things as they happen, so pages need not
poll for them: health problems, skipped items, changes made from another
phone, and who is connected. Later it carries display requests for live input
(docs/protocol-receiver-plan.md). As with every API, anyone on the sign's
Wi-Fi can connect; nothing sent over this channel changes the sign yet.

Who did what ("by") comes from a header the page sends and is a courtesy, not
proof; the planned operator PIN is what will make approvals trustworthy.
"""

import asyncio
import itertools
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

MAX_SESSIONS = 32  # open pages; more are turned away rather than slowing the sign
QUEUE_LIMIT = 100  # events held for a page that has stopped reading; the oldest go first
MAX_LABEL = 30
MAX_MESSAGE_CHARS = 1024
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# API areas a change can be in, by the path segment after /api/.
AREAS = {
    "playlists": "playlists",
    "text-panels": "messages",
    "media": "library",
    "playback": "sign",
    "display": "display",
}
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def device_label(user_agent: str) -> str:
    """A plain starting name for a page, until its user names it."""
    agent = user_agent or ""
    for marker, label in (
        ("iPad", "an iPad"), ("iPhone", "an iPhone"), ("Android", "an Android phone"),
        ("Macintosh", "a Mac"), ("Windows", "a Windows PC"), ("Linux", "a computer"),
    ):
        if marker in agent:
            return label
    return "a device"


def clean_label(value: object) -> Optional[str]:
    """A name another person will read: printable, one line, and short."""
    if not isinstance(value, str):
        return None
    text = "".join(char for char in " ".join(value.split()) if char.isprintable())[:MAX_LABEL].strip()
    return text or None


def changed_area(method: str, path: str) -> Optional[str]:
    """The part of the sign a successful request changed, or None if it changed nothing."""
    if method not in MUTATING or not path.startswith("/api/"):
        return None
    if "preview" in path or path.endswith("/run-through"):
        return None  # previews draw, they do not change anything
    return AREAS.get(path.split("/")[2])


def session_from(value: Optional[str]) -> Optional[str]:
    return value if value and SESSION_PATTERN.match(value) else None


@dataclass
class Session:
    id: str
    label: str
    queue: "asyncio.Queue[dict]"
    since: float


class EventHub:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._sequence = itertools.count(1)

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Events are delivered on the web server's loop; publishing works from any thread."""
        self._loop = loop

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def presence(self) -> dict:
        with self._lock:
            people = [{"id": session.id, "label": session.label} for session in self._sessions.values()]
        return {"count": len(people), "people": people}

    def publish(self, event_type: str, /, **data: object) -> None:
        """Sends an event to every open page. Never blocks and never raises."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        event = {"type": event_type, "id": next(self._sequence), "at": self.clock(), **data}
        try:
            loop.call_soon_threadsafe(self._fan_out, event)
        except RuntimeError:
            pass  # the loop closed while shutting down

    def _fan_out(self, event: dict) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if session.queue.full():
                try:
                    session.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            session.queue.put_nowait(event)

    async def serve(self, websocket: WebSocket) -> None:
        with self._lock:
            full = len(self._sessions) >= MAX_SESSIONS
        if full:
            await websocket.close(code=1013)  # "try again later"
            return
        await websocket.accept()
        session = Session(
            secrets.token_urlsafe(12), device_label(websocket.headers.get("user-agent", "")),
            asyncio.Queue(QUEUE_LIMIT), self.clock(),
        )
        session.queue.put_nowait({"type": "welcome", "session": session.id, "label": session.label})
        with self._lock:
            self._sessions[session.id] = session
        logger.debug("events_connected", extra={"session": session.id})
        sender = asyncio.ensure_future(self._send(websocket, session))
        self._fan_out({"type": "presence", "id": next(self._sequence), "at": self.clock(), **self.presence()})
        try:
            while True:
                text = await websocket.receive_text()
                if len(text) <= MAX_MESSAGE_CHARS:
                    self._handle(session, text)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            sender.cancel()
            with self._lock:
                self._sessions.pop(session.id, None)
            logger.debug("events_disconnected", extra={"session": session.id})
            self.publish("presence", **self.presence())

    async def _send(self, websocket: WebSocket, session: Session) -> None:
        try:
            while True:
                await websocket.send_text(json.dumps(await session.queue.get(), separators=(",", ":")))
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            pass

    def _handle(self, session: Session, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        if message.get("type") == "hello":
            # A page names itself (a name saved on the phone, or a new one).
            label = clean_label(message.get("label"))
            if label and label != session.label:
                session.label = label
                self.publish("presence", **self.presence())


class ChangeAnnouncer:
    """ASGI middleware: after any successful change, tells open pages which area changed.

    Pages then catch up, so two phones editing the sign see each other's changes.
    Pure ASGI rather than BaseHTTPMiddleware, so streamed downloads pass straight through.
    """

    def __init__(self, app: ASGIApp, hub: EventHub) -> None:
        self.app = app
        self.hub = hub

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        area = changed_area(scope.get("method", ""), scope.get("path", "")) if scope["type"] == "http" else None
        if area is None:
            await self.app(scope, receive, send)
            return
        status: List[int] = []

        async def watch(message: Message) -> None:
            if message["type"] == "http.response.start":
                status.append(message["status"])
            await send(message)

        await self.app(scope, receive, watch)
        if status and status[0] < 400:
            headers = dict(scope.get("headers") or [])
            by = session_from(headers.get(b"x-session", b"").decode("latin-1"))
            self.hub.publish("activity", area=area, by=by)


class QuietWebSocketFilter(logging.Filter):
    """Keeps pages connecting and reconnecting to /api/events out of the journal."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.msg if isinstance(record.msg, str) else ""
        if message in ("connection open", "connection closed"):
            return False
        args = record.args if isinstance(record.args, tuple) else ()
        if "WebSocket" in message and len(args) >= 2 and str(args[1]).split("?", 1)[0] == "/api/events":
            return False
        return True
