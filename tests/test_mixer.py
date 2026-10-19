"""The layer mixer: words on top of pictures, GIFs, and videos."""

from io import BytesIO
from pathlib import Path
import time

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image, ImageDraw
import pytest

from matrix_player.app import create_app
from matrix_player.matrix import MockMatrixAdapter
from matrix_player.playback import PlaybackItem, PlaybackService
from matrix_player.rendering import Frame, FrameSource, ImageFrameSource, TextRenderer, TextStyle
from matrix_player.rendering.mixer import BLENDS, Mix, MixFrameSource, band, blend, key_keep
from matrix_player.rendering.text import colour_field

from test_signage import make_settings, png_bytes, wait_for

SIZES = [(96, 64), (64, 32), (192, 64), (384, 128)]


def pixels(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.int16)


def first(source: FrameSource) -> Image.Image:
    return next(iter(source)).image


def mixed(picture: Image.Image, style: TextStyle = TextStyle(), **mix) -> Image.Image:
    size = picture.size
    return first(MixFrameSource(ImageFrameSource(picture, size, 1.0), "NO KINGS", style, Mix(**mix), size))


@pytest.mark.parametrize("size", SIZES)
def test_words_over_a_picture_match_the_message_drawn_on_it(size) -> None:
    picture = colour_field("plasma", size, 1.0, "#ffffff")
    style = TextStyle(color_value="#ffdd00", backing=True, border_style="solid")
    expected = TextRenderer("NO KINGS", size, style).frame(0.0, picture)
    # Recovering the message as a layer is exact up to rounding.
    assert np.abs(pixels(mixed(picture, style)) - pixels(expected)).max() <= 2


@pytest.mark.parametrize("size", SIZES)
def test_a_band_keeps_the_words_and_the_treatment_inside_it(size) -> None:
    picture = Image.new("RGB", size, (200, 40, 40))
    for placement in ("top", "bottom"):
        left, top, right, bottom = band(placement, size)
        assert bottom - top == round(size[1] * 0.4)
        out = pixels(mixed(picture, TextStyle(color_value="#ffffff"), placement=placement, dim=50))
        outside = np.ones(size[::-1], bool)
        outside[top:bottom] = False
        assert np.array_equal(out[outside], pixels(picture)[outside])
        assert (out[top:bottom] == 255).all(axis=-1).any()  # white letters in the band


def test_dim_and_blur_soften_the_picture_where_the_words_sit() -> None:
    size = (96, 64)
    stripes = Image.new("RGB", size)
    draw = ImageDraw.Draw(stripes)
    for x in range(0, 96, 2):
        draw.line((x, 0, x, 63), fill=(255, 255, 255))
    plain = pixels(mixed(stripes, placement="bottom"))
    dimmed = pixels(mixed(stripes, placement="bottom", dim=60))
    blurred = pixels(mixed(stripes, placement="bottom", blur=True))
    corner = (slice(40, 42), slice(0, 4))  # inside the band, clear of the letters
    assert dimmed[corner].mean() < plain[corner].mean()
    assert blurred[corner].std() < plain[corner].std()


def test_pick_the_clearest_colour_turns_text_dark_on_a_light_picture() -> None:
    light = Image.new("RGB", (96, 64), (240, 240, 230))
    style = TextStyle(color_value="#ffffff", outline=False)
    plain = pixels(mixed(light, style))
    chosen = pixels(mixed(light, style, auto_color=True))
    assert plain.min() > 200  # white on near-white: barely there
    assert chosen.min() < 40  # now dark letters


def test_blend_modes_on_pure_colours() -> None:
    a = np.array([[[0.2, 0.5, 1.0]]], np.float32)
    b = np.array([[[0.5, 0.5, 0.5]]], np.float32)
    expected = {
        "normal": [0.5, 0.5, 0.5], "add": [0.7, 1.0, 1.0], "screen": [0.6, 0.75, 1.0],
        "multiply": [0.1, 0.25, 0.5], "lighten": [0.5, 0.5, 1.0], "darken": [0.2, 0.5, 0.5],
        "difference": [0.3, 0.0, 0.5], "overlay": [0.2, 0.5, 1.0],
    }
    assert set(expected) == set(BLENDS)
    for mode, values in expected.items():
        assert blend(mode, a, b)[0, 0].tolist() == pytest.approx(values, abs=1e-6), mode


def test_keys_make_black_or_one_colour_see_through() -> None:
    swatches = np.array([[[0, 0, 0], [1, 1, 1], [0, 1, 0], [0, 0.6, 0], [1, 0, 1]]], np.float32)
    black = key_keep(swatches, Mix(key_mode="black"))
    assert black[0].tolist()[:2] == [0.0, 1.0]
    green = key_keep(swatches, Mix(key_mode="color", key_color="#00ff00"))
    assert green[0, 2] == 0.0  # the key colour itself
    assert green[0, 4] == 1.0  # magenta is far from green
    assert green[0, 3] < 0.5  # darker green is still green


def test_keying_the_words_or_the_picture() -> None:
    size = (96, 64)
    picture = colour_field("plasma", size, 1.0, "#ffffff")
    boxed = TextStyle(color_value="#ffdd00", backing=True, outline=True)
    # Words keyed on black: the dark box and outline vanish, the letters stay.
    keyed = pixels(mixed(picture, boxed, key_mode="black", key_layer="b"))
    plain = pixels(mixed(picture, boxed))
    assert (keyed == pixels(picture)).all(axis=-1).mean() > (plain == pixels(picture)).all(axis=-1).mean()
    # Picture keyed on green: the picture goes on top, the words show through its green.
    screen = Image.new("RGB", size, (0, 255, 0))
    ImageDraw.Draw(screen).rectangle((0, 0, 47, 63), fill=(255, 0, 255))
    out = pixels(mixed(screen, TextStyle(color_value="#ffffff", outline=False), key_mode="color", key_layer="a", key_color="#00ff00"))
    assert (out[:, :48] == [255, 0, 255]).all()  # the picture's own colour covers the words
    right = out[:, 48:]
    assert not (right == [0, 255, 0]).all(axis=-1).any()  # no green left
    assert (right == 255).all(axis=-1).any()  # white words show through


