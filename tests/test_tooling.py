"""Guards against problems that reached the sign before: browser dialogs that
the iPhone captive-portal window silently refuses, configuration drift, and
UI rendering faults such as stray "null" text."""

import importlib.util
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "src" / "matrix_player" / "static" / "app.js"


def test_ui_never_uses_browser_dialogs() -> None:
    # The iPhone captive-portal window refuses them silently, so a delete
    # confirmed with window.confirm() simply never happens there.
    # Comments may name the forbidden calls (to explain why), so scan code only.
    source = re.sub(r"/\*.*?\*/", "", APP_JS.read_text(), flags=re.S)
    source = re.sub(r"(?<!:)//.*", "", source)
    # A bare or window.-qualified call; our own confirmAction() does not match.
    calls = re.findall(r"(?:\bwindow\.|(?<![\w.]))(confirm|alert|prompt)\s*\(", source)
    assert calls == [], f"use the in-page confirmAction() instead of {calls}"


def test_every_setting_the_code_reads_is_documented() -> None:
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), re.M))
    read = set()
    for path in list((ROOT / "src").rglob("*.py")):
        read |= set(re.findall(r'getenv\(\s*"([A-Z][A-Z0-9_]+)"', path.read_text()))
    for path in (ROOT / "scripts" / "run-service.sh", ROOT / "scripts" / "health-check.sh"):
        read |= set(re.findall(r"\$\{(PROTEST_[A-Z0-9_]+):=", path.read_text()))
    assert read - documented == set(), "add these to .env.example"


def _ui_check_available() -> bool:
    if importlib.util.find_spec("websocket") is None:
        return False
    sys.path.insert(0, str(ROOT / "tools"))
    import ui_check

    return ui_check.find_chrome() is not None


@pytest.mark.skipif(not _ui_check_available(), reason="needs Chrome and websocket-client")
def test_every_view_renders_cleanly_at_phone_size(tmp_path: Path) -> None:
    import ui_check

    assert ui_check.run(tmp_path) == 0, f"see the report above and screenshots in {tmp_path}"
    assert len(list(tmp_path.glob("*.png"))) >= 9
