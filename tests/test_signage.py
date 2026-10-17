"""Playback timing, play modes, resume, item-level errors, and sign text rendering."""

from io import BytesIO
import json
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Callable, Iterator, List

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from matrix_player.app import create_app
from matrix_player.config import Settings
from matrix_player.database import SCHEMA, Database
from matrix_player.matrix import MockMatrixAdapter
from matrix_player.playback import PlaybackItem, PlaybackService
from matrix_player.rendering import Frame, FrameSource, TextRenderer, TextStyle, VideoFrameSource


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


def wait_for(condition: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class CountingAnimation(FrameSource):
    """A short animation that records how many complete passes were requested."""

    def __init__(self, frames: int = 3, frame_seconds: float = 0.05) -> None:
        self.frames = frames
        self.frame_seconds = frame_seconds
        self.passes = 0

    def __iter__(self) -> Iterator[Frame]:
        self.passes += 1
        for index in range(self.frames):
            yield Frame(Image.new("RGB", (96, 64), (index * 60 + 20, 0, 0)), self.frame_seconds)


def png_bytes(color: str = "red") -> bytes:
    output = BytesIO()
    Image.new("RGB", (40, 30), color).save(output, format="PNG")
    return output.getvalue()


def run_once(items: List[PlaybackItem]) -> float:
    service = PlaybackService(MockMatrixAdapter())
    started = time.monotonic()
    service.start(items, mode="once")
    assert wait_for(lambda: not service.is_running, timeout=5)
    return time.monotonic() - started


def test_time_mode_loops_short_animation_to_fill_its_duration() -> None:
    animation = CountingAnimation()  # 150 ms long
    elapsed = run_once([PlaybackItem(source=animation, duration=0.6, play_by="time")])
    assert animation.passes >= 4
    assert 0.55 <= elapsed < 1.0


def test_repeats_mode_plays_whole_animation_loops() -> None:
    animation = CountingAnimation()
    elapsed = run_once([PlaybackItem(source=animation, duration=10.0, repeats=3, play_by="repeats")])
    assert animation.passes == 3
    assert elapsed < 1.0


def test_play_orders() -> None:
    order = PlaybackService._order
    once = list(order(4, "once", 2))
    assert once == [2, 3]
    shuffled = order(5, "shuffle", 3)
    first_pass = [next(shuffled) for _ in range(5)]
    assert first_pass[0] == 3 and sorted(first_pass) == [0, 1, 2, 3, 4]
    random_order = order(3, "random", 0)
    picks = [next(random_order) for _ in range(50)]
    assert all(a != b for a, b in zip(picks, picks[1:]))
    looped = order(2, "loop", 1)
    assert [next(looped) for _ in range(4)] == [1, 0, 1, 0]


def add_panel(client: TestClient, content: str, **style) -> dict:
    return client.post("/api/text-panels", json={"name": content, "content": content, **style}).json()


def add_item(client: TestClient, playlist_id: int, **fields) -> dict:
    response = client.post(f"/api/playlists/{playlist_id}/items", json=fields)
    assert response.status_code == 201, response.text
    return response.json()


def test_unreadable_item_is_skipped_and_the_sign_keeps_playing(tmp_path: Path) -> None:
    matrix = MockMatrixAdapter()
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=matrix)) as client:
        media = client.post(
            "/api/media", files={"file": ("photo.png", png_bytes(), "image/png")}
        ).json()
        panel = add_panel(client, "STILL HERE", color_value="#00ff00")
        playlist = client.post("/api/playlists", json={"name": "Mixed"}).json()
        add_item(client, playlist["id"], item_type="media", media_id=media["id"], duration_ms=200)
        add_item(client, playlist["id"], item_type="text_panel", text_panel_id=panel["id"], duration_ms=200)
        (tmp_path / "media" / media["filename"]).unlink()

        assert client.post("/api/playback/start", json={"playlist_id": playlist["id"]}).status_code == 200
        assert wait_for(lambda: client.get("/api/playback/state").json()["warnings"])
        state = client.get("/api/playback/state").json()
        assert state["status"] == "playing"
        assert "Skipped" in state["warnings"][0]
        assert wait_for(lambda: any(frame.getextrema()[1][1] > 200 for frame in matrix.history))
        client.post("/api/playback/stop")


