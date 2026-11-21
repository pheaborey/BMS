"""Create and restore portable, validated SQLite backups."""

from __future__ import annotations

import base64
import json
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

FORMAT = "meridian-bms-backup"
VERSION = 1
MAX_ARCHIVE_BYTES = 250 * 1024 * 1024
MAX_UNPACKED_BYTES = 512 * 1024 * 1024


class BackupError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def database_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _encode(value):
    if isinstance(value, bytes):
        return {"$blob": base64.b64encode(value).decode("ascii")}
    return value


def _decode(value):
    if isinstance(value, dict) and set(value) == {"$blob"}:
        try:
            return base64.b64decode(value["$blob"], validate=True)
        except (ValueError, TypeError):
            raise BackupError("invalid") from None
    return value


def _invalidate_sessions(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('auth_generation', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (secrets.token_urlsafe(24),),
    )


def create_backup(
    conn: sqlite3.Connection,
    output_path: Path,
    *,
    selected_tables: list[str] | None,
    store_id: str,
    store_name: str,
) -> list[str]:
    available = database_tables(conn)
    if selected_tables is None:
        mode = "full"
        tables = available
    else:
        if not selected_tables or len(set(selected_tables)) != len(selected_tables):
            raise BackupError("select_tables")
        if any(table not in available for table in selected_tables):
            raise BackupError("invalid")
        mode = "tables"
        tables = sorted(selected_tables)

    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "store_id": store_id,
        "store_name": store_name,
        "tables": tables,
        "schema": {
            table: _columns(conn, table)
            for table in available
        },
    }

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        if mode == "full":
            with tempfile.TemporaryDirectory(prefix="meridian-backup-") as temp_dir:
                snapshot_path = Path(temp_dir) / "database.sqlite3"
                snapshot = sqlite3.connect(snapshot_path)
                try:
                    conn.backup(snapshot)
                finally:
                    snapshot.close()
                archive.write(snapshot_path, "database.sqlite3")
        else:
            for index, table in enumerate(tables):
                columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")]
                rows = conn.execute(f"SELECT * FROM {_quote(table)}").fetchall()
                payload = {
                    "columns": columns,
                    "rows": [[_encode(value) for value in row] for row in rows],
                }
                archive.writestr(
                    f"tables/table-{index:04d}.json",
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                )
    return tables


def _read_manifest(archive: zipfile.ZipFile) -> tuple[dict, list[str]]:
    entries = archive.infolist()
    if len(entries) > 102:
        raise BackupError("invalid")
    if sum(entry.file_size for entry in entries) > MAX_UNPACKED_BYTES:
        raise BackupError("too_large")
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)) or any(
        name.startswith("/") or "\\" in name or ".." in name.split("/") for name in names
    ):
        raise BackupError("invalid")
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        raise BackupError("invalid") from None
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != FORMAT
        or manifest.get("version") != VERSION
        or manifest.get("mode") not in ("full", "tables")
        or not isinstance(manifest.get("store_id"), str)
        or not isinstance(manifest.get("tables"), list)
        or not all(isinstance(table, str) for table in manifest["tables"])
        or len(manifest["tables"]) != len(set(manifest["tables"]))
    ):
        raise BackupError("invalid")
    schema = manifest.get("schema")
    if schema is not None and (
        not isinstance(schema, dict)
        or not schema
        or not all(
            isinstance(table, str)
            and isinstance(columns, list)
            and columns
            and all(isinstance(column, str) for column in columns)
            and len(columns) == len(set(columns))
            for table, columns in schema.items()
        )
    ):
        raise BackupError("invalid")
    tables = manifest["tables"]
    expected = {"manifest.json"}
    if manifest["mode"] == "full":
        expected.add("database.sqlite3")
    else:
        if not tables:
            raise BackupError("invalid")
        expected.update(f"tables/table-{index:04d}.json" for index in range(len(tables)))
    if set(names) != expected:
        raise BackupError("invalid")
    return manifest, tables


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")]


def _columns_match(table: str, supplied: list[str], current: list[str]) -> bool:
    if supplied == current:
        return True
    legacy_products = [
        column for column in current if column not in {"image_data", "image_mime"}
    ]
    return (
        table == "products"
        and {"image_data", "image_mime"} <= set(current)
        and supplied == legacy_products
    )


def _schema_matches(conn: sqlite3.Connection, schema: dict, tables: set[str]) -> bool:
    return set(schema) == tables and all(
        _columns_match(table, schema[table], _columns(conn, table))
        for table in tables
    )


