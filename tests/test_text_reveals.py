"""Text reveals, the globe scroll, the backing plate, and the eased and new transitions."""

from io import BytesIO
from pathlib import Path
import sqlite3
import time

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from matrix_player.app import create_app
from matrix_player.database import SCHEMA, Database
from matrix_player.matrix import MockMatrixAdapter
from matrix_player.playback import PlaybackItem, PlaybackService
from matrix_player.rendering import TextFrameSource, TextRenderer, TextStyle, transition_frames
from matrix_player.rendering.reveals import CAP_SECONDS, REVEALS
from matrix_player.rendering.text import _stamp, colour_field

from test_signage import make_settings, wait_for

SIZES = [(96, 64), (64, 32), (192, 64), (384, 128)]
STILL_REVEALS = [reveal for reveal in REVEALS if reveal != "none"]


def pixels(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.int16)


@pytest.mark.parametrize("style", [
    TextStyle(), TextStyle(font_family="comic"), TextStyle(font_family="pixel", outline=False),
    TextStyle(italic=True), TextStyle(bold=True, alignment="left"),
])
def test_letters_drawn_one_by_one_match_the_whole_block(style: TextStyle) -> None:
    for text in ("NO KINGS", "WE THE PEOPLE ARE WATCHING"):
        renderer = TextRenderer(text, (96, 64), style)
        width, height = renderer.block.size
        drawn = np.zeros((height, width), np.float32)
        for letter in renderer.letters:
            if letter.glyph is not None:
                _stamp(drawn, letter.glyph, *letter.glyph_at)
        whole = np.asarray(renderer.block.glyph, np.float32)
        # Only antialiased edges may differ, by a little.
        assert np.abs(drawn - whole).mean() < 2.0
        assert (np.abs(drawn - whole) > 96).mean() < 0.01
        assert [letter.placement.char for letter in renderer.letters] == [
            char for line, _, _ in renderer.block.lines for char in line
        ]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("reveal", STILL_REVEALS)
def test_every_reveal_ends_on_exactly_the_still_frame(size, reveal) -> None:
    plain = TextRenderer("NO KINGS", size, TextStyle(color_value="#ffdd00"))
    revealed = TextRenderer("NO KINGS", size, TextStyle(color_value="#ffdd00", reveal=reveal))
    lead = revealed.lead_seconds
    assert 0 < lead <= CAP_SECONDS
    assert not np.array_equal(pixels(revealed.frame(0.0)), pixels(plain.frame(0.0)))
    assert np.array_equal(pixels(revealed.frame(lead)), pixels(plain.frame(0.0)))
    # Then it plays the message's own motion from its start.
    moving = TextRenderer("NO KINGS", size, TextStyle(effect="pulse", reveal=reveal))
    still_moving = TextRenderer("NO KINGS", size, TextStyle(effect="pulse"))
    assert np.array_equal(pixels(moving.frame(moving.lead_seconds + 0.3)), pixels(still_moving.frame(0.3)))


@pytest.mark.parametrize("reveal", STILL_REVEALS)
def test_reveals_fit_the_cap_however_long_or_slow(reveal) -> None:
    short = TextRenderer("NO", (96, 64), TextStyle(reveal=reveal)).lead_seconds
    long_text = "WE ARE THE MANY AND WE WILL BE HEARD " * 4
    for speed in ("slow", "normal", "fast"):
        long_lead = TextRenderer(long_text, (96, 64), TextStyle(reveal=reveal, motion_speed=speed)).lead_seconds
        assert long_lead <= CAP_SECONDS + 1e-6
    slow = TextRenderer("NO", (96, 64), TextStyle(reveal=reveal, motion_speed="slow")).lead_seconds
    fast = TextRenderer("NO", (96, 64), TextStyle(reveal=reveal, motion_speed="fast")).lead_seconds
    assert fast < short <= slow


