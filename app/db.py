from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


DB_PATH = Path("/data/app/messages.db")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,
                state TEXT NOT NULL,
                source_hash TEXT,
                destination_hash TEXT,
                title TEXT,
                content TEXT NOT NULL,
                lxmf_hash TEXT,
                transport_encryption TEXT,
                ratchet_id TEXT,
                stamp_valid INTEGER,
                signature_validated INTEGER,
                retry_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at REAL,
                last_attempt_at REAL,
                last_error TEXT,
                attachments_json TEXT,
                attachment_count INTEGER NOT NULL DEFAULT 0,
                attachment_bytes INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        _ensure_column(conn, "messages", "retry_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "messages", "next_retry_at", "REAL")
        _ensure_column(conn, "messages", "last_attempt_at", "REAL")
        _ensure_column(conn, "messages", "last_error", "TEXT")
        _ensure_column(conn, "messages", "attachments_json", "TEXT")
        _ensure_column(conn, "messages", "attachment_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "messages", "attachment_bytes", "INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = {row[1] for row in rows}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