def test_playlist_with_only_broken_items_retries_instead_of_erroring(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        media = client.post(
            "/api/media", files={"file": ("photo.png", png_bytes(), "image/png")}
        ).json()
        playlist = client.post("/api/playlists", json={"name": "Broken"}).json()
        add_item(client, playlist["id"], item_type="media", media_id=media["id"], duration_ms=200)
        (tmp_path / "media" / media["filename"]).unlink()
        client.post("/api/playback/start", json={"playlist_id": playlist["id"]})
        time.sleep(0.5)
        state = client.get("/api/playback/state").json()
        assert state["status"] == "playing"
        assert state["error"] is None
        assert state["warnings"]
        client.post("/api/playback/stop")


def test_playlist_resumes_where_it_left_off(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        playlist = client.post("/api/playlists", json={"name": "Chant"}).json()
        for word in ("ONE", "TWO", "THREE"):
            panel = add_panel(client, word)
            add_item(client, playlist["id"], item_type="text_panel", text_panel_id=panel["id"], duration_ms=150)
        client.post("/api/playback/start", json={"playlist_id": playlist["id"]})
        assert wait_for(lambda: client.get("/api/playback/state").json()["position"] == 2)
        client.post("/api/playback/stop")
        assert client.get(f"/api/playlists/{playlist['id']}").json()["resume_position"] == 2

        resumed = client.post("/api/playback/start", json={"playlist_id": playlist["id"]}).json()
        assert resumed["item_count"] == 3
        assert wait_for(lambda: client.get("/api/playback/state").json()["position"] is not None)
        assert client.get("/api/playback/state").json()["position"] in {2, 0}

        restarted = client.post(
            "/api/playback/start", json={"playlist_id": playlist["id"], "from_start": True}
        )
        assert restarted.status_code == 200
        assert wait_for(lambda: client.get("/api/playback/state").json()["position"] == 0)

        # Showing a message on its own and coming back resumes the playlist.
        assert wait_for(lambda: client.get("/api/playback/state").json()["position"] == 1)
        solo = add_panel(client, "SOLO")
        client.post("/api/playback/send", json={"target_type": "text_panel", "target_id": solo["id"]})
        released = client.post("/api/playback/release").json()
        assert released["playlist_id"] == playlist["id"]
        assert wait_for(lambda: client.get("/api/playback/state").json()["position"] in {1, 2})
        client.post("/api/playback/stop")


def test_play_once_stops_at_the_end_and_rewinds(tmp_path: Path) -> None:
    matrix = MockMatrixAdapter()
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=matrix)) as client:
        playlist = client.post("/api/playlists", json={"name": "Once", "play_mode": "once"}).json()
        assert playlist["play_mode"] == "once"
        for word in ("A", "B"):
            panel = add_panel(client, word)
            add_item(client, playlist["id"], item_type="text_panel", text_panel_id=panel["id"], duration_ms=100)
        client.post("/api/playback/start", json={"playlist_id": playlist["id"]})
        assert wait_for(lambda: client.get("/api/playback/state").json()["status"] == "stopped")
        assert matrix.frame.getbbox() is None
        assert client.get(f"/api/playlists/{playlist['id']}").json()["resume_position"] == 0


def test_items_round_trip_timing_and_new_transitions(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        media = client.post(
            "/api/media", files={"file": ("photo.png", png_bytes(), "application/octet-stream")}
        )
        assert media.status_code == 201, media.text
        playlist = client.post("/api/playlists", json={"name": "Edit", "play_mode": "shuffle"}).json()
        item = add_item(client, playlist["id"], item_type="media", media_id=media.json()["id"])
        changed = client.put(
            f"/api/playlists/{playlist['id']}/items/{item['id']}",
            json={**item, "play_by": "repeats", "repeat_count": 3, "transition_type": "impact"},
        )
        assert changed.status_code == 200
        assert changed.json()["play_by"] == "repeats"
        assert changed.json()["transition_type"] == "impact"
        copy = client.post(f"/api/playlists/{playlist['id']}/duplicate").json()
        assert copy["play_mode"] == "shuffle"
        assert copy["items"][0]["play_by"] == "repeats"


def test_text_renderer_fills_the_panel_and_animates() -> None:
    size = (96, 64)
    still = TextRenderer("NO KINGS", size, TextStyle())
    assert not still.animated
    assert still.block.size[0] >= 80 or still.block.size[1] >= 50

    rainbow = TextRenderer("VOTE", size, TextStyle(color_mode="rainbow"))
    assert rainbow.animated
    assert list(rainbow.frame(0.0).getdata()) != list(rainbow.frame(0.5).getdata())

    pow_style = TextRenderer("POW!", size, TextStyle(effect="pow", accent_color="#ffd400"))
    assert pow_style.frame(1.0).getpixel((1, 32)) != (0, 0, 0) or pow_style.frame(1.0).getbbox()

    scroll = TextRenderer("WE WILL NOT BE SILENCED", size, TextStyle(effect="scroll"))
    assert scroll.cycle_seconds > 10

    for family in ("impact", "comic", "block", "headline", "marker", "pixel", "sans", "default"):
        frame = TextRenderer("Hi", size, TextStyle(font_family=family, bold=True, italic=True)).frame()
        assert frame.size == size and frame.getbbox()


def test_text_preview_endpoints_and_validation(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        fonts = client.get("/api/text-panels/fonts").json()
        assert {"impact", "comic", "pixel"} <= {font["id"] for font in fonts}

        still = client.post("/api/text-panels/preview", json={"content": "HELLO"})
        assert still.status_code == 200 and still.headers["content-type"] == "image/png"
        assert Image.open(BytesIO(still.content)).size == (96, 64)

        animated = client.post(
            "/api/text-panels/preview", json={"content": "HELLO", "effect": "pulse"}
        )
        assert animated.headers["content-type"] == "image/gif"
        assert Image.open(BytesIO(animated.content)).n_frames > 10

        bad_font = client.post("/api/text-panels", json={"name": "x", "content": "x", "font_family": "/etc/passwd"})
        assert bad_font.status_code == 422

        media = client.post(
            "/api/media", files={"file": ("bg.png", png_bytes("blue"), "image/png")}
        ).json()
        panel = client.post(
            "/api/text-panels",
            json={"name": "Over", "content": "OVER", "background_media_id": media["id"], "effect": "shake"},
        )
        assert panel.status_code == 201
        preview = client.get(f"/api/text-panels/{panel.json()['id']}/preview")
        assert preview.headers["content-type"] == "image/gif"
        assert Image.open(BytesIO(preview.content)).convert("RGB").getpixel((1, 1))[2] > 150
        assert client.delete(f"/api/media/{media['id']}").status_code == 409


def test_live_frame_mirrors_the_panel(tmp_path: Path) -> None:
    matrix = MockMatrixAdapter()
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=matrix)) as client:
        panel = add_panel(client, "LIVE", color_value="#ff0000")
        client.post("/api/playback/send", json={"target_type": "text_panel", "target_id": panel["id"]})
        assert wait_for(lambda: matrix.frame.getbbox() is not None)
        response = client.get("/api/playback/frame")
        assert response.status_code == 200
        assert Image.open(BytesIO(response.content)).getbbox()
        client.post("/api/playback/stop")


