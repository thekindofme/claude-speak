import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "daemon"))

from speakd import should_speak  # noqa: E402


def test_manual_requests_always_speak():
    ok, reason = should_speak("some-active", {"text": "hi"})
    assert ok and reason == "manual"
    ok, _ = should_speak(None, {"text": "hi"})
    assert ok


def test_active_session_speaks():
    ok, reason = should_speak("abc", {"session_id": "abc", "text": "x"})
    assert ok and reason == "active-session"


def test_other_session_blocked_when_active_set():
    ok, reason = should_speak("abc", {"session_id": "zzz", "interactive": True})
    assert not ok and reason == "inactive-session"


def test_fresh_daemon_interactive_fallback():
    ok, reason = should_speak(None, {"session_id": "abc", "interactive": True})
    assert ok and reason == "interactive-fallback"


def test_fresh_daemon_blocks_headless():
    ok, reason = should_speak(None, {"session_id": "abc", "interactive": False})
    assert not ok and reason == "not-interactive"
