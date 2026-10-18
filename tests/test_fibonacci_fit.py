"""Fibonastretch: the Fibonacci edge fill for pictures that do not match the sign."""

from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image
import pytest

from matrix_player import models
from matrix_player.database import Database
from matrix_player.rendering import FitMode, VideoFrameSource, blend_names, contained_size, fit_image
from matrix_player.rendering.fibonacci import BLENDS, FibonacciFit, fibonacci_widths


def gradient(size) -> Image.Image:
    width, height = size
    x = np.linspace(20, 235, width, dtype=np.uint8)[None, :, None]
    y = np.linspace(20, 235, height, dtype=np.uint8)[:, None, None]
    return Image.fromarray(np.broadcast_to(np.stack([x[..., 0] + 0 * y[..., 0], 0 * x[..., 0] + y[..., 0], x[..., 0] // 2 + 0 * y[..., 0]], axis=2), (height, width, 3)).astype(np.uint8))


def test_the_spec_example_640x480_on_1024x1024() -> None:
    assert contained_size((640, 480), (1024, 1024)) == (1024, 768)
    widths = fibonacci_widths(128, 10_000)
    assert widths == [55, 34, 21, 13, 8, 5, 3, 2, 1, 1]
    assert sum(widths) == 143 and sum(widths) - 128 == 15
    assert sum(fibonacci_widths(88, 100)) == 88  # up to 34 sums to 88


@pytest.mark.parametrize("gap", [1, 2, 3, 7, 50, 129, 1000])
def test_widths_cover_the_gap_and_end_at_one(gap) -> None:
    widths = fibonacci_widths(gap, 10_000)
    assert sum(widths) >= gap
    assert widths[-1] == 1
    assert widths[0] == max(widths)


def test_thin_sources_take_the_shortfall_in_the_outer_line() -> None:
    widths = fibonacci_widths(100, 3)
    assert len(widths) == 3 and sum(widths) == 100


@pytest.mark.parametrize("size", [(64, 32), (96, 64), (192, 64), (128, 128), (384, 128), (257, 100)])
@pytest.mark.parametrize("source", [(640, 480), (480, 640), (300, 300), (1000, 100), (7, 500)])
def test_output_is_exactly_the_sign_size_on_any_sign(size, source) -> None:
    out = fit_image(gradient(source), size, "fibonacci")
    assert out.size == size
    picture = contained_size(source, size)
    fitted = fit_image(gradient(source), size, "fit")
    assert picture[0] == size[0] or picture[1] == size[1]
    if picture == size:
        assert np.array_equal(np.asarray(out), np.asarray(fitted))


def test_outer_strip_repeats_the_picture_edge_and_far_side_mirrors() -> None:
    picture = gradient((100, 40))
    fit = FibonacciFit(picture.size, (140, 40))
    out = np.asarray(fit.apply(picture, "normal"))
    array = np.asarray(picture)
    assert out.shape == (40, 140, 3)
    assert np.array_equal(out[:, 0], array[:, 0])
    assert np.array_equal(out[:, -1], array[:, -1])
    # Outermost source line is the widest strip step.
    assert np.array_equal(out[:, 0], out[:, 5])


def test_vertical_gap_works_like_horizontal() -> None:
    picture = gradient((40, 100))
    horizontal = FibonacciFit((100, 40), (140, 40)).apply(picture.transpose(Image.Transpose.TRANSPOSE), "screen")
    vertical = FibonacciFit((40, 100), (40, 140)).apply(picture, "screen")
    assert np.array_equal(np.asarray(vertical), np.asarray(horizontal).transpose(1, 0, 2))


def test_odd_gap_gives_the_extra_pixel_to_the_far_side() -> None:
    fit = FibonacciFit((10, 10), (21, 10))
    assert (fit.near, fit.far) == (5, 6)


def test_no_gap_leaves_the_picture_alone() -> None:
    picture = gradient((96, 64))
    assert np.array_equal(np.asarray(fit_image(picture, (96, 64), "fibonacci")), np.asarray(picture))


def test_every_blend_stays_in_range_and_unknown_means_screen() -> None:
    picture = gradient((60, 40))
    fit = FibonacciFit(picture.size, (110, 40))  # 25 px a side: strips of 33 overlap by 8
    results = {name: np.asarray(fit.apply(picture, name)) for name in blend_names()}
    assert {"screen", "additive", "multiply", "subtractive", "overlay"} <= set(results)
    assert np.array_equal(np.asarray(fit.apply(picture, "nonsense")), results["screen"])
    assert not np.array_equal(results["screen"], results["multiply"])


def test_blend_formulas_on_a_worked_pixel() -> None:
    b, s = np.float32(0.5), np.float32(0.5)
    assert BLENDS["screen"](b, s) == pytest.approx(0.75)
    assert BLENDS["additive"](b, s) == pytest.approx(1.0)
    assert BLENDS["multiply"](b, s) == pytest.approx(0.25)
    assert BLENDS["subtractive"](b, s) == pytest.approx(0.0)
    assert BLENDS["overlay"](np.float32(0.25), s) == pytest.approx(0.25)


def test_the_two_fit_mode_lists_agree() -> None:
    assert {m.value for m in models.FitMode} == {m.value for m in FitMode}


def test_older_database_gains_the_blend_column(tmp_path: Path) -> None:
    database = Database(tmp_path / "old.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute("ALTER TABLE playlist_items DROP COLUMN fit_blend")
    assert "fit_blend" not in [r[1] for r in database.fetch_all("PRAGMA table_info(playlist_items)")]
    database.initialize()
    assert "fit_blend" in [r[1] for r in database.fetch_all("PRAGMA table_info(playlist_items)")]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_in_the_wrong_shape_fills_the_bars(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
         "-t", "1", "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    frames = list(VideoFrameSource(video, (192, 64), fit_mode="fibonacci"))
    assert frames and all(f.image.size == (192, 64) for f in frames)
    plain = list(VideoFrameSource(video, (192, 64), fit_mode="fit"))
    assert np.asarray(plain[0].image)[:, 0].max() == 0  # letterbox bars are black
    assert np.asarray(frames[0].image)[:, 0].max() > 0  # the stretch lights them


def test_video_whose_size_cannot_be_read_falls_back_to_fit(tmp_path: Path) -> None:
    source = VideoFrameSource(tmp_path / "x.mp4", (96, 64), fit_mode="fibonacci", ffprobe="no-such-ffprobe")
    assert source._probe_size() is None


def test_blend_choice_round_trips_through_the_api(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from matrix_player.app import create_app
    from matrix_player.matrix import MockMatrixAdapter
    from test_signage import make_settings

    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        panel = client.post("/api/text-panels", json={"name": "P", "content": "HI"}).json()
        playlist = client.post("/api/playlists", json={"name": "L"}).json()
        url = f"/api/playlists/{playlist['id']}/items"
        body = {"item_type": "text_panel", "text_panel_id": panel["id"], "fit_mode": "fibonacci", "fit_blend": "overlay"}
        item = client.post(url, json=body).json()
        assert (item["fit_mode"], item["fit_blend"]) == ("fibonacci", "overlay")
        assert client.post(url, json={**body, "fit_blend": "bogus"}).status_code == 422
        assert client.post(url, json={k: v for k, v in body.items() if k != "fit_blend"}).json()["fit_blend"] == "screen"
        duplicate = client.post(f"/api/playlists/{playlist['id']}/duplicate").json()
        assert duplicate["items"][0]["fit_blend"] == "overlay"