def test_old_database_gains_new_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute("INSERT INTO text_panels (name, content) VALUES ('Old', 'OLD')")
    connection.execute("INSERT INTO playlists (name) VALUES ('Old list')")
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()
    database.initialize()
    panel = database.fetch_one("SELECT effect, outline, background_media_id FROM text_panels")
    assert (panel["effect"], panel["outline"], panel["background_media_id"]) == ("none", 1, None)
    playlist = database.fetch_one("SELECT play_mode, resume_position FROM playlists")
    assert (playlist["play_mode"], playlist["resume_position"]) == ("loop", 0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_cutting_a_video_short_does_not_stall(tmp_path: Path) -> None:
    import subprocess

    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30",
         "-t", "20", "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    source = VideoFrameSource(video, (96, 64))
    frames = iter(source)
    next(frames)
    started = time.monotonic()
    frames.close()
    assert time.monotonic() - started < 0.5


class SlowDecoder(FrameSource):
    """Claims 24 fps but takes 100 ms to produce each frame, like 4K phone video."""

    def __iter__(self) -> Iterator[Frame]:
        for index in range(30):
            time.sleep(0.1)
            yield Frame(Image.new("RGB", (96, 64), (index * 8, 0, 0)), 1 / 24)


def test_video_slower_than_real_time_keeps_moving_instead_of_freezing() -> None:
    shown: List[tuple] = []

    class Recording(MockMatrixAdapter):
        def show(self, image: Image.Image) -> None:
            shown.append(image.getpixel((0, 0)))
            super().show(image)

    service = PlaybackService(Recording())
    service.start([PlaybackItem(source=SlowDecoder(), duration=2.0, play_by="time")], mode="once")
    assert wait_for(lambda: not service.is_running, timeout=6)
    assert len(set(shown)) >= 10


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_phone_video_gets_a_small_playback_copy(tmp_path: Path) -> None:
    import subprocess

    from matrix_player.storage import MediaStorage

    settings = make_settings(tmp_path)
    settings.ensure_directories()
    original = settings.media_dir / "abc.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60",
         "-t", "2", "-pix_fmt", "yuv420p", str(original)],
        check=True,
    )
    storage = MediaStorage(settings)
    assert storage.playable_path("abc.mov") == original
    assert storage.build_proxy("abc.mov")
    proxy = storage.playable_path("abc.mov")
    assert proxy.name == "abc.play.mp4"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate", "-of", "csv=p=0", str(proxy)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    width, height, rate = probe.split(",")
    assert max(int(width), int(height)) <= 320
    numerator, denominator = rate.split("/")
    assert int(numerator) / int(denominator) <= 30
    storage.delete("abc.mov", None)
    assert not proxy.exists()


def test_scroll_directions_move_opposite_ways() -> None:
    def text_left_edge(effect: str, t: float) -> int:
        frame = TextRenderer("HELLO WORLD", (96, 64), TextStyle(effect=effect, color_value="#ffffff")).frame(t)
        return frame.getbbox()[0]

    def text_right_edge(effect: str, t: float) -> int:
        frame = TextRenderer("HELLO WORLD", (96, 64), TextStyle(effect=effect, color_value="#ffffff")).frame(t)
        return frame.getbbox()[2]

    # Right to left: text enters at the right edge and its left edge moves left.
    assert text_left_edge("scroll", 0.5) > text_left_edge("scroll", 1.5)
    # Left to right: text enters at the left edge and its right edge moves right.
    assert text_right_edge("scroll_right", 0.5) < text_right_edge("scroll_right", 1.5)
    assert TextRenderer("HELLO WORLD", (96, 64), TextStyle(effect="scroll_right")).cycle_seconds > 3


def test_motion_speed_paces_scrolling(tmp_path: Path) -> None:
    def seconds_per_pass(speed: str) -> float:
        style = TextStyle(effect="scroll", motion_speed=speed)
        return TextRenderer("HELLO WORLD", (96, 64), style).cycle_seconds

    assert seconds_per_pass("slow") > seconds_per_pass("normal") > seconds_per_pass("fast")
    fast = TextRenderer("HELLO WORLD", (96, 64), TextStyle(effect="scroll", motion_speed="fast"))
    normal = TextRenderer("HELLO WORLD", (96, 64), TextStyle(effect="scroll"))
    assert fast.frame(1.0).getbbox()[0] < normal.frame(1.0).getbbox()[0]

    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        panel = client.post(
            "/api/text-panels",
            json={"name": "Fast", "content": "GO", "effect": "scroll_right", "motion_speed": "fast"},
        )
        assert panel.status_code == 201
        assert panel.json()["motion_speed"] == "fast"
        assert client.post(
            "/api/text-panels", json={"name": "x", "content": "x", "motion_speed": "warp"}
        ).status_code == 422


