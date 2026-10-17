"""Playback timing, play modes, resume, item-level errors, and sign text rendering."""

from io import BytesIO
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
