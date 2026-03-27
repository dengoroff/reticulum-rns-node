from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import LXMF
import RNS

from app import repository


class LXMFService:
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

    def _on_delivery(self, message: LXMF.LXMessage) -> None:
        content = self._as_string(message.content_as_string)
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
                "created_at": getattr(message, "timestamp", time.time()),
            }
        )

    def send_message(self, destination_hex: str, content: str, title: str | None = None) -> int:
        if not self.router or not self.destination:
            raise RuntimeError("LXMF service is not started")

        normalized_destination = destination_hex.strip().lower()
        try:
            destination_hash = bytes.fromhex(normalized_destination)
        except ValueError as exc:
            raise ValueError("Destination address must be a valid hex string.") from exc

        record_id = repository.insert_message(
            {
                "direction": "outbox",
                "state": "queued",
                "source_hash": self.address,
                "destination_hash": normalized_destination,
                "title": title,
                "content": content,
                "created_at": time.time(),
            }
        )
        self._log(
            f"Outbound request accepted id={record_id} destination={normalized_destination} "
            f"title={title!r} bytes={len(content.encode('utf-8'))}"
        )

        if destination_hash == self.destination.hash:
            self._log(f"Outbound id={record_id} resolved as self-send")
            return self._deliver_to_self(record_id, content, title)

        has_path = RNS.Transport.has_path(destination_hash)
        self._log(f"Outbound id={record_id} initial path known={has_path}")
        if not has_path:
            self._log(f"Outbound id={record_id} requesting path to {normalized_destination}")
            RNS.Transport.request_path(destination_hash)
            deadline = time.time() + 20
            while time.time() < deadline and not RNS.Transport.has_path(destination_hash):
                time.sleep(0.25)
            has_path = RNS.Transport.has_path(destination_hash)
            self._log(f"Outbound id={record_id} path request complete known={has_path}")

        recipient_identity = RNS.Identity.recall(destination_hash)
        if recipient_identity is None:
            repository.update_message(record_id, state="failed")
            self._log(
                f"Outbound id={record_id} failed because destination identity {normalized_destination} "
                "is still unknown after path handling",
                level=RNS.LOG_ERROR,
            )
            raise ValueError("Destination identity is unknown. Wait for an announce and try again.")
        self._log(f"Outbound id={record_id} destination identity recalled successfully")

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
        message.register_delivery_callback(lambda msg: self._on_outbound_state(record_id, msg))
        message.register_failed_callback(lambda msg: self._on_outbound_failure(record_id, msg))
        try:
            self._log(
                f"Outbound id={record_id} handing message to LXMF router with desired method DIRECT"
            )
            self.router.handle_outbound(message)
            repository.update_message(
                record_id,
                state=self._state_name(message.state),
                lxmf_hash=self._pretty_hex(getattr(message, "hash", None)),
                transport_encryption=str(getattr(message, "transport_encryption", "")),
                ratchet_id=self._pretty_hex(getattr(message, "ratchet_id", None)),
            )
            self._log(
                f"Outbound id={record_id} queued in LXMF state={self._state_name(message.state)} "
                f"hash={self._pretty_hex(getattr(message, 'hash', None))}"
            )
        except Exception:
            repository.update_message(record_id, state="failed")
            self._log(f"Outbound id={record_id} router handling raised an exception", level=RNS.LOG_ERROR)
            raise
        return record_id

    @property
    def address(self) -> str:
        if not self.destination:
            return ""
        return self._pretty_hex(self.destination.hash)

    def stats(self) -> dict[str, Any]:
        configured_peers = [
            peer.strip()
            for peer in os.environ.get(
                "RNS_PEERS",
                "amsterdam.connect.reticulum.network:4965,reticulum.betweentheborders.com:4242,rns.quad4.io:4242",
            ).split(",")
            if peer.strip()
        ]
        return {
            "address": self.address,
            "display_name": self.display_name,
            "uptime_seconds": int(time.time() - self.started_at),
            "transport_enabled": True,
            "configured_peers": configured_peers,
            "known_paths": self._transport_count("PATHFINDER_M"),
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

    def _deliver_to_self(self, record_id: int, content: str, title: str | None) -> int:
        if not self.destination:
            raise RuntimeError("LXMF destination is not ready")

        message = LXMF.LXMessage(
            self.destination,
            self.destination,
            content,
            title,
            desired_method=LXMF.LXMessage.DIRECT,
        )
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
                "created_at": time.time(),
            }
        )
        repository.update_message(
            record_id,
            state="delivered",
            lxmf_hash=self._pretty_hex(message.hash),
            transport_encryption="local",
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
        )
        self._log(
            f"Outbound id={record_id} delivery callback state={state_name} "
            f"hash={self._pretty_hex(getattr(message, 'hash', None))}"
        )

    def _on_outbound_failure(self, record_id: int, message: LXMF.LXMessage) -> None:
        state_name = self._state_name(getattr(message, "state", LXMF.LXMessage.FAILED))
        repository.update_message(
            record_id,
            state=state_name,
            lxmf_hash=self._pretty_hex(getattr(message, "hash", None)),
            transport_encryption=str(getattr(message, "transport_encryption", "")),
            ratchet_id=self._pretty_hex(getattr(message, "ratchet_id", None)),
        )
        self._log(
            f"Outbound id={record_id} failure callback state={state_name} "
            f"hash={self._pretty_hex(getattr(message, 'hash', None))}",
            level=RNS.LOG_ERROR,
        )

    def _emit_announce(self, reason: str) -> None:
        if not self.router or not self.destination:
            return
        self.router.announce(self.destination.hash)
        self._log(f"LXMF announce sent for {self.address} reason={reason}")

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


service = LXMFService()
