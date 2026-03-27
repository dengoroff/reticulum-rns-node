from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any


APP_DATA_DIR = Path(os.environ.get("APP_DATA_DIR", "/data/app"))
HEALTH_CACHE_PATH = APP_DATA_DIR / "peer_health.json"
BOOTSTRAP_PEERS_FILE = Path(os.environ.get("BOOTSTRAP_PEERS_FILE", str(Path(__file__).resolve().parents[1] / "config" / "bootstrap_peers.txt")))


def load_candidate_peers(env_peers: str | None = None, file_path: Path | None = None) -> list[dict[str, Any]]:
    if env_peers:
        return _parse_env_peers(env_peers)
    return _parse_file_peers(file_path or BOOTSTRAP_PEERS_FILE)


def refresh_peer_health(
    candidates: list[dict[str, Any]],
    cache_path: Path | None = None,
    timeout: float = 3.0,
) -> list[dict[str, Any]]:
    cache_path = cache_path or HEALTH_CACHE_PATH
    previous = _load_cache(cache_path)
    previous_map = {item["peer"]: item for item in previous}
    now = int(time.time())
    results = []

    for peer in candidates:
        host = peer["host"]
        port = int(peer["port"])
        peer_key = f"{host}:{port}"
        prev = previous_map.get(peer_key, {})
        result = probe_peer(host, port, timeout=timeout)
        entry = {
            "peer": peer_key,
            "host": host,
            "port": port,
            "source": peer.get("source", "file"),
            "checked_at": now,
            "dns_ok": result["dns_ok"],
            "dns_addresses": result["dns_addresses"],
            "tcp_ok": result["tcp_ok"],
            "error": result["error"],
            "selected": False,
            "last_success_at": prev.get("last_success_at"),
            "consecutive_failures": prev.get("consecutive_failures", 0),
        }
        if entry["tcp_ok"]:
            entry["last_success_at"] = now
            entry["consecutive_failures"] = 0
        else:
            entry["consecutive_failures"] = int(prev.get("consecutive_failures", 0)) + 1
        results.append(entry)

    _save_cache(cache_path, results)
    return results


def select_active_peers(
    candidates: list[dict[str, Any]],
    cache_path: Path | None = None,
    max_active: int = 3,
    timeout: float = 2.5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    health = refresh_peer_health(candidates, cache_path=cache_path, timeout=timeout)

    healthy = sorted(
        [item for item in health if item["tcp_ok"]],
        key=lambda item: (item.get("last_success_at") or 0, -item.get("consecutive_failures", 0)),
        reverse=True,
    )
    unhealthy = [item for item in health if not item["tcp_ok"]]

    selected_keys: list[str] = []
    for item in healthy:
        if len(selected_keys) >= max_active:
            break
        selected_keys.append(item["peer"])

    if len(selected_keys) < max_active:
        for item in unhealthy:
            if len(selected_keys) >= max_active:
                break
            selected_keys.append(item["peer"])

    selected = []
    for candidate in candidates:
        peer_key = f"{candidate['host']}:{candidate['port']}"
        if peer_key in selected_keys:
            selected.append(candidate)

    selected_key_set = set(selected_keys)
    for item in health:
        item["selected"] = item["peer"] in selected_key_set

    _save_cache(cache_path or HEALTH_CACHE_PATH, health)
    return selected, health


def probe_peer(host: str, port: int, timeout: float = 3.0) -> dict[str, Any]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        addresses = sorted({item[4][0] for item in infos})
    except Exception as exc:
        return {
            "dns_ok": False,
            "dns_addresses": [],
            "tcp_ok": False,
            "error": f"DNS: {exc}",
        }

    for address in addresses:
        try:
            with socket.create_connection((address, port), timeout=timeout):
                return {
                    "dns_ok": True,
                    "dns_addresses": addresses,
                    "tcp_ok": True,
                    "error": "",
                }
        except Exception as exc:
            last_error = str(exc)

    return {
        "dns_ok": True,
        "dns_addresses": addresses,
        "tcp_ok": False,
        "error": f"TCP: {last_error}",
    }


def format_dns_report(health: list[dict[str, Any]]) -> str:
    if not health:
        return "No bootstrap peers found."
    lines = []
    for item in health:
        if item["dns_ok"]:
            lines.append(f"{item['host']}: {', '.join(item['dns_addresses'])}")
        else:
            lines.append(f"{item['host']}: FAIL {item['error']}")
    return "\n".join(lines)


def format_tcp_report(health: list[dict[str, Any]]) -> str:
    if not health:
        return "No bootstrap peers found."
    lines = []
    for item in health:
        if item["tcp_ok"]:
            lines.append(f"{item['peer']} OK")
        else:
            lines.append(f"{item['peer']} FAIL {item['error']}")
    return "\n".join(lines)


def _parse_env_peers(value: str) -> list[dict[str, Any]]:
    items = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        host, _, port = item.partition(":")
        items.append({"host": host.strip(), "port": int(port.strip() or "4242"), "source": "env"})
    return items


def _parse_file_peers(path: Path) -> list[dict[str, Any]]:
    items = []
    if not path.exists():
        return items
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        host, _, port = line.partition(":")
        if not host:
            continue
        items.append({"host": host.strip(), "port": int(port.strip() or "4242"), "source": str(path)})
    return items


def _load_cache(path: Path) -> list[dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def _save_cache(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2, sort_keys=True))
