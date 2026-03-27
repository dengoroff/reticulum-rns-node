#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
from string import Template


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
RNS_CONFIG_DIR = Path(os.environ["RNS_CONFIG_DIR"])
LXMD_CONFIG_DIR = Path(os.environ["LXMD_CONFIG_DIR"])


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_peers(value: str) -> list[dict[str, str]]:
    peers = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        host, _, port = item.partition(":")
        peers.append({"host": host.strip(), "port": port.strip() or "4242"})
    return peers


def render_reticulum() -> str:
    template = (CONFIG_DIR / "reticulum.template.conf").read_text()
    peers = parse_peers(
        os.environ.get(
            "RNS_PEERS",
            "amsterdam.connect.reticulum.network:4965,reticulum.betweentheborders.com:4242,rns.quad4.io:4242",
        )
    )
    enable_server = parse_bool(os.environ.get("RNS_ENABLE_SERVER"), True)
    enable_discovery = parse_bool(os.environ.get("RNS_ENABLE_DISCOVERY"), True)
    lines = []
    skip_stack: list[bool] = []
    loop_items: list[dict[str, str]] | None = None
    loop_buffer: list[str] = []

    for line in template.splitlines():
        stripped = line.strip()
        if stripped == "{% if enable_server %}":
            skip_stack.append(not enable_server)
            continue
        if stripped == "{% if enable_discovery %}":
            skip_stack.append(not enable_discovery)
            continue
        if stripped == "{% endif %}":
            if skip_stack:
                skip_stack.pop()
            continue
        if stripped == "{% for peer in peers %}":
            loop_items = peers
            loop_buffer = []
            continue
        if stripped == "{% endfor %}":
            if loop_items is not None:
                for index, peer in enumerate(loop_items, start=1):
                    for buffered in loop_buffer:
                        lines.append(
                            buffered.replace("{{ loop.index }}", str(index))
                            .replace("{{ peer.host }}", peer["host"])
                            .replace("{{ peer.port }}", peer["port"])
                        )
            loop_items = None
            loop_buffer = []
            continue
        if True in skip_stack:
            continue
        if loop_items is not None:
            loop_buffer.append(line)
            continue
        lines.append(line)

    rendered = "\n".join(lines)
    rendered = rendered.replace("{{ rns_server_port }}", os.environ.get("RNS_SERVER_PORT", "4242"))
    return f"{rendered}\n"


def render_lxmd() -> str:
    template = Template((CONFIG_DIR / "lxmd.template.ini").read_text())
    return template.substitute(
        display_name=os.environ.get("LXMF_DISPLAY_NAME", "Web Node"),
        announce_interval=os.environ.get("LXMF_ANNOUNCE_INTERVAL", "3600"),
    )


def main() -> None:
    RNS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LXMD_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (RNS_CONFIG_DIR / "config").write_text(render_reticulum())
    (LXMD_CONFIG_DIR / "config").write_text(render_lxmd())


if __name__ == "__main__":
    main()
