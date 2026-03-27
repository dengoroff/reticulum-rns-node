#!/usr/bin/env python3
from __future__ import annotations

import os
import time

import RNS


def main() -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            RNS.Reticulum(
                configdir=os.environ.get("RNS_CONFIG_DIR"),
                require_shared_instance=True,
            )
            return
        except Exception:
            time.sleep(1)
    raise SystemExit("Reticulum daemon did not become ready in time")


if __name__ == "__main__":
    main()
