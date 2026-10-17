"""Playlist and ordered item CRUD endpoints."""

import sqlite3
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from ..database import Database
from ..schemas import (
    Playlist,
    PlaylistCreate,
    PlaylistItem,
    PlaylistItemCreate,
    PlaylistItemUpdate,
    PlaylistUpdate,
)
from .dependencies import get_database


router = APIRouter(prefix="/api/playlists", tags=["playlists"])
PLAYLIST_FIELDS = ("name", "description", "is_default", "is_enabled")
ITEM_FIELDS = (
    "item_type", "media_id", "text_panel_id", "duration_ms", "repeat_count",
    "transition_type", "transition_duration_ms", "fit_mode",
)


def _playlist(playlist_id: int, database: Database) -> Playlist:
    row = database.fetch_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="playlist not found")
    items = [
        PlaylistItem.model_validate(dict(item))
        for item in database.fetch_all(
            "SELECT * FROM playlist_items WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        )
    ]
    values = dict(row)
    values["items"] = items
    return Playlist.model_validate(values)


def _ensure_target(connection: sqlite3.Connection, body: PlaylistItemCreate) -> None:
    table, target_id = (
        ("media", body.media_id)
        if body.media_id is not None
        else ("text_panels", body.text_panel_id)
    )
    if connection.execute(f"SELECT id FROM {table} WHERE id = ?", (target_id,)).fetchone() is None:
        raise HTTPException(status_code=422, detail=f"{body.item_type.value} target not found")


def _set_default(connection: sqlite3.Connection, playlist_id: int, is_default: bool) -> None:
    if is_default:
        connection.execute("UPDATE playlists SET is_default = 0 WHERE id != ?", (playlist_id,))


@router.get("", response_model=List[Playlist])
def list_playlists(database: Database = Depends(get_database)) -> List[Playlist]:
    ids = [row["id"] for row in database.fetch_all("SELECT id FROM playlists ORDER BY name, id")]
    return [_playlist(playlist_id, database) for playlist_id in ids]


@router.post("", response_model=Playlist, status_code=status.HTTP_201_CREATED)
def create_playlist(
    body: PlaylistCreate, database: Database = Depends(get_database)
) -> Playlist:
    values = body.model_dump()
    with database.transaction() as connection:
        cursor = connection.execute(
            f"INSERT INTO playlists ({','.join(PLAYLIST_FIELDS)}) VALUES (?,?,?,?)",
            tuple(values[field] for field in PLAYLIST_FIELDS),
        )
        playlist_id = int(cursor.lastrowid)
        _set_default(connection, playlist_id, body.is_default)
    return _playlist(playlist_id, database)


@router.get("/{playlist_id}", response_model=Playlist)
def get_playlist(playlist_id: int, database: Database = Depends(get_database)) -> Playlist:
    return _playlist(playlist_id, database)


@router.put("/{playlist_id}", response_model=Playlist)
def update_playlist(
    playlist_id: int,
    body: PlaylistUpdate,
    database: Database = Depends(get_database),
) -> Playlist:
    _playlist(playlist_id, database)
    values = body.model_dump()
    with database.transaction() as connection:
        connection.execute(
            f"""UPDATE playlists SET
                {','.join(f'{field} = ?' for field in PLAYLIST_FIELDS)},
                updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            tuple(values[field] for field in PLAYLIST_FIELDS) + (playlist_id,),
        )
        _set_default(connection, playlist_id, body.is_default)
    return _playlist(playlist_id, database)


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_playlist(playlist_id: int, database: Database = Depends(get_database)) -> None:
    _playlist(playlist_id, database)
    with database.transaction() as connection:
        connection.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))


@router.post("/{playlist_id}/duplicate", response_model=Playlist, status_code=201)
def duplicate_playlist(
    playlist_id: int, database: Database = Depends(get_database)
) -> Playlist:
    original = _playlist(playlist_id, database)
    with database.transaction() as connection:
        cursor = connection.execute(
            """INSERT INTO playlists (name, description, is_default, is_enabled)
               VALUES (?, ?, 0, ?)""",
            (f"{original.name} copy", original.description, original.is_enabled),
        )
        new_id = int(cursor.lastrowid)
        connection.execute(
            """INSERT INTO playlist_items
               (playlist_id, position, item_type, media_id, text_panel_id, duration_ms,
                repeat_count, transition_type, transition_duration_ms, fit_mode)
               SELECT ?, position, item_type, media_id, text_panel_id, duration_ms,
                      repeat_count, transition_type, transition_duration_ms, fit_mode
               FROM playlist_items WHERE playlist_id = ?""",
            (new_id, playlist_id),
        )
    return _playlist(new_id, database)


@router.post("/{playlist_id}/items", response_model=PlaylistItem, status_code=201)
def add_item(
    playlist_id: int,
    body: PlaylistItemCreate,
    database: Database = Depends(get_database),
) -> PlaylistItem:
    _playlist(playlist_id, database)
    values = body.model_dump()
    with database.transaction() as connection:
        _ensure_target(connection, body)
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM playlist_items WHERE playlist_id = ?", (playlist_id,)
            ).fetchone()[0]
        )
        position = min(body.position if body.position is not None else count, count)
        connection.execute(
            "UPDATE playlist_items SET position = position + 100000 WHERE playlist_id = ? AND position >= ?",
            (playlist_id, position),
        )
        connection.execute(
            "UPDATE playlist_items SET position = position - 99999 WHERE playlist_id = ? AND position >= ?",
            (playlist_id, position + 100000),
        )
        cursor = connection.execute(
            f"""INSERT INTO playlist_items
                (playlist_id, position, {','.join(ITEM_FIELDS)})
                VALUES (?, ?, {','.join('?' for _ in ITEM_FIELDS)})""",
            (playlist_id, position) + tuple(
                values[field].value if hasattr(values[field], "value") else values[field]
                for field in ITEM_FIELDS
            ),
        )
        row = connection.execute(
            "SELECT * FROM playlist_items WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return PlaylistItem.model_validate(dict(row))


def _item(playlist_id: int, item_id: int, database: Database) -> PlaylistItem:
    row = database.fetch_one(
        "SELECT * FROM playlist_items WHERE playlist_id = ? AND id = ?",
        (playlist_id, item_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="playlist item not found")
    return PlaylistItem.model_validate(dict(row))


@router.put("/{playlist_id}/items/{item_id}", response_model=PlaylistItem)
def update_item(
    playlist_id: int,
    item_id: int,
    body: PlaylistItemUpdate,
    database: Database = Depends(get_database),
) -> PlaylistItem:
    current = _item(playlist_id, item_id, database)
    values = body.model_dump()
    with database.transaction() as connection:
        _ensure_target(connection, PlaylistItemCreate(**values))
        count = int(connection.execute(
            "SELECT COUNT(*) FROM playlist_items WHERE playlist_id = ?", (playlist_id,)
        ).fetchone()[0])
        new_position = min(body.position if body.position is not None else current.position, count - 1)
        connection.execute(
            "UPDATE playlist_items SET position = -1 WHERE id = ?", (item_id,)
        )
        if new_position < current.position:
            connection.execute(
                """UPDATE playlist_items SET position = position + 100000
                   WHERE playlist_id = ? AND position >= ? AND position < ?""",
                (playlist_id, new_position, current.position),
            )
            connection.execute(
                """UPDATE playlist_items SET position = position - 99999
                   WHERE playlist_id = ? AND position >= ? AND position < ?""",
                (playlist_id, new_position + 100000, current.position + 100000),
            )
        elif new_position > current.position:
            connection.execute(
                """UPDATE playlist_items SET position = position + 100000
                   WHERE playlist_id = ? AND position > ? AND position <= ?""",
                (playlist_id, current.position, new_position),
            )
            connection.execute(
                """UPDATE playlist_items SET position = position - 100001
                   WHERE playlist_id = ? AND position > ? AND position <= ?""",
                (playlist_id, current.position + 100000, new_position + 100000),
            )
        connection.execute(
            f"""UPDATE playlist_items SET position = ?,
                {','.join(f'{field} = ?' for field in ITEM_FIELDS)}
                WHERE id = ?""",
            (new_position,) + tuple(
                values[field].value if hasattr(values[field], "value") else values[field]
                for field in ITEM_FIELDS
            ) + (item_id,),
        )
    return _item(playlist_id, item_id, database)


@router.delete("/{playlist_id}/items/{item_id}", status_code=204)
def delete_item(
    playlist_id: int,
    item_id: int,
    database: Database = Depends(get_database),
) -> None:
    item = _item(playlist_id, item_id, database)
    with database.transaction() as connection:
        connection.execute("DELETE FROM playlist_items WHERE id = ?", (item_id,))
        connection.execute(
            "UPDATE playlist_items SET position = position - 1 WHERE playlist_id = ? AND position > ?",
            (playlist_id, item.position),
        )


@router.post("/{playlist_id}/preview")
def preview_playlist(
    playlist_id: int, database: Database = Depends(get_database)
) -> dict:
    playlist = _playlist(playlist_id, database)
    return {
        "playlist_id": playlist.id,
        "width": None,
        "height": None,
        "item_count": len(playlist.items),
        "items": [item.model_dump(mode="json") for item in playlist.items],
    }

