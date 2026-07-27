#!/usr/bin/env python3
"""claude-speak MCP server — Claude Code's voice.

Exposes one tool, `speak`, so the model can say something out loud *mid-turn*
without polluting the on-screen reply. That is the whole point: the screen keeps
the dense technical answer, the voice channel carries the one line a colleague
would say out loud.

Stdio JSON-RPC, stdlib only, no third-party MCP SDK — startup must be
instantaneous because Claude Code spawns this on every session.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import client, state  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "claude-speak", "version": "2.0.0"}

TOOL = {
    "name": "speak",
    "description": (
        "Say one short line out loud to the user through their speakers. "
        "Use it ONLY while voice mode is on (the session context says so). "
        "The voice channel is separate from your reply: the reply stays on "
        "screen with the detail, this is the one line you'd say out loud. "
        "One or two spoken sentences, under 25 words, plain English, no "
        "markdown, no code, no file paths, no IDs. Speak at moments that "
        "matter — starting long work, a real milestone, a question you need "
        "answered, the closing outcome — not as narration of every step."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "What to say, written for the ear. Under 25 words.",
            },
            "kind": {
                "type": "string",
                "enum": ["open", "progress", "milestone", "question", "done", "alert"],
                "description": (
                    "open: what you're about to do on work that will take a while. "
                    "progress: a mid-flight update (rate-limited, superseded by newer ones). "
                    "milestone: something genuinely notable landed. "
                    "question: you need a decision — jumps the queue. "
                    "done: the closing line of the turn. "
                    "alert: something is wrong and they should know now."
                ),
                "default": "progress",
            },
        },
        "required": ["text"],
    },
}


def _voice_plausibly_on() -> bool:
    """Cheap pre-check so a tool call can't cold-start TTS while voice is off."""
    if state.load_config()["enabled"]:
        return True
    return any(s.get("enabled") for s in state.list_sessions())


def call_speak(args: dict) -> str:
    text = (args.get("text") or "").strip()
    kind = args.get("kind") or "progress"
    if not text:
        return "nothing to say: text was empty"

    if not _voice_plausibly_on() and not client.alive():
        return ("not spoken: voice is off. Do not call speak again this session "
                "unless the user turns it on.")

    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    res = client.speak(text, session_id=session_id, kind=kind, source="tool")

    if res.get("spoken"):
        note = "; ".join(res.get("issues") or [])
        return f"spoken ({kind})" + (f" — note: {note}" if note else "")

    reason = res.get("reason", "unknown")
    hints = {
        "voice-off": ("not spoken: voice is off. Do not call speak again this "
                      "session unless the user turns it on."),
        "muted": "not spoken: the user muted voice temporarily. Stay quiet.",
        "rate-limited": ("not spoken: too soon after the last line. Speak at "
                         "moments, not continuously."),
        "repeat": "not spoken: you already said that.",
        "inactive-session": "not spoken: the user is looking at another session.",
        "daemon-down": "not spoken: the voice daemon is not running.",
    }
    return hints.get(reason, f"not spoken: {reason}")


# ------------------------------------------------------------------ jsonrpc

def respond(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def handle(msg: dict):
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        client_proto = (msg.get("params") or {}).get("protocolVersion")
        respond(msg_id, {
            "protocolVersion": client_proto or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return  # notifications carry no id and take no response
    elif method == "ping":
        respond(msg_id, {})
    elif method == "tools/list":
        respond(msg_id, {"tools": [TOOL]})
    elif method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") != "speak":
            respond(msg_id, error={"code": -32602,
                                   "message": f"unknown tool {params.get('name')}"})
            return
        try:
            text = call_speak(params.get("arguments") or {})
        except Exception as e:  # a broken voice must never break the turn
            text = f"not spoken: {type(e).__name__}"
        respond(msg_id, {"content": [{"type": "text", "text": text}],
                         "isError": False})
    elif msg_id is not None:
        respond(msg_id, error={"code": -32601, "message": f"unknown method {method}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        try:
            handle(msg)
        except Exception:
            pass


if __name__ == "__main__":
    main()
