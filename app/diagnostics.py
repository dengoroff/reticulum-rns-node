from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path


RNS_CONFIG_DIR = os.environ.get("RNS_CONFIG_DIR", "/data/rns")
RNS_CONFIG_FILE = Path(RNS_CONFIG_DIR) / "config"


def collect_diagnostics() -> dict[str, str]:
    peers = _parse_bootstrap_peers(RNS_CONFIG_FILE)
    return {
        "config_path": str(RNS_CONFIG_FILE),
        "config": _read_file(RNS_CONFIG_FILE),
        "rnstatus": _run_command(["rnstatus", "--config", RNS_CONFIG_DIR]),
        "rnpath": _run_command(["rnpath", "-t", "--config", RNS_CONFIG_DIR]),
        "dns": _dns_report(peers),
        "tcp": _tcp_report(peers),
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


def _parse_bootstrap_peers(path: Path) -> list[tuple[str, int]]:
    peers: list[tuple[str, int]] = []
    if not path.exists():
        return peers

    current_host: str | None = None
    current_port: int | None = None

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if line.startswith("[[Bootstrap "):
            if current_host and current_port:
                peers.append((current_host, current_port))
            current_host = None
            current_port = None
            continue
        if line.startswith("target_host = "):
            current_host = line.split("=", 1)[1].strip()
            continue
        if line.startswith("target_port = "):
            try:
                current_port = int(line.split("=", 1)[1].strip())
            except ValueError:
                current_port = None

    if current_host and current_port:
        peers.append((current_host, current_port))

    return peers


def _dns_report(peers: list[tuple[str, int]]) -> str:
    if not peers:
        return "No bootstrap peers found in rendered config."

    lines = []
    for host, _ in peers:
        try:
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
            lines.append(f"{host}: {' , '.join(addresses)}")
        except Exception as exc:
            lines.append(f"{host}: FAIL {exc}")
    return "\n".join(lines)


def _tcp_report(peers: list[tuple[str, int]]) -> str:
    if not peers:
        return "No bootstrap peers found in rendered config."

    lines = []
    for host, port in peers:
        try:
            with socket.create_connection((host, port), timeout=5):
                lines.append(f"{host}:{port} OK")
        except Exception as exc:
            lines.append(f"{host}:{port} FAIL {exc}")
    return "\n".join(lines)
