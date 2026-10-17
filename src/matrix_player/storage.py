"""Validated media filesystem storage."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import BinaryIO, Optional, Tuple

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

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
            raise ValueError("unsupported media type")
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
                    digest.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise ValueError("empty upload")
            filename = f"{digest.hexdigest()}{extension}"
            destination = self.settings.media_dir / filename
            staging.replace(destination)
            width, height, duration_ms, frame_rate, thumbnail = self._inspect(
                destination, media_type, digest.hexdigest()
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
    ) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[float], Optional[str]]:
        if media_type == MediaType.VIDEO:
            return None, None, None, None, None
        try:
            with Image.open(path) as image:
                width, height = image.size
                duration = None
                frame_rate = None
                if media_type == MediaType.GIF:
                    frames = getattr(image, "n_frames", 1)
                    frame_duration = int(image.info.get("duration", 100))
                    duration = frames * frame_duration
                    frame_rate = 1000.0 / frame_duration if frame_duration else None
                image.seek(0)
                image.thumbnail((256, 256))
                thumb_name = f"{digest}.png"
                image.convert("RGB").save(self.settings.thumbnail_dir / thumb_name, "PNG")
                return width, height, duration, frame_rate, thumb_name
        except (UnidentifiedImageError, OSError) as error:
            path.unlink(missing_ok=True)
            raise ValueError("uploaded file is not a valid image") from error

    def media_path(self, filename: str) -> Path:
        return self.settings.media_dir / Path(filename).name

    def thumbnail_path(self, filename: str) -> Path:
        return self.settings.thumbnail_dir / Path(filename).name

    def delete(self, filename: str, thumbnail: Optional[str]) -> None:
        self.media_path(filename).unlink(missing_ok=True)
        if thumbnail:
            self.thumbnail_path(thumbnail).unlink(missing_ok=True)
