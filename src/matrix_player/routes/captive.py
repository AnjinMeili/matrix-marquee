"""Captive portal probes used by common operating systems."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse


router = APIRouter(tags=["captive-portal"])


def _portal(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/"


@router.get("/hotspot-detect.html", response_class=HTMLResponse)
@router.get("/library/test/success.html", response_class=HTMLResponse)
def apple_probe(request: Request) -> HTMLResponse:
    portal = _portal(request)
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><title>MatrixMarquee</title>"
        f"<meta http-equiv='refresh' content='0;url={portal}'></head>"
        f"<body><a href='{portal}'>Open MatrixMarquee</a></body></html>"
    )


@router.get("/generate_204")
@router.get("/gen_204")
def android_probe(request: Request) -> RedirectResponse:
    return RedirectResponse(_portal(request), status_code=302)


@router.get("/connecttest.txt")
def windows_connect_test(request: Request) -> RedirectResponse:
    return RedirectResponse(_portal(request), status_code=302)


@router.get("/ncsi.txt")
def windows_ncsi(request: Request) -> RedirectResponse:
    return RedirectResponse(_portal(request), status_code=302)


@router.get("/canonical.html")
@router.get("/success.txt")
def generic_probe(request: Request) -> RedirectResponse:
    return RedirectResponse(_portal(request), status_code=302)
