"""Small, workspace-local event store. Project data is never copied into it."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

SQLITE_HEADER = b"SQLite format 3\x00"
SCHEMA_VERSION = 1


def _database(root: str, create: bool) -> Path:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError("Workspace root must be an existing directory")
    metadata = base / ".flowlens"
    db = metadata / "history.sqlite3"
    if metadata.is_symlink() or db.is_symlink():
        raise ValueError("Refusing to access FlowLens history through a symlink")
    if create:
        metadata.mkdir(exist_ok=True)
        if not metadata.is_dir():
            raise ValueError(".flowlens exists but is not a directory")
        if db.exists() and db.stat().st_size and _header(db) != SQLITE_HEADER:
            raise ValueError("Refusing to overwrite a non-SQLite .flowlens/history.sqlite3 file")
    return db


def record_event(root: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = _database(root, create=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path, timeout=5) as connection:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise ValueError(f"FlowLens history schema version {version} is newer than supported version {SCHEMA_VERSION}")
        connection.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        cursor = connection.execute(
            "INSERT INTO events(kind, created_at, payload) VALUES (?, ?, ?)",
            (kind, timestamp, json.dumps(payload, separators=(",", ":"), default=str)),
        )
        event_id = cursor.lastrowid
    return {"id": event_id, "kind": kind, "created_at": timestamp, "payload": payload}


def list_events(root: str, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
    path = _database(root, create=False)
    if not path.exists():
        return []
    if path.stat().st_size and _header(path) != SQLITE_HEADER:
        raise ValueError("The FlowLens history file is not a valid SQLite database")
    db_uri = f"file:{quote(path.as_posix(), safe='/:')}?mode=ro"
    with sqlite3.connect(db_uri, uri=True, timeout=5) as connection:
        connection.row_factory = sqlite3.Row
        try:
            query = "SELECT id, kind, created_at, payload FROM events"
            params: tuple[Any, ...] = ()
            if kind:
                query += " WHERE kind = ?"
                params = (kind,)
            rows = connection.execute(query + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise
    return [{"id": row["id"], "kind": row["kind"], "created_at": row["created_at"], "payload": json.loads(row["payload"])} for row in rows]


def _header(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(len(SQLITE_HEADER))


def previous_dataset_events(root: str, kind: str, dataset: str, limit: int = 100) -> list[dict[str, Any]]:
    return [event for event in list_events(root, limit=500, kind=kind)
            if event["payload"].get("dataset") == dataset][:limit]
