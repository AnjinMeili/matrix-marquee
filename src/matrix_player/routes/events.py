"""The live events channel that open pages listen on."""

from fastapi import APIRouter, WebSocket


router = APIRouter()


@router.websocket("/api/events")
async def events(websocket: WebSocket) -> None:
    await websocket.app.state.events.serve(websocket)
