"""Media library endpoints."""

import logging
from pathlib import Path
import sqlite3
from typing import Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from ..database import Database
from ..models import MediaType
from ..schemas import MAX_TAGS, Media, MediaBatchTags, MediaUpdate, TagCount, normalize_tag
from ..storage import MediaStorage, StorageFullError
from .dependencies import get_database, get_storage


router = APIRouter(prefix="/api/media", tags=["media"])
logger = logging.getLogger(__name__)


def _tags_for(database: Database, media_ids: Iterable[int]) -> Dict[int, List[str]]:
    ids = list(media_ids)
    tags: Dict[int, List[str]] = {media_id: [] for media_id in ids}
    if not ids:
        return tags
    rows = database.fetch_all(
        f"""SELECT mt.media_id, t.name FROM media_tags mt JOIN tags t ON t.id = mt.tag_id
            WHERE mt.media_id IN ({','.join('?' for _ in ids)}) ORDER BY t.name""",
        ids,
    )
    for row in rows:
        tags[row["media_id"]].append(row["name"])
    return tags


def _with_status(row: object, request: Request, tags: Optional[List[str]] = None) -> Media:
    media = Media.model_validate(dict(row))
    media.tags = tags or []
    if media.media_type == MediaType.VIDEO:
        media.preparing = request.app.state.proxies.is_pending(media.filename)
    return media


TAGGED_WITH = """id IN (SELECT mt.media_id FROM media_tags mt JOIN tags t ON t.id = mt.tag_id
                       WHERE {condition})"""


@router.get("", response_model=List[Media])
def list_media(
    request: Request,
    media_type: Optional[MediaType] = Query(default=None, alias="type"),
    query: Optional[str] = Query(default=None, max_length=200),
    tag: Optional[str] = Query(default=None, max_length=64),
    database: Database = Depends(get_database),
) -> List[Media]:
    clauses = []
    parameters: List[object] = []
    if media_type:
        clauses.append("media_type = ?")
        parameters.append(media_type.value)
    if query:
        # Search matches names and tags, so a tag works as a search word.
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        clauses.append(
            "(display_name LIKE ? ESCAPE '\\' OR "
            + TAGGED_WITH.format(condition="t.name LIKE ? ESCAPE '\\'")
            + ")"
        )
        parameters += [pattern, pattern.lower()]
    if tag:
        clauses.append(TAGGED_WITH.format(condition="t.name = ?"))
        parameters.append(normalize_tag(tag))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = database.fetch_all(f"SELECT * FROM media{where} ORDER BY created_at DESC, id DESC", parameters)
    tags = _tags_for(database, (row["id"] for row in rows))
    return [_with_status(row, request, tags[row["id"]]) for row in rows]


@router.get("/tags", response_model=List[TagCount])
def list_tags(database: Database = Depends(get_database)) -> List[TagCount]:
    """Tags in use, most used first, for grouping chips in the library."""
    rows = database.fetch_all(
        """SELECT t.name, COUNT(*) AS count FROM tags t JOIN media_tags mt ON mt.tag_id = t.id
           GROUP BY t.id ORDER BY count DESC, t.name"""
    )
    return [TagCount(name=row["name"], count=row["count"]) for row in rows]


@router.post("", response_model=Media, status_code=status.HTTP_201_CREATED)
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
    database: Database = Depends(get_database),
    storage: MediaStorage = Depends(get_storage),
) -> Media:
    try:
        stored = await storage.save(file)
    except StorageFullError as error:
        logger.warning("media_upload_rejected", extra={"reason": str(error)})
        raise HTTPException(status_code=507, detail=str(error)) from error
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
    if stored.media_type == MediaType.VIDEO:
        request.app.state.proxies.submit(stored.filename)
    return _with_status(row, request)


def _get_media(media_id: int, database: Database) -> Media:
    row = database.fetch_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="media not found")
    return Media.model_validate(dict(row))