def test_morph_flows_between_images_and_lands_exactly() -> None:
    from matrix_player.rendering import transition_frames

    old = TextRenderer("NO", (96, 64), TextStyle(color_value="#ffff00")).frame()
    new = TextRenderer("YES", (96, 64), TextStyle(color_value="#ff0000")).frame()
    frames = list(transition_frames(old, new, "morph", 12))
    assert len(frames) == 12
    assert list(frames[-1].getdata()) == list(new.getdata())
    middle = frames[6]
    assert list(middle.getdata()) != list(old.getdata())
    assert list(middle.getdata()) != list(new.getdata())
    # Works from and to an all-black frame too.
    black = Image.new("RGB", (96, 64))
    assert list(list(transition_frames(black, new, "morph", 6))[-1].getdata()) == list(new.getdata())
    assert list(transition_frames(old, black, "morph", 6))[-1].getbbox() is None


def test_media_list_reports_videos_still_being_prepared(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        with client.app.state.database.transaction() as connection:
            connection.execute(
                """INSERT INTO media (filename, display_name, media_type, mime_type, file_size_bytes)
                   VALUES ('abc.mov', 'IMG_0001.mov', 'video', 'video/quicktime', 10)"""
            )
        client.app.state.proxies.submit("abc.mov")
        listed = client.get("/api/media")
        assert listed.status_code == 200
        video = listed.json()[0]
        assert video["media_type"] == "video"
        assert isinstance(video["preparing"], bool)


def gif_frames(data: bytes) -> List[Image.Image]:
    from PIL import ImageSequence

    with Image.open(BytesIO(data)) as gif:
        return [frame.convert("RGB") for frame in ImageSequence.Iterator(gif)]


def gif_seconds(data: bytes) -> float:
    """Total play time of an animated GIF or WebP; encoders may merge identical
    consecutive frames, so count time rather than frames."""
    from PIL import ImageSequence

    total = 0
    with Image.open(BytesIO(data)) as animation:
        for frame in ImageSequence.Iterator(animation):
            frame.load()  # WebP fills in per-frame timing only once a frame is loaded
            total += frame.info.get("duration", 0)
    return total / 1000


def test_item_preview_shows_previous_item_then_unsaved_entrance(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        green = add_panel(client, "GO", color_value="#00ff00", background_color="#003300")
        red = add_panel(client, "STOP", color_value="#ff0000", background_color="#330000")
        playlist = client.post("/api/playlists", json={"name": "Preview"}).json()
        before = add_item(client, playlist["id"], item_type="text_panel", text_panel_id=green["id"], duration_ms=2000)

        unsaved = {
            "item_type": "text_panel", "text_panel_id": red["id"], "duration_ms": 1000,
            "transition_type": "morph", "transition_duration_ms": 600,
            "previous_item_id": before["id"],
        }
        response = client.post("/api/playlists/preview-item", json=unsaved)
        assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
        frames = gif_frames(response.content)
        # Lead-in (0.6 s) + entrance (~0.6 s) + item (1 s).
        assert 1.8 <= gif_seconds(response.content) <= 2.6
        assert frames[0].getpixel((1, 1))[1] > frames[0].getpixel((1, 1))[0]  # starts on the green panel
        assert frames[-1].getpixel((1, 1))[0] > frames[-1].getpixel((1, 1))[1]  # ends on the red one

        # Nothing was saved by previewing.
        assert len(client.get(f"/api/playlists/{playlist['id']}").json()["items"]) == 1


def test_run_through_pages_and_survives_a_missing_file(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        playlist = client.post("/api/playlists", json={"name": "Run"}).json()
        media = client.post(
            "/api/media", files={"file": ("gone.png", png_bytes(), "image/png")}
        ).json()
        add_item(client, playlist["id"], item_type="media", media_id=media["id"], duration_ms=1000)
        for word in ("ONE", "TWO", "THREE"):
            panel = add_panel(client, word)
            add_item(client, playlist["id"], item_type="text_panel", text_panel_id=panel["id"],
                     duration_ms=1000, transition_type="impact", transition_duration_ms=500)
        (tmp_path / "media" / media["filename"]).unlink()

        whole = client.get(f"/api/playlists/{playlist['id']}/run-through")
        assert whole.status_code == 200 and whole.headers["content-type"] == "image/webp"
        # Error card (1 s) + three items of 1 s, each with a 0.5 s entrance.
        assert 4.5 <= gif_seconds(whole.content) <= 5.5
        page = client.get(f"/api/playlists/{playlist['id']}/run-through?start=3&count=8")
        assert page.status_code == 200
        assert gif_seconds(page.content) < gif_seconds(whole.content)
        assert client.get(f"/api/playlists/{playlist['id']}/run-through?start=10").status_code == 404


def test_media_tags_rename_search_and_group(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        ids = [
            client.post("/api/media", files={"file": (f"IMG_{n}.png", png_bytes(color), "image/png")}).json()["id"]
            for n, color in ((1, "red"), (2, "green"), (3, "blue"))
        ]
        first = client.patch(f"/api/media/{ids[0]}", json={"display_name": "  Crowd   shot ", "tags": ["#March", "night", "march", " "]})
        assert first.status_code == 200
        assert first.json()["display_name"] == "Crowd shot"
        assert first.json()["tags"] == ["march", "night"]
        client.patch(f"/api/media/{ids[1]}", json={"tags": ["march"]})

        assert [tag["name"] for tag in client.get("/api/media/tags").json()] == ["march", "night"]
        assert client.get("/api/media/tags").json()[0]["count"] == 2
        grouped = {item["id"] for item in client.get("/api/media", params={"tag": "#March"}).json()}
        assert grouped == {ids[0], ids[1]}
        searched = {item["id"] for item in client.get("/api/media", params={"query": "nig"}).json()}
        assert searched == {ids[0]}
        assert client.get(f"/api/media/{ids[2]}").json()["tags"] == []

        # Renaming alone leaves tags; clearing tags drops tags nobody uses.
        assert client.patch(f"/api/media/{ids[0]}", json={"display_name": "Crowd"}).json()["tags"] == ["march", "night"]
        client.patch(f"/api/media/{ids[0]}", json={"tags": []})
        assert [tag["name"] for tag in client.get("/api/media/tags").json()] == ["march"]
        client.delete(f"/api/media/{ids[1]}")
        assert client.get("/api/media/tags").json() == []

        assert client.patch(f"/api/media/{ids[0]}", json={"tags": ["x" * 33]}).status_code == 422
        assert client.patch(f"/api/media/{ids[0]}", json={"display_name": "   "}).status_code == 422
        assert client.patch("/api/media/9999", json={"tags": ["a"]}).status_code == 404


def test_backup_downloads_everything_except_secrets_and_proxies(tmp_path: Path, monkeypatch) -> None:
    import zipfile

    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        media = client.post("/api/media", files={"file": ("photo.png", png_bytes(), "image/png")}).json()
        client.patch(f"/api/media/{media['id']}", json={"tags": ["march"]})
        panel = add_panel(client, "NO KINGS")
        playlist = client.post("/api/playlists", json={"name": "Keep"}).json()
        add_item(client, playlist["id"], item_type="text_panel", text_panel_id=panel["id"])
        (tmp_path / "media" / "abc.play.mp4").write_bytes(b"proxy")
        (tmp_path / "media" / ".upload-123").write_bytes(b"partial")
        (tmp_path / "protest.env").write_text("SECRET=1")

        estimate = client.get("/api/diagnostics").json()["backup_estimate_bytes"]
        assert estimate > 0
        response = client.get("/api/backup")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "protest-backup-" in response.headers["content-disposition"]

        archive = tmp_path / "downloaded.zip"
        archive.write_bytes(response.content)
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            manifest = json.loads(bundle.read("protest-backup/manifest.json"))
            bundle.extract("protest-backup/database/db.sqlite3", tmp_path / "out")
        assert f"protest-backup/media/{media['filename']}" in names
        assert f"protest-backup/thumbnails/{media['thumbnail_path']}" in names
        assert "protest-backup/README.txt" in names
        assert not any(name.endswith((".play.mp4", ".env")) or ".upload-" in name for name in names)
        assert manifest["counts"]["text_panels"] == 1 and manifest["counts"]["tags"] == 1

        restored = sqlite3.connect(tmp_path / "out" / "protest-backup" / "database" / "db.sqlite3")
        assert restored.execute("SELECT content FROM text_panels").fetchone()[0] == "NO KINGS"
        restored.close()
        assert not list(tmp_path.glob(".protest-backup-*.zip"))
        assert not list(tmp_path.glob(".backup-*"))

        import matrix_player.backup as backup_module

        monkeypatch.setattr(backup_module.shutil, "disk_usage", lambda _path: shutil._ntuple_diskusage(10, 10, 0))
        assert client.get("/api/backup").status_code == 507


def gif_bytes(delays: List[int]) -> bytes:
    """A GIF with one frame per delay (ms); a single delay makes a one-frame GIF."""
    # Visibly different frames: the GIF encoder merges identical consecutive ones.
    frames = [Image.new("RGB", (40, 30), (index * 90 % 256, 255 - index * 70 % 256, 40)) for index in range(len(delays))]
    output = BytesIO()
    frames[0].save(output, format="GIF", save_all=len(frames) > 1, append_images=frames[1:],
                   duration=delays if len(delays) > 1 else delays[0], loop=0, disposal=1)
    return output.getvalue()


def test_gif_uploads_are_checked_for_animation(tmp_path: Path) -> None:
    from matrix_player.playback import build_media_source
    from matrix_player.rendering import GifFrameSource, ImageFrameSource

    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        still = client.post("/api/media", files={"file": ("still.gif", gif_bytes([50]), "image/gif")}).json()
        moving = client.post("/api/media", files={"file": ("moving.gif", gif_bytes([50, 10, 200]), "image/gif")}).json()
        assert (still["media_type"], still["duration_ms"], still["mime_type"]) == ("image", None, "image/gif")
        assert moving["media_type"] == "gif"
        assert moving["duration_ms"] == 350  # the 10 ms delay counts as 100 ms, like browsers

        storage = client.app.state.storage
        assert isinstance(build_media_source(storage, still["filename"], (96, 64), 6.0, "fit", "image"), ImageFrameSource)
        assert isinstance(build_media_source(storage, moving["filename"], (96, 64), 6.0, "fit", "gif"), GifFrameSource)
        assert {item["id"] for item in client.get("/api/media", params={"type": "gif"}).json()} == {moving["id"]}


def test_existing_gifs_are_rechecked_once_at_startup(tmp_path: Path) -> None:
    from matrix_player.maintenance import GIF_CHECK_MARKER

    settings = make_settings(tmp_path)
    settings.ensure_directories()
    (settings.media_dir / "still.gif").write_bytes(gif_bytes([50]))
    (settings.media_dir / "moving.gif").write_bytes(gif_bytes([100, 100]))
    database = Database(settings.database_path)
    database.initialize()
    with database.transaction() as connection:
        for name, wrong_ms in (("still.gif", 50), ("moving.gif", 999)):
            connection.execute(
                """INSERT INTO media (filename, display_name, media_type, mime_type, duration_ms, file_size_bytes)
                   VALUES (?, ?, 'gif', 'image/gif', ?, 1)""",
                (name, name, wrong_ms),
            )

    def types() -> dict:
        return {row["filename"]: (row["media_type"], row["duration_ms"]) for row in database.fetch_all("SELECT * FROM media")}

    with TestClient(create_app(settings, matrix_adapter=MockMatrixAdapter())):
        assert wait_for(lambda: (settings.data_dir / GIF_CHECK_MARKER).exists())
    assert types() == {"still.gif": ("image", None), "moving.gif": ("gif", 200)}

    with database.transaction() as connection:
        connection.execute("UPDATE media SET media_type = 'gif' WHERE filename = 'still.gif'")
    with TestClient(create_app(settings, matrix_adapter=MockMatrixAdapter())):
        time.sleep(0.3)
    assert types()["still.gif"][0] == "gif"  # the check does not repeat once done


def test_edit_pane_player_for_animations(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        still = client.post("/api/media", files={"file": ("still.png", png_bytes(), "image/png")}).json()
        moving = client.post("/api/media", files={"file": ("moving.gif", gif_bytes([100, 100, 100]), "image/gif")}).json()
        assert client.get(f"/api/media/{still['id']}/playback").status_code == 404

        played = client.get(f"/api/media/{moving['id']}/playback")
        assert played.status_code == 200
        expected = "video/mp4" if shutil.which("ffmpeg") else "image/gif"
        assert played.headers["content-type"] == expected
        # iPhone Safari only plays video from servers that honour byte ranges.
        partial = client.get(f"/api/media/{moving['id']}/playback", headers={"Range": "bytes=0-9"})
        assert partial.status_code == 206 and len(partial.content) == 10

        if shutil.which("ffmpeg"):
            proxy = tmp_path / "media" / (Path(moving["filename"]).stem + ".play.mp4")
            assert proxy.is_file()
            client.delete(f"/api/media/{moving['id']}")
            assert not proxy.exists()


def test_polling_requests_stay_out_of_the_access_log() -> None:
    import logging

    from matrix_player.app import PollingAccessFilter

    def record(path: str, status: int) -> logging.LogRecord:
        return logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
            ("10.42.0.5:5000", "GET", path, "1.1", status), None,
        )

    keep = PollingAccessFilter().filter
    assert not keep(record("/api/playback/frame?t=123", 200))
    assert not keep(record("/api/status", 200))
    assert keep(record("/api/playback/frame?t=123", 503))  # failures still show
    assert keep(record("/api/media", 200))
    assert keep(record("/api/playback/send", 200))


def test_sending_a_disabled_playlist_is_refused_like_starting_it(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        playlist = client.post("/api/playlists", json={"name": "Off", "is_enabled": False}).json()
        panel = add_panel(client, "HI")
        add_item(client, playlist["id"], item_type="text_panel", text_panel_id=panel["id"])
        sent = client.post("/api/playback/send", json={"target_type": "playlist", "target_id": playlist["id"]})
        assert sent.status_code == 409
        assert client.post("/api/playback/send", json={"target_type": "playlist", "target_id": 999}).status_code == 404


class FakeSensors:
    def __init__(self) -> None:
        from matrix_player.health import Reading

        self.reading = Reading(45.0, 0, 1500)

    def __call__(self):
        return self.reading


def make_monitor(tmp_path: Path, clock: List[float]):
    from matrix_player.health import HealthMonitor

    database = Database(tmp_path / "health.sqlite3")
    database.initialize()
    sensors = FakeSensors()
    return HealthMonitor(database, sampler=sensors, clock=lambda: clock[0]), sensors, database


def test_heat_levels_events_and_hysteresis(tmp_path: Path) -> None:
    from matrix_player.health import Reading

    clock = [1_800_000_000.0]
    monitor, sensors, _ = make_monitor(tmp_path, clock)

    def step(temp: float, flags: int = 0) -> dict:
        sensors.reading = Reading(temp, flags, 1500)
        clock[0] += 10
        monitor.sample()
        return monitor.summary()

    assert step(45)["level"] == "ok"
    warm = step(72)
    assert warm["level"] == "warning" and "Getting warm (72" in warm["message"]
    hot = step(81, 0x8)  # soft temperature limit active
    assert hot["level"] == "critical" and "slowing itself down" in hot["message"]
    assert "cooled" not in [event["kind"] for event in monitor.events()]  # heating up is not cooling
    step(79, 0x8)
    step(78)  # still within the 3 °C margin below "hot": no flapping
    kinds = [event["kind"] for event in monitor.events()]
    assert kinds.count("hot") == 1 and kinds.count("warm") == 1

    cooled = step(50)
    assert cooled["level"] == "warning"  # the overheating is still reported for the day
    assert "earlier" in cooled["message"]
    assert cooled["day_max_c"] == 81
    assert {"cooled_from_hot", "throttle_cleared"} <= {event["kind"] for event in monitor.events()}


def test_power_problems_now_and_since_boot(tmp_path: Path) -> None:
    from matrix_player.health import Reading

    clock = [1_800_000_000.0]
    monitor, sensors, _ = make_monitor(tmp_path, clock)
    sensors.reading = Reading(45.0, 0x1 | 0x10000, 1500)
    monitor.sample()
    now = monitor.summary()
    assert now["level"] == "critical" and now["undervoltage_now"] and "Power is too low" in now["message"]

    sensors.reading = Reading(45.0, 0x10000, 1500)  # cleared, but it happened since boot
    clock[0] += 10
    monitor.sample()
    later = monitor.summary()
    assert later["level"] == "warning" and later["undervoltage_since_boot"] and not later["undervoltage_now"]
    assert [event["kind"] for event in monitor.events()][:2] == ["power_ok", "power_low"]


def test_heat_record_survives_restart_and_charts(tmp_path: Path) -> None:
    from matrix_player.health import HealthMonitor, Reading

    clock = [1_800_000_000.0]
    monitor, sensors, database = make_monitor(tmp_path, clock)
    for minute, temp in enumerate((50, 64, 58)):
        sensors.reading = Reading(float(temp), 0, 1500)
        clock[0] = 1_800_000_000.0 + minute * 60
        monitor.sample()
    monitor.close()

    restarted = HealthMonitor(database, sampler=sensors, clock=lambda: clock[0])
    sensors.reading = Reading(55.0, 0, 1500)
    clock[0] += 60
    restarted.sample()
    assert restarted.summary()["day_max_c"] == 64
    history = restarted.history(hours=1, bucket_minutes=1)
    assert [point["temp_max"] for point in history] == [50, 64, 58, 55]


def test_no_sensors_is_reported_honestly(tmp_path: Path) -> None:
    from matrix_player.health import Reading

    clock = [1_800_000_000.0]
    monitor, sensors, _ = make_monitor(tmp_path, clock)
    sensors.reading = Reading(None, None, None)
    monitor.sample()
    assert monitor.summary()["level"] == "unknown"


def test_health_api(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        assert "level" in client.get("/api/status").json()["health"]
        details = client.get("/api/health").json()
        assert set(details) == {"summary", "history", "events"}


def test_gamma_table_maths() -> None:
    from matrix_player.display import gamma_table

    table = gamma_table(2.2)
    assert len(table) == 768
    assert (table[0], table[128], table[255]) == (0, 56, 255)
    assert gamma_table(1.0, 0.5)[255] == 128


def test_brightness_and_gamma_apply_live_and_persist(tmp_path: Path) -> None:
    from matrix_player.display import Correction

    settings = make_settings(tmp_path)
    matrix = MockMatrixAdapter()
    with TestClient(create_app(settings, matrix_adapter=matrix)) as client:
        assert client.get("/api/display").json() == {"brightness": 100, "gamma": 1.0}
        grey = Image.new("RGB", (96, 64), (128, 128, 128))
        matrix.show(grey)
        assert matrix.history[-1].getpixel((0, 0)) == (128, 128, 128)

        changed = client.put("/api/display", json={"brightness": 50, "gamma": 2.2})
        assert changed.status_code == 200 and changed.json() == {"brightness": 50, "gamma": 2.2}
        # The held picture is redrawn at once with the new settings...
        assert matrix.history[-1].getpixel((0, 0)) == (28, 28, 28)
        # ...while the live mirror keeps showing the design itself.
        assert matrix.frame.getpixel((0, 0)) == (128, 128, 128)

        assert client.put("/api/display", json={"brightness": 2, "gamma": 1.0}).status_code == 422
        assert client.put("/api/display", json={"brightness": 80, "gamma": 3.5}).status_code == 422

    restarted = MockMatrixAdapter()
    with TestClient(create_app(settings, matrix_adapter=restarted)) as client:
        assert client.get("/api/display").json() == {"brightness": 50, "gamma": 2.2}
        assert restarted.correction == Correction(50, 2.2)


def test_test_card_shows_and_returns_to_the_playlist(tmp_path: Path) -> None:
    matrix = MockMatrixAdapter()
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=matrix)) as client:
        playlist = client.post("/api/playlists", json={"name": "Loop"}).json()
        panel = add_panel(client, "LOOP")
        add_item(client, playlist["id"], item_type="text_panel", text_panel_id=panel["id"], duration_ms=500)
        client.post("/api/playback/start", json={"playlist_id": playlist["id"]})

        shown = client.post("/api/display/test-card")
        assert shown.status_code == 200
        assert shown.json()["source_label"] == "Test card"
        assert shown.json()["resume_playlist_id"] == playlist["id"]
        # The card has a white block on the left and black at the far right of the ramp start.
        assert wait_for(lambda: matrix.frame.getpixel((2, 60)) == (255, 255, 255))
        assert matrix.frame.getpixel((0, 0)) == (0, 0, 0)

        released = client.post("/api/playback/release").json()
        assert released["playlist_id"] == playlist["id"]
        client.post("/api/playback/stop")


def test_batch_tags_add_and_remove_across_a_selection(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        ids = [
            client.post("/api/media", files={"file": (f"p{n}.png", png_bytes(color), "image/png")}).json()["id"]
            for n, color in ((1, "red"), (2, "green"), (3, "blue"))
        ]
        client.patch(f"/api/media/{ids[0]}", json={"tags": ["night"]})

        added = client.post("/api/media/batch-tags", json={"media_ids": ids[:2], "add": ["#March", "rally"]})
        assert added.status_code == 200
        by_id = {item["id"]: item["tags"] for item in added.json()}
        assert by_id == {ids[0]: ["march", "night", "rally"], ids[1]: ["march", "rally"]}
        assert client.get(f"/api/media/{ids[2]}").json()["tags"] == []

        swapped = client.post(
            "/api/media/batch-tags", json={"media_ids": ids, "add": ["crowd"], "remove": ["rally"]}
        ).json()
        assert {item["id"]: item["tags"] for item in swapped}[ids[0]] == ["crowd", "march", "night"]
        assert "rally" not in [tag["name"] for tag in client.get("/api/media/tags").json()]

        missing = client.post("/api/media/batch-tags", json={"media_ids": [ids[0], 9999], "add": ["x"]})
        assert missing.status_code == 404
        assert "x" not in client.get(f"/api/media/{ids[0]}").json()["tags"]  # nothing half-applied

        many = [f"t{n}" for n in range(19)]
        assert client.post("/api/media/batch-tags", json={"media_ids": [ids[0]], "add": many}).status_code == 422
        assert client.post("/api/media/batch-tags", json={"media_ids": [], "add": ["x"]}).status_code == 422


def test_usage_counters_record_how_items_are_used(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings, matrix_adapter=MockMatrixAdapter())) as client:
        media = client.post("/api/media", files={"file": ("p.png", png_bytes(), "image/png")}).json()
        loved = add_panel(client, "LOVED")
        dropped = add_panel(client, "DROPPED")
        playlist = client.post("/api/playlists", json={"name": "Count"}).json()
        add_item(client, playlist["id"], item_type="text_panel", text_panel_id=loved["id"], duration_ms=1200)
        add_item(client, playlist["id"], item_type="media", media_id=media["id"], duration_ms=1200)
        quick = add_item(client, playlist["id"], item_type="text_panel", text_panel_id=dropped["id"], duration_ms=1200)

        client.delete(f"/api/playlists/{playlist['id']}/items/{quick['id']}")  # tried and dropped
        client.post("/api/playback/start", json={"playlist_id": playlist["id"]})
        time.sleep(3.0)  # long enough for both remaining items to play once
        client.post("/api/playback/send", json={"target_type": "text_panel", "target_id": loved["id"]})
        client.post("/api/playback/stop")
        client.put(f"/api/text-panels/{loved['id']}", json={**{k: loved[k] for k in ("name", "content")}, "color_value": "#ff0000"})

        totals = {(row["kind"], row["item_id"]): row for row in client.get("/api/usage").json()}
        loved_row = totals[("text_panel", loved["id"])]
        assert loved_row["plays"] >= 1 and loved_row["seconds"] >= 1.0
        assert loved_row["sends"] == 1 and loved_row["edits"] == 1
        assert totals[("media", media["id"])]["plays"] >= 1
        assert totals[("text_panel", dropped["id"])]["quick_removals"] == 1

    # Counts are written to the database and survive a restart.
    with TestClient(create_app(settings, matrix_adapter=MockMatrixAdapter())) as client:
        rows = {(row["kind"], row["item_id"]): row for row in client.get("/api/usage").json()}
        assert rows[("text_panel", loved["id"])]["sends"] == 1


def test_usage_removal_long_after_adding_is_not_quick(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    from matrix_player.usage import UsageRecorder

    database = Database(tmp_path / "usage.sqlite3")
    database.initialize()
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    recorder = UsageRecorder(database, clock=lambda: now)
    recorder.record_removal("media", 7, (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"))
    recorder.record_removal("media", 8, (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"))
    recorder.record_removal("media", 9, None)
    recorder.record_play("media", 7, 0.4)  # a glimpse adds time but not a play
    rows = {row["item_id"]: row for row in recorder.totals()}
    assert 8 in rows and rows[8]["quick_removals"] == 1
    assert rows[7]["quick_removals"] == 0 and rows[7]["plays"] == 0 and rows[7]["seconds"] == 0.4
    assert 9 not in rows
