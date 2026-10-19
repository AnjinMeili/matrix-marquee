#!/usr/bin/env python3
"""Check the web UI in headless Chrome at iPhone size and screenshot every view.

Starts the app in mock mode on a free port with throwaway sample data, opens
each view (and the message editor and media dialog), and fails when a page
shows stray "null", "undefined" or "NaN" text, is wider than the screen,
throws a JavaScript error, or uses a browser dialog (the iPhone captive-portal
window silently refuses window.confirm/alert/prompt).

Usage: python tools/ui_check.py [--out DIR]
Needs Google Chrome or Chromium (or CHROME=/path/to/chrome) and the
websocket-client package (pip install -e '.[test]').
Exit status: 0 when every check passes, 1 on problems, 2 when it cannot run.
"""

import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
VIEWPORT = {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True}
VIEWS = ("dashboard", "text", "playlists", "library", "settings")
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
)
PAGE_PROBLEMS = """(() => {
  const open = document.querySelector("dialog[open]");
  const scope = open || document.querySelector(".view:not([hidden])") || document.body;
  const words = (scope.innerText.match(/\\b(null|undefined|NaN)\\b/g) || []);
  const root = document.documentElement;
  return { words, overflow: root.scrollWidth > root.clientWidth + 1 ? root.scrollWidth : 0 };
})()"""


def find_chrome() -> Optional[str]:
    for candidate in ([os.environ["CHROME"]] if os.environ.get("CHROME") else []) + list(CHROME_CANDIDATES):
        path = candidate if os.path.isabs(candidate) else shutil.which(candidate)
        if path and os.path.exists(path):
            return path
    return None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"{url} did not answer")


class Api:
    def __init__(self, base: str) -> None:
        self.base = base

    def json(self, path: str, body: Optional[dict] = None, method: str = "POST") -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        return json.load(urllib.request.urlopen(request, timeout=30))

    def upload(self, name: str, data: bytes, mime: str) -> dict:
        boundary = uuid.uuid4().hex
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            self.base + "/api/media", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return json.load(urllib.request.urlopen(request, timeout=30))


def seed(api: Api) -> None:
    """Enough content that every view, list, tag bar, and editor has something to draw."""
    from io import BytesIO

    from PIL import Image

    def png(colour: str) -> bytes:
        output = BytesIO()
        Image.new("RGB", (60, 40), colour).save(output, format="PNG")
        return output.getvalue()

    def gif() -> bytes:
        frames = [Image.new("RGB", (40, 30), colour) for colour in ("red", "blue", "green")]
        output = BytesIO()
        frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=120, loop=0)
        return output.getvalue()

    picture = api.upload("crowd.png", png("purple"), "image/png")
    animation = api.upload("dance.gif", gif(), "image/gif")
    api.json(f"/api/media/{picture['id']}", {"tags": ["march", "night"]}, "PATCH")
    api.json(f"/api/media/{animation['id']}", {"tags": ["march"]}, "PATCH")
    still = api.json("/api/text-panels", {"name": "No kings", "content": "NO KINGS"})
    moving = api.json("/api/text-panels", {"name": "POW", "content": "POW!", "font_family": "comic", "effect": "pow"})
    playlist = api.json("/api/playlists", {"name": "March loop", "is_default": True})
    for body in (
        {"item_type": "text_panel", "text_panel_id": still["id"], "transition_type": "impact"},
        {"item_type": "text_panel", "text_panel_id": moving["id"], "transition_type": "morph"},
        {"item_type": "media", "media_id": animation["id"], "play_by": "repeats", "repeat_count": 2},
    ):
        api.json(f"/api/playlists/{playlist['id']}/items", body)
    api.json("/api/playback/start", {"playlist_id": playlist["id"]})


