"""Shared config + session state for claude-speak.

Stdlib only and import-cheap: hooks run this on every prompt with the system
python, so it must never pull in numpy/torch or touch the network.

Every surface (hook, daemon, MCP server, CLI) answers "is voice on right now?"
through `resolve()`, so there is exactly one answer.

State lives in two layers:

    config.json          global defaults          (voice off until opted in)
    sessions/<sid>.json  per-session override     (what `speak on` flips)

A session override always wins over the global default, which is what makes
`speak on` inside one Claude Code tab affect only that tab.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULTS = {
    # --- master switch -----------------------------------------------------
    "enabled": False,          # voice is OFF until the user opts in
    "scope": "session",        # what `speak on` means by default
    # --- audio -------------------------------------------------------------
    "engine": "kokoro-mlx",
    "voice": "af_heart",
    "speed": 1.05,
    "volume": 1.0,
    # --- behaviour ---------------------------------------------------------
    "style": "companion",      # companion | terse | narrate
    "fallback": "lead",        # off | lead | full — used only if Claude stayed silent
    "min_gap_s": None,         # explicit floor between non-urgent lines;
                               # None means the floor follows `style`
    "utterance_ttl_s": 45.0,   # a line this stale is no longer worth saying
    "max_utterance_chars": 320,
    "max_chars": 1200,         # cap on fallback speech
    "notify": True,            # speak permission prompts / idle nudges
    # --- when NOT to talk ---------------------------------------------------
    "respect_lock": True,      # locked screen or screensaver: stay quiet
    "respect_mute": True,      # Mac output muted: the user already said no
    "quiet_apps": [],          # running app names that mean "in a meeting"
    "quiet_hours": "",         # "22:00-08:00"
    # --- first run ----------------------------------------------------------
    "onboarded": False,
    # --- plumbing ----------------------------------------------------------
    "port": 7865,
    "session_ttl_h": 48,
}

# Old (v1) `mode` values map onto the new fallback behaviour. The <speak> block
# and full-reply narration are both superseded by the speak tool channel.
_MODE_MIGRATION = {"full": "full", "summary": "off", "auto": "lead"}

SESSION_KEYS = ("enabled", "style", "voice", "speed", "fallback", "muted_until",
                "needs_brief", "label")


def home() -> Path:
    """Root of claude-speak's state. Overridable for tests via CLAUDE_SPEAK_HOME."""
    return Path(os.environ.get("CLAUDE_SPEAK_HOME") or (Path.home() / ".claude-speak"))


def config_path() -> Path:
    return home() / "config.json"


def sessions_dir() -> Path:
    return home() / "sessions"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)  # atomic: a torn config would silently mute the product


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    raw = _read_json(config_path())
    if "mode" in raw and "fallback" not in raw:
        raw["fallback"] = _MODE_MIGRATION.get(raw.pop("mode"), "lead")
    raw.pop("mode", None)
    if raw.get("min_gap_s") == 6.0:
        # 6.0 was the old hardcoded default, so nobody chose it on purpose.
        # Hand the floor back to `style`, which now actually controls it.
        raw["min_gap_s"] = None
    cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    return cfg


def save_config(patch: dict) -> dict:
    cfg = load_config()
    cfg.update({k: v for k, v in patch.items() if k in DEFAULTS})
    _write_json(config_path(), cfg)
    return cfg


# ------------------------------------------------------------------ sessions

def _session_path(session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64]
    return sessions_dir() / f"{safe}.json"


def load_session(session_id: str | None) -> dict:
    if not session_id:
        return {}
    return _read_json(_session_path(session_id))


def save_session(session_id: str, patch: dict) -> dict:
    if not session_id:
        return {}
    s = load_session(session_id)
    s.update({k: v for k, v in patch.items() if k in SESSION_KEYS})
    s["updated"] = time.time()
    _write_json(_session_path(session_id), s)
    return s


def forget_session(session_id: str):
    _session_path(session_id).unlink(missing_ok=True)