@router.get("/{media_id}", response_model=Media)
def get_media(media_id: int, request: Request, database: Database = Depends(get_database)) -> Media:
    row = database.fetch_one("SELECT * FROM media WHERE id = ?", (media_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="media not found")
    return _with_status(row, request, _tags_for(database, [media_id])[media_id])


REMOVE_UNUSED_TAGS = "DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM media_tags)"


@router.post("/batch-tags", response_model=List[Media])
def batch_tags(
    body: MediaBatchTags,
    request: Request,
    database: Database = Depends(get_database),
) -> List[Media]:
    """Adds and removes tags across a selection in one transaction."""
    ids = list(dict.fromkeys(body.media_ids))
    current = _tags_for(database, ids)
    found = {
        row["id"]
        for row in database.fetch_all(
            f"SELECT id FROM media WHERE id IN ({','.join('?' for _ in ids)})", ids
        )
    }
    missing = [media_id for media_id in ids if media_id not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"media not found: {missing[:5]}")
    removing = set(body.remove)
    results = {}
    for media_id in ids:
        tags = [tag for tag in current[media_id] if tag not in removing]
        tags += [tag for tag in body.add if tag not in tags and tag not in removing]
        if len(tags) > MAX_TAGS:
            raise HTTPException(status_code=422, detail=f"an item would have more than {MAX_TAGS} tags")
        results[media_id] = tags
    with database.transaction() as connection:
        for tag in body.add:
            connection.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
        for media_id, tags in results.items():
            connection.execute("DELETE FROM media_tags WHERE media_id = ?", (media_id,))
            for tag in tags:
                connection.execute(
                    "INSERT INTO media_tags (media_id, tag_id) SELECT ?, id FROM tags WHERE name = ?",
                    (media_id, tag),
                )
        connection.execute(REMOVE_UNUSED_TAGS)
    logger.info(
        "media_tags_batch_updated",
        extra={"count": len(ids), "added": len(body.add), "removed": len(body.remove)},
    )
    rows = database.fetch_all(f"SELECT * FROM media WHERE id IN ({','.join('?' for _ in ids)})", ids)
    tags = _tags_for(database, ids)
    return [_with_status(row, request, tags[row["id"]]) for row in rows]


@router.patch("/{media_id}", response_model=Media)
def update_media(
    media_id: int,
    body: MediaUpdate,
    request: Request,
    database: Database = Depends(get_database),
) -> Media:
    """Renames media and/or replaces its tags; omitted fields are left alone."""
    _get_media(media_id, database)
    with database.transaction() as connection:
        if body.display_name is not None:
            connection.execute(
                "UPDATE media SET display_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (body.display_name, media_id),
            )
        if body.tags is not None:
            connection.execute("DELETE FROM media_tags WHERE media_id = ?", (media_id,))
            for tag in body.tags:
                connection.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
                connection.execute(
                    """INSERT INTO media_tags (media_id, tag_id)
                       SELECT ?, id FROM tags WHERE name = ?""",
                    (media_id, tag),
                )
            connection.execute(REMOVE_UNUSED_TAGS)
    logger.info(
        "media_updated",
        extra={"media_id": media_id, "tag_count": len(body.tags) if body.tags is not None else None},
    )
    return get_media(media_id, request, database)


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


@router.get("/{media_id}/playback", response_class=FileResponse)
async def get_media_playback(
    media_id: int,
    database: Database = Depends(get_database),
    storage: MediaStorage = Depends(get_storage),
) -> FileResponse:
    """A phone-friendly clip for the edit pane's player: the small H.264 playback copy
    of a video or animated GIF (built on first request for GIFs), else the original."""
    media = _get_media(media_id, database)
    if media.media_type == MediaType.IMAGE:
        raise HTTPException(status_code=404, detail="still pictures have nothing to play")
    original = storage.media_path(media.filename)
    if not original.is_file():
        raise HTTPException(status_code=410, detail="media file is missing")
    proxy = storage.proxy_path(media.filename)
    if not proxy.is_file() and media.media_type == MediaType.GIF:
        await run_in_threadpool(storage.build_proxy, media.filename)
    if proxy.is_file():
        return FileResponse(proxy, media_type="video/mp4", headers={"Cache-Control": "no-cache"})
    return FileResponse(original, media_type=media.mime_type, headers={"Cache-Control": "no-cache"})


@router.get("/{media_id}/thumbnail", response_class=FileResponse)
async def get_thumbnail(
    media_id: int,
    database: Database = Depends(get_database),
    storage: MediaStorage = Depends(get_storage),
) -> FileResponse:
    media = _get_media(media_id, database)
    if not media.thumbnail_path and media.media_type == MediaType.VIDEO:
        # Videos uploaded before thumbnailing existed get one on first view.
        path = storage.media_path(media.filename)
        thumbnail = await run_in_threadpool(
            storage.video_thumbnail, path, Path(media.filename).stem, media.duration_ms
        ) if path.is_file() else None
        if thumbnail:
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE media SET thumbnail_path = ? WHERE id = ?", (thumbnail, media_id)
                )
            media = media.model_copy(update={"thumbnail_path": thumbnail})
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
        raise HTTPException(
            status_code=409, detail="media is used by a playlist or as a text background"
        ) from error
    with database.transaction() as connection:
        connection.execute(REMOVE_UNUSED_TAGS)
    storage.delete(media.filename, media.thumbnail_path)
    logger.info("media_deleted", extra={"media_id": media_id})

