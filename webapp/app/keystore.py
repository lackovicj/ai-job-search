"""Anthropic API key entered in the web UI, encrypted at rest.

The key lands in the SQLite file under /data, which the LaTeX compiler (running
LLM-written source as the same OS user) could in principle read. So it is stored
encrypted with a key derived from APP_PASSWORD, which exists only in this process's
memory (config.load_secret removes it from the environment and from disk). A copy
of app.db alone does not reveal the API key. Changing APP_PASSWORD makes the stored
key unreadable; the UI then asks for it again.
"""

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import config, db

_cache: dict[str, str] = {}


def _fernet(salt: bytes) -> Fernet:
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(config.APP_PASSWORD.encode())))


def save(api_key: str) -> None:
    salt = os.urandom(16)
    token = _fernet(salt).encrypt(api_key.encode())
    db.set_setting("anthropic_key_salt", base64.b64encode(salt).decode())
    db.set_setting("anthropic_key_enc", token.decode())
    _cache.clear()


def clear() -> None:
    db.set_setting("anthropic_key_enc", "")
    db.set_setting("anthropic_key_salt", "")
    _cache.clear()


def stored() -> str:
    """Decrypted UI key, '' if none, None if present but undecryptable."""
    token = db.get_setting("anthropic_key_enc")
    if not token:
        return ""
    if token in _cache:
        return _cache[token]
    try:
        salt = base64.b64decode(db.get_setting("anthropic_key_salt"))
        value = _fernet(salt).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
    _cache[token] = value
    return value


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
