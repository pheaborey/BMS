"""Registry for independent store databases."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from db import DB_PATH, get_settings, now

STORE_DIR = DB_PATH.parent / "stores"
REGISTRY_PATH = DB_PATH.parent / "stores.sqlite3"


def _connect_registry() -> sqlite3.Connection:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(REGISTRY_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _registry():
    conn = _connect_registry()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_store_registry() -> None:
    with _registry() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS stores ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO stores (id, name, created_at) VALUES ('default', ?, ?)",
            (get_settings().get("company_name", "Main store"), now()),
        )


def get_store_path(store_id: str) -> Path | None:
    if store_id == "default":
        return DB_PATH
    with _registry() as conn:
        row = conn.execute("SELECT id FROM stores WHERE id=?", (store_id,)).fetchone()
    if not row:
        return None
    return STORE_DIR / f"{row['id']}.db"


def list_stores() -> list[dict]:
    with _registry() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM stores ORDER BY created_at, name"
        ).fetchall()
    return [dict(row) for row in rows]


def create_store_record(name: str) -> dict | None:
    store = {"id": uuid.uuid4().hex, "name": name, "created_at": now()}
    try:
        with _registry() as conn:
            conn.execute(
                "INSERT INTO stores (id, name, created_at) VALUES (?, ?, ?)",
                (store["id"], store["name"], store["created_at"]),
            )
    except sqlite3.IntegrityError:
        return None
    return store


def delete_store_record(store_id: str) -> None:
    with _registry() as conn:
        conn.execute("DELETE FROM stores WHERE id=?", (store_id,))
