"""speakd — local TTS daemon for claude-speak.

Keeps a TTS engine warm and plays speech through the Mac's speakers.

HTTP API (127.0.0.1 only):
  POST /speak   {"text": "...", "session_id": "..."}  -> queues speech, returns immediately
  POST /stop                                          -> interrupt current speech, clear queue
  POST /config  {"voice"|"speed"|"engine"|...}        -> live-update config
  GET  /status                                        -> engine, voice, queue, speaking
  GET  /voices                                        -> voices for current engine

Config: ~/.claude-speak/config.json
Log:    ~/.claude-speak/speakd.log
"""

from __future__ import annotations

import json
import os
import queue
import re
from collections import deque
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engines"))

HOME = Path.home() / ".claude-speak"
HOME.mkdir(exist_ok=True)
CONFIG_PATH = HOME / "config.json"
PID_PATH = HOME / "speakd.pid"
LOG_PATH = HOME / "speakd.log"

DEFAULTS = {
    "enabled": True,
    "engine": "kokoro-mlx",
    "voice": "af_heart",
    "speed": 1.0,
    "mode": "auto",          # auto: prefer <speak> block, else speechified full text
    "max_chars": 2200,        # cap on speechified text (~2.5 min)
    "port": 7865,
}


def log(msg: str):
    with LOG_PATH.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except Exception as e:
            log(f"config parse error: {e}")
    return cfg


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


# ---------------------------------------------------------------- speechify

