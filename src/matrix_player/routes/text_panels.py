"""Text panel CRUD and preview endpoints."""

from io import BytesIO
import logging
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from PIL import Image

from ..database import Database
from ..rendering import TextRenderer, font_choices, load_background, preview_frames, style_from_mapping
from ..schemas import TextPanel, TextPanelCreate, TextPanelPreview, TextPanelUpdate
from ..storage import MediaStorage
from .dependencies import get_database, get_storage


router = APIRouter(prefix="/api/text-panels", tags=["text-panels"])
logger = logging.getLogger(__name__)
FIELDS = (
    "name", "content", "font_family", "font_size", "bold", "italic", "alignment",
    "color_mode", "color_value", "outline", "outline_color", "border_style",
    "border_color", "effect", "accent_color", "background_color", "background_media_id",
    "motion_speed",
)


def _panel(panel_id: int, database: Database) -> TextPanel:
    row = database.fetch_one("SELECT * FROM text_panels WHERE id = ?", (panel_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="text panel not found")
    return TextPanel.model_validate(dict(row))


def _check_background(media_id: Optional[int], database: Database) -> None:
    if media_id is None:
        return
    row = database.fetch_one("SELECT media_type FROM media WHERE id = ?", (media_id,))
    if row is None:
        raise HTTPException(status_code=422, detail="background image not found")
    if row["media_type"] not in {"image", "gif"}:
        raise HTTPException(status_code=422, detail="background must be an image or GIF")


def _matrix_size(request: Request) -> tuple:
    matrix = request.app.state.matrix
    if matrix is not None:
        return tuple(matrix.size)
    settings = request.app.state.settings
    return settings.matrix_width, settings.matrix_height


def _render_preview(
    values: dict, size: tuple, database: Database, storage: MediaStorage
) -> Response:
    background: Optional[Image.Image] = None
    media_id = values.get("background_media_id")
    if media_id:
        row = database.fetch_one("SELECT filename FROM media WHERE id = ?", (media_id,))
        if row is not None:
            background = load_background(storage.media_path(row["filename"]), size)
    renderer = TextRenderer(str(values["content"]), size, style_from_mapping(values), background)
    frames = preview_frames(renderer)
    output = BytesIO()
    if len(frames) == 1:
        frames[0].save(output, format="PNG")
        media_type = "image/png"
    else:
        frames[0].save(
            output, format="GIF", save_all=True, append_images=frames[1:],
            duration=round(1000 / 12), loop=0,
        )
        media_type = "image/gif"
    return Response(
        output.getvalue(),
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Matrix-Width": str(size[0]),
            "X-Matrix-Height": str(size[1]),
        },
    )


@router.get("", response_model=List[TextPanel])
def list_panels(database: Database = Depends(get_database)) -> List[TextPanel]:
    return [
        TextPanel.model_validate(dict(row))
        for row in database.fetch_all("SELECT * FROM text_panels ORDER BY name, id")
    ]


@router.get("/fonts")
def list_fonts() -> List[dict]:
    return font_choices()


@router.post("/preview")
async def preview_unsaved(
    body: TextPanelPreview,
    request: Request,
    database: Database = Depends(get_database),
    storage: MediaStorage = Depends(get_storage),
) -> Response:
    """Renders editor state that has not been saved yet."""
    return await run_in_threadpool(
        _render_preview, body.model_dump(), _matrix_size(request), database, storage
    )


@router.post("", response_model=TextPanel, status_code=status.HTTP_201_CREATED)
def create_panel(
    body: TextPanelCreate, database: Database = Depends(get_database)
) -> TextPanel:
    _check_background(body.background_media_id, database)
    values = body.model_dump()
    with database.transaction() as connection:
        cursor = connection.execute(
            f"INSERT INTO text_panels ({','.join(FIELDS)}) VALUES ({','.join('?' for _ in FIELDS)})",
            tuple(values[field] for field in FIELDS),
        )
        row = connection.execute(
            "SELECT * FROM text_panels WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    logger.info("text_panel_created", extra={"text_panel_id": row["id"]})
    return TextPanel.model_validate(dict(row))


@router.get("/{panel_id}", response_model=TextPanel)
def get_panel(panel_id: int, database: Database = Depends(get_database)) -> TextPanel:
    return _panel(panel_id, database)


@router.put("/{panel_id}", response_model=TextPanel)
def update_panel(
    panel_id: int, body: TextPanelUpdate, database: Database = Depends(get_database)
) -> TextPanel:
    _panel(panel_id, database)
    _check_background(body.background_media_id, database)
    values = body.model_dump()
    assignments = ",".join(f"{field} = ?" for field in FIELDS)
    with database.transaction() as connection:
        connection.execute(
            f"UPDATE text_panels SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(values[field] for field in FIELDS) + (panel_id,),
        )
        row = connection.execute(
            "SELECT * FROM text_panels WHERE id = ?", (panel_id,)
        ).fetchone()
    logger.info("text_panel_updated", extra={"text_panel_id": panel_id})
    return TextPanel.model_validate(dict(row))


@router.delete("/{panel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_panel(panel_id: int, database: Database = Depends(get_database)) -> None:
    _panel(panel_id, database)
    try:
        with database.transaction() as connection:
            connection.execute("DELETE FROM text_panels WHERE id = ?", (panel_id,))
    except sqlite3.IntegrityError as error:
        raise HTTPException(status_code=409, detail="text panel is used by a playlist") from error
    logger.info("text_panel_deleted", extra={"text_panel_id": panel_id})


@router.get("/{panel_id}/preview")
async def preview_panel(
    panel_id: int,
    request: Request,
    database: Database = Depends(get_database),
    storage: MediaStorage = Depends(get_storage),
) -> Response:
    """Renders the saved panel exactly as the matrix will show it."""
    panel = _panel(panel_id, database)
    return await run_in_threadpool(
        _render_preview, panel.model_dump(), _matrix_size(request), database, storage
    )