def _replace_tables(conn: sqlite3.Connection, tables: list[str], records: dict[str, tuple[list[str], list]]) -> None:
    current_tables = set(database_tables(conn))
    if any(table not in current_tables for table in tables):
        raise BackupError("schema")
    for table in tables:
        columns, rows = records[table]
        if (
            not columns
            or not all(isinstance(column, str) for column in columns)
            or len(columns) != len(set(columns))
            or not _columns_match(table, columns, _columns(conn, table))
            or not isinstance(rows, list)
        ):
            raise BackupError("schema")
        if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
            raise BackupError("invalid")

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in tables:
            conn.execute(f"DELETE FROM {_quote(table)}")
        for table in tables:
            columns, rows = records[table]
            quoted_columns = ", ".join(_quote(column) for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            sql = f"INSERT INTO {_quote(table)} ({quoted_columns}) VALUES ({placeholders})"
            conn.executemany(sql, [[_decode(value) for value in row] for row in rows])
        if "users" in tables:
            active_admins = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"
            ).fetchone()[0]
            if not active_admins:
                raise BackupError("admin_required")
        _invalidate_sessions(conn)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise BackupError("related_tables")
        conn.execute("COMMIT")
    except BackupError:
        conn.execute("ROLLBACK")
        raise
    except sqlite3.DatabaseError:
        conn.execute("ROLLBACK")
        raise BackupError("invalid_data") from None
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _replace_tables_from_database(
    conn: sqlite3.Connection,
    source: sqlite3.Connection,
    tables: list[str],
) -> None:
    for table in tables:
        if not _columns_match(table, _columns(source, table), _columns(conn, table)):
            raise BackupError("schema")

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in tables:
            conn.execute(f"DELETE FROM {_quote(table)}")
        for table in tables:
            columns = _columns(source, table)
            quoted_columns = ", ".join(_quote(column) for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            sql = f"INSERT INTO {_quote(table)} ({quoted_columns}) VALUES ({placeholders})"
            cursor = source.execute(f"SELECT * FROM {_quote(table)}")
            while rows := cursor.fetchmany(500):
                conn.executemany(sql, rows)
        if "users" in tables:
            active_admins = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"
            ).fetchone()[0]
            if not active_admins:
                raise BackupError("admin_required")
        _invalidate_sessions(conn)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise BackupError("related_tables")
        conn.execute("COMMIT")
    except BackupError:
        conn.execute("ROLLBACK")
        raise
    except sqlite3.DatabaseError:
        conn.execute("ROLLBACK")
        raise BackupError("invalid_data") from None
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def restore_backup(
    conn: sqlite3.Connection,
    source: BinaryIO,
) -> tuple[str, list[str]]:
    try:
        source.seek(0, 2)
        archive_size = source.tell()
        source.seek(0)
        if archive_size > MAX_ARCHIVE_BYTES:
            raise BackupError("too_large")
        with zipfile.ZipFile(source) as archive:
            manifest, tables = _read_manifest(archive)
            schema = manifest.get("schema")
            if schema is not None:
                current_tables = database_tables(conn)
                if not _schema_matches(conn, schema, set(current_tables)):
                    raise BackupError("schema")
            if manifest["mode"] == "tables":
                records = {}
                for index, table in enumerate(tables):
                    try:
                        payload = json.loads(archive.read(f"tables/table-{index:04d}.json"))
                    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
                        raise BackupError("invalid") from None
                    if not isinstance(payload, dict) or not isinstance(payload.get("columns"), list):
                        raise BackupError("invalid")
                    records[table] = (payload["columns"], payload.get("rows"))
                _replace_tables(conn, tables, records)
            else:
                with tempfile.TemporaryDirectory(prefix="meridian-restore-") as temp_dir:
                    database_path = Path(temp_dir) / "database.sqlite3"
                    with archive.open("database.sqlite3") as backup_file, database_path.open("wb") as target:
                        shutil.copyfileobj(backup_file, target)
                    source_conn = sqlite3.connect(database_path)
                    source_conn.row_factory = sqlite3.Row
                    try:
                        if source_conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise BackupError("invalid")
                        if source_conn.execute("PRAGMA foreign_key_check").fetchone():
                            raise BackupError("invalid_data")
                        source_tables = database_tables(source_conn)
                        if set(source_tables) != set(database_tables(conn)) or set(tables) != set(source_tables):
                            raise BackupError("schema")
                        if schema is not None and any(
                            schema[table] != _columns(source_conn, table)
                            for table in source_tables
                        ):
                            raise BackupError("schema")
                        _replace_tables_from_database(conn, source_conn, tables)
                    finally:
                        source_conn.close()
            return manifest["mode"], tables
    except BackupError:
        raise
    except (zipfile.BadZipFile, OSError, sqlite3.DatabaseError):
        raise BackupError("invalid") from None
