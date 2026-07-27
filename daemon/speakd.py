"""speakd — the voice of claude-speak.

Holds a TTS engine warm and owns every decision about when the machine is
allowed to make noise: which session may speak, how often, what jumps the
queue, and when to shut up because the user started typing.

HTTP API (127.0.0.1 only):
  GET  /health                     liveness, cheap
  GET  /status                     engine, voice, queue, per-session state
  GET  /voices                     voices for the current engine
  GET  /metrics                    counters by outcome
  POST /speak    {text, kind, session_id, source, request_id}
  POST /turn     {session_id}      user submitted a prompt: barge-in + new turn
  POST /close    {session_id, text} turn ended: speak a fallback iff Claude was silent
  POST /stop     {}                interrupt playback, clear queue
  POST /session  {session_id, enabled|muted_until|...}
  POST /config   {voice|speed|engine|enabled|...}

State: ~/.claude-speak/  (config.json, sessions/, speakd.log, utterances.jsonl)
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engines"))

from core import policy, presence, state, voice  # noqa: E402

LOG_MAX_BYTES = 2_000_000

# Paths are resolved per call, not at import: the state root is env-overridable
# and a long-lived daemon must not pin a stale one.


def log_path() -> Path:
    return state.home() / "speakd.log"


def utterances_path() -> Path:
    return state.home() / "utterances.jsonl"


def log(msg: str):
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > LOG_MAX_BYTES:
            p.replace(p.with_suffix(".log.1"))
        with p.open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def record(entry: dict):
    """Append to the utterance ledger — powers `speak tail`, `speak report`."""
    try:
        entry["ts"] = time.time()
        p = utterances_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- speaker

class Item:
    __slots__ = ("prio", "seq", "gen", "kind", "text", "session_id", "cancelled",
                 "created", "voice", "issues")

    def __init__(self, prio, seq, gen, kind, text, session_id,
                 created=None, voice=None, issues=None):
        self.prio, self.seq, self.gen = prio, seq, gen
        self.kind, self.text, self.session_id = kind, text, session_id
        self.cancelled = False
        self.created = created or time.time()
        self.voice = voice          # per-line override, for `speak preview`
        self.issues = issues or []

    def __lt__(self, other):
        return (self.prio, self.seq) < (other.prio, other.seq)


class Speaker:
    def __init__(self):
        self.q: queue.PriorityQueue[Item] = queue.PriorityQueue()
        self.gen = 0
        self.seq = 0
        self.lock = threading.RLock()
        self.current_proc: subprocess.Popen | None = None
        self.speaking = False
        self.engine = None
        self.engine_name = None
        self.engine_voice = None
        self.engine_lock = threading.Lock()
        self.tmpdir = Path(tempfile.mkdtemp(prefix="claude-speak-"))
        self.pending_progress: Item | None = None
        self.last_utterance_at = 0.0
        self.last_text = ""
        self.last_text_at = 0.0
        self.closed_this_turn: dict[str, bool] = {}
        threading.Thread(target=self._worker, daemon=True).start()

    # -- engine
    def ensure_engine(self, cfg: dict):
        with self.engine_lock:
            want, want_voice = cfg["engine"], cfg["voice"]
            if self.engine is not None and self.engine_name == want:
                if self.engine_voice != want_voice:
                    self.engine.voice = want_voice
                    self.engine_voice = want_voice
                return
            from adapters import ENGINES

            log(f"loading engine {want} voice={want_voice}")
            t0 = time.perf_counter()
            eng = ENGINES[want](voice=want_voice)
            eng.load()
            self.engine, self.engine_name, self.engine_voice = eng, want, want_voice
            log(f"engine {want} ready in {time.perf_counter() - t0:.1f}s")

    # -- enqueue
    def enqueue(self, text: str, kind: str, session_id: str, prio: int,
                coalesce: bool, source: str = "tool", created: float | None = None,
                voice: str | None = None, issues: list | None = None) -> None:
        with self.lock:
            if coalesce and self.pending_progress is not None:
                self.pending_progress.cancelled = True
            self.seq += 1
            item = Item(prio, self.seq, self.gen, kind, text, session_id,
                        created=created, voice=voice, issues=issues)
            if kind == "progress":
                self.pending_progress = item
            if session_id and source == "tool" and kind == "done":
                # Only a closing line closes the turn. Opening with "give me a
                # minute" and then going quiet is worse than never speaking —
                # the promise was made out loud, so the answer has to be too.
                # (Also why a spoken permission prompt can't cancel the close.)
                self.closed_this_turn[session_id] = True
            self.q.put(item)

    def stop(self):
        """Barge-in: drop everything queued and cut the current line short."""
        with self.lock:
            self.gen += 1
            self.pending_progress = None
            proc = self.current_proc
        drained = 0
        while True:
            try:
                self.q.get_nowait()
                drained += 1
            except queue.Empty:
                break
        if proc and proc.poll() is None:
            proc.terminate()
        return drained

    def new_turn(self, session_id: str):
        self.stop()
        with self.lock:
            self.closed_this_turn[session_id] = False

    def _worker(self):
        idx = 0
        while True:
            item = self.q.get()
            with self.lock:
                stale = item.gen != self.gen or item.cancelled
                if self.pending_progress is item:
                    self.pending_progress = None
            if stale:
                continue
            cfg0 = state.resolve(item.session_id)
            age = time.time() - item.created
            if age > float(cfg0.get("utterance_ttl_s", 45.0)) and item.kind != "done":
                # Speech is a live channel. A line that waited out a cold engine
                # start or a long queue is now a lie about the present.
                log(f"dropped stale {item.kind} after {age:.0f}s: {item.text[:50]!r}")
                record({"event": "suppressed", "reason": "stale", "kind": item.kind,
                        "text": item.text[:200], "session": item.session_id[:12],
                        "age_s": round(age, 1)})
                continue
            with self.lock:
                # Stamp on dequeue, not on playback: synthesis takes long enough
                # that a burst of lines would slip under the min-gap floor.
                self.last_utterance_at = time.time()
                self.last_text, self.last_text_at = item.text, self.last_utterance_at
            cfg = dict(cfg0)
            if item.voice:
                cfg["voice"] = item.voice
            try:
                self.ensure_engine(cfg)
            except Exception as e:
                log(f"engine load failed: {type(e).__name__}: {e}")
                continue
            chunks = voice.sentences(item.text)
            t_start = time.perf_counter()
            for chunk in chunks:
                with self.lock:
                    if item.gen != self.gen:
                        break
                    self.speaking = True
                try:
                    idx += 1
                    wav = str(self.tmpdir / f"chunk{idx % 8}.wav")
                    self.engine.synthesize(chunk, wav, speed=float(cfg["speed"]))
                    with self.lock:
                        if item.gen != self.gen:
                            break
                        cmd = ["afplay", wav]
                        vol = float(cfg.get("volume", 1.0))
                        if abs(vol - 1.0) > 0.01:
                            cmd += ["-v", f"{vol:.2f}"]
                        proc = subprocess.Popen(
                            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                        self.current_proc = proc
                    proc.wait()
                except Exception as e:
                    log(f"worker error: {type(e).__name__}: {e}")
                    break
            with self.lock:
                self.speaking = self.q.qsize() > 0
            record({"event": "spoken", "kind": item.kind, "text": item.text,
                    "session": item.session_id[:12], "chunks": len(chunks),
                    "words": len(item.text.split()), "issues": item.issues,
                    "lag_s": round(time.time() - item.created, 1),
                    "wall_s": round(time.perf_counter() - t_start, 2)})

    def runtime(self, active_session: str | None, seen: set,
                presence_block: str | None = None) -> policy.Runtime:
        with self.lock:
            return policy.Runtime(
                now=time.time(),
                active_session=active_session,
                last_utterance_at=self.last_utterance_at,
                last_text=self.last_text,
                last_text_at=self.last_text_at,
                queued_progress=self.pending_progress is not None,
                queue_depth=self.q.qsize(),
                presence_block=presence_block,
                seen_ids=seen,
            )


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    speaker: Speaker = None
    started = time.time()
    active_session: str | None = None
    seen_ids: deque = None
    counters: Counter = Counter()

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

    # -- GET
    def do_GET(self):
        sp = self.speaker
        if self.path == "/health":
            self._json(200, {"ok": True, "pid": os.getpid(),
                             "uptime_s": round(time.time() - self.started)})
        elif self.path == "/status":
            cfg = state.resolve(Handler.active_session)
            self._json(200, {
                "ok": True,
                "enabled": cfg["enabled"],
                "scope": cfg["source"],
                "engine": sp.engine_name or cfg["engine"],
                "engine_loaded": sp.engine is not None,
                "voice": cfg["voice"],
                "speed": cfg["speed"],
                "style": cfg["style"],
                "fallback": cfg["fallback"],
                "speaking": sp.speaking,
                "queued": sp.q.qsize(),
                "presence_block": presence.check(cfg),
                "active_session": Handler.active_session or "",
                "sessions": [
                    {"id": s["session_id"][:12], "enabled": s.get("enabled"),
                     "muted_until": s.get("muted_until", 0)}
                    for s in state.list_sessions()[:8]
                ],
                "uptime_s": round(time.time() - self.started),
                "pid": os.getpid(),
            })
        elif self.path == "/metrics":
            self._json(200, {"counters": dict(Handler.counters)})
        elif self.path == "/voices":
            try:
                sp.ensure_engine(state.load_config())
                self._json(200, {"voices": sp.engine.voices()})
            except Exception as e:
                self._json(500, {"error": str(e)})
        else:
            self._json(404, {"error": "not found"})

    # -- POST
    def do_POST(self):
        sp = self.speaker
        body = self._body()
        sid = body.get("session_id") or ""

        if self.path == "/speak":
            self._handle_speak(body, sid)
        elif self.path == "/turn":
            if sid:
                if Handler.active_session != sid:
                    log(f"active session -> {sid[:12]}")
                Handler.active_session = sid
            sp.new_turn(sid)
            eff = state.resolve(sid)
            self._json(200, {"ok": True, "enabled": eff["enabled"]})
        elif self.path == "/close":
            self._handle_close(body, sid)
        elif self.path == "/stop":
            drained = sp.stop()
            self._json(200, {"stopped": True, "drained": drained})
        elif self.path == "/session":
            patch = {k: v for k, v in body.items() if k in state.SESSION_KEYS}
            s = state.save_session(sid, patch) if sid else {}
            self._json(200, {"ok": True, "session": s,
                             "effective": state.resolve(sid)})
        elif self.path == "/config":
            cfg = state.save_config(body)
            if "engine" in body:
                with sp.engine_lock:
                    sp.engine = None  # reloads lazily on the next line
            self._json(200, {"ok": True, "config": cfg})
        else:
            self._json(404, {"error": "not found"})

    def _handle_speak(self, body: dict, sid: str):
        sp = self.speaker
        # The MCP server has no session id of its own: a tool call belongs to
        # whichever session the user last typed in, which is the active one.
        if not sid and body.get("source") == "tool":
            sid = Handler.active_session or ""
        if not sid and body.get("source") == "tool":
            # No live session yet (daemon restarted, or voice was switched on
            # mid-turn). One session with voice on is unambiguous: it's that
            # one. Two or more, and we don't guess — global config decides.
            live = [s["session_id"] for s in state.list_sessions()
                    if s.get("enabled")]
            if len(live) == 1:
                sid = live[0]
                Handler.active_session = sid
                log(f"adopted lone enabled session -> {sid[:12]}")
        kind = body.get("kind", "progress")
        source = body.get("source", "tool")
        eff = state.resolve(sid)
        block = None if source == "cli" else presence.check(eff)
        rt = sp.runtime(Handler.active_session, set(Handler.seen_ids), block)
        text, issues = voice.lint(body.get("text", ""),
                                  int(eff["max_utterance_chars"]))
        d = policy.decide(kind, text, sid, eff, rt,
                          request_id=body.get("request_id", ""), source=source)
        Handler.counters[d.reason] += 1
        if body.get("request_id"):
            Handler.seen_ids.append(body["request_id"])
        if not d.accept:
            record({"event": "suppressed", "reason": d.reason, "kind": kind,
                    "text": text[:200], "session": sid[:12]})
            self._json(200, {"spoken": False, "reason": d.reason, "issues": issues})
            return
        sp.enqueue(text, kind, sid, d.priority, d.coalesce, source,
                   created=body.get("created"), voice=body.get("voice"),
                   issues=issues)
        self._json(200, {"spoken": True, "reason": d.reason, "issues": issues,
                         "text": text})

    def _handle_close(self, body: dict, sid: str):
        """End of turn. Only speak if Claude never used its own voice."""
        sp = self.speaker
        eff = state.resolve(sid)
        if not eff["enabled"]:
            self._json(200, {"spoken": False, "reason": "voice-off"})
            return
        block = presence.check(eff)
        if block:
            Handler.counters[block] += 1
            self._json(200, {"spoken": False, "reason": block})
            return
        if sid and Handler.active_session and sid != Handler.active_session:
            self._json(200, {"spoken": False, "reason": "inactive-session"})
            return
        closed = sp.closed_this_turn.get(sid, False)
        mode = eff["fallback"]
        if closed and mode != "full":
            Handler.counters["close-already-spoke"] += 1
            self._json(200, {"spoken": False, "reason": "already-spoke"})
            return
        if mode == "off":
            Handler.counters["close-off"] += 1
            self._json(200, {"spoken": False, "reason": "fallback-off"})
            return
        text = voice.speechify(body.get("text", ""), int(eff["max_chars"]),
                               lead_only=(mode == "lead"))
        if not text:
            self._json(200, {"spoken": False, "reason": "empty"})
            return
        Handler.counters["close-fallback"] += 1
        log(f"fallback-{mode} sid={sid[:12]}: {text[:60]!r}")
        sp.enqueue(text, "done", sid, policy.PRIORITY["done"], False, source="fallback")
        self._json(200, {"spoken": True, "reason": f"fallback-{mode}"})

    def log_message(self, *a):
        pass


def main():
    cfg = state.load_config()
    port = int(cfg["port"])
    speaker = Speaker()
    Handler.speaker = speaker
    Handler.seen_ids = deque(maxlen=256)
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        log(f"port {port} in use — another speakd owns it, exiting")
        return
    home = state.home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "speakd.pid").write_text(str(os.getpid()))
    state.prune_sessions()
    log(f"speakd up on 127.0.0.1:{port} engine={cfg['engine']} enabled={cfg['enabled']}")
    threading.Thread(target=lambda: _safe_warm(speaker, cfg), daemon=True).start()
    server.serve_forever()


def _safe_warm(speaker: Speaker, cfg: dict):
    try:
        speaker.ensure_engine(cfg)
    except Exception as e:
        log(f"warm load failed: {e}")


if __name__ == "__main__":
    main()
