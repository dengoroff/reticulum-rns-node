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
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