CODE_FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`([^`]*)`")
LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
URL = re.compile(r"https?://\S+")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
HEADER = re.compile(r"^#{1,6}\s+", re.M)
BULLET = re.compile(r"^\s*[-*+]\s+", re.M)
NUMBULLET = re.compile(r"^\s*\d+[.)]\s+", re.M)
EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)")
SPEAK_BLOCK = re.compile(r"<speak>(.*?)</speak>", re.S | re.I)
PATHLIKE = re.compile(r"(?:~|/)?(?:[\w.-]+/){2,}[\w.-]+")


def speechify(text: str, max_chars: int) -> str:
    """Turn a Claude Code markdown reply into speakable prose."""
    m = SPEAK_BLOCK.search(text)
    if m:
        text = m.group(1)
    else:
        n_fences = len(CODE_FENCE.findall(text))
        text = CODE_FENCE.sub(" Code block omitted. " if n_fences else "", text)
        # collapse tables: keep nothing, mention once
        if TABLE_ROW.search(text):
            text = TABLE_ROW.sub("", text)
            text += " There is a table with details on screen."
    text = SPEAK_BLOCK.sub("", text)
    text = LINK.sub(r"\1", text)
    text = URL.sub("a link", text)
    text = INLINE_CODE.sub(r"\1", text)
    text = HEADER.sub("", text)
    text = BULLET.sub("", text)
    text = NUMBULLET.sub("", text)
    text = EMPHASIS.sub("", text)
    text = PATHLIKE.sub(lambda m: m.group(0).split("/")[-1], text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        cut = text[:max_chars]
        cut = cut[: cut.rfind(". ") + 1] or cut
        text = cut + " The rest is on screen."
    return text


SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str, chunk_chars: int = 260) -> list[str]:
    """Split into speakable chunks: sentences, merged up to chunk_chars."""
    parts = [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]
    out: list[str] = []
    buf = ""
    for s in parts:
        if buf and len(buf) + len(s) + 1 > chunk_chars:
            out.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip()
    if buf:
        out.append(buf)
    return out


# ---------------------------------------------------------------- gating

def should_speak(active_session: str | None, body: dict) -> tuple[bool, str]:
    """Decide whether a /speak request may play.

    - No session_id (CLI/tests): always allowed.
    - Active session recorded: only that session speaks (last place the user typed).
    - No active session yet (fresh daemon): only interactive sessions speak.
    """
    sid = body.get("session_id")
    if not sid:
        return True, "manual"
    if active_session:
        if sid == active_session:
            return True, "active-session"
        return False, "inactive-session"
    if body.get("interactive"):
        return True, "interactive-fallback"
    return False, "not-interactive"


# ---------------------------------------------------------------- player

class Speaker:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.q: queue.Queue = queue.Queue()
        self.gen = 0                      # interrupt generation counter
        self.lock = threading.Lock()
        self.current_proc: subprocess.Popen | None = None
        self.speaking = False
        self.engine = None
        self.engine_name = None
        self.engine_lock = threading.Lock()
        self.tmpdir = Path(tempfile.mkdtemp(prefix="claude-speak-"))
        threading.Thread(target=self._worker, daemon=True).start()

    # -- engine management
    def ensure_engine(self):
        with self.engine_lock:
            want = self.cfg["engine"]
            if self.engine is not None and self.engine_name == want:
                return
            from adapters import ENGINES
            log(f"loading engine {want} voice={self.cfg['voice']}")
            t0 = time.perf_counter()
            eng = ENGINES[want](voice=self.cfg["voice"])
            eng.load()
            self.engine = eng
            self.engine_name = want
            log(f"engine {want} ready in {time.perf_counter()-t0:.1f}s")

    def set_voice(self, voice: str):
        with self.engine_lock:
            if self.engine is not None:
                self.engine.voice = voice

    # -- speaking
    def speak(self, text: str):
        clean = speechify(text, self.cfg["max_chars"])
        if not clean:
            return
        with self.lock:
            gen = self.gen
        for chunk in sentences(clean):
            self.q.put((gen, chunk))

    def stop(self):
        with self.lock:
            self.gen += 1
            proc = self.current_proc
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        if proc and proc.poll() is None:
            proc.terminate()

    def _worker(self):
        idx = 0
        while True:
            gen, chunk = self.q.get()
            with self.lock:
                if gen != self.gen:
                    continue
            try:
                self.ensure_engine()
                self.speaking = True
                idx += 1
                wav = str(self.tmpdir / f"chunk{idx % 8}.wav")
                speed = float(self.cfg.get("speed", 1.0))
                t0 = time.perf_counter()
                self.engine.synthesize(chunk, wav, speed=speed)
                log(f"synth {time.perf_counter()-t0:.2f}s: {chunk[:60]!r}")
                with self.lock:
                    if gen != self.gen:
                        continue
                    proc = subprocess.Popen(
                        ["afplay", wav],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self.current_proc = proc
                proc.wait()
            except Exception as e:
                log(f"worker error: {type(e).__name__}: {e}")
            finally:
                self.speaking = self.q.qsize() > 0


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    speaker: Speaker = None  # set at startup
    started = time.time()
    active_session: str | None = None
    seen_ids: "deque[str]" = None  # request_id dedupe ring

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except Exception:
            return {}

    def do_GET(self):
        sp = self.speaker
        if self.path == "/status":
            self._json(200, {
                "ok": True,
                "engine": sp.engine_name or sp.cfg["engine"],
                "engine_loaded": sp.engine is not None,
                "voice": sp.cfg["voice"],
                "speed": sp.cfg["speed"],
                "mode": sp.cfg["mode"],
                "enabled": sp.cfg["enabled"],
                "speaking": sp.speaking,
                "queued": sp.q.qsize(),
                "uptime_s": round(time.time() - self.started),
                "pid": os.getpid(),
            })
        elif self.path == "/voices":
            try:
                sp.ensure_engine()
                self._json(200, {"voices": sp.engine.voices()})
            except Exception as e:
                self._json(500, {"error": str(e)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        sp = self.speaker
        if self.path == "/speak":
            body = self._body()
            text = body.get("text", "")
            if not sp.cfg["enabled"]:
                self._json(200, {"queued": False, "reason": "disabled"})
                return
            rid = body.get("request_id")
            if rid:
                if rid in Handler.seen_ids:
                    self._json(200, {"queued": False, "reason": "duplicate"})
                    return
                Handler.seen_ids.append(rid)
            ok, reason = should_speak(Handler.active_session, body)
            if not ok:
                self._json(200, {"queued": False, "reason": reason})
                return
            if text.strip():
                sp.speak(text)
            self._json(200, {"queued": True, "reason": reason})
        elif self.path == "/stop":
            body = self._body()
            sid = body.get("session_id")
            if sid and body.get("interactive"):
                if Handler.active_session != sid:
                    log(f"active session -> {sid[:12]}")
                Handler.active_session = sid
            sp.stop()
            self._json(200, {"stopped": True})
        elif self.path == "/config":
            body = self._body()
            allowed = {"enabled", "engine", "voice", "speed", "mode", "max_chars"}
            changed = {k: v for k, v in body.items() if k in allowed}
            sp.cfg.update(changed)
            save_config(sp.cfg)
            if "voice" in changed:
                sp.set_voice(changed["voice"])
            if "engine" in changed:
                # lazy: next speak loads it
                with sp.engine_lock:
                    sp.engine = None
            self._json(200, {"ok": True, "config": sp.cfg})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, *a):
        pass


def main():
    cfg = load_config()
    port = int(cfg.get("port", 7865))
    speaker = Speaker(cfg)
    Handler.speaker = speaker
    Handler.seen_ids = deque(maxlen=64)
    PID_PATH.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    log(f"speakd starting on 127.0.0.1:{port} engine={cfg['engine']}")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        log(f"port {port} already in use — another speakd is running, exiting")
        return
    # Warm engine in background so first /speak is fast but startup isn't blocked
    threading.Thread(target=lambda: _safe_warm(speaker), daemon=True).start()
    server.serve_forever()


def _safe_warm(speaker):
    try:
        speaker.ensure_engine()
    except Exception as e:
        log(f"warm load failed: {e}")


if __name__ == "__main__":
    main()
