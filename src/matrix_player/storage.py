"""Validated media filesystem storage."""

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Optional, Tuple

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from PIL import Image, ImageSequence, UnidentifiedImageError

from .config import Settings
from .models import MediaType


ALLOWED_MIME_TYPES = {
    "image/jpeg": (MediaType.IMAGE, ".jpg"),
    "image/png": (MediaType.IMAGE, ".png"),
    "image/webp": (MediaType.IMAGE, ".webp"),
    "image/gif": (MediaType.GIF, ".gif"),
    "video/mp4": (MediaType.VIDEO, ".mp4"),
    "video/webm": (MediaType.VIDEO, ".webm"),
    "video/quicktime": (MediaType.VIDEO, ".mov"),
}
# Some browsers send a generic type; fall back to the file extension.
EXTENSION_MIME_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime",
}
MIN_FREE_BYTES = 200 * 1024 * 1024
# Phone video is far too large to decode live on a Pi; playback uses a small
# upright H.264 copy instead. 320px keeps headroom for crop and fit modes.
PROXY_SUFFIX = ".play.mp4"
PROXY_MAX_EDGE = 320
PROXY_MAX_FPS = 30
logger = logging.getLogger(__name__)


class StorageFullError(ValueError):
    pass


def gif_animation(image: Image.Image) -> Optional[Tuple[int, float]]:
    """(loop length in ms, average frames per second) for an animated GIF, or None
    when it has a single frame and is really a still picture.

    Delays under 20 ms count as 100 ms, matching browsers and the player."""
    if not getattr(image, "is_animated", False):
        return None
    total = 0
    frames = 0
    for frame in ImageSequence.Iterator(image):
        delay = int(frame.info.get("duration", 0) or 0)
        total += delay if delay >= 20 else 100
        frames += 1
    return total, (frames * 1000.0 / total if total else None)


@dataclass(frozen=True)
class StoredMedia:
    filename: str
    display_name: str
    media_type: MediaType
    mime_type: str
    width: Optional[int]
    height: Optional[int]
    duration_ms: Optional[int]
    frame_rate: Optional[float]
    file_size_bytes: int
    thumbnail_path: Optional[str]


