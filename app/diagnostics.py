from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from app.peer_health import (
    APP_DATA_DIR,
    HEALTH_CACHE_PATH,
    format_dns_report,
    format_tcp_report,
    load_candidate_peers,
    refresh_peer_health,
)

RNS_CONFIG_DIR = os.environ.get("RNS_CONFIG_DIR", "/data/rns")
RNS_CONFIG_FILE = Path(RNS_CONFIG_DIR) / "config"
RNPATH_CACHE_PATH = APP_DATA_DIR / "rnpath_cache.json"
RNPATH_CACHE_TTL = 120


def collect_diagnostics() -> dict[str, object]:
    health = refresh_peer_health(load_candidate_peers(os.environ.get("RNS_PEERS")))
    rnpath_cache = _get_rnpath_cache()
    return {
        "config_path": str(RNS_CONFIG_FILE),
        "config": _read_file(RNS_CONFIG_FILE),
        "rnstatus": _run_command(["rnstatus", "--config", RNS_CONFIG_DIR]),
        "rnpath": rnpath_cache["output"],
        "rnpath_cached_at": rnpath_cache["cached_at"],
        "rnpath_age_seconds": rnpath_cache["age_seconds"],
        "rnpath_cache_path": str(RNPATH_CACHE_PATH),
        "dns": format_dns_report(health),
        "tcp": format_tcp_report(health),
        "peer_health": health,
        "peer_health_cache_path": str(HEALTH_CACHE_PATH),
    }


def _read_file(path: Path) -> str:
    try:
        return path.read_text()
    except Exception as exc:
        return f"Failed to read {path}: {exc}"


def _run_command(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        return f"Command not found: {' '.join(command)}"
    except subprocess.TimeoutExpired:
        return f"Command timed out: {' '.join(command)}"
    except Exception as exc:
        return f"Command failed to start ({' '.join(command)}): {exc}"

    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()

    if result.returncode == 0:
        return output or "(no output)"

    if output and error:
        return f"{output}\n\n[stderr]\n{error}"
    if output:
        return output
    if error:
        return error
    return f"Command exited with code {result.returncode} and no output"


def _get_rnpath_cache() -> dict[str, object]:
    cached = _load_rnpath_cache()
    now = int(time.time())

    if cached:
        age = now - int(cached.get("cached_at", 0))
        if age < RNPATH_CACHE_TTL:
            cached["age_seconds"] = age
            return cached

    output = _run_command(["rnpath", "-t", "--config", RNS_CONFIG_DIR])
    payload = {
        "output": output,
        "cached_at": now,
        "age_seconds": 0,
    }
    _save_rnpath_cache(payload)
    return payload


def _load_rnpath_cache() -> dict[str, object] | None:
    try:
        return json.loads(RNPATH_CACHE_PATH.read_text())
    except Exception:
        return None


def _save_rnpath_cache(payload: dict[str, object]) -> None:
    RNPATH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RNPATH_CACHE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))
