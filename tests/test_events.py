"""The live events channel: presence, names, change announcements, warnings, and quiet logs."""

import logging
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from matrix_player.app import create_app
from matrix_player.events import MAX_SESSIONS, QuietWebSocketFilter, changed_area, clean_label, device_label
from matrix_player.matrix import MockMatrixAdapter
from matrix_player.playback import PlaybackItem

from test_signage import make_settings, png_bytes, wait_for

IPHONE = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X)"}


def receive_until(socket, event_type: str, limit: int = 20) -> dict:
    for _ in range(limit):
        event = socket.receive_json()
        if event["type"] == event_type:
            return event
    raise AssertionError(f"no {event_type} event")


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as test_client:
        yield test_client


def test_pages_see_who_is_connected_and_can_name_themselves(client) -> None:
    with client.websocket_connect("/api/events", headers=IPHONE) as first:
        welcome = first.receive_json()
        assert welcome["type"] == "welcome" and welcome["label"] == "an iPhone"
        assert receive_until(first, "presence")["count"] == 1
        with client.websocket_connect("/api/events") as second:
            second_id = second.receive_json()["session"]
            assert receive_until(second, "presence")["count"] == 2
            assert receive_until(first, "presence")["count"] == 2
            second.send_json({"type": "hello", "label": "  Sam's\n phone\x07 that has a very long name indeed  "})
            people = receive_until(first, "presence")["people"]
            named = next(person for person in people if person["id"] == second_id)
            assert named["label"] == "Sam's phone that has a very lo"
            assert len(named["label"]) <= 30
            second.send_text("not json")  # ignored, the channel stays open
            second.send_text("x" * 5000)
        assert receive_until(first, "presence")["count"] == 1


def test_a_change_is_announced_to_other_pages_with_who_made_it(client) -> None:
    with client.websocket_connect("/api/events") as watcher, client.websocket_connect("/api/events") as editor:
        editor_id = editor.receive_json()["session"]
        client.post("/api/playlists", json={"name": "March"}, headers={"X-Session": editor_id})
        activity = receive_until(watcher, "activity")
        assert (activity["area"], activity["by"]) == ("playlists", editor_id)
        # Previews and failed requests change nothing, so they are not announced.
        client.post("/api/text-panels/preview", json={"content": "HI"})
        assert client.post("/api/playlists", json={}).status_code == 422
        client.post("/api/text-panels", json={"name": "Hi", "content": "HI"}, headers={"X-Session": "bad id!"})
        activity = receive_until(watcher, "activity")
        assert (activity["area"], activity["by"]) == ("messages", None)


def test_skipped_items_and_health_problems_reach_open_pages(client) -> None:
    with client.websocket_connect("/api/events") as page:
        page.receive_json()
        service = client.app.state.playback.service
        service.start([PlaybackItem(source=Path("/no/such/file.png"), duration=0.1, label="“Gone”")], mode="once")
        warning = receive_until(page, "playback_warning")
        assert warning["text"].startswith("Skipped “Gone”")
        assert wait_for(lambda: not service.is_running, timeout=5)

        client.app.state.health._record("hot", "Running hot: 81.0 °C", 0.0, problem=True)
        health = receive_until(page, "health")
        assert (health["kind"], health["problem"], health["text"]) == ("hot", True, "Running hot: 81.0 °C")


def test_too_many_pages_are_turned_away(client) -> None:
    from starlette.websockets import WebSocketDisconnect

    sockets = []
    try:
        for _ in range(MAX_SESSIONS):
            socket = client.websocket_connect("/api/events").__enter__()
            socket.receive_json()
            sockets.append(socket)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/events") as extra:
                extra.receive_json()
    finally:
        for socket in sockets:
            socket.__exit__(None, None, None)


def test_helpers() -> None:
    assert device_label(IPHONE["user-agent"]) == "an iPhone"
    assert device_label("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)") == "an iPad"
    assert device_label("") == "a device"
    assert clean_label(42) is None and clean_label("   ") is None
    assert changed_area("POST", "/api/media") == "library"
    assert changed_area("PUT", "/api/display") == "display"
    assert changed_area("GET", "/api/playlists") is None
    assert changed_area("POST", "/api/playlists/preview-item") is None
    assert changed_area("POST", "/api/playlists/3/preview") is None
    assert changed_area("POST", "/upload") is None


def test_reconnecting_pages_stay_out_of_the_journal() -> None:
    quiet = QuietWebSocketFilter()

    def record(message: str, *args) -> logging.LogRecord:
        return logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, message, args, None)

    assert not quiet.filter(record('%s - "WebSocket %s" [accepted]', "10.0.0.2:5000", "/api/events"))
    assert not quiet.filter(record("connection open"))
    assert not quiet.filter(record("connection closed"))
    assert quiet.filter(record('%s - "WebSocket %s" [accepted]', "10.0.0.2:5000", "/elsewhere"))
    assert quiet.filter(record("Application startup complete."))


def test_uploads_are_announced_too(client) -> None:
    with client.websocket_connect("/api/events") as page:
        page.receive_json()
        response = client.post("/api/media", files={"file": ("red.png", png_bytes("red"), "image/png")})
        assert response.status_code == 201
        assert receive_until(page, "activity")["area"] == "library"
