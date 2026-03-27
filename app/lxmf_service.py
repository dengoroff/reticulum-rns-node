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

    def start(self) -> None:
        with self.lock:
            if self.router is not None:
                return
            self.reticulum = RNS.Reticulum(configdir=os.environ.get("RNS_CONFIG_DIR"))
            self.router = LXMF.LXMRouter(storagepath=str(self.data_path))
            self.identity = self._load_or_create_identity()
            self.destination = self.router.register_delivery_identity(
                self.identity,
                display_name=self.display_name,
                stamp_cost=8,
            )
            self.router.register_delivery_callback(self._on_delivery)
            self.router.announce(self.destination.hash)
            self.announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
            self.announce_thread.start()

    def _load_or_create_identity(self) -> RNS.Identity:
        identity_path = self.data_path / "web-ui.identity"
        if identity_path.exists():
            return RNS.Identity.from_file(str(identity_path))
        identity = RNS.Identity()
        identity.to_file(str(identity_path))
        return identity

    def _announce_loop(self) -> None:
        while True:
            time.sleep(max(self.announce_interval, 300))
            try:
                if self.router and self.destination:
                    self.router.announce(self.destination.hash)
            except Exception:
                RNS.log("Failed to announce LXMF destination", RNS.LOG_ERROR)

    def _on_delivery(self, message: LXMF.LXMessage) -> None:
        repository.insert_message(
            {
                "direction": "inbox",
                "state": "received",
                "source_hash": self._pretty_hex(message.source_hash),
                "destination_hash": self._pretty_hex(message.destination_hash),
                "title": self._as_string(message.title_as_string),
                "content": self._as_string(message.content_as_string),
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

        destination_hash = bytes.fromhex(destination_hex.strip())
        if not RNS.Transport.has_path(destination_hash):
            RNS.Transport.request_path(destination_hash)
            deadline = time.time() + 20
            while time.time() < deadline and not RNS.Transport.has_path(destination_hash):
                time.sleep(0.25)

        recipient_identity = RNS.Identity.recall(destination_hash)
        if recipient_identity is None:
            raise ValueError("Destination identity is unknown. Wait for an announce and try again.")

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
        record_id = repository.insert_message(
            {
                "direction": "outbox",
                "state": "queued",
                "source_hash": self.address,
                "destination_hash": destination_hex.lower(),
                "title": title,
                "content": content,
                "created_at": time.time(),
            }
        )
        try:
            self.router.handle_outbound(message)
            repository.update_message(
                record_id,
                state="dispatched",
                lxmf_hash=self._pretty_hex(getattr(message, "hash", None)),
                transport_encryption=str(getattr(message, "transport_encryption", "")),
            )
        except Exception:
            repository.update_message(record_id, state="failed")
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


service = LXMFService()