class Browser:
    """A minimal Chrome DevTools Protocol client: enough to navigate, evaluate, and screenshot."""

    def __init__(self, chrome: str, profile: str) -> None:
        import websocket  # websocket-client

        port = free_port()
        self.process = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
             f"--remote-debugging-port={port}", f"--remote-allow-origins=http://127.0.0.1:{port}",
             f"--user-data-dir={profile}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 20
        while True:
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2))
                page = next(target for target in targets if target["type"] == "page")
                break
            except (OSError, StopIteration):
                if time.monotonic() > deadline:
                    raise RuntimeError("Chrome did not start")
                time.sleep(0.2)
        self.socket = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
        self.next_id = 0
        self.errors: List[str] = []
        self.call("Emulation.setDeviceMetricsOverride", **VIEWPORT)
        self.call("Runtime.enable")
        self.call("Page.enable")

    def _event(self, message: dict) -> None:
        method = message.get("method")
        params = message.get("params", {})
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {})
            self.errors.append(details.get("exception", {}).get("description") or details.get("text", "exception"))
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            self.errors.append(" ".join(str(arg.get("value", arg.get("description", ""))) for arg in params.get("args", [])))
        elif method == "Page.javascriptDialogOpening":
            self.errors.append(f"browser dialog used: {params.get('type')} {params.get('message', '')!r}")
            self.call("Page.handleJavaScriptDialog", accept=False)

    def call(self, method: str, **params) -> dict:
        self.next_id += 1
        request_id = self.next_id
        self.socket.send(json.dumps({"id": request_id, "method": method, "params": params}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self._event(message)

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        return result.get("result", {}).get("value")

    def settle(self, seconds: float) -> None:
        """Waits while still collecting console errors and dialog events."""
        self.evaluate(f"new Promise(resolve => setTimeout(resolve, {int(seconds * 1000)}))")

    def screenshot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=True)["data"]
        path.write_bytes(base64.b64decode(data))

    def close(self) -> None:
        try:
            self.socket.close()
        finally:
            self.process.kill()
            self.process.wait()


def run(out: Path) -> int:
    chrome = find_chrome()
    if chrome is None:
        print("cannot run: no Chrome or Chromium found (set CHROME=/path/to/chrome)")
        return 2
    if importlib.util.find_spec("websocket") is None:
        print("cannot run: pip install websocket-client (included in the [test] extra)")
        return 2

    out.mkdir(parents=True, exist_ok=True)
    problems: Dict[str, List[str]] = {}
    with tempfile.TemporaryDirectory(prefix="ui-check-") as work:
        port = free_port()
        environment = {
            **os.environ, "MATRIX_PLAYER_DATA_DIR": work, "MATRIX_PLAYER_MATRIX_MODE": "mock",
            "MATRIX_PLAYER_LOG_LEVEL": "WARNING", "PYTHONPATH": str(ROOT / "src"),
        }
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "matrix_player.app:app", "--port", str(port), "--log-level", "warning"],
            cwd=str(ROOT), env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        browser = None
        try:
            base = f"http://127.0.0.1:{port}"
            wait_for(base + "/health")
            seed(Api(base))
            browser = Browser(chrome, str(Path(work) / "chrome"))
            browser.call("Page.navigate", url=base + "/#dashboard")
            browser.settle(1.5)
            steps = [(view, f'location.hash = "{view}"') for view in VIEWS] + [
                ("playlist-editor", 'location.hash = "playlists"; document.querySelector("#playlist-list .selection-item").click()'),
                ("message-detail", 'location.hash = "text"; document.querySelector("#text-list .selection-item").click()'),
                ("message-editor", 'location.hash = "text"; document.querySelector("[data-action=new-text-panel]").click()'),
                ("media-dialog", 'document.querySelectorAll("dialog[open]").forEach(d => d.close()); location.hash = "library"; '
                                 'document.querySelector("#media-grid [aria-label=\\"Name and tags\\"]").click()'),
                # A picture with words on top, every mixing option showing.
                ("item-dialog", 'document.querySelectorAll("dialog[open]").forEach(d => d.close()); location.hash = "playlists"; '
                                'document.querySelector("#playlist-list .selection-item").click(); '
                                '[...document.querySelectorAll("#playlist-editor button")].find(b => /Add/.test(b.textContent)).click(); '
                                'const f = document.querySelector("#item-form").elements; '
                                'for (const [name, value] of [["item_type", "media"], ["overlay_panel", f.overlay_panel.options[1].value], ["overlay_key", "color"]]) '
                                '{ f[name].value = value; f[name].dispatchEvent(new Event("change", {bubbles: true})); } '
                                'document.querySelector(".overlay-more").open = true'),
            ]
            for name, action in steps:
                browser.evaluate(action)
                browser.settle(1.2)
                found = browser.evaluate(PAGE_PROBLEMS) or {}
                issues = [f"shows {word!r}" for word in found.get("words", [])]
                if found.get("overflow"):
                    issues.append(f"wider than the screen ({found['overflow']}px > {VIEWPORT['width']}px)")
                issues += [f"script error: {error}" for error in browser.errors]
                browser.errors.clear()
                browser.screenshot(out / f"{name}.png")
                if issues:
                    problems[name] = issues
                print(("FAIL  " if issues else "ok    ") + name + ("" if not issues else ": " + "; ".join(issues)))
        finally:
            if browser is not None:
                browser.close()
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    print(f"screenshots: {out}")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "protest-ui-check")
    args = parser.parse_args()
    return run(args.out)


if __name__ == "__main__":
    sys.exit(main())
