import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple, Union

from PIL import Image, ImageSequence

from .fibonacci import DEFAULT_BLEND, fibonacci_fit_for
from .fit import FitMode, contained_size, fit_image
from .text import SCROLL_EFFECTS, TextRenderer, TextStyle


@dataclass(frozen=True)
class Frame:
    image: Image.Image
    duration: float

    def __post_init__(self) -> None:
        if self.duration < 0:
            raise ValueError("frame duration cannot be negative")


class FrameSource:
    def __iter__(self) -> Iterator[Frame]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class ImageFrameSource(FrameSource):
    def __init__(
        self,
        source: Union[str, Path, Image.Image],
        size: Tuple[int, int],
        duration: float = 1.0,
        fit_mode: Union[FitMode, str] = FitMode.LETTERBOX,
        fit_blend: str = DEFAULT_BLEND,
    ) -> None:
        self.source = source
        self.size = size
        self.duration = duration
        self.fit_mode = fit_mode
        self.fit_blend = fit_blend

    def __iter__(self) -> Iterator[Frame]:
        if isinstance(self.source, Image.Image):
            image = self.source.copy()
        else:
            with Image.open(self.source) as opened:
                image = opened.copy()
        yield Frame(fit_image(image, self.size, self.fit_mode, blend=self.fit_blend), self.duration)


class GifFrameSource(FrameSource):
    """Animated GIF frames, fitted once and cached so loops do not re-decode."""

    MAX_CACHED_FRAMES = 600

    def __init__(
        self,
        path: Union[str, Path],
        size: Tuple[int, int],
        fit_mode: Union[FitMode, str] = FitMode.LETTERBOX,
        max_frames: Optional[int] = None,
        default_duration: float = 0.1,
        fit_blend: str = DEFAULT_BLEND,
    ) -> None:
        if max_frames is not None and max_frames <= 0:
            raise ValueError("max_frames must be positive")
        self.path = Path(path)
        self.size = size
        self.fit_mode = fit_mode
        self.fit_blend = fit_blend
        self.max_frames = max_frames
        self.default_duration = default_duration
        self._cache: Optional[List[Frame]] = None

    def __iter__(self) -> Iterator[Frame]:
        if self._cache is not None:
            yield from self._cache
            return
        decoded: Optional[List[Frame]] = []
        with Image.open(self.path) as gif:
            for index, image in enumerate(ImageSequence.Iterator(gif)):
                if self.max_frames is not None and index >= self.max_frames:
                    break
                milliseconds = image.info.get("duration", gif.info.get("duration", 0))
                # Browsers treat sub-20ms GIF delays as 100ms; match them.
                duration = milliseconds / 1000 if milliseconds and milliseconds >= 20 else self.default_duration
                frame = Frame(fit_image(image.convert("RGBA"), self.size, self.fit_mode, blend=self.fit_blend), duration)
                if decoded is not None:
                    decoded.append(frame)
                    if len(decoded) > self.MAX_CACHED_FRAMES:
                        decoded = None
                yield frame
        if decoded:
            self._cache = decoded

    def close(self) -> None:
        self._cache = None


def render_text(
    text: str,
    size: Tuple[int, int],
    style: Optional[TextStyle] = None,
    background: Optional[Image.Image] = None,
    t: float = 0.0,
) -> Image.Image:
    """Renders a single text-panel frame at time t."""
    return TextRenderer(text, size, style, background).frame(t)


class TextFrameSource(FrameSource):
    """Text panel frames; animated styles yield frames until the caller stops."""

    def __init__(
        self,
        text: str,
        size: Tuple[int, int],
        duration: float = 1.0,
        style: Optional[TextStyle] = None,
        background: Optional[Image.Image] = None,
    ) -> None:
        self.text = text
        self.size = size
        self.duration = duration
        self.style = style or TextStyle()
        self.background = background
        self._renderer: Optional[TextRenderer] = None

    @property
    def renderer(self) -> TextRenderer:
        if self._renderer is None:
            self._renderer = TextRenderer(self.text, self.size, self.style, self.background)
        return self._renderer

    @property
    def cycle_seconds(self) -> float:
        return self.renderer.cycle_seconds

    @property
    def min_seconds(self) -> float:
        """Scrolling text needs one full pass to be readable."""
        return self.renderer.cycle_seconds if self.style.effect in SCROLL_EFFECTS else 0.0

    def __iter__(self) -> Iterator[Frame]:
        for image, duration in self.renderer.frames(None if self.renderer.animated else self.duration):
            yield Frame(image, duration)


