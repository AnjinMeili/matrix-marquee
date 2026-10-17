import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple, Union

from PIL import Image, ImageDraw, ImageFont, ImageSequence

from .fit import FitMode, fit_image


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
    ) -> None:
        self.source = source
        self.size = size
        self.duration = duration
        self.fit_mode = fit_mode

    def __iter__(self) -> Iterator[Frame]:
        if isinstance(self.source, Image.Image):
            image = self.source.copy()
        else:
            with Image.open(self.source) as opened:
                image = opened.copy()
        yield Frame(fit_image(image, self.size, self.fit_mode), self.duration)


class GifFrameSource(FrameSource):
    def __init__(
        self,
        path: Union[str, Path],
        size: Tuple[int, int],
        fit_mode: Union[FitMode, str] = FitMode.LETTERBOX,
        max_frames: Optional[int] = None,
        default_duration: float = 0.1,
    ) -> None:
        if max_frames is not None and max_frames <= 0:
            raise ValueError("max_frames must be positive")
        self.path = Path(path)
        self.size = size
        self.fit_mode = fit_mode
        self.max_frames = max_frames
        self.default_duration = default_duration

    def __iter__(self) -> Iterator[Frame]:
        with Image.open(self.path) as gif:
            for index, image in enumerate(ImageSequence.Iterator(gif)):
                if self.max_frames is not None and index >= self.max_frames:
                    break
                milliseconds = image.info.get("duration", gif.info.get("duration", 0))
                duration = milliseconds / 1000 if milliseconds else self.default_duration
                yield Frame(fit_image(image.convert("RGBA"), self.size, self.fit_mode), duration)


def _load_font(font: Optional[Union[str, Path]], size: int) -> ImageFont.ImageFont:
    if font:
        return ImageFont.truetype(str(font), size)
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _wrapped_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> List[str]:
    result: List[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            result.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = "{} {}".format(line, word)
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                line = candidate
            else:
                result.append(line)
                line = word
        result.append(line)
    return result


def render_text(
    text: str,
    size: Tuple[int, int],
    font: Optional[Union[str, Path]] = None,
    font_size: int = 12,
    color: Union[str, Tuple[int, int, int]] = "white",
    background: Union[str, Tuple[int, int, int]] = "black",
    align: str = "center",
    padding: int = 1,
    border_color: Optional[Union[str, Tuple[int, int, int]]] = None,
    border_width: int = 0,
) -> Image.Image:
    if align not in {"left", "center", "right"}:
        raise ValueError("align must be left, center, or right")
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    selected_font = _load_font(font, font_size)
    inset = padding + max(0, border_width)
    lines = _wrapped_lines(draw, text, selected_font, max(1, size[0] - inset * 2))
    spacing = max(1, font_size // 5)
    boxes = [draw.textbbox((0, 0), line or " ", font=selected_font) for line in lines]
    heights = [box[3] - box[1] for box in boxes]
    total_height = sum(heights) + spacing * max(0, len(lines) - 1)
    y = max(inset, (size[1] - total_height) // 2)
    for line, box, line_height in zip(lines, boxes, heights):
        line_width = box[2] - box[0]
        if align == "left":
            x = inset
        elif align == "right":
            x = size[0] - inset - line_width
        else:
            x = (size[0] - line_width) // 2
        draw.text((x, y), line, font=selected_font, fill=color)
        y += line_height + spacing
    if border_color is not None and border_width > 0:
        for offset in range(border_width):
            draw.rectangle(
                (offset, offset, size[0] - 1 - offset, size[1] - 1 - offset),
                outline=border_color,
            )
    return image


class TextFrameSource(FrameSource):
    def __init__(self, text: str, size: Tuple[int, int], duration: float = 1.0, **style: object) -> None:
        self.text = text
        self.size = size
        self.duration = duration
        self.style = style

    def __iter__(self) -> Iterator[Frame]:
        yield Frame(render_text(self.text, self.size, **self.style), self.duration)


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
        self._process: Optional[subprocess.Popen[bytes]] = None

    @staticmethod
    def available(executable: str = "ffmpeg") -> bool:
        return shutil.which(executable) is not None

    def _detected_fps(self) -> float:
        if self.requested_fps is not None:
            return self.requested_fps
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

    def __iter__(self) -> Iterator[Frame]:
        executable = shutil.which(self.ffmpeg)
        if executable is None:
            raise RuntimeError("ffmpeg is required for video playback")
        fps = self._detected_fps()
        width, height = self.size
        command = [
            executable, "-v", "error", "-i", str(self.path), "-an",
            "-vf", "scale={}:{}:force_original_aspect_ratio=decrease,"
            "pad={}:{}:(ow-iw)/2:(oh-ih)/2:black".format(width, height, width, height),
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
                yield Frame(Image.frombytes("RGB", self.size, data), 1.0 / fps)
                index += 1
        finally:
            self.close()

    def close(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
