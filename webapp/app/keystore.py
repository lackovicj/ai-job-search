"""Anthropic API key entered in the web UI, encrypted at rest.

The key lands in the SQLite file under /data, which the LaTeX compiler (running
LLM-written source as the same OS user) could in principle read. So it is stored
encrypted under a key derived from the login password. The password is never
stored, only its scrypt hash with a different salt, so the derived key exists only
in memory: login unlocks it, a restart forgets it until the next login. A copy of
app.db alone does not reveal the API key.
"""

import base64
import hashlib
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

from . import config, db

_lock = threading.Lock()
_fernet: Fernet | None = None


def _derive(password: str, salt: bytes) -> Fernet:
    raw = hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1,
                         maxmem=64 * 1024 * 1024, dklen=32)
    return Fernet(base64.urlsafe_b64encode(raw))


def _salt() -> bytes:
    stored = db.get_setting("anthropic_key_salt")
    if stored:
        return base64.b64decode(stored)
    salt = os.urandom(16)
    db.set_setting("anthropic_key_salt", base64.b64encode(salt).decode())
    return salt


def unlock(password: str) -> None:
    global _fernet
    fernet = _derive(password, _salt())
    with _lock:
        _fernet = fernet


def rekey(new_password: str) -> None:
    """Password change: re-encrypt the stored key under the new password."""
    global _fernet
    current = stored()
    salt = os.urandom(16)
    db.set_setting("anthropic_key_salt", base64.b64encode(salt).decode())
    fernet = _derive(new_password, salt)
    with _lock:
        _fernet = fernet
    db.set_setting("anthropic_key_enc", fernet.encrypt(current.encode()).decode() if current else "")


def save(api_key: str) -> None:
    with _lock:
        if _fernet is None:
            raise RuntimeError("Key store is locked; log in again.")
        db.set_setting("anthropic_key_enc", _fernet.encrypt(api_key.encode()).decode())


def clear() -> None:
    db.set_setting("anthropic_key_enc", "")


def stored() -> str | None:
    """Decrypted UI key, '' if none, None if present but locked or undecryptable."""
    token = db.get_setting("anthropic_key_enc")
    if not token:
        return ""
    with _lock:
        fernet = _fernet
    if fernet is None:
        return None
    try:
        return fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def active() -> str:
    """The key to use: the one saved in the UI, else ANTHROPIC_API_KEY from the environment."""
    return stored() or config.ANTHROPIC_API_KEY


def status() -> dict:
    ui = stored()
    if ui:
        return {"source": "ui", "hint": f"…{ui[-4:]}"}
    if ui is None:
        return {"source": "unreadable", "hint": ""}
    if config.ANTHROPIC_API_KEY:
        return {"source": "env", "hint": f"…{config.ANTHROPIC_API_KEY[-4:]}"}
    return {"source": "none", "hint": ""}
