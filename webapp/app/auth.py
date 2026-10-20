"""Single-user login: account created in the browser, cookie sessions, setup code.

First boot has no account. Anyone who reaches the URL first could claim it, and new
domains are probed by bots within minutes of their certificate appearing in public
CT logs, so account creation needs a one-time setup code that only the operator can
see: it is printed to the container log (Coolify -> Logs) on every boot until an
account exists.
"""

import hashlib
import hmac
import secrets
import threading
import time

from . import db

SESSION_COOKIE = "jobsearch_session"
SESSION_SECONDS = 14 * 24 * 3600
MIN_PASSWORD = 10
# Global, not per-IP: behind Coolify's proxy the client address is either the
# proxy's or a spoofable header. One user, so a global cap costs nothing real.
MAX_FAILURES, FAILURE_WINDOW = 10, 15 * 60

_lock = threading.Lock()
_sessions: dict[str, float] = {}
_failures: list[float] = []
setup_code = ""


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1,
                          maxmem=64 * 1024 * 1024, dklen=32).hex()


def has_account() -> bool:
    return bool(db.get_setting("auth_hash"))


def username() -> str:
    return db.get_setting("auth_user")


def init_setup_code() -> None:
    """Called at startup; prints a fresh code while no account exists."""
    global setup_code
    if has_account():
        setup_code = ""
        return
    setup_code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    print("=" * 64, flush=True)
    print(f"  AI Job Search setup code: {setup_code}", flush=True)
    print("  Open the app in your browser and enter it to create your login.", flush=True)
    print("=" * 64, flush=True)


def check_new_password(password: str, confirm: str) -> str:
    if password != confirm:
        return "The passwords do not match."
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    return ""


def set_password(user: str, password: str) -> None:
    salt = secrets.token_bytes(16)
    db.set_setting("auth_user", user)
    db.set_setting("auth_salt", salt.hex())
    db.set_setting("auth_hash", _hash(password, salt))


def create_account(code: str, user: str, password: str) -> bool:
    global setup_code
    with _lock:
        if has_account() or not setup_code or not hmac.compare_digest(
                code.strip().upper().encode(), setup_code.encode()):
            return False
        set_password(user, password)
        setup_code = ""
        return True


def locked_out() -> bool:
    now = time.time()
    with _lock:
        _failures[:] = [t for t in _failures if now - t < FAILURE_WINDOW]
        return len(_failures) >= MAX_FAILURES


def verify(user: str, password: str) -> bool:
    if not has_account() or locked_out():
        return False
    salt = bytes.fromhex(db.get_setting("auth_salt"))
    ok = (hmac.compare_digest(user.encode(), username().encode())
          & hmac.compare_digest(_hash(password, salt), db.get_setting("auth_hash")))
    if not ok:
        with _lock:
            _failures.append(time.time())
    return ok


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        _sessions[token] = time.time() + SESSION_SECONDS
    return token


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    with _lock:
        expiry = _sessions.get(token)
        if expiry is None:
            return False
        if expiry < time.time():
            del _sessions[token]
            return False
        return True


def end_session(token: str | None) -> None:
    with _lock:
        _sessions.pop(token or "", None)


def end_other_sessions(keep: str | None) -> None:
    with _lock:
        for token in [t for t in _sessions if t != keep]:
            del _sessions[token]


def reset_account() -> None:
    """Forgotten password: drop the login and the API key encrypted with it."""
    for key in ("auth_user", "auth_salt", "auth_hash",
                "anthropic_key_enc", "anthropic_key_salt"):
        db.set_setting(key, "")
    with _lock:
        _sessions.clear()
