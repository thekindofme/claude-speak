"""Is it safe to make noise in this room, right now?

The worst thing a voice product can do is talk during someone's meeting. These
are the cheap, supported macOS signals for "don't". Each is a subprocess, so the
whole verdict is cached for a couple of seconds — a burst of utterances must not
turn into a burst of `ioreg` calls.

Deliberately NOT here: macOS Focus / Do Not Disturb. On macOS 26 there is no
supported read path for Focus state (the old `com.apple.notificationcenterui`
key and `~/Library/DoNotDisturb/DB` are both gone), and guessing wrong in the
permissive direction is exactly the failure this module exists to prevent. Quiet
hours and the call-app list cover the same ground honestly.
"""

from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime

_CACHE: tuple[float, str | None] = (0.0, None)
CACHE_TTL_S = 3.0

DEFAULT_QUIET_APPS = ["zoom.us", "Microsoft Teams", "Webex", "RingCentral",
                      "BlueJeans", "GoToMeeting", "Around"]


def _run(cmd: list[str], timeout: float = 1.5) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def screen_locked() -> bool:
    code, out = _run(["ioreg", "-n", "Root", "-d1", "-k", "CGSSessionScreenIsLocked"])
    if code != 0:
        return False
    m = re.search(r'"CGSSessionScreenIsLocked"\s*=\s*(\w+)', out)
    # Key absent means unlocked. Only a positive value means locked, so an
    # unreadable ioreg can never silence the product by accident.
    return bool(m) and m.group(1).lower() in ("yes", "true", "1")


def screensaver_running() -> bool:
    return _run(["pgrep", "-x", "ScreenSaverEngine"])[0] == 0


def output_muted() -> bool:
    code, out = _run(["osascript", "-e", "output muted of (get volume settings)"])
    return code == 0 and out.strip() == "true"


def call_app_running(apps: list[str]) -> str | None:
    for app in apps:
        if _run(["pgrep", "-x", app])[0] == 0:
            return app
    return None


def in_quiet_hours(window: str, now: datetime | None = None) -> bool:
    """window is "HH:MM-HH:MM"; it may wrap past midnight."""
    if not window:
        return False
    try:
        start_s, end_s = window.split("-")
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
    except Exception:
        return False
    n = now or datetime.now()
    cur = n.hour * 60 + n.minute
    start, end = sh * 60 + sm, eh * 60 + em
    return start <= cur < end if start <= end else (cur >= start or cur < end)


def check(cfg: dict, now: datetime | None = None, use_cache: bool = True) -> str | None:
    """Return the reason to stay silent, or None if it's fine to speak."""
    global _CACHE
    if in_quiet_hours(cfg.get("quiet_hours", ""), now):
        return "quiet-hours"          # config-only, always fresh and free

    if use_cache and time.time() - _CACHE[0] < CACHE_TTL_S:
        return _CACHE[1]

    reason = None
    if cfg.get("respect_lock", True) and (screen_locked() or screensaver_running()):
        reason = "screen-locked"
    elif cfg.get("respect_mute", True) and output_muted():
        reason = "output-muted"
    else:
        app = call_app_running(cfg.get("quiet_apps") or DEFAULT_QUIET_APPS)
        if app:
            reason = f"call-app:{app}"

    _CACHE = (time.time(), reason)
    return reason


def invalidate():
    global _CACHE
    _CACHE = (0.0, None)
