from __future__ import annotations

import base64
import os
import threading
import time
from pathlib import Path
from typing import Any

import LXMF
import RNS

from app.peer_health import load_candidate_peers, refresh_peer_health
from app import repository


class LXMFService:
    RETRY_DELAYS = (10, 30, 60, 300, 900)
    ATTACHMENT_FIELD_KEY = "attachments"

    def __init__(self) -> None:
        self.data_path = Path(os.environ.get("APP_DATA_DIR", "/data/app"))
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.display_name = os.environ.get("LXMF_DISPLAY_NAME", "Web Node")
        self.announce_interval = int(os.environ.get("LXMF_ANNOUNCE_INTERVAL", "3600"))
        self.reticulum: RNS.Reticulum | None = None
        self.router: LXMF.LXMRouter | None = None
        self.identity: RNS.Identity | None = None
        self.destination = None
        self.started_at = time.time()
        self.lock = threading.Lock()
        self.announce_thread: threading.Thread | None = None
        self.startup_announce_thread: threading.Thread | None = None
        self.peer_monitor_thread: threading.Thread | None = None
        self.outbound_worker_thread: threading.Thread | None = None

    def start(self) -> None:
        with self.lock:
            if self.router is not None:
                return
            self._log(
                f"Starting LXMF service with config dir {os.environ.get('RNS_CONFIG_DIR')} and data dir {self.data_path}"
            )
            self.reticulum = RNS.Reticulum(configdir=os.environ.get("RNS_CONFIG_DIR"))
            self._log("Reticulum instance initialised")
            self.router = LXMF.LXMRouter(storagepath=str(self.data_path))
            self._log(f"LXMRouter initialised with storage path {self.data_path}")
            self.identity = self._load_or_create_identity()
            self.destination = self.router.register_delivery_identity(
                self.identity,
                display_name=self.display_name,
                stamp_cost=8,
            )
            self.router.register_delivery_callback(self._on_delivery)
            self._log(
                f"Registered LXMF delivery identity {self.address} with display name '{self.display_name}'"
            )
            self._emit_announce("initial")
            self.announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
            self.announce_thread.start()
            self.startup_announce_thread = threading.Thread(target=self._startup_announce_loop, daemon=True)
            self.startup_announce_thread.start()
            self.peer_monitor_thread = threading.Thread(target=self._peer_monitor_loop, daemon=True)
            self.peer_monitor_thread.start()
            self.outbound_worker_thread = threading.Thread(target=self._outbound_worker_loop, daemon=True)
            self.outbound_worker_thread.start()

    def _load_or_create_identity(self) -> RNS.Identity:
        identity_path = self.data_path / "web-ui.identity"
        if identity_path.exists():
            self._log(f"Loading existing identity from {identity_path}")
            return RNS.Identity.from_file(str(identity_path))
        self._log(f"Creating new identity at {identity_path}")
        identity = RNS.Identity()
        identity.to_file(str(identity_path))
        return identity

    def _announce_loop(self) -> None:
        while True:
            time.sleep(max(self.announce_interval, 300))
            try:
                if self.router and self.destination:
                    self._emit_announce(f"periodic interval={self.announce_interval}s")
            except Exception:
                RNS.log("Failed to announce LXMF destination", RNS.LOG_ERROR)

    def _startup_announce_loop(self) -> None:
        elapsed = 0
        for delay in (15, 60):
            time.sleep(delay - elapsed)
            elapsed = delay
            try:
                if self.router and self.destination:
                    self._emit_announce(f"startup-delay {delay}s")
            except Exception:
                self._log(f"Failed delayed announce after {delay}s", level=RNS.LOG_ERROR)

    def _peer_monitor_loop(self) -> None:
        while True:
            try:
                health = refresh_peer_health(load_candidate_peers(os.environ.get("RNS_PEERS")), timeout=3.0)
                healthy = sum(1 for item in health if item["tcp_ok"])
                self._log(f"Peer health monitor updated: {healthy}/{len(health)} peers reachable")
            except Exception as exc:
                self._log(f"Peer health monitor failed: {exc}", level=RNS.LOG_ERROR)
            time.sleep(300)

    def _outbound_worker_loop(self) -> None:
        while True:
            try:
                item = repository.pop_next_outbound_message()
                if item is None:
                    time.sleep(2)
                    continue
                self._process_outbound_message(item)
            except Exception as exc:
                self._log(f"Outbound worker crashed on iteration: {exc}", level=RNS.LOG_ERROR)
                time.sleep(2)

    def _on_delivery(self, message: LXMF.LXMessage) -> None:
        content = self._as_string(message.content_as_string)
        attachments = self._extract_attachments(message)
        self._log(
            "Inbound LXMF received "
            f"from={self._pretty_hex(message.source_hash)} "
            f"to={self._pretty_hex(message.destination_hash)} "
            f"hash={self._pretty_hex(getattr(message, 'hash', None))}"
        )
        repository.insert_message(
            {
                "direction": "inbox",
                "state": "received",
                "source_hash": self._pretty_hex(message.source_hash),
                "destination_hash": self._pretty_hex(message.destination_hash),
                "title": self._as_string(message.title_as_string),
                "content": content,
                "lxmf_hash": self._pretty_hex(getattr(message, "hash", None)),
                "transport_encryption": str(getattr(message, "transport_encryption", "")),
                "ratchet_id": self._pretty_hex(getattr(message, "ratchet_id", None)),
                "stamp_valid": getattr(message, "stamp_valid", None),
                "signature_validated": getattr(message, "signature_validated", None),
                "attachments": attachments,
                "attachment_count": len(attachments),
                "attachment_bytes": self._attachment_bytes(attachments),
                "created_at": getattr(message, "timestamp", time.time()),
            }
        )

    def send_message(
        self,
        destination_hex: str,
        content: str,
        title: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> int:
        if not self.router or not self.destination:
            raise RuntimeError("LXMF service is not started")

        normalized_destination = destination_hex.strip().lower()
        try:
            bytes.fromhex(normalized_destination)
        except ValueError as exc:
            raise ValueError("Destination address must be a valid hex string.") from exc

        record_id = repository.insert_message(
            {
                "direction": "outbox",
                "state": "outbound",
                "source_hash": self.address,
                "destination_hash": normalized_destination,
                "title": title,
                "content": content,
                "attachments": attachments or [],
                "attachment_count": len(attachments or []),
                "attachment_bytes": self._attachment_bytes(attachments or []),
                "next_retry_at": time.time(),
                "created_at": time.time(),
            }
        )
        self._log(
            f"Outbound request queued id={record_id} destination={normalized_destination} "
            f"title={title!r} bytes={len(content.encode('utf-8'))} attachments={len(attachments or [])}"
        )
        return record_id

    @property
    def address(self) -> str:
        if not self.destination:
            return ""
        return self._pretty_hex(self.destination.hash)

    def stats(self) -> dict[str, Any]:
        configured_peers = self._configured_peers()
        return {
            "address": self.address,
            "display_name": self.display_name,
            "uptime_seconds": int(time.time() - self.started_at),
            "transport_enabled": True,
            "configured_peers": configured_peers,
            "known_paths": self._transport_count("path_table"),
            "announces": self._transport_count("announce_table"),
        }

    def _transport_count(self, name: str) -> int | None:
        transport = getattr(RNS, "Transport", None)
        if transport is None:
            return None
        table = getattr(transport, name, None)
        try:
            return len(table) if table is not None else None
        except Exception:
            return None

    @staticmethod
    def _pretty_hex(value: bytes | None) -> str | None:
        if value is None:
            return None
        return value.hex()

    @staticmethod
    def _as_string(accessor) -> str:
        try:
            return str(accessor())
        except Exception:
            return ""

    def _deliver_to_self(
        self,
        record_id: int,
        content: str,
        title: str | None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> int:
        if not self.destination:
            raise RuntimeError("LXMF destination is not ready")

        message = LXMF.LXMessage(
            self.destination,
            self.destination,
            content,
            title,
            desired_method=LXMF.LXMessage.DIRECT,
        )
        if attachments:
            message.set_fields({self.ATTACHMENT_FIELD_KEY: attachments})
        message.pack()
        repository.insert_message(
            {
                "direction": "inbox",
                "state": "received",
                "source_hash": self.address,
                "destination_hash": self.address,
                "title": title,
                "content": content,
                "lxmf_hash": self._pretty_hex(message.hash),
                "transport_encryption": "local",
                "signature_validated": True,
                "stamp_valid": True,
                "attachments": attachments or [],
                "attachment_count": len(attachments or []),
                "attachment_bytes": self._attachment_bytes(attachments or []),
                "created_at": time.time(),
            }
        )
        repository.update_message(
            record_id,
            state="delivered",
            lxmf_hash=self._pretty_hex(message.hash),
            transport_encryption="local",
            next_retry_at=None,
            last_error=None,
        )
        self._log(
            f"Outbound id={record_id} delivered locally to self hash={self._pretty_hex(message.hash)}"
        )
        return record_id

    def _on_outbound_state(self, record_id: int, message: LXMF.LXMessage) -> None:
        state_name = self._state_name(message.state)
        repository.update_message(
            record_id,
            state=state_name,
            lxmf_hash=self._pretty_hex(getattr(message, "hash", None)),
            transport_encryption=str(getattr(message, "transport_encryption", "")),
            ratchet_id=self._pretty_hex(getattr(message, "ratchet_id", None)),
            next_retry_at=None,
            last_error=None,
        )
        self._log(
            f"Outbound id={record_id} delivery callback state={state_name} "
            f"hash={self._pretty_hex(getattr(message, 'hash', None))}"
        )

    def _on_outbound_failure(self, record_id: int, message: LXMF.LXMessage) -> None:
        self._schedule_retry(
            record_id,
            f"Delivery callback failure for {self._pretty_hex(getattr(message, 'hash', None)) or 'unknown message'}",
            lxmf_hash=self._pretty_hex(getattr(message, "hash", None)),
            transport_encryption=str(getattr(message, "transport_encryption", "")),
            ratchet_id=self._pretty_hex(getattr(message, "ratchet_id", None)),
        )
        self._log(
            f"Outbound id={record_id} failure callback state={self._state_name(getattr(message, 'state', LXMF.LXMessage.FAILED))} "
            f"hash={self._pretty_hex(getattr(message, 'hash', None))}",
            level=RNS.LOG_ERROR,
        )

    def _emit_announce(self, reason: str) -> None:
        if not self.router or not self.destination:
            return
        self.router.announce(self.destination.hash)
        self._log(f"LXMF announce sent for {self.address} reason={reason}")

    def announce_now(self, reason: str = "manual") -> None:
        self._emit_announce(reason)

    @staticmethod
    def _state_name(state: int | None) -> str:
        mapping = {
            LXMF.LXMessage.GENERATING: "generating",
            LXMF.LXMessage.OUTBOUND: "outbound",
            LXMF.LXMessage.SENDING: "sending",
            LXMF.LXMessage.SENT: "sent",
            LXMF.LXMessage.DELIVERED: "delivered",
            LXMF.LXMessage.REJECTED: "rejected",
            LXMF.LXMessage.CANCELLED: "cancelled",
            LXMF.LXMessage.FAILED: "failed",
        }
        return mapping.get(state, "unknown")

    @staticmethod
    def _log(message: str, level: int = RNS.LOG_NOTICE) -> None:
        RNS.log(f"[web-ui] {message}", level)

    def _configured_peers(self) -> list[str]:
        env_peers = os.environ.get("RNS_PEERS")
        if env_peers:
            return [peer.strip() for peer in env_peers.split(",") if peer.strip()]

        peers_file = Path("/app/config/bootstrap_peers.txt")
        if not peers_file.exists():
            return []

        peers = []
        for raw_line in peers_file.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            peers.append(line)
        return peers

    def retry_message(self, message_id: int) -> None:
        message = repository.get_message(message_id)
        if not message or message["direction"] != "outbox":
            raise ValueError("Message not found")
        repository.update_message(
            message_id,
            state="outbound",
            next_retry_at=time.time(),
            last_error=None,
        )
        self._log(f"Outbound id={message_id} scheduled for immediate retry")

    def cancel_message(self, message_id: int) -> None:
        message = repository.get_message(message_id)
        if not message or message["direction"] != "outbox":
            raise ValueError("Message not found")
        repository.update_message(
            message_id,
            state="cancelled",
            next_retry_at=None,
        )
        self._log(f"Outbound id={message_id} cancelled by user")

    def _process_outbound_message(self, item: dict[str, Any]) -> None:
        if not self.router or not self.destination:
            self._schedule_retry(item["id"], "LXMF service is not started")
            return

        destination_hex = (item.get("destination_hash") or "").strip().lower()
        content = item.get("content") or ""
        title = item.get("title")
        attachments = item.get("attachments") or []
        try:
            destination_hash = bytes.fromhex(destination_hex)
        except ValueError:
            repository.update_message(item["id"], state="failed", next_retry_at=None, last_error="Invalid destination hex")
            return

        if destination_hash == self.destination.hash:
            self._log(f"Outbound id={item['id']} resolved as self-send in worker")
            self._deliver_to_self(item["id"], content, title, attachments=attachments)
            return

        has_path = RNS.Transport.has_path(destination_hash)
        self._log(f"Outbound id={item['id']} worker initial path known={has_path}")
        if not has_path:
            self._log(f"Outbound id={item['id']} requesting path to {destination_hex}")
            RNS.Transport.request_path(destination_hash)
            deadline = time.time() + 20
            while time.time() < deadline and not RNS.Transport.has_path(destination_hash):
                time.sleep(0.25)
            has_path = RNS.Transport.has_path(destination_hash)
            self._log(f"Outbound id={item['id']} path request complete known={has_path}")

        recipient_identity = RNS.Identity.recall(destination_hash)
        if recipient_identity is None:
            self._schedule_retry(
                item["id"],
                f"Destination identity {destination_hex} is unknown after path lookup",
            )
            return

        dest = RNS.Destination(
            recipient_identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            "lxmf",
            "delivery",
        )
        message = LXMF.LXMessage(
            dest,
            self.destination,
            content,
            title,
            desired_method=LXMF.LXMessage.DIRECT,
            include_ticket=True,
        )
        if attachments:
            message.set_fields({self.ATTACHMENT_FIELD_KEY: attachments})
        message.register_delivery_callback(lambda msg: self._on_outbound_state(item["id"], msg))
        message.register_failed_callback(lambda msg: self._on_outbound_failure(item["id"], msg))
        try:
            self._log(f"Outbound id={item['id']} handing message to LXMF router with desired method DIRECT")
            self.router.handle_outbound(message)
            repository.update_message(
                item["id"],
                state="sending",
                lxmf_hash=self._pretty_hex(getattr(message, "hash", None)),
                transport_encryption=str(getattr(message, "transport_encryption", "")),
                ratchet_id=self._pretty_hex(getattr(message, "ratchet_id", None)),
                next_retry_at=None,
                last_error=None,
            )
            self._log(
                f"Outbound id={item['id']} handed to LXMF and now tracked as sending "
                f"hash={self._pretty_hex(getattr(message, 'hash', None))}"
            )
        except Exception as exc:
            self._schedule_retry(item["id"], str(exc))
            self._log(f"Outbound id={item['id']} router handling raised an exception: {exc}", level=RNS.LOG_ERROR)

    def _extract_attachments(self, message: LXMF.LXMessage) -> list[dict[str, Any]]:
        fields = None
        getter = getattr(message, "get_fields", None)
        if callable(getter):
            fields = getter()
        elif hasattr(message, "fields"):
            fields = getattr(message, "fields", None)
        if not isinstance(fields, dict):
            return []
        fields = self._normalise_msgpack_value(fields)
        attachments = fields.get(self.ATTACHMENT_FIELD_KEY)
        if not isinstance(attachments, list):
            return []

        normalized = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "filename": str(item.get("filename") or "attachment"),
                    "content_type": str(item.get("content_type") or "application/octet-stream"),
                    "size": int(item.get("size") or self._decoded_attachment_size(item.get("data_b64"))),
                    "data_b64": str(item.get("data_b64") or ""),
                }
            )
        return normalized

    @classmethod
    def _normalise_msgpack_value(cls, value: Any) -> Any:
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except Exception:
                return value
        if isinstance(value, list):
            return [cls._normalise_msgpack_value(item) for item in value]
        if isinstance(value, dict):
            return {cls._normalise_msgpack_value(key): cls._normalise_msgpack_value(item) for key, item in value.items()}
        return value

    @staticmethod
    def _decoded_attachment_size(value: Any) -> int:
        if not value:
            return 0
        try:
            return len(base64.b64decode(value))
        except Exception:
            return 0

    @staticmethod
    def _attachment_bytes(attachments: list[dict[str, Any]]) -> int:
        return sum(int(item.get("size", 0) or 0) for item in attachments)

    def _schedule_retry(self, message_id: int, error: str, **extra_updates: Any) -> None:
        message = repository.get_message(message_id)
        if not message:
            return
        retry_count = int(message.get("retry_count") or 0) + 1
        delay = self.RETRY_DELAYS[min(retry_count - 1, len(self.RETRY_DELAYS) - 1)]
        state = "failed" if retry_count >= len(self.RETRY_DELAYS) else "retry_wait"
        updates = {
            "state": state,
            "retry_count": retry_count,
            "next_retry_at": None if state == "failed" else time.time() + delay,
            "last_error": error,
        }
        updates.update(extra_updates)
        repository.update_message(message_id, **updates)
        self._log(
            f"Outbound id={message_id} scheduled retry_count={retry_count} state={state} "
            f"next_delay={delay if state != 'failed' else 'none'} error={error}",
            level=RNS.LOG_ERROR if state == "failed" else RNS.LOG_NOTICE,
        )


service = LXMFService()
