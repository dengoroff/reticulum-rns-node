# Reticulum RNS Node Web UI

This project provides a self-contained Reticulum node with LXMF messaging and a minimal web interface.

The container runs:
- `rnsd` for the Reticulum transport daemon
- `lxmd` for LXMF message routing
- a FastAPI web application for node status and messaging

Data is persisted with Docker volumes, including:
- Reticulum config and state
- LXMF daemon data
- web app identity and message database

## Features

- Dashboard with node address and basic stats
- Inbox and outbox views
- Send LXMF messages from the browser
- Message details page
- Local SQLite storage for received and sent messages
- Docker restart policy: `unless-stopped`

## Requirements

- Docker
- Docker Compose

Tested target environments:
- macOS with Docker Desktop
- Linux x86 with Docker Engine + Compose

## Quick Start

### Option 1: helper script

```bash
./start.sh
```

This builds the image and starts the service in the background.

The web UI is available at:

```bash
http://localhost:8080
```

### Option 2: Docker Compose directly

```bash
docker compose up -d --build
```

To stop the service:

```bash
docker compose down
```

## Configuration

The main runtime settings are passed through environment variables in [`docker-compose.yml`](/Users/denis/Projects/reticulum-rns-node/docker-compose.yml):

- `WEB_PORT` default `8080`
- `RNS_SERVER_PORT` default `4242`
- `RNS_PEERS` comma-separated bootstrap peers
- `LXMF_DISPLAY_NAME` node display name
- `LXMF_ANNOUNCE_INTERVAL` announce interval in seconds

Example:

```bash
WEB_PORT=8090 LXMF_DISPLAY_NAME="My RNS Node" docker compose up -d --build
```

## Persistence

Docker named volumes are used automatically:

- `reticulum-rns`
- `reticulum-lxmd`
- `reticulum-app`

These volumes keep your node identity, config, and stored messages across container restarts.

## Notes

- No authentication is enabled
- The UI is intentionally minimal
- Self-send to the node's own LXMF address is supported
