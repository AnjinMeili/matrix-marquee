"""Captive portal probes used by common operating systems."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse


router = APIRouter(tags=["captive-portal"])


def _portal(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/"


@router.get("/hotspot-detect.html", response_class=HTMLResponse)
@router.get("/library/test/success.html", response_class=HTMLResponse)
def apple_probe() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><title>Success</title></head>"
        "<body>Success</body></html>"
    )


@router.get("/generate_204")
@router.get("/gen_204")
def android_probe(request: Request) -> RedirectResponse:
    return RedirectResponse(_portal(request), status_code=302)


@router.get("/connecttest.txt", response_class=PlainTextResponse)
def windows_connect_test() -> PlainTextResponse:
    return PlainTextResponse("Microsoft Connect Test")


@router.get("/ncsi.txt", response_class=PlainTextResponse)
def windows_ncsi() -> PlainTextResponse:
    return PlainTextResponse("Microsoft NCSI")


@router.get("/canonical.html")
@router.get("/success.txt")
def generic_probe(request: Request) -> RedirectResponse:
    return RedirectResponse(_portal(request), status_code=302)

