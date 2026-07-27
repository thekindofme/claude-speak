#!/usr/bin/env python3
"""Claude Code hooks for claude-speak.

    session-start   voice on? warm the engine, brief Claude on the voice channel
    prompt          user typed: barge-in, mark the active session, keep the brief live
    notification    Claude needs permission / is waiting — say so out loud
    stop            turn ended: speak a fallback line only if Claude stayed silent
    end             session over: drop its override

Runs under the system python on the turn path, so: stdlib only, fast exit when
voice is off, and never raise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import client, state  # noqa: E402

BRIEF_PATH = ROOT / "voice/brief.md"
BRIEF_FALLBACK_PATH = ROOT / "voice/brief-fallback.md"

STYLE_NOTE = {
    "companion": "Style: companion — warm and brief, like a teammate at the next desk.",
    "terse": "Style: terse — facts only, fewest words, usually just the closing line. Lines are spaced far apart, so make each one count.",
    "narrate": "Style: narrate — play-by-play on long work; lines may come every few seconds.",
}


def hlog(msg: str):
    try:
        with (state.home() / "hook.log").open("a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def emit(event: str, context: str):
    """Inject context into the session (Claude Code reads this from stdout)."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": context,
    }}))


def read_stdin() -> dict:
    try:
        return json.load(sys.stdin) or {}
    except Exception:
        return {}


# ------------------------------------------------------------ interactivity

def cmux_bin() -> str | None:
    b = os.environ.get("CMUX_CLAUDE_HOOK_CMUX_BIN") or os.environ.get("CMUX_BUNDLED_CLI_PATH")
    return b if b and Path(b).exists() else None


def cmux_focus() -> tuple[bool, bool]:
    """(running under cmux, this session's surface is the focused one)."""
    b = cmux_bin()
    if not b or not os.environ.get("CMUX_PANEL_ID"):
        return False, False
    try:
        r = subprocess.run([b, "identify"], capture_output=True, text=True, timeout=3)
        j = json.loads(r.stdout)
        caller = (j.get("caller") or {}).get("surface_ref")
        focused = (j.get("focused") or {}).get("surface_ref")
        return True, bool(caller) and caller == focused
    except Exception:
        return True, False


def is_interactive() -> bool:
    """True when a human is looking at / typing into this session right now.

    cmux/tasuki: only the focused surface. Plain terminal: a controlling tty.
    Headless (`claude -p` under launchd): neither — those never speak.
    """
    in_cmux, focused = cmux_focus()
    if in_cmux:
        return focused
    try:
        with open("/dev/tty"):
            return True
    except OSError:
        return False


# ------------------------------------------------------------------- brief

def brief_text(eff: dict) -> str:
    """The full brief — or the degraded one when the speak tool isn't loaded."""
    path = BRIEF_PATH if state.mcp_registered() else BRIEF_FALLBACK_PATH
    try:
        body = path.read_text().strip()
    except Exception:
        body = "Voice mode is ON. Use the `speak` tool for short spoken lines."
    style = STYLE_NOTE.get(eff.get("style", "companion"), "")
    return f"<voice-mode>\n{body}\n\n{style}\n</voice-mode>"


REMINDER = ("<voice-mode>Voice is ON. Speak through the `speak` tool at real "
            "moments — opening long work, a milestone, a question, the close. "
            "Short spoken lines under 25 words; the screen keeps the detail. "
            "{style}</voice-mode>")

REMINDER_FALLBACK = ("<voice-mode>Voice is ON but the `speak` tool isn't loaded "
                     "here. End the reply with a `<speak>`…`</speak>` block: one "
                     "or two spoken sentences under 25 words, no markdown or "
                     "paths. Only that is read aloud. {style}</voice-mode>")


# ------------------------------------------------------------------- hooks

def cmd_session_start():
    data = read_stdin()
    sid = data.get("session_id", "")
    state.bind_surface(sid)
    state.prune_sessions()
    eff = state.resolve(sid)
    if not eff["enabled"]:
        return  # voice is opt-in: silent, zero-cost, no context spent
    client.ensure_daemon()  # warm the engine before the first line is needed
    state.save_session(sid, {"needs_brief": False})
    emit("SessionStart", brief_text(eff))


def cmd_prompt():
    data = read_stdin()
    sid = data.get("session_id", "")
    # Leave the breadcrumb that lets `speak on` in this terminal find this
    # session — Claude Code exposes no session id to child processes.
    state.bind_surface(sid)
    eff = state.resolve(sid)
    if not eff["enabled"]:
        return
    if not is_interactive() and not state.load_session(sid).get("enabled"):
        return  # global-on must not make headless sessions talk
    client.ensure_daemon()
    client.post("/turn", {"session_id": sid, "interactive": True}, timeout=0.5)
    if eff.get("needs_brief"):
        state.save_session(sid, {"needs_brief": False})
        emit("UserPromptSubmit", brief_text(eff))
    else:
        template = REMINDER if state.mcp_registered() else REMINDER_FALLBACK
        emit("UserPromptSubmit", template.format(
            style=STYLE_NOTE.get(eff.get("style", "companion"), "")))


def cmd_notification():
    data = read_stdin()
    sid = data.get("session_id", "")
    eff = state.resolve(sid)
    if not eff["enabled"] or not eff.get("notify", True):
        return
    msg = (data.get("message") or "").strip()
    if not msg:
        return
    low = msg.lower()
    if "permission" in low:
        spoken = "I need your go-ahead on something."
    elif "waiting" in low or "idle" in low:
        spoken = "I'm waiting on you."
    else:
        spoken = msg
    client.speak(spoken, session_id=sid, kind="alert", source="hook")


def cmd_stop():
    data = read_stdin()
    if data.get("stop_hook_active"):
        return
    sid = data.get("session_id", "")
    eff = state.resolve(sid)
    if not eff["enabled"]:
        return
    text = last_assistant_text(data.get("transcript_path", ""))
    if not text.strip():
        return
    res = client.post("/close", {
        "session_id": sid,
        "text": text[:8000],
        "interactive": is_interactive(),
    }, timeout=1.0)
    if res is None:
        hlog(f"close undelivered sid={sid[:12]}")


def cmd_end():
    data = read_stdin()
    sid = data.get("session_id", "")
    if sid:
        state.forget_session(sid)


def last_assistant_text(transcript_path: str) -> str:
    """Final assistant text message in the transcript JSONL."""
    best = ""
    try:
        with open(transcript_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") != "assistant":
                    continue
                content = (entry.get("message") or {}).get("content") or []
                texts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
                joined = "\n".join(t for t in texts if t.strip())
                if joined.strip():
                    best = joined
    except Exception:
        return ""
    return best


COMMANDS = {
    "session-start": cmd_session_start,
    "prompt": cmd_prompt,
    "notification": cmd_notification,
    "stop": cmd_stop,
    "end": cmd_end,
}


def main():
    fn = COMMANDS.get(sys.argv[1] if len(sys.argv) > 1 else "")
    if fn:
        fn()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # a hook must never break Claude Code
        hlog(f"error: {type(e).__name__}: {e}")
    sys.exit(0)