def prune_sessions(ttl_h: float | None = None) -> int:
    """Drop session overrides older than the TTL. Returns how many were dropped."""
    ttl = (ttl_h if ttl_h is not None else load_config()["session_ttl_h"]) * 3600
    cutoff = time.time() - ttl
    n = 0
    try:
        for p in sessions_dir().glob("*.json"):
            if _read_json(p).get("updated", 0) < cutoff:
                p.unlink(missing_ok=True)
                n += 1
    except Exception:
        pass
    return n


def list_sessions() -> list[dict]:
    out = []
    try:
        for p in sorted(sessions_dir().glob("*.json")):
            s = _read_json(p)
            s["session_id"] = p.stem
            out.append(s)
    except Exception:
        pass
    return sorted(out, key=lambda s: s.get("updated", 0), reverse=True)


def mcp_registered() -> bool:
    """Is the `speak` tool wired into Claude Code?

    A session that started before install has no such tool, so the brief must
    fall back to the <speak> block instead of promising a tool that isn't there.
    """
    try:
        cfg = json.loads((Path.home() / ".claude.json").read_text())
    except Exception:
        return False
    if "claude-speak" in (cfg.get("mcpServers") or {}):
        return True
    return any("claude-speak" in (p.get("mcpServers") or {})
               for p in (cfg.get("projects") or {}).values())


def any_enabled() -> bool:
    """Is voice on anywhere — globally, or for some live session?"""
    return bool(load_config()["enabled"]) or any(
        s.get("enabled") for s in list_sessions()
    )


# ------------------------------------------------------------------ surfaces
#
# Claude Code does not put its session id in the environment, so `speak on`
# typed inside a session cannot name the session it belongs to. The hooks know
# both, so they leave a breadcrumb: terminal surface -> session id. The CLI runs
# in that same surface and follows it back.

def surface_key() -> str:
    for var in ("CMUX_PANEL_ID", "WEZTERM_PANE", "ITERM_SESSION_ID", "TERM_SESSION_ID"):
        v = os.environ.get(var)
        if v:
            return "".join(c for c in v if c.isalnum() or c in "-_")[:64]
    try:
        return "tty-" + os.ttyname(0).replace("/", "-")
    except Exception:
        return "default"


def surfaces_dir() -> Path:
    return home() / "surfaces"


def bind_surface(session_id: str, key: str | None = None):
    if not session_id:
        return
    p = surfaces_dir() / (key or surface_key())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(session_id)


def current_session() -> str | None:
    """Best guess at the session id of the Claude Code session we're inside."""
    env_sid = os.environ.get("CLAUDE_SESSION_ID")
    if env_sid:
        return env_sid
    try:
        return (surfaces_dir() / surface_key()).read_text().strip() or None
    except Exception:
        return None


# ------------------------------------------------------------------ resolve

def resolve(session_id: str | None = None) -> dict:
    """Effective settings for a session: session override > global default."""
    cfg = load_config()
    s = load_session(session_id)
    eff = dict(cfg)
    for k in SESSION_KEYS:
        if k in s and k in cfg:
            eff[k] = s[k]
    eff["source"] = "session" if "enabled" in s else "global"

    muted_until = s.get("muted_until", 0)
    if muted_until and time.time() < muted_until:
        eff["enabled"] = False
        eff["source"] = "muted"
        eff["muted_for_s"] = round(muted_until - time.time())
    eff["needs_brief"] = bool(s.get("needs_brief"))
    eff["session_id"] = session_id or ""
    return eff


def is_on(session_id: str | None = None) -> bool:
    return bool(resolve(session_id)["enabled"])


def set_enabled(enabled: bool, session_id: str | None = None, scope: str = "session"):
    """Turn voice on/off. `scope='global'` moves the default for new sessions."""
    if scope == "global" or not session_id:
        save_config({"enabled": enabled})
        # A global flip should not be shadowed by a stale session override.
        for s in list_sessions():
            if "enabled" in s:
                p = _session_path(s["session_id"])
                data = _read_json(p)
                data.pop("enabled", None)
                data["updated"] = time.time()
                _write_json(p, data)
    else:
        save_session(session_id, {"enabled": enabled})
    if enabled and session_id:
        # Claude has not been briefed yet in this session; the next prompt hook
        # injects the full voice brief and clears this.
        save_session(session_id, {"needs_brief": True})