def test_moving_words_play_at_the_sign_rate_over_a_still_picture() -> None:
    size = (96, 64)
    picture = Image.new("RGB", size, (0, 0, 120))
    still = list(MixFrameSource(ImageFrameSource(picture, size, 2.0), "NO", TextStyle(), Mix(), size))
    assert len(still) == 1 and still[0].duration == pytest.approx(2.0)
    moving = list(MixFrameSource(ImageFrameSource(picture, size, 2.0), "NO", TextStyle(effect="pulse"), Mix(), size))
    assert len(moving) == 60


def test_a_reveal_plays_once_across_the_media_loops() -> None:
    size = (96, 64)

    class ShortLoop(FrameSource):
        def __iter__(self):
            for shade in (40, 80):
                yield Frame(Image.new("RGB", size, (shade, 0, 0)), 0.1)

    source = MixFrameSource(ShortLoop(), "NO KINGS", TextStyle(reveal="typewriter", color_value="#ffffff"), Mix(), size)
    lead = source.lead_seconds
    frames = []
    while sum(frame.duration for frame in frames) < lead + 0.5:
        frames += list(source)  # each pass is one loop of the media
    # Once revealed, the letters stay put through later loops instead of typing again.
    letters = []
    elapsed = 0.0
    for frame in frames:
        if elapsed >= lead + 1e-6:
            letters.append((pixels(frame.image) == 255).all(axis=-1))
        elapsed += frame.duration
    assert len(letters) > 5 and letters[0].any()
    assert all(np.array_equal(mask, letters[0]) for mask in letters)


def test_the_reveal_adds_to_an_items_time_with_words_on_top() -> None:
    size = (96, 64)
    source = MixFrameSource(
        ImageFrameSource(Image.new("RGB", size, (0, 0, 90)), size, 0.4), "NO KINGS",
        TextStyle(reveal="drop_in"), Mix(), size,
    )
    lead = source.lead_seconds
    service = PlaybackService(MockMatrixAdapter())
    started = time.monotonic()
    service.start([PlaybackItem(source=source, duration=0.4, play_by="time")], mode="once")
    assert wait_for(lambda: not service.is_running, timeout=5)
    assert 0.4 + lead - 0.05 <= time.monotonic() - started < 0.4 + lead + 0.6


def test_words_on_top_are_saved_previewed_played_and_copied(tmp_path: Path) -> None:
    matrix = MockMatrixAdapter()
    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=matrix)) as client:
        media = client.post("/api/media", files={"file": ("bg.png", png_bytes("blue"), "image/png")}).json()
        panel = client.post(
            "/api/text-panels", json={"name": "Words", "content": "NO KINGS", "color_value": "#ffffff", "outline": False}
        ).json()
        playlist = client.post("/api/playlists", json={"name": "Over"}).json()
        overlay = {"text_panel_id": panel["id"], "placement": "bottom", "halo": True, "dim": 40}
        item = client.post(
            f"/api/playlists/{playlist['id']}/items",
            json={"item_type": "media", "media_id": media["id"], "overlay": overlay, "duration_ms": 3000},
        )
        assert item.status_code == 201, item.text
        saved = item.json()["overlay"]
        assert (saved["placement"], saved["halo"], saved["dim"], saved["blend"]) == ("bottom", True, 40, "normal")

        # Only pictures and videos take words on top, and the message must exist.
        assert client.post(
            f"/api/playlists/{playlist['id']}/items",
            json={"item_type": "text_panel", "text_panel_id": panel["id"], "overlay": overlay},
        ).status_code == 422
        assert client.post(
            f"/api/playlists/{playlist['id']}/items",
            json={"item_type": "media", "media_id": media["id"], "overlay": {**overlay, "text_panel_id": 999}},
        ).status_code == 422
        assert client.post(
            f"/api/playlists/{playlist['id']}/items",
            json={"item_type": "media", "media_id": media["id"], "overlay": {**overlay, "blend": "explode"}},
        ).status_code == 422
        # A message in use as words on top cannot be deleted.
        assert client.delete(f"/api/text-panels/{panel['id']}").status_code == 409

        preview = client.post(
            "/api/playlists/preview-item",
            json={"item_type": "media", "media_id": media["id"], "overlay": overlay, "duration_ms": 3000},
        )
        assert preview.status_code == 200 and preview.headers["content-type"] == "image/webp"

        copy = client.post(f"/api/playlists/{playlist['id']}/duplicate").json()
        assert copy["items"][0]["overlay"]["placement"] == "bottom"

        assert client.post("/api/playback/start", json={"playlist_id": playlist["id"]}).status_code == 200
        top, bottom = band("bottom", (96, 64))[1], 64
        assert wait_for(lambda: (pixels(matrix.frame)[top:bottom] == 255).all(axis=-1).any())
        assert not (pixels(matrix.frame)[:top] == 255).all(axis=-1).any()
        client.post("/api/playback/stop")

        item_id = item.json()["id"]
        changed = client.put(
            f"/api/playlists/{playlist['id']}/items/{item_id}",
            json={"item_type": "media", "media_id": media["id"], "overlay": None},
        )
        assert changed.status_code == 200 and changed.json()["overlay"] is None
        assert client.get(f"/api/playlists/{playlist['id']}").json()["items"][0]["overlay"] is None
