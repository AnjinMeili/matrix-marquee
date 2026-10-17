"""Text panel CRUD and preview endpoints."""

from io import BytesIO
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from PIL import Image, ImageColor, ImageDraw, ImageFont

from ..config import Settings
from ..database import Database
from ..schemas import TextPanel, TextPanelCreate, TextPanelUpdate
from .dependencies import get_database, get_settings


router = APIRouter(prefix="/api/text-panels", tags=["text-panels"])
logger = logging.getLogger(__name__)
FIELDS = (
    "name", "content", "font_family", "font_size", "bold", "italic", "alignment",
    "color_mode", "color_value", "border_style", "border_color",
)


def _panel(panel_id: int, database: Database) -> TextPanel:
    row = database.fetch_one("SELECT * FROM text_panels WHERE id = ?", (panel_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="text panel not found")
    return TextPanel.model_validate(dict(row))


@router.get("", response_model=List[TextPanel])
def list_panels(database: Database = Depends(get_database)) -> List[TextPanel]:
    return [
        TextPanel.model_validate(dict(row))
        for row in database.fetch_all("SELECT * FROM text_panels ORDER BY name, id")
    ]


@router.post("", response_model=TextPanel, status_code=status.HTTP_201_CREATED)
def create_panel(
    body: TextPanelCreate, database: Database = Depends(get_database)
) -> TextPanel:
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
    return TextPanel.model_validate(dict(row))


@router.delete("/{panel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_panel(panel_id: int, database: Database = Depends(get_database)) -> None:
    _panel(panel_id, database)
    try:
        with database.transaction() as connection:
            connection.execute("DELETE FROM text_panels WHERE id = ?", (panel_id,))
    except Exception as error:
        if "FOREIGN KEY" in str(error):
            raise HTTPException(status_code=409, detail="text panel is used by a playlist") from error
        raise
    logger.info("text_panel_deleted", extra={"text_panel_id": panel_id})


@router.get("/{panel_id}/preview")
def preview_panel(
    panel_id: int,
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> Response:
    panel = _panel(panel_id, database)
    image = Image.new("RGB", (settings.matrix_width, settings.matrix_height), "black")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    color = ImageColor.getrgb(panel.color_value)
    if panel.border_style != "none":
        draw.rectangle(
            (0, 0, settings.matrix_width - 1, settings.matrix_height - 1),
            outline=ImageColor.getrgb(panel.border_color),
        )
    box = draw.multiline_textbbox((0, 0), panel.content, font=font, align=panel.alignment)
    text_width, text_height = box[2] - box[0], box[3] - box[1]
    if panel.alignment == "left":
        x = 2
    elif panel.alignment == "right":
        x = max(2, settings.matrix_width - text_width - 2)
    else:
        x = max(2, (settings.matrix_width - text_width) // 2)
    y = max(2, (settings.matrix_height - text_height) // 2)
    draw.multiline_text((x, y), panel.content, fill=color, font=font, align=panel.alignment)
    output = BytesIO()
    image.save(output, format="PNG")
    return Response(
        output.getvalue(),
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Matrix-Width": str(settings.matrix_width),
            "X-Matrix-Height": str(settings.matrix_height),
        },
    )