class MediaStorage:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def safe_display_name(filename: Optional[str]) -> str:
        name = Path(filename or "upload").name.replace("\x00", "")
        name = re.sub(r"[\r\n\t]", " ", name).strip()
        return name[:255] or "upload"

    async def save(self, upload: UploadFile) -> StoredMedia:
        mime = (upload.content_type or "").lower()
        if mime not in ALLOWED_MIME_TYPES:
            mime = EXTENSION_MIME_TYPES.get(Path(upload.filename or "").suffix.lower(), mime)
        if mime not in ALLOWED_MIME_TYPES:
            raise ValueError("unsupported media type")
        if shutil.disk_usage(self.settings.media_dir).free < MIN_FREE_BYTES:
            raise StorageFullError("not enough free storage on the device")
        media_type, extension = ALLOWED_MIME_TYPES[mime]
        display_name = self.safe_display_name(upload.filename)
        digest = hashlib.sha256()
        staging = self.settings.media_dir / f".upload-{id(upload)}"
        size = 0
        try:
            with staging.open("wb") as output:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        raise ValueError("upload exceeds configured size limit")
                    if size % (64 * 1024 * 1024) < len(chunk) and (
                        shutil.disk_usage(self.settings.media_dir).free < MIN_FREE_BYTES
                    ):
                        raise StorageFullError("not enough free storage on the device")
                    digest.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise ValueError("empty upload")
            filename = f"{digest.hexdigest()}{extension}"
            destination = self.settings.media_dir / filename
            staging.replace(destination)
            media_type, width, height, duration_ms, frame_rate, thumbnail = await run_in_threadpool(
                self._inspect, destination, media_type, digest.hexdigest()
            )
            return StoredMedia(
                filename, display_name, media_type, mime, width, height,
                duration_ms, frame_rate, size, thumbnail,
            )
        finally:
            await upload.close()
            staging.unlink(missing_ok=True)

    def _inspect(
        self, path: Path, media_type: MediaType, digest: str
    ) -> Tuple[MediaType, Optional[int], Optional[int], Optional[int], Optional[float], Optional[str]]:
        """Returns the real media type (a one-frame GIF is a still picture) and metadata."""
        if media_type == MediaType.VIDEO:
            return (media_type,) + self._inspect_video(path, digest)
        try:
            with Image.open(path) as image:
                width, height = image.size
                duration = None
                frame_rate = None
                if media_type == MediaType.GIF:
                    animation = gif_animation(image)
                    if animation is None:
                        media_type = MediaType.IMAGE
                    else:
                        duration, frame_rate = animation
                image.seek(0)
                image.thumbnail((256, 256))
                thumb_name = f"{digest}.png"
                image.convert("RGB").save(self.settings.thumbnail_dir / thumb_name, "PNG")
                return media_type, width, height, duration, frame_rate, thumb_name
        except (UnidentifiedImageError, OSError) as error:
            path.unlink(missing_ok=True)
            raise ValueError("uploaded file is not a valid image") from error

    def _inspect_video(
        self, path: Path, digest: str
    ) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[float], Optional[str]]:
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            logger.warning("video_probe_unavailable")
            return None, None, None, None, None
        command = [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
            "-of", "json", str(path),
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
            probe = json.loads(result.stdout)
            stream = probe["streams"][0]
            width, height = int(stream["width"]), int(stream["height"])
        except (OSError, subprocess.SubprocessError, KeyError, IndexError, ValueError):
            path.unlink(missing_ok=True)
            raise ValueError("uploaded file is not a playable video")
        duration_ms = None
        try:
            duration_ms = round(float(probe["format"]["duration"]) * 1000)
        except (KeyError, ValueError, TypeError):
            pass
        frame_rate = None
        try:
            numerator, denominator = stream["avg_frame_rate"].split("/")
            frame_rate = float(numerator) / float(denominator) if float(denominator) else None
        except (KeyError, ValueError):
            pass
        thumbnail = self.video_thumbnail(path, digest, duration_ms)
        return width, height, duration_ms, frame_rate, thumbnail

    def video_thumbnail(self, path: Path, digest: str, duration_ms: Optional[int] = None) -> Optional[str]:
        """Grabs a frame near the start of a video; None when ffmpeg cannot."""
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return None
        offset = min(1.0, (duration_ms or 0) / 2000)
        thumb_name = f"{digest}.png"
        command = [
            ffmpeg, "-v", "error", "-y", "-ss", f"{offset:.2f}", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=256:-2", str(self.settings.thumbnail_dir / thumb_name),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return thumb_name if (self.settings.thumbnail_dir / thumb_name).is_file() else None

    def proxy_path(self, filename: str) -> Path:
        return self.settings.media_dir / (Path(filename).stem + PROXY_SUFFIX)

    def playable_path(self, filename: str) -> Path:
        """The lightweight playback copy when it exists, else the original."""
        proxy = self.proxy_path(filename)
        return proxy if proxy.is_file() else self.media_path(filename)

    def build_proxy(self, filename: str) -> bool:
        """Transcodes a video into its playback copy; True when one is available."""
        target = self.proxy_path(filename)
        source = self.media_path(filename)
        if target.is_file():
            return True
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None or not source.is_file():
            return False
        partial = target.with_name(target.name + ".part")
        command = [
            ffmpeg, "-v", "error", "-y", "-i", str(source),
            "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", (
                f"scale={PROXY_MAX_EDGE}:{PROXY_MAX_EDGE}:"
                "force_original_aspect_ratio=decrease:force_divisible_by=2"
            ),
            "-fpsmax", str(PROXY_MAX_FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-f", "mp4", str(partial),
        ]
        try:
            # Low priority so the matrix refresh and web UI stay responsive.
            subprocess.run(
                command, check=True, capture_output=True, timeout=1800,
                preexec_fn=lambda: os.nice(10),
            )
            os.replace(partial, target)
        except (OSError, subprocess.SubprocessError):
            partial.unlink(missing_ok=True)
            return False
        return True

    def media_path(self, filename: str) -> Path:
        return self.settings.media_dir / Path(filename).name

    def thumbnail_path(self, filename: str) -> Path:
        return self.settings.thumbnail_dir / Path(filename).name

    def delete(self, filename: str, thumbnail: Optional[str]) -> None:
        self.media_path(filename).unlink(missing_ok=True)
        self.proxy_path(filename).unlink(missing_ok=True)
        if thumbnail:
            self.thumbnail_path(thumbnail).unlink(missing_ok=True)
