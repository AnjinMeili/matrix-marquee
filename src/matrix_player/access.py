"""The operator PIN: who may change the sign's settings and, later, approve live input.

Whoever looks after the sign sets the PIN in /etc/protest/protest.env
(``MATRIX_PLAYER_OPERATOR_PIN``); someone who knows it can change it from
Settings. Editing protest.env always wins over a PIN changed in Settings,
which is the way back from a forgotten PIN. Until a PIN is set, settings stay
open as before and Settings says so.

A phone that enters the PIN is trusted for 12 hours by an HttpOnly cookie.
The PIN's hash and the trusted phones' tokens (hashed) live in their own file,
mode 0600, never in the database, so backups never carry them.
"""

from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

PIN_PATTERN = re.compile(r"^[0-9]{4,12}$")
TRUST_SECONDS = 12 * 3600
ITERATIONS = 120_000
MAX_TRIES = 5  # wrong PINs from one address before it must wait
GLOBAL_TRIES = 20  # wrong PINs from anywhere before everyone must wait
LOCK_SECONDS = 300
COOKIE = "matrixmarquee_operator"


class LockedOut(Exception):
    def __init__(self, seconds: float) -> None:
        super().__init__("too many wrong tries")
        self.seconds = seconds


class PinRejected(ValueError):
    pass


@dataclass(frozen=True)
class Status:
    pin_set: bool
    trusted_until: Optional[float]
    problem: Optional[str]


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _digest(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()


class AccessControl:
    def __init__(self, path: Path, env_pin: Optional[str], clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.env_pin = (env_pin or "").strip() or None
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: Dict[str, List[float]] = {}
        self._all_failures: List[float] = []
        self._state = self._read()
        if self.env_pin and not PIN_PATTERN.match(self.env_pin):
            logger.error("operator_pin_invalid")

    # ----- the PIN -----

    @property
    def problem(self) -> Optional[str]:
        if self.env_pin and not PIN_PATTERN.match(self.env_pin):
            return "The PIN in protest.env must be 4 to 12 digits, so it is being ignored."
        return None

    def _env(self) -> Optional[str]:
        return self.env_pin if self.env_pin and PIN_PATTERN.match(self.env_pin) else None

    def _stored(self) -> Optional[dict]:
        """The PIN set from Settings, unless protest.env has changed since, which wins."""
        stored = self._state.get("pin")
        if not isinstance(stored, dict):
            return None
        env = self._env()
        if env is not None and stored.get("env_marker") != _digest(env, stored["salt"]):
            return None
        return stored

    @property
    def pin_set(self) -> bool:
        return self._stored() is not None or self._env() is not None

    def _matches(self, pin: str) -> bool:
        if not PIN_PATTERN.match(pin or ""):
            return False
        stored = self._stored()
        if stored is not None:
            return hmac.compare_digest(_digest(pin, stored["salt"]), stored["hash"])
        env = self._env()
        return env is not None and hmac.compare_digest(pin, env)

    # ----- trusted phones -----

    def status(self, token: Optional[str]) -> Status:
        return Status(self.pin_set, self.trusted_until(token), self.problem)

    def trusted_until(self, token: Optional[str]) -> Optional[float]:
        if not token:
            return None
        with self._lock:
            expires = self._state.get("tokens", {}).get(_token_key(token))
        return expires if isinstance(expires, (int, float)) and expires > self.clock() else None

    def allows(self, token: Optional[str]) -> bool:
        """Settings are open until a PIN is set; after that only trusted phones may change them."""
        return not self.pin_set or self.trusted_until(token) is not None

    def login(self, pin: str, client: str) -> str:
        """Returns a token for a cookie, or raises PinRejected or LockedOut."""
        self._check_lock(client)
        if not self._matches(pin):
            self._fail(client)
            raise PinRejected("wrong PIN")
        logger.info("operator_login_succeeded", extra={"client": client})
        return self._issue()

    def logout(self, token: Optional[str]) -> None:
        if not token:
            return
        with self._lock:
            removed = self._state.get("tokens", {}).pop(_token_key(token), None)
            if removed is not None:
                self._write()
        logger.info("operator_logout")

    def change_pin(self, current: str, new: str, client: str) -> str:
        """Sets a new PIN, logs every phone out, and returns a token for the phone that changed it."""
        self._check_lock(client)
        if not self._matches(current):
            self._fail(client)
            raise PinRejected("wrong PIN")
        if not PIN_PATTERN.match(new or ""):
            raise ValueError("The new PIN must be 4 to 12 digits.")
        salt = secrets.token_hex(16)
        env = self._env()
        with self._lock:
            self._state["pin"] = {
                "salt": salt,
                "hash": _digest(new, salt),
                # Remembers which protest.env PIN this replaced, so editing protest.env later still wins.
                "env_marker": _digest(env, salt) if env else None,
            }
            self._state["tokens"] = {}
        logger.info("operator_pin_changed", extra={"client": client})
        return self._issue()

    # ----- internals -----

    def _issue(self) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self._lock:
            tokens = {
                key: expires for key, expires in self._state.get("tokens", {}).items()
                if isinstance(expires, (int, float)) and expires > now
            }
            tokens[_token_key(token)] = now + TRUST_SECONDS
            self._state["tokens"] = tokens
            self._write()
        return token

    def _check_lock(self, client: str) -> None:
        now = self.clock()
        with self._lock:
            window = now - LOCK_SECONDS
            self._all_failures = [at for at in self._all_failures if at > window]
            mine = [at for at in self._failures.get(client, []) if at > window]
            self._failures[client] = mine
            for failures, limit in ((mine, MAX_TRIES), (self._all_failures, GLOBAL_TRIES)):
                if len(failures) >= limit:
                    raise LockedOut(failures[-limit] + LOCK_SECONDS - now)

    def _fail(self, client: str) -> None:
        now = self.clock()
        with self._lock:
            self._failures.setdefault(client, []).append(now)
            self._all_failures.append(now)
        logger.warning("operator_login_failed", extra={"client": client})

    def _read(self) -> dict:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("operator_state_unreadable")
            return {}

    def _write(self) -> None:
        """Atomically replaces the file, readable by the service only."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(self.path.parent), prefix=".operator-")
        try:
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(self._state, file)
            os.replace(temporary, self.path)
        except OSError:
            logger.exception("operator_state_write_failed")
            try:
                os.unlink(temporary)
            except OSError:
                pass
