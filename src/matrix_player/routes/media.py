"""Media library endpoints."""

import logging
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from ..database import Database
from ..models import MediaType
from ..schemas import Media
from ..storage import MediaStorage
from .dependencies import get_database, get_storage


router = APIRouter(prefix="/api/media", tags=["media"])
logger = logging.getLogger(__name__)


@router.get("", response_model=List[Media])
def list_media(
    media_type: Optional[MediaType] = Query(default=None, alias="type"),
    query: Optional[str] = Query(default=None, max_length=200),
    database: Database = Depends(get_database),
) -> List[Media]:
    clauses = []
    parameters = []
    if media_type:
        clauses.append("media_type = ?")
        parameters.append(media_type.value)
    if query:
        clauses.append("display_name LIKE ? ESCAPE '\\'")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        parameters.append(f"%{escaped}%")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = database.fetch_all(f"SELECT * FROM media{where} ORDER BY created_at DESC, id DESC", parameters)
    return [Media.model_validate(dict(row)) for row in rows]


@router.post("", response_model=Media, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    database: Database = Depends(get_database),
    storage: MediaStorage = Depends(get_storage),
) -> Media:
    try:
        stored = await storage.save(file)
    except ValueError as error:
        logger.warning("media_upload_rejected", extra={"reason": str(error)})
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        with database.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO media
                   (filename, display_name, media_type, mime_type, width, height,
                    duration_ms, frame_rate, file_size_bytes, thumbnail_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    stored.filename, stored.display_name, stored.media_type.value,
                    stored.mime_type, stored.width, stored.height, stored.duration_ms,
                    stored.frame_rate, stored.file_size_bytes, stored.thumbnail_path,
                ),
            )
            row = connection.execute("SELECT * FROM media WHERE id = ?", (cursor.lastrowid,)).fetchone()
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="media already exists") from error
    logger.info("media_uploaded", extra={"media_id": row["id"], "size_bytes": stored.file_size_bytes})
    return Media.model_validate(dict(row))


def _get_media(media_id: int, database: Database) -> Media:
    row = database.fetch_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="media not found")
    return Media.model_validate(dict(row))


@router.get("/{media_id}", response_model=Media)
def get_media(media_id: int, database: Database = Depends(get_database)) -> Media:
    return _get_media(media_id, database)


@router.get("/{media_id}/content", response_class=FileResponse)
def get_media_content(
    media_id: int,
    database: Database = Depends(get_database),
    storage: MediaStorage = Depends(get_storage),
) -> FileResponse:
    media = _get_media(media_id, database)
    path = storage.media_path(media.filename)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="media file is missing")
    return FileResponse(path, media_type=media.mime_type, filename=media.display_name)


@router.get("/{media_id}/thumbnail", response_class=FileResponse)
def get_thumbnail(
    media_id: int,
    database: Database = Depends(get_database),
    storage: MediaStorage = Depends(get_storage),
) -> FileResponse:
    media = _get_media(media_id, database)
    if not media.thumbnail_path:
        raise HTTPException(status_code=404, detail="thumbnail unavailable")
    path = storage.thumbnail_path(media.thumbnail_path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="thumbnail file is missing")
    return FileResponse(path, media_type="image/png")


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_media(
    media_id: int,
    database: Database = Depends(get_database),
    storage: MediaStorage = Depends(get_storage),
) -> None:
    media = _get_media(media_id, database)
    try:
        with database.transaction() as connection:
            connection.execute("DELETE FROM media WHERE id = ?", (media_id,))
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="media is used by a playlist") from error
    storage.delete(media.filename, media.thumbnail_path)
    logger.info("media_deleted", extra={"media_id": media_id})

