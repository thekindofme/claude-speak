"""Thin client for speakd, plus on-demand daemon start.

Callers (hooks, MCP server, CLI) must never block on speech. Everything here
either returns fast or gives up fast: a wedged TTS daemon must not be able to
stall a Claude Code turn.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import state

ROOT = Path(__file__).resolve().parent.parent


def base_url(port: int | None = None) -> str:
    return f"http://127.0.0.1:{port or state.load_config()['port']}"


def request(method: str, path: str, payload: dict | None = None,
            timeout: float = 0.8, port: int | None = None) -> dict | None:
    """Returns the decoded response, or None if the daemon didn't answer."""
    url = base_url(port) + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
        return json.loads(body) if body else {}
    except Exception:
        return None


def get(path: str, timeout: float = 0.8, port: int | None = None) -> dict | None:
    return request("GET", path, None, timeout, port)


def post(path: str, payload: dict, timeout: float = 0.8, port: int | None = None):
    return request("POST", path, payload, timeout, port)


def alive(port: int | None = None) -> bool:
    return get("/health", timeout=0.5, port=port) is not None


def python_bin() -> str:
    venv = ROOT / ".venv/bin/python"
    return str(venv) if venv.exists() else sys.executable


def spawn_daemon() -> bool:
    """Start speakd detached. Returns False if we couldn't even launch it."""
    home = state.home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        logf = (home / "speakd.out").open("a")
        subprocess.Popen(
            [python_bin(), str(ROOT / "daemon/speakd.py")],
            stdout=logf, stderr=logf, start_new_session=True,
        )
        return True
    except Exception:
        return False


def ensure_daemon(wait_s: float = 0.0, port: int | None = None) -> bool:
    """Make sure speakd is up. wait_s=0 means fire-and-forget (hook path)."""
    if alive(port):
        return True
    if not spawn_daemon():
        return False
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if alive(port):
            return True
        time.sleep(0.25)
    return alive(port)


def speak(text: str, session_id: str = "", kind: str = "progress",
          source: str = "tool", timeout: float = 0.8,
          spawn: bool = True, voice: str | None = None) -> dict:
    """Queue one utterance. Never raises; always returns a verdict dict."""
    payload = {
        "text": text,
        "session_id": session_id,
        "kind": kind,
        "source": source,
        "request_id": uuid.uuid4().hex,
        "created": time.time(),   # the daemon drops lines that went stale waiting
        "voice": voice,
        "pid": os.getpid(),
    }
    res = post("/speak", payload, timeout=timeout)
    if res is not None:
        return res
    if not spawn:
        return {"spoken": False, "reason": "daemon-down"}
    # Cold start: park the payload and let a detached deliverer retry, so the
    # caller (a hook, mid-turn) returns immediately.
    home = state.home()
    home.mkdir(parents=True, exist_ok=True)
    pending = home / f"pending-{payload['request_id']}.json"
    try:
        pending.write_text(json.dumps(payload))
    except Exception:
        return {"spoken": False, "reason": "daemon-down"}
    spawn_daemon()
    try:
        subprocess.Popen(
            [sys.executable, str(ROOT / "core/deliver.py"), str(pending)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pending.unlink(missing_ok=True)
        return {"spoken": False, "reason": "daemon-down"}
    return {"spoken": True, "reason": "queued-cold-start"}
