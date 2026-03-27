#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.peer_health import load_candidate_peers, select_active_peers

CONFIG_DIR = ROOT / "config"
RNS_CONFIG_DIR = Path(os.environ["RNS_CONFIG_DIR"])
LXMD_CONFIG_DIR = Path(os.environ["LXMD_CONFIG_DIR"])


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def render_reticulum() -> str:
    template = (CONFIG_DIR / "reticulum.template.conf").read_text()
    candidates = load_candidate_peers(os.environ.get("RNS_PEERS"))
    max_active = int(os.environ.get("MAX_ACTIVE_PEERS", "3"))
    peers, _ = select_active_peers(candidates, max_active=max_active)
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
                            .replace("{{ peer.port }}", str(peer["port"]))
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
