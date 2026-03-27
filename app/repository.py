from __future__ import annotations

import json
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
        "retry_count": data.get("retry_count", 0),
        "next_retry_at": data.get("next_retry_at"),
        "last_attempt_at": data.get("last_attempt_at"),
        "last_error": data.get("last_error"),
        "attachments_json": json.dumps(data.get("attachments", [])),
        "attachment_count": data.get("attachment_count", len(data.get("attachments", []))),
        "attachment_bytes": data.get("attachment_bytes", _attachment_bytes(data.get("attachments", []))),
        "created_at": data.get("created_at", now),
        "updated_at": now,
    }
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO messages (
                direction, state, source_hash, destination_hash, title, content, lxmf_hash,
                transport_encryption, ratchet_id, stamp_valid, signature_validated,
                retry_count, next_retry_at, last_attempt_at, last_error,
                attachments_json, attachment_count, attachment_bytes,
                created_at, updated_at
            ) VALUES (
                :direction, :state, :source_hash, :destination_hash, :title, :content, :lxmf_hash,
                :transport_encryption, :ratchet_id, :stamp_valid, :signature_validated,
                :retry_count, :next_retry_at, :last_attempt_at, :last_error,
                :attachments_json, :attachment_count, :attachment_bytes,
                :created_at, :updated_at
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


def list_messages(direction: str, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE direction = ?
            ORDER BY created_at DESC
            LIMIT ?
            OFFSET ?
            """,
            (direction, limit, offset),
        ).fetchall()
    return [_decode_message(row) for row in rows]


def count_messages(direction: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM messages
            WHERE direction = ?
            """,
            (direction,),
        ).fetchone()
    return int(row["count"]) if row else 0


def get_message(message_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM messages
            WHERE id = ?
            """,
            (message_id,),
        ).fetchone()
    return _decode_message(row) if row else None


def delete_message(message_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))


def pop_next_outbound_message() -> dict[str, Any] | None:
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM messages
            WHERE direction = 'outbox'
              AND state IN ('outbound', 'retry_wait')
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY COALESCE(next_retry_at, created_at) ASC, id ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if row is None:
            return None
        message_id = row["id"]
        updated_at = time.time()
        claimed = conn.execute(
            """
            UPDATE messages
            SET state = 'sending',
                last_attempt_at = ?,
                updated_at = ?
            WHERE id = ?
              AND state IN ('outbound', 'retry_wait')
            """,
            (updated_at, updated_at, message_id),
        )
        if claimed.rowcount == 0:
            return None
        fresh = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return _decode_message(fresh) if fresh else None


def list_retryable_messages(limit: int = 100) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE direction = 'outbox'
              AND state IN ('retry_wait', 'failed')
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _decode_message(row) -> dict[str, Any]:
    payload = dict(row)
    attachments_raw = payload.get("attachments_json")
    try:
        payload["attachments"] = json.loads(attachments_raw) if attachments_raw else []
    except json.JSONDecodeError:
        payload["attachments"] = []
    return payload


def _attachment_bytes(attachments: list[dict[str, Any]] | None) -> int:
    if not attachments:
        return 0
    return sum(int(item.get("size", 0) or 0) for item in attachments)
