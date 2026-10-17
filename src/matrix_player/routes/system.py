"""Operational status, network, and diagnostics endpoints."""

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import socket
from typing import Optional

from fastapi import APIRouter, Depends, Request

from ..config import Settings
from ..database import Database
from ..playback import PlaybackController
from .dependencies import get_database, get_playback, get_settings


router = APIRouter(prefix="/api", tags=["system"])


def _temperature() -> Optional[float]:
    path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(float(path.read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def _local_address() -> Optional[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return str(sock.getsockname()[0])
    except OSError:
        return None
    finally:
        sock.close()


@router.get("/status")
def status_endpoint(
    request: Request,
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    playback: PlaybackController = Depends(get_playback),
) -> dict:
    usage = shutil.disk_usage(settings.data_dir)
    media_count = database.fetch_one("SELECT COUNT(*) AS count FROM media")
    return {
        "service": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "matrix": {
            "width": request.app.state.matrix.size[0] if request.app.state.matrix else settings.matrix_width,
            "height": request.app.state.matrix.size[1] if request.app.state.matrix else settings.matrix_height,
            "connected": request.app.state.matrix_connected,
            "mode": settings.matrix_mode,
        },
        "playback": playback.state().model_dump(mode="json"),
        "storage": {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "media_count": media_count["count"] if media_count else 0,
        },
        "temperature_c": _temperature(),
    }


@router.get("/network")
def network_endpoint() -> dict:
    return {
        "hostname": socket.gethostname(),
        "local_address": _local_address(),
        "access_point": {
            "ssid": os.getenv("MATRIX_PLAYER_AP_SSID"),
            "active": os.getenv("MATRIX_PLAYER_AP_ACTIVE", "").lower() in {"1", "true", "yes"},
        },
        "upstream": {
            "available": _local_address() is not None,
            "interface": os.getenv("MATRIX_PLAYER_UPSTREAM_INTERFACE"),
        },
    }


@router.get("/diagnostics")
def diagnostics_endpoint(
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
) -> dict:
    tables = {}
    for table in ("media", "text_panels", "playlists", "playlist_items"):
        row = database.fetch_one(f"SELECT COUNT(*) AS count FROM {table}")
        tables[table] = row["count"] if row else 0
    try:
        load_average = list(os.getloadavg())
    except OSError:
        load_average = None
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": {"ok": True, "records": tables},
        "directories": {
            "data_writable": os.access(settings.data_dir, os.W_OK),
            "media_writable": os.access(settings.media_dir, os.W_OK),
        },
        "process": {"pid": os.getpid(), "load_average": load_average},
        "temperature_c": _temperature(),
    }
