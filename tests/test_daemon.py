"""End-to-end tests of the running daemon: HTTP in, playback out.

Real server, real queue, real worker thread — with the silent engine and a fake
`afplay`, so the whole path is exercised without making a sound.
"""

import json
import socket
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engines"))


class FakePlayer:
    """Stands in for `afplay`: finishes instantly, remembers it was asked."""

    played: list = []

    def __init__(self, cmd, **kw):
        FakePlayer.played.append(cmd)
        self._done = False

    def poll(self):
        return 0

    def wait(self):
        self._done = True
        return 0

    def terminate(self):
        self._done = True


@pytest.fixture
def daemon(monkeypatch, isolated_home):
    from core import presence, state
    import daemon.speakd as speakd

    state.save_config({"enabled": False, "engine": "null", "min_gap_s": 0.0})
    monkeypatch.setattr(speakd.subprocess, "Popen", FakePlayer)
    monkeypatch.setattr(presence, "check", lambda *a, **k: None)
    FakePlayer.played = []

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    speaker = speakd.Speaker()
    speakd.Handler.speaker = speaker
    speakd.Handler.seen_ids = speakd.deque(maxlen=64)
    speakd.Handler.active_session = None
    speakd.Handler.counters = speakd.Counter()
    server = ThreadingHTTPServer(("127.0.0.1", port), speakd.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield port
    server.shutdown()


def call(port, path, payload=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def spoken_texts(home):
    path = home / "utterances.jsonl"
    if not path.exists():
        return []
    return [json.loads(l)["text"] for l in path.read_text().splitlines()
            if json.loads(l).get("event") == "spoken"]


# --------------------------------------------------------------------- tests

def test_voice_off_means_nothing_reaches_the_speakers(daemon, isolated_home):
    r = call(daemon, "/speak", {"text": "hello", "session_id": "s1"})
    assert r == {"spoken": False, "reason": "voice-off", "issues": []}
    assert FakePlayer.played == []


def test_a_full_turn_speaks_and_records(daemon, isolated_home):
    from core import state

    state.set_enabled(True, "s1", scope="session")
    call(daemon, "/turn", {"session_id": "s1"})
    r = call(daemon, "/speak", {"text": "Tests are green.", "session_id": "s1",
                                "kind": "done", "source": "tool"})
    assert r["spoken"] is True
    assert wait_for(lambda: spoken_texts(isolated_home) == ["Tests are green."])
    assert FakePlayer.played, "afplay was never invoked"


def test_typing_cuts_speech_off_and_empties_the_queue(daemon, isolated_home):
    from core import state

    state.set_enabled(True, "s1", scope="session")
    call(daemon, "/turn", {"session_id": "s1"})
    for i in range(3):
        call(daemon, "/speak", {"text": f"Line {i}.", "session_id": "s1",
                                "kind": "milestone", "source": "tool"})
    r = call(daemon, "/turn", {"session_id": "s1"})   # user typed again
    assert r["ok"] is True
    time.sleep(0.3)
    before = len(spoken_texts(isolated_home))
    time.sleep(0.3)
    assert len(spoken_texts(isolated_home)) == before, "queue kept draining after barge-in"


def test_the_closing_fallback_only_fires_when_the_model_was_silent(daemon, isolated_home):
    from core import state

    state.set_enabled(True, "s1", scope="session")
    state.save_config({"fallback": "lead"})
    call(daemon, "/turn", {"session_id": "s1"})

    r = call(daemon, "/close", {"session_id": "s1", "text": "## Done\n\nShipped it."})
    assert r["reason"] == "fallback-lead"
    assert wait_for(lambda: "Shipped it." in spoken_texts(isolated_home))

    call(daemon, "/turn", {"session_id": "s1"})
    call(daemon, "/speak", {"text": "That's merged and deployed.", "session_id": "s1",
                            "kind": "done", "source": "tool"})
    r = call(daemon, "/close", {"session_id": "s1", "text": "Merged and deployed."})
    assert r["reason"] == "already-spoke"


def test_an_opening_line_still_gets_a_closing_one(daemon, isolated_home):
    """Saying "give me a minute" out loud and then going quiet is worse than
    never having spoken. Only a `done` line closes the turn."""
    from core import state

    state.set_enabled(True, "s1", scope="session")
    state.save_config({"fallback": "lead", "min_gap_s": 0.0})
    call(daemon, "/turn", {"session_id": "s1"})
    call(daemon, "/speak", {"text": "Walking you through it now.", "session_id": "s1",
                            "kind": "milestone", "source": "tool"})
    r = call(daemon, "/close", {"session_id": "s1", "text": "Here is the whole tour."})
    assert r["reason"] == "fallback-lead"


def test_a_spoken_permission_prompt_does_not_eat_the_closing_line(daemon, isolated_home):
    from core import state

    state.set_enabled(True, "s1", scope="session")
    call(daemon, "/turn", {"session_id": "s1"})
    call(daemon, "/speak", {"text": "I need your go-ahead.", "session_id": "s1",
                            "kind": "alert", "source": "hook"})
    r = call(daemon, "/close", {"session_id": "s1", "text": "All done here."})
    assert r["reason"] == "fallback-lead"


def test_a_line_that_went_stale_is_never_said(daemon, isolated_home):
    from core import state

    state.set_enabled(True, "s1", scope="session")
    state.save_config({"utterance_ttl_s": 5.0})
    call(daemon, "/turn", {"session_id": "s1"})
    call(daemon, "/speak", {"text": "This was true a minute ago.", "session_id": "s1",
                            "kind": "progress", "source": "tool",
                            "created": time.time() - 120})
    time.sleep(0.4)
    assert "This was true a minute ago." not in spoken_texts(isolated_home)


def test_a_meeting_silences_even_an_alert(daemon, isolated_home, monkeypatch):
    from core import presence, state

    state.set_enabled(True, "s1", scope="session")
    monkeypatch.setattr(presence, "check", lambda *a, **k: "call-app:zoom.us")
    call(daemon, "/turn", {"session_id": "s1"})
    r = call(daemon, "/speak", {"text": "Build failed!", "session_id": "s1",
                                "kind": "alert", "source": "tool"})
    assert r == {"spoken": False, "reason": "call-app:zoom.us", "issues": []}


def test_the_cli_can_still_talk_while_voice_is_off(daemon, isolated_home):
    r = call(daemon, "/speak", {"text": "Testing one two.", "source": "cli"})
    assert r["spoken"] is True
    assert wait_for(lambda: "Testing one two." in spoken_texts(isolated_home))


def test_status_reports_what_the_user_needs_to_know(daemon, isolated_home):
    from core import state

    state.set_enabled(True, "s1", scope="session")
    call(daemon, "/turn", {"session_id": "s1"})
    st = call(daemon, "/status")
    assert st["enabled"] is True and st["engine"] == "null"
    assert st["active_session"] == "s1"
    assert st["sessions"][0]["enabled"] is True
