#!/usr/bin/env python3
"""Detached retry-deliverer for a parked utterance (daemon cold start).

Runs as its own process so nothing on the Claude Code turn path waits for the
TTS engine's first load. request_id makes the retry idempotent, so a response
that times out after the daemon already queued the line can't double-speak it.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import client  # noqa: E402


def main(path: str, budget_s: float = 90.0):
    p = Path(path)
    try:
        payload = json.loads(p.read_text())
    except Exception:
        return
    deadline = time.time() + budget_s
    while time.time() < deadline:
        if client.post("/speak", payload, timeout=5.0) is not None:
            break
        time.sleep(0.5)
    p.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1])
