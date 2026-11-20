"""The operator PIN: what it guards, lockouts, changing it, recovery, and keeping it secret."""

from dataclasses import replace
from io import BytesIO
from pathlib import Path
import stat
import zipfile

from fastapi.testclient import TestClient
import pytest

from matrix_player.access import COOKIE, MAX_TRIES, TRUST_SECONDS, AccessControl, LockedOut, PinRejected
from matrix_player.app import create_app
from matrix_player.matrix import MockMatrixAdapter

from test_signage import make_settings


def app_client(tmp_path: Path, pin=None) -> TestClient:
    settings = replace(make_settings(tmp_path), operator_pin=pin)
    return TestClient(create_app(settings, matrix_adapter=MockMatrixAdapter()))


def test_without_a_pin_settings_stay_open_and_say_so(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        assert client.get("/api/operator").json() == {"pin_set": False, "trusted_until": None, "problem": None}
        assert client.put("/api/display", json={"brightness": 80, "gamma": 1.0}).status_code == 200
        assert client.post("/api/operator/login", json={"pin": "1234"}).status_code == 409


def test_the_pin_guards_settings_but_not_making_signs(tmp_path: Path) -> None:
    with app_client(tmp_path, pin="2468") as client:
        assert client.put("/api/display", json={"brightness": 80, "gamma": 1.0}).status_code == 403
        assert client.post("/api/display/test-card").status_code == 403
        assert client.get("/api/backup").status_code == 403
        assert client.get("/api/diagnostics/logs").status_code == 403
        # Operators still make and show messages without it.
        panel = client.post("/api/text-panels", json={"name": "Hi", "content": "HI"})
        assert panel.status_code == 201
        assert client.get("/api/display").status_code == 200

        wrong = client.post("/api/operator/login", json={"pin": "1111"})
        assert wrong.status_code == 403 and wrong.json()["detail"]["message"] == "That PIN is not right."
        right = client.post("/api/operator/login", json={"pin": "2468"})
        assert right.status_code == 200 and right.json()["trusted_until"] is not None
        cookie = right.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert client.put("/api/display", json={"brightness": 80, "gamma": 1.0}).status_code == 200
        backup = client.get("/api/backup")
        assert backup.status_code == 200
        names = zipfile.ZipFile(BytesIO(backup.content)).namelist()
        assert not any("operator" in name for name in names)  # the PIN never leaves in a backup

        assert client.post("/api/operator/logout").status_code == 200
        assert client.put("/api/display", json={"brightness": 70, "gamma": 1.0}).status_code == 403


def test_wrong_guesses_lock_out_for_a_while(tmp_path: Path) -> None:
    with app_client(tmp_path, pin="2468") as client:
        for _ in range(MAX_TRIES):
            assert client.post("/api/operator/login", json={"pin": "0000"}).status_code == 403
        locked = client.post("/api/operator/login", json={"pin": "2468"})
        assert locked.status_code == 429
        assert "Wait 5 minutes" in locked.json()["detail"]["message"]


def test_lockout_ends_and_trust_expires(tmp_path: Path) -> None:
    now = [1000.0]
    access = AccessControl(tmp_path / "operator.json", "2468", clock=lambda: now[0])
    for _ in range(MAX_TRIES):
        with pytest.raises(PinRejected):
            access.login("9999", "10.0.0.5")
    with pytest.raises(LockedOut):
        access.login("2468", "10.0.0.5")
    token = access.login("2468", "10.0.0.6")  # another phone is not locked out by this one
    now[0] += 301
    assert access.login("2468", "10.0.0.5")
    assert access.allows(token)
    now[0] += TRUST_SECONDS + 1
    assert not access.allows(token)


def test_changing_the_pin_logs_others_out_and_protest_env_still_wins(tmp_path: Path) -> None:
    path = tmp_path / "operator.json"
    access = AccessControl(path, "2468")
    other_phone = access.login("2468", "a")
    with pytest.raises(ValueError):
        access.change_pin("2468", "12", "b")
    changer = access.change_pin("2468", "975310", "b")
    assert access.allows(changer) and not access.allows(other_phone)
    with pytest.raises(PinRejected):
        access.login("2468", "c")
    # It is kept across a restart...
    assert AccessControl(path, "2468").login("975310", "c")
    # ...until whoever looks after the sign edits protest.env, which is the recovery route.
    recovered = AccessControl(path, "8642")
    assert recovered.login("8642", "c")
    with pytest.raises(PinRejected):
        recovered.login("975310", "d")


def test_secrets_are_hashed_in_a_private_file_and_never_shown(tmp_path: Path) -> None:
    path = tmp_path / "operator.json"
    access = AccessControl(path, "2468")
    token = access.login("2468", "a")
    access.change_pin("2468", "975310", "a")
    text = path.read_text()
    assert "975310" not in text and "2468" not in text and token not in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "2468" not in repr(replace(make_settings(tmp_path), operator_pin="2468"))


def test_a_malformed_pin_in_protest_env_is_ignored_and_reported(tmp_path: Path) -> None:
    access = AccessControl(tmp_path / "operator.json", "12ab")
    assert not access.pin_set
    assert "4 to 12 digits" in access.problem
