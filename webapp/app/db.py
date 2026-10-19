"""SQLite storage: the application tracker plus the editable profile documents."""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

DB_PATH = config.DATA_DIR / "app.db"
_lock = threading.Lock()

# Tracker status vocabulary, same spellings as /outcome (.claude/commands/outcome.md).
STATUSES = ["drafted", "applied", "interview", "offer", "hired",
            "rejected", "no_response", "offer_declined", "withdrawn"]
FINAL_STATUSES = {"hired", "rejected", "no_response", "offer_declined", "withdrawn"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    company TEXT DEFAULT '',
    role TEXT DEFAULT '',
    location TEXT DEFAULT '',
    sector TEXT DEFAULT '',
    contact_person TEXT DEFAULT '',
    posting_language TEXT DEFAULT '',
    deadline TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    host_class TEXT DEFAULT '',
    posting TEXT DEFAULT '',
    guard_verdict TEXT DEFAULT '',
    guard_findings TEXT DEFAULT '{}',
    evaluation TEXT DEFAULT '',
    fit_score INTEGER,
    fit_verdict TEXT DEFAULT '',
    stage TEXT DEFAULT 'queued',
    busy INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    status TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    review TEXT DEFAULT '',
    decisions TEXT DEFAULT '[]',
    checks TEXT DEFAULT '{}',
    log TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

JSON_FIELDS = {"guard_findings", "evaluation", "decisions", "checks"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)
        # A crash or redeploy mid-pipeline leaves busy=1 forever; clear it on boot.
        conn.execute("UPDATE applications SET busy=0, error=CASE WHEN error='' "
                     "THEN 'Interrupted by a restart - run the step again.' ELSE error END "
                     "WHERE busy=1")


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    for field in JSON_FIELDS:
        raw = item.get(field) or ""
        try:
            item[field] = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            item[field] = None
    return item


def create_application(**fields) -> int:
    fields.setdefault("created", now())
    fields["updated"] = now()
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with _lock, connect() as conn:
        cur = conn.execute(f"INSERT INTO applications ({cols}) VALUES ({marks})",
                           [_encode(k, v) for k, v in fields.items()])
        return cur.lastrowid


def _encode(key, value):
    if key in JSON_FIELDS and not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return value


def update_application(app_id: int, **fields) -> None:
    fields["updated"] = now()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _lock, connect() as conn:
        conn.execute(f"UPDATE applications SET {sets} WHERE id=?",
                     [_encode(k, v) for k, v in fields.items()] + [app_id])


def append_log(app_id: int, line: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    with _lock, connect() as conn:
        conn.execute("UPDATE applications SET log = log || ?, updated=? WHERE id=?",
                     (f"[{stamp}] {line}\n", now(), app_id))


def get_application(app_id: int) -> dict | None:
    with connect() as conn:
        return _decode(conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())


def list_applications() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM applications ORDER BY id DESC").fetchall()
    return [_decode(r) for r in rows]


def delete_application(app_id: int) -> None:
    with _lock, connect() as conn:
        conn.execute("DELETE FROM applications WHERE id=?", (app_id,))


def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _lock, connect() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