def test_typewriter_types_in_reading_order_with_a_cursor() -> None:
    renderer = TextRenderer("NO KINGS", (96, 64), TextStyle(reveal="typewriter", color_value="#ffffff"))
    widths = []
    for step in range(1, 8):
        box = renderer.frame(renderer.schedule.gap * step + 0.001).getbbox()
        widths.append(box[2] - box[0])
    assert widths == sorted(widths) and widths[-1] > widths[0]
    # Before any letter, only the cursor shows: a narrow upright bar.
    left, top, right, bottom = renderer.frame(0.0).getbbox()
    assert right - left < bottom - top


def test_reveals_that_need_still_text_are_ignored_where_they_do_not_fit() -> None:
    for effect in ("scroll", "scroll_right", "globe"):
        assert TextRenderer("NO KINGS", (96, 64), TextStyle(effect=effect, reveal="drop_in")).reveal == "none"
        assert TextRenderer("NO KINGS", (96, 64), TextStyle(effect=effect, reveal="neon")).reveal == "neon"
    for effect in ("pow", "zoom"):
        assert TextRenderer("NO KINGS", (96, 64), TextStyle(effect=effect, reveal="typewriter")).lead_seconds == 0


def test_neon_on_a_scrolling_message_lights_letters_as_they_arrive() -> None:
    style = TextStyle(effect="scroll", color_value="#ffffff", outline=False)
    plain = TextRenderer("NO KINGS", (96, 64), style)
    neon = TextRenderer("NO KINGS", (96, 64), TextStyle(**{**style.__dict__, "reveal": "neon"}))
    assert neon.lead_seconds == 0  # it reveals while scrolling, adding no time
    # Letters come onto the sign as cold tubes and light up after.
    entering = [index / 30 for index in range(1, 60)]
    assert all(pixels(neon.frame(t)).sum() <= pixels(plain.frame(t)).sum() for t in entering)
    for letter in neon.letters[:3]:
        if letter.glyph is None:
            continue
        arrived = letter.placement.x / neon.scroll_speed + 0.05
        assert pixels(neon.frame(arrived)).sum() < pixels(plain.frame(arrived)).sum()
    late = plain.cycle_seconds * 0.9
    assert np.array_equal(pixels(neon.frame(late)), pixels(plain.frame(late)))


