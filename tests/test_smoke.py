from pathlib import Path

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
