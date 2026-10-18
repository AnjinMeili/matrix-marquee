"""Fibonastretch: the Fibonacci edge fill for pictures that do not match the sign."""

from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image
import pytest

from matrix_player import models
from matrix_player.database import Database
from matrix_player.rendering import FitMode, VideoFrameSource, blur_names, contained_size, fit_image
from matrix_player.rendering.fibonacci import BLURS, FibonacciFit, fibonacci_widths


def gradient(size) -> Image.Image:
    width, height = size
    x = np.linspace(20, 235, width, dtype=np.uint8)[None, :, None]
    y = np.linspace(20, 235, height, dtype=np.uint8)[:, None, None]
    return Image.fromarray(np.broadcast_to(np.stack([x[..., 0] + 0 * y[..., 0], 0 * x[..., 0] + y[..., 0], x[..., 0] // 2 + 0 * y[..., 0]], axis=2), (height, width, 3)).astype(np.uint8))


def test_the_spec_example_640x480_on_1024x1024() -> None:
    assert contained_size((640, 480), (1024, 1024)) == (1024, 768)
    widths = fibonacci_widths(128, 10_000)
    # 1, 1, 2, 3, 5, 8, 13, 21, 34 sum to 88; the next term, 55, is cut to the 40 left.
    assert widths == [1, 1, 2, 3, 5, 8, 13, 21, 34, 40]
    assert sum(widths) == 128


@pytest.mark.parametrize("gap", [1, 2, 3, 7, 50, 129, 1000])
def test_widths_fill_the_gap_exactly_and_grow_outward(gap) -> None:
    widths = fibonacci_widths(gap, 10_000)
    assert sum(widths) == gap
    assert widths[0] == 1
    assert widths[:-1] == sorted(widths[:-1])


def test_thin_sources_take_the_shortfall_in_the_last_line() -> None:
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


def test_strip_continues_the_picture_edge_and_grows_outward() -> None:
    picture = gradient((100, 40))
    fit = FibonacciFit(picture.size, (140, 40))
    out = np.asarray(fit.apply(picture))
    array = np.asarray(picture)
    assert out.shape == (40, 140, 3)
    assert np.array_equal(out[:, 20:120], array)  # the picture is untouched
    # Next to the picture, lines 0 and 1 each take one pixel (no blur at size 1),
    # then line 2 takes two, blurred over two rows.
    assert np.array_equal(out[:, 19], array[:, 0])
    assert np.array_equal(out[:, 18], array[:, 1])
    assert np.array_equal(out[:, 17], out[:, 16])
    assert np.abs(out[:, 17].astype(int) - array[:, 2].astype(int)).max() <= 8
    # The far side mirrors it.
    assert np.array_equal(out[:, 120], array[:, -1])
    assert np.array_equal(out[:, 121], array[:, -2])


def test_vertical_gap_works_like_horizontal() -> None:
    picture = gradient((40, 100))
    horizontal = FibonacciFit((100, 40), (140, 40)).apply(picture.transpose(Image.Transpose.TRANSPOSE))
    vertical = FibonacciFit((40, 100), (40, 140)).apply(picture)
    assert np.array_equal(np.asarray(vertical), np.asarray(horizontal).transpose(1, 0, 2))


def test_odd_gap_gives_the_extra_pixel_to_the_far_side() -> None:
    fit = FibonacciFit((10, 10), (21, 10))
    assert (fit.near, fit.far) == (5, 6)


def test_no_gap_leaves_the_picture_alone() -> None:
    picture = gradient((96, 64))
    assert np.array_equal(np.asarray(fit_image(picture, (96, 64), "fibonacci")), np.asarray(picture))


def test_the_bars_blur_more_toward_the_sign_edge() -> None:
    picture = np.zeros((64, 60, 3), dtype=np.uint8)
    picture[30:34, :] = 255  # a thin bright line across the picture
    out = np.asarray(FibonacciFit((60, 64), (200, 64)).apply(Image.fromarray(picture)))
    spread = [int((out[:, x, 0] > 0).sum()) for x in (69, 60, 40, 20, 0)]  # near the picture ... the edge
    assert spread == sorted(spread) and spread[-1] > spread[0] * 4


def test_crisp_keeps_each_pixel_a_flat_block() -> None:
    picture = gradient((100, 40))
    out = np.asarray(FibonacciFit(picture.size, (140, 40)).apply(picture, "none"))
    array = np.asarray(picture)
    assert np.array_equal(out[:, 17], array[:, 2]) and np.array_equal(out[:, 16], array[:, 2])
    assert np.array_equal(out[:, 0], array[:, 5])  # the outermost step is one source line, unblended


def test_every_blur_keeps_the_picture_and_stays_in_range() -> None:
    picture = gradient((60, 40))
    fit = FibonacciFit(picture.size, (110, 40))
    for name in blur_names():
        out = np.asarray(fit.apply(picture, name))
        assert np.array_equal(out[:, 25:85], np.asarray(picture))
    assert {"average", "lighten", "screen", "additive", "multiply"} <= set(BLURS)
    assert np.array_equal(np.asarray(fit.apply(picture, "nonsense")), np.asarray(fit.apply(picture, "average")))


def test_average_keeps_a_flat_colour_and_screen_washes_it_out() -> None:
    flat = Image.new("RGB", (60, 40), (90, 120, 150))
    fit = FibonacciFit(flat.size, (160, 40))
    average = np.asarray(fit.apply(flat, "average"))
    assert (average == (90, 120, 150)).all()
    assert np.asarray(fit.apply(flat, "screen"))[:, 0].min() > 200


def test_the_two_fit_mode_lists_agree() -> None:
    assert {m.value for m in models.FitMode} == {m.value for m in FitMode}


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


def test_fibonacci_fit_round_trips_through_the_api(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from matrix_player.app import create_app
    from matrix_player.matrix import MockMatrixAdapter
    from test_signage import make_settings

    with TestClient(create_app(make_settings(tmp_path), matrix_adapter=MockMatrixAdapter())) as client:
        panel = client.post("/api/text-panels", json={"name": "P", "content": "HI"}).json()
        playlist = client.post("/api/playlists", json={"name": "L"}).json()
        url = f"/api/playlists/{playlist['id']}/items"
        body = {"item_type": "text_panel", "text_panel_id": panel["id"], "fit_mode": "fibonacci", "fit_blur": "lighten"}
        item = client.post(url, json=body).json()
        assert (item["fit_mode"], item["fit_blur"]) == ("fibonacci", "lighten")
        assert client.post(url, json={**body, "fit_blur": "bogus"}).status_code == 422
        plain = {k: v for k, v in body.items() if k != "fit_blur"}
        assert client.post(url, json=plain).json()["fit_blur"] == "average"
        duplicate = client.post(f"/api/playlists/{playlist['id']}/duplicate").json()
        assert duplicate["items"][0]["fit_blur"] == "lighten"


def test_older_database_gains_the_blur_column(tmp_path: Path) -> None:
    database = Database(tmp_path / "old.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute("ALTER TABLE playlist_items DROP COLUMN fit_blur")
    database.initialize()
    assert "fit_blur" in [r[1] for r in database.fetch_all("PRAGMA table_info(playlist_items)")]


# Fitted frames kept on disk -------------------------------------------------

from matrix_player.rendering import Frame, FrameSource  # noqa: E402
from matrix_player.rendering.fitcache import FitCache  # noqa: E402


class CountingSource(FrameSource):
    runs = 0

    def __init__(self, size=(32, 16), frames=5) -> None:
        self.size, self.frames = size, frames

    def __iter__(self):
        CountingSource.runs += 1
        for index in range(self.frames):
            yield Frame(Image.new("RGB", self.size, (index * 40, 10, 10)), 0.05 + index / 100)


def built(cache: FitCache, key: str, factory, size=(32, 16)) -> bool:
    from test_signage import wait_for

    cache.submit(key, factory, size)
    return wait_for(lambda: cache.get(key, size) is not None, timeout=5)


@pytest.fixture
def cache(tmp_path: Path):
    fit_cache = FitCache(tmp_path / "fitted", max_bytes=10_000_000)
    fit_cache.start()
    yield fit_cache
    fit_cache.close()


def test_frames_are_worked_out_once_and_read_back_exactly(cache: FitCache, tmp_path: Path) -> None:
    media = tmp_path / "clip.gif"
    media.write_bytes(b"x")
    key = cache.key("clip", media, (32, 16), "fibonacci", "average")
    CountingSource.runs = 0
    assert built(cache, key, CountingSource)
    assert CountingSource.runs == 1
    for _ in range(2):  # a cached source can be played again and again
        frames = list(cache.get(key, (32, 16)))
        assert [f.duration for f in frames] == pytest.approx([0.05 + i / 100 for i in range(5)])
        assert [f.image.getpixel((0, 0)) for f in frames] == [(i * 40, 10, 10) for i in range(5)]
    assert CountingSource.runs == 1
    assert cache.submit(key, CountingSource, (32, 16)) is False  # already built


def test_the_key_changes_with_the_file_the_size_and_the_fit(tmp_path: Path) -> None:
    media = tmp_path / "clip.gif"
    media.write_bytes(b"x")
    base = FitCache.key("clip", media, (32, 16), "fibonacci", "average")
    assert base == FitCache.key("clip", media, (32, 16), "fibonacci", "average")
    assert base != FitCache.key("clip", media, (32, 16), "fibonacci", "none")
    assert base != FitCache.key("clip", media, (64, 32), "fibonacci", "average")
    media.write_bytes(b"changed")
    assert base != FitCache.key("clip", media, (32, 16), "fibonacci", "average")


def test_a_clip_too_big_to_keep_plays_live_and_is_not_retried(tmp_path: Path) -> None:
    from test_signage import wait_for

    small = FitCache(tmp_path / "fitted", max_bytes=4 * 32 * 16 * 3 * 2)  # room for two frames per file
    small.start()
    try:
        small.submit("clip-big", CountingSource, (32, 16))
        assert wait_for(lambda: small.is_skipped("clip-big"), timeout=5)
        assert small.get("clip-big", (32, 16)) is None
        assert not list((tmp_path / "fitted").glob("*.rgb*"))
        assert small.submit("clip-big", CountingSource, (32, 16)) is False
    finally:
        small.close()


def test_a_wrong_sized_or_truncated_file_is_ignored(cache: FitCache, tmp_path: Path) -> None:
    media = tmp_path / "clip.gif"
    media.write_bytes(b"x")
    key = cache.key("clip", media, (32, 16), "fibonacci", "average")
    assert built(cache, key, CountingSource)
    assert cache.get(key, (64, 32)) is None
    raw = cache.directory / f"{key}.rgb"
    raw.write_bytes(raw.read_bytes()[:-5])
    assert cache.get(key, (32, 16)) is None


def test_the_oldest_files_are_pruned_past_the_limit_and_deleting_media_removes_its_files(tmp_path: Path) -> None:
    import os

    frame = 32 * 16 * 3
    pruned = FitCache(tmp_path / "fitted", max_bytes=frame * 20)  # a file may use a quarter: 5 frames
    pruned.start()
    try:
        for index, name in enumerate(["a-1", "b-1", "c-1", "d-1", "e-1"]):
            assert built(pruned, name, CountingSource)
            os.utime(pruned.directory / f"{name}.json", (1000 + index, 1000 + index))
        pruned.prune(keep="e-1")
        assert pruned.get("a-1", (32, 16)) is None and pruned.get("e-1", (32, 16)) is not None
        pruned.remove_media("e")
        assert pruned.get("e-1", (32, 16)) is None
    finally:
        pruned.close()


def test_a_fibonacci_gif_plays_live_first_then_from_the_cache(tmp_path: Path) -> None:
    from matrix_player.playback import build_media_source
    from matrix_player.rendering import GifFrameSource
    from matrix_player.rendering.fitcache import CachedFrameSource
    from matrix_player.storage import MediaStorage
    from test_signage import make_settings, wait_for

    settings = make_settings(tmp_path)
    settings.ensure_directories()
    storage = MediaStorage(settings)
    storage.fit_cache.start()
    try:
        frames = [Image.new("RGB", (40, 30), (i * 60, 50, 90)) for i in range(4)]
        frames[0].save(settings.media_dir / "anim.gif", save_all=True, append_images=frames[1:], duration=80, loop=0)
        size = (96, 64)
        first = build_media_source(storage, "anim.gif", size, 5.0, "fibonacci", "gif", "average")
        assert isinstance(first, GifFrameSource)  # nothing waits for the cache
        live = [np.asarray(f.image) for f in first]
        assert wait_for(lambda: isinstance(
            build_media_source(storage, "anim.gif", size, 5.0, "fibonacci", "gif", "average"), CachedFrameSource
        ), timeout=5)
        cached = build_media_source(storage, "anim.gif", size, 5.0, "fibonacci", "gif", "average")
        assert all(np.array_equal(a, np.asarray(b.image)) for a, b in zip(live, cached)) and len(live) == 4
        # Other fits and other blurs are not served from it.
        assert isinstance(build_media_source(storage, "anim.gif", size, 5.0, "fit", "gif"), GifFrameSource)
        assert isinstance(build_media_source(storage, "anim.gif", size, 5.0, "fibonacci", "gif", "none"), GifFrameSource)
        storage.delete("anim.gif", None)
        assert not list(settings.fit_cache_path.glob("anim-*"))
    finally:
        storage.fit_cache.close()