@pytest.mark.parametrize("size", SIZES)
def test_globe_bends_text_round_a_cylinder(size) -> None:
    flat = TextRenderer("NO KINGS", size, TextStyle(effect="scroll", color_value="#ffffff"))
    globe = TextRenderer("NO KINGS", size, TextStyle(effect="globe", color_value="#ffffff"))
    # The strip round the cylinder is longer than the flat box, so a pass takes longer.
    assert globe.cycle_seconds > flat.cycle_seconds
    middle = globe.cycle_seconds / 2
    frame = pixels(globe.frame(middle)).sum(axis=2)
    columns = frame.sum(axis=0)
    width = size[0]
    # Letters dim as they turn away: the middle third is brighter than the edges.
    assert columns[width // 3: 2 * width // 3].mean() > columns[: width // 8].mean()
    assert frame.max() > 600  # near white in the middle


def test_backing_plate_darkens_a_busy_picture_round_the_text() -> None:
    size = (96, 64)
    busy = colour_field("plasma", size, 1.0, "#ffffff")
    style = TextStyle(font_size=20, color_value="#ffffff")
    plain = pixels(TextRenderer("NO", size, style, background=busy).frame(0))
    plated = pixels(TextRenderer("NO", size, TextStyle(font_size=20, color_value="#ffffff", backing=True), background=busy).frame(0))
    centre_row = size[1] // 2
    # Beside the text but inside the plate, the picture is darkened; the far corner is not.
    assert plated[centre_row].sum() < plain[centre_row].sum()
    assert np.array_equal(plated[0, 0], plain[0, 0])


NEW_TRANSITIONS = ["wipe_left", "wipe_right", "wipe_up", "wipe_down", "glitch", "neon"]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("kind", NEW_TRANSITIONS + ["slide_left", "fade", "fade_out"])
def test_transitions_start_near_the_old_frame_and_end_on_the_new(size, kind) -> None:
    old = TextRenderer("VOTE", size, TextStyle(color_value="#ff3080", background_color="#102040")).frame(0)
    new = TextRenderer("NO KINGS", size, TextStyle(color_value="#ffdd00")).frame(0)
    frames = list(transition_frames(old, new, kind, 12))
    assert len(frames) == 12  # playback paces by frame count
    assert np.array_equal(pixels(frames[-1]), pixels(new))
    distance_old = np.abs(pixels(frames[0]) - pixels(old)).mean()
    distance_new = np.abs(pixels(frames[0]) - pixels(new)).mean()
    assert distance_old < distance_new


def test_wipes_and_slides_ease_out() -> None:
    old = Image.new("RGB", (96, 64), (0, 0, 0))
    new = Image.new("RGB", (96, 64), (255, 255, 255))
    for kind in ("wipe_right", "slide_left", "fade"):
        frames = list(transition_frames(old, new, kind, 10))
        # Ease-out: more than half the change is done by a third of the way through.
        assert pixels(frames[2]).mean() > 255 * 0.5


def test_neon_transition_flickers_once_without_strobing() -> None:
    old = Image.new("RGB", (96, 64), (255, 255, 255))
    new = Image.new("RGB", (96, 64), (0, 0, 255))
    levels = [pixels(frame)[..., 0].mean() for frame in transition_frames(old, new, "neon", 30)]
    rising = [after > before + 1 for before, after in zip(levels, levels[1:])]
    runs = sum(1 for index, up in enumerate(rising) if up and (index == 0 or not rising[index - 1]))
    assert runs == 1  # one recovery in the old picture; the new one has no red to rise


def test_a_reveal_adds_to_the_items_time_rather_than_taking_from_it() -> None:
    style = TextStyle(reveal="drop_in")
    source = TextFrameSource("NO KINGS", (96, 64), duration=0.4, style=style)
    lead = source.lead_seconds
    assert lead > 0.3
    service = PlaybackService(MockMatrixAdapter())
    started = time.monotonic()
    service.start([PlaybackItem(source=source, duration=0.4, play_by="time")], mode="once")
    assert wait_for(lambda: not service.is_running, timeout=5)
    elapsed = time.monotonic() - started
    assert 0.4 + lead - 0.05 <= elapsed < 0.4 + lead + 0.6


def test_reveal_and_backing_are_saved_and_previewed(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        made = client.post(
            "/api/text-panels",
            json={"name": "Typed", "content": "NO KINGS", "reveal": "typewriter", "backing": True, "effect": "globe"},
        )
        assert made.status_code == 201
        assert (made.json()["reveal"], made.json()["backing"], made.json()["effect"]) == ("typewriter", True, "globe")
        assert client.post("/api/text-panels", json={"name": "x", "content": "x", "reveal": "explode"}).status_code == 422

        preview = client.post("/api/text-panels/preview", json={"content": "NO KINGS", "reveal": "letters_rise"})
        assert preview.headers["content-type"] == "image/gif"
        animation = Image.open(BytesIO(preview.content))
        assert animation.n_frames > 5  # the held still merges into one long GIF frame
        animation.seek(0)
        first = animation.convert("RGB").getbbox()
        animation.seek(animation.n_frames - 1)
        assert first is None or first != animation.convert("RGB").getbbox()

        for kind in NEW_TRANSITIONS:
            playlist = client.post("/api/playlists", json={"name": kind}).json()
            item = client.post(
                f"/api/playlists/{playlist['id']}/items",
                json={"item_type": "text_panel", "text_panel_id": made.json()["id"], "transition_type": kind},
            )
            assert item.status_code == 201, item.text


def test_old_database_gains_reveal_and_backing(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute("INSERT INTO text_panels (name, content) VALUES ('Old', 'OLD')")
    connection.commit()
    connection.close()
    database = Database(path)
    database.initialize()
    row = database.fetch_one("SELECT reveal, backing FROM text_panels")
    assert (row["reveal"], row["backing"]) == ("none", 0)
