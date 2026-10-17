from dataclasses import replace
from pathlib import Path
import time

from fastapi.testclient import TestClient
from PIL import Image

from matrix_player.app import create_app
from matrix_player.config import Settings
from matrix_player.matrix import MatrixConfig, MockMatrixAdapter
from matrix_player.rendering import Transition, transition_frames


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "db.sqlite3",
        media_dir=tmp_path / "media",
        thumbnail_dir=tmp_path / "thumbs",
        max_upload_bytes=1024 * 1024,
        matrix_width=96,
        matrix_height=64,
        log_level="WARNING",
    )


def test_health_and_captive_routes(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/generate_204").status_code in {200, 307}
        assert client.get("/").status_code == 200
        assert "Matrix Player" in client.get("/").text


def test_playlist_and_text_panel_crud(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        panel = client.post("/api/text-panels", json={"name": "Welcome", "content": "Hello"}).json()
        playlist = client.post("/api/playlists", json={"name": "Loop"}).json()
        item = client.post(
            f"/api/playlists/{playlist['id']}/items",
            json={"item_type": "text_panel", "text_panel_id": panel["id"]},
        )
        assert item.status_code == 201
        assert client.get(f"/api/playlists/{playlist['id']}").json()["items"][0]["position"] == 0


def test_transitions_end_with_incoming_frame() -> None:
    old = Image.new("RGB", (8, 8), "black")
    new = Image.new("RGB", (8, 8), "white")
    for transition in Transition:
        frames = list(transition_frames(old, new, transition, frames=3))
        assert frames[-1].getpixel((0, 0)) == (255, 255, 255)


def test_matrix_profile_preserves_driver_and_viewing_orientations() -> None:
    profile = MatrixConfig()
    assert profile.size == (64, 96)
    assert profile.logical_size == (96, 64)
    assert MockMatrixAdapter().size == profile.logical_size


def test_playlist_start_renders_frames_and_stop_blanks_matrix(tmp_path: Path) -> None:
    matrix = MockMatrixAdapter()
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=matrix)) as client:
        panel = client.post(
            "/api/text-panels",
            json={"name": "Live", "content": "RENDER", "color_value": "#ff0000"},
        ).json()
        playlist = client.post("/api/playlists", json={"name": "Live loop"}).json()
        item = client.post(
            f"/api/playlists/{playlist['id']}/items",
            json={
                "item_type": "text_panel",
                "text_panel_id": panel["id"],
                "duration_ms": 100,
                "repeat_count": 2,
                "transition_type": "fade",
                "transition_duration_ms": 10,
                "fit_mode": "fit",
            },
        ).json()

        response = client.post(
            "/api/playback/start", json={"playlist_id": playlist["id"]}
        )
        assert response.status_code == 200
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(
            frame.getbbox() for frame in matrix.history
        ):
            time.sleep(0.01)

        assert any(frame.getbbox() for frame in matrix.history)
        state = client.get("/api/playback/state").json()
        assert state["status"] in {"playing", "paused"}
        assert state["playlist_id"] == playlist["id"]
        assert state["item_id"] == item["id"]
        status = client.get("/api/status").json()
        assert status["matrix"]["connected"] is True
        assert status["matrix"]["mode"] == "mock"

        stopped = client.post("/api/playback/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopped"
        assert matrix.frame.getbbox() is None


def test_playback_service_emits_frames_at_matrix_size_and_blanks_on_stop() -> None:
    from matrix_player.matrix import MatrixConfig
    from matrix_player.playback import PlaybackItem, PlaybackService

    config = MatrixConfig()
    shown: list = []

    class CountingMatrix(MockMatrixAdapter):
        def show(self, image: Image.Image) -> None:
            shown.append(image.size)
            super().show(image)

    matrix = CountingMatrix(config.logical_size)
    service = PlaybackService(matrix)
    red = Image.new("RGB", config.logical_size, (255, 0, 0))
    blue = Image.new("RGB", config.logical_size, (0, 0, 255))
    service.start(
        [
            PlaybackItem(source=red, duration=0.2),
            PlaybackItem(source=blue, duration=0.2, transition=Transition.FADE,
                         transition_duration=0.1),
        ]
    )
    deadline = time.monotonic() + 5
    while service.is_running and time.monotonic() < deadline:
        time.sleep(0.02)
    service.stop()

    assert shown, "playback produced no frames"
    assert set(shown) == {(96, 64)}
    assert matrix.frame.getbbox() is None


def test_default_playlist_autoplays_on_startup_when_enabled(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings, matrix_adapter=MockMatrixAdapter())) as client:
        panel = client.post(
            "/api/text-panels", json={"name": "Startup", "content": "START"}
        ).json()
        playlist = client.post(
            "/api/playlists",
            json={"name": "Startup", "is_default": True, "is_enabled": True},
        ).json()
        item = client.post(
            f"/api/playlists/{playlist['id']}/items",
            json={
                "item_type": "text_panel",
                "text_panel_id": panel["id"],
                "duration_ms": 1000,
            },
        )
        assert item.status_code == 201

    autoplay = replace(settings, autoplay_default=True)
    with TestClient(
        create_app(autoplay, matrix_adapter=MockMatrixAdapter())
    ) as client:
        state = client.get("/api/playback/state").json()
        assert state["status"] == "playing"
        assert state["playlist_id"] == playlist["id"]
