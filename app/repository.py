from __future__ import annotations

import time
from typing import Any

from app.db import get_conn


def insert_message(data: dict[str, Any]) -> int:
    now = time.time()
    payload = {
        "direction": data["direction"],
        "state": data.get("state", "received"),
        "source_hash": data.get("source_hash"),
        "destination_hash": data.get("destination_hash"),
        "title": data.get("title"),
        "content": data.get("content", ""),
        "lxmf_hash": data.get("lxmf_hash"),
        "transport_encryption": data.get("transport_encryption"),
        "ratchet_id": data.get("ratchet_id"),
        "stamp_valid": int(bool(data.get("stamp_valid"))) if data.get("stamp_valid") is not None else None,
        "signature_validated": int(bool(data.get("signature_validated"))) if data.get("signature_validated") is not None else None,
        "created_at": data.get("created_at", now),
        "updated_at": now,
    }
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO messages (
                direction, state, source_hash, destination_hash, title, content, lxmf_hash,
                transport_encryption, ratchet_id, stamp_valid, signature_validated, created_at, updated_at
            ) VALUES (
                :direction, :state, :source_hash, :destination_hash, :title, :content, :lxmf_hash,
                :transport_encryption, :ratchet_id, :stamp_valid, :signature_validated, :created_at, :updated_at
            )
            """,
            payload,
        )
        return int(cursor.lastrowid)


def update_message(message_id: int, **updates: Any) -> None:
    if not updates:
        return
    updates["updated_at"] = time.time()
    assignments = ", ".join(f"{column} = :{column}" for column in updates)
    updates["id"] = message_id
    with get_conn() as conn:
        conn.execute(f"UPDATE messages SET {assignments} WHERE id = :id", updates)


def list_messages(direction: str, limit: int = 100) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE direction = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (direction, limit),
        ).fetchall()
    return [dict(row) for row in rows]
