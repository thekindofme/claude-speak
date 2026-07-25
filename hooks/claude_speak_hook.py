#!/usr/bin/env python3
"""Claude Code hook for claude-speak. Stdlib only, fast exit paths.

Usage:
  claude_speak_hook.py stop      # Stop hook: speak the final assistant reply
  claude_speak_hook.py prompt    # UserPromptSubmit: interrupt speech; inject voice-mode context
  claude_speak_hook.py deliver <textfile>  # internal: retry-post after daemon spawn
"""

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home() / ".claude-speak"
CONFIG_PATH = HOME / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def port(cfg) -> int:
    return int(cfg.get("port", 7865))


def post(path: str, payload: dict, port_: int, timeout: float = 0.6) -> bool:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port_}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def hlog(msg: str):
    try:
        with (HOME / "hook.log").open("a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def is_interactive() -> bool:
    """True when this hook runs under a session with a controlling terminal.

    Headless runs (`claude -p` from launchd/cron, CI) have no /dev/tty, so
    their replies are never spoken.
    """
    try:
        with open("/dev/tty"):
            return True
    except OSError:
        return False


def spawn_daemon():
    HOME.mkdir(exist_ok=True)
    logf = (HOME / "speakd.out").open("a")
    subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "daemon/speakd.py")],
        stdout=logf, stderr=logf, start_new_session=True,
    )


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
                texts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                joined = "\n".join(t for t in texts if t.strip())
                if joined.strip():
                    best = joined
    except Exception:
        return ""
    return best


def cmd_stop_hook():
    data = json.load(sys.stdin)
    cfg = load_config()
    if not cfg.get("enabled", False):
        return
    if data.get("stop_hook_active"):
        return
    tty = is_interactive()
    hlog(f"stop sid={data.get('session_id','')[:12]} tty={tty}")
    if not tty:
        return  # never voice headless/background sessions
    text = last_assistant_text(data.get("transcript_path", ""))
    if not text.strip():
        return
    payload = {
        "text": text,
        "session_id": data.get("session_id", ""),
        "interactive": True,
        "request_id": uuid.uuid4().hex,
    }
    p = port(cfg)
    if post("/speak", payload, p):
        return
    # Daemon down: park payload, spawn daemon + detached deliverer, return now.
    tf = tempfile.NamedTemporaryFile(
        "w", dir=str(HOME), prefix="pending-", suffix=".json", delete=False
    )
    json.dump(payload, tf)
    tf.close()
    spawn_daemon()
    subprocess.Popen(
        [sys.executable, __file__, "deliver", tf.name],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def cmd_deliver(path: str):
    cfg = load_config()
    p = port(cfg)
    try:
        payload = json.loads(Path(path).read_text())
    except Exception:
        return
    deadline = time.time() + 60
    while time.time() < deadline:
        # request_id makes retries idempotent even if a response times out
        if post("/speak", payload, p, timeout=5.0):
            break
        time.sleep(0.5)
    Path(path).unlink(missing_ok=True)


def cmd_prompt_hook():
    cfg = load_config()
    if not cfg.get("enabled", False):
        return
    if not is_interactive():
        return
    data = {}
    try:
        data = json.load(sys.stdin)
    except Exception:
        pass
    post(
        "/stop",
        {"session_id": data.get("session_id", ""), "interactive": True},
        port(cfg),
        timeout=0.3,
    )
    if cfg.get("mode") == "summary":
        print(
            "<voice-mode>Voice replies are active in summary mode. End your final "
            "reply with a spoken summary wrapped in <speak>...</speak>: one to three "
            "short, plain-English sentences, written for the ear (no markdown, no "
            "code, no file paths). It will be read aloud; the full reply stays on "
            "screen.</voice-mode>"
        )


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "stop":
        cmd_stop_hook()
    elif cmd == "prompt":
        cmd_prompt_hook()
    elif cmd == "deliver":
        cmd_deliver(sys.argv[2])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # hooks must never break Claude Code
    sys.exit(0)