class VideoFrameSource(FrameSource):
    """Stream raw frames from ffmpeg; at most one decoded frame is held in memory."""

    def __init__(
        self,
        path: Union[str, Path],
        size: Tuple[int, int],
        fps: Optional[float] = None,
        max_frames: Optional[int] = None,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        fit_mode: Union[FitMode, str] = FitMode.FIT,
        fit_blend: str = DEFAULT_BLEND,
    ) -> None:
        if fps is not None and fps <= 0:
            raise ValueError("fps must be positive")
        if max_frames is not None and max_frames <= 0:
            raise ValueError("max_frames must be positive")
        self.path = Path(path)
        self.size = size
        self.requested_fps = fps
        self.max_frames = max_frames
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.fit_mode = FitMode(fit_mode)
        self.fit_blend = fit_blend
        self._fps: Optional[float] = None
        self._process: Optional["subprocess.Popen[bytes]"] = None

    @staticmethod
    def available(executable: str = "ffmpeg") -> bool:
        return shutil.which(executable) is not None

    def _detected_fps(self) -> float:
        if self.requested_fps is not None:
            return self.requested_fps
        if self._fps is None:
            self._fps = self._probe_fps()
        return self._fps

    def _probe_fps(self) -> float:
        if shutil.which(self.ffprobe):
            command = [
                self.ffprobe, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=avg_frame_rate", "-of", "json", str(self.path),
            ]
            try:
                result = subprocess.run(
                    command, check=True, capture_output=True, text=True, timeout=10
                )
                rate = json.loads(result.stdout)["streams"][0]["avg_frame_rate"]
                numerator, denominator = rate.split("/")
                if float(denominator):
                    return float(numerator) / float(denominator)
            except (OSError, subprocess.SubprocessError, KeyError, IndexError, ValueError, json.JSONDecodeError):
                pass
        return 30.0

    def _probe_size(self) -> Optional[Tuple[int, int]]:
        """The picture's displayed size, or None if it cannot be read."""
        if not shutil.which(self.ffprobe):
            return None
        command = [
            self.ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:stream_side_data=rotation",
            "-of", "json", str(self.path),
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
            stream = json.loads(result.stdout)["streams"][0]
            width, height = int(stream["width"]), int(stream["height"])
            rotation = next((abs(int(d["rotation"])) for d in stream.get("side_data_list", []) if "rotation" in d), 0)
        except (OSError, subprocess.SubprocessError, KeyError, IndexError, ValueError, json.JSONDecodeError):
            return None
        if rotation % 180 == 90:
            width, height = height, width
        return (width, height) if width > 0 and height > 0 else None

    def __iter__(self) -> Iterator[Frame]:
        executable = shutil.which(self.ffmpeg)
        if executable is None:
            raise RuntimeError("ffmpeg is required for video playback")
        fps = self._detected_fps()
        width, height = self.size
        fibonacci = None
        if self.fit_mode is FitMode.FIBONACCI:
            probed = self._probe_size()
            if probed is not None:
                # ffmpeg only scales; the edge strips are drawn here, per frame.
                width, height = contained_size(probed, self.size)
                fibonacci = fibonacci_fit_for((width, height), self.size)
        command = [
            executable, "-v", "error", "-i", str(self.path), "-an",
            "-vf", self._filter(width, height, fibonacci is not None),
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ]
        self._process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        frame_bytes = width * height * 3
        try:
            assert self._process.stdout is not None
            index = 0
            while self.max_frames is None or index < self.max_frames:
                data = self._process.stdout.read(frame_bytes)
                if len(data) != frame_bytes:
                    break
                frame = Image.frombytes("RGB", (width, height), data)
                if fibonacci is not None:
                    frame = fibonacci.apply(frame, self.fit_blend)
                yield Frame(frame, 1.0 / fps)
                index += 1
        finally:
            self.close()

    def _filter(self, width: int, height: int, exact: bool = False) -> str:
        if exact or self.fit_mode is FitMode.STRETCH:
            return "scale={}:{}".format(width, height)
        if self.fit_mode is FitMode.CROP:
            return "scale={0}:{1}:force_original_aspect_ratio=increase,crop={0}:{1}".format(width, height)
        return (
            "scale={0}:{1}:force_original_aspect_ratio=decrease,"
            "pad={0}:{1}:(ow-iw)/2:(oh-ih)/2:black".format(width, height)
        )

    def close(self) -> None:
        # ffmpeg blocked on a full pipe ignores SIGTERM, so stop it outright.
        process, self._process = self._process, None
        if process is None:
            return
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            process.kill()
        process.wait()
