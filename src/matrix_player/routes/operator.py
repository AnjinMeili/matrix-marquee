"""Entering, forgetting, and changing the operator PIN."""

import math
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..access import COOKIE, TRUST_SECONDS, AccessControl, LockedOut, PinRejected

router = APIRouter(prefix="/api/operator", tags=["operator"])


class PinEntry(BaseModel):
    pin: str = Field(max_length=12)


class PinChange(BaseModel):
    current_pin: str = Field(max_length=12)
    new_pin: str = Field(max_length=12)


def _access(request: Request) -> AccessControl:
    return request.app.state.access


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _status(access: AccessControl, token: Optional[str]) -> dict:
    status = access.status(token)
    return {"pin_set": status.pin_set, "trusted_until": status.trusted_until, "problem": status.problem}


def _trust(response: Response, token: str) -> None:
    # HttpOnly keeps it from page scripts; SameSite=Strict keeps other sites from using it.
    response.set_cookie(COOKIE, token, max_age=TRUST_SECONDS, httponly=True, samesite="strict", path="/")


def _refused(error: Exception) -> HTTPException:
    if isinstance(error, LockedOut):
        minutes = max(1, math.ceil(error.seconds / 60))
        return HTTPException(
            status_code=429,
            detail={"code": "locked_out", "message": f"Too many wrong tries. Wait {minutes} minute{'s' if minutes > 1 else ''}."},
        )
    return HTTPException(status_code=403, detail={"code": "wrong_pin", "message": "That PIN is not right."})


@router.get("")
def operator_status(request: Request) -> dict:
    return _status(_access(request), request.cookies.get(COOKIE))


@router.post("/login")
def login(body: PinEntry, request: Request, response: Response) -> dict:
    access = _access(request)
    if not access.pin_set:
        raise HTTPException(status_code=409, detail={"code": "no_pin", "message": "No operator PIN is set on the sign yet."})
    try:
        token = access.login(body.pin, _client(request))
    except (LockedOut, PinRejected) as error:
        raise _refused(error) from error
    _trust(response, token)
    return _status(access, token)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    access = _access(request)
    access.logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return _status(access, None)


@router.post("/pin")
def change_pin(body: PinChange, request: Request, response: Response) -> dict:
    access = _access(request)
    if not access.pin_set:
        raise HTTPException(status_code=409, detail={"code": "no_pin", "message": "Set the first PIN in protest.env on the sign."})
    try:
        token = access.change_pin(body.current_pin, body.new_pin, _client(request))
    except (LockedOut, PinRejected) as error:
        raise _refused(error) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"code": "bad_pin", "message": str(error)}) from error
    _trust(response, token)
    return _status(access, token)
