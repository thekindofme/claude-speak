import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))


@pytest.fixture
def srv(monkeypatch):
    import server

    importlib.reload(server)
    return server


def rpc(srv, capsys, method, params=None, msg_id=1):
    srv.handle({"jsonrpc": "2.0", "id": msg_id, "method": method,
                "params": params or {}})
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_handshake_echoes_the_client_protocol(srv, capsys):
    r = rpc(srv, capsys, "initialize", {"protocolVersion": "2024-11-05"})
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == "claude-speak"


def test_notifications_get_no_reply(srv, capsys):
    assert rpc(srv, capsys, "notifications/initialized") is None


def test_the_only_tool_is_speak(srv, capsys):
    tools = rpc(srv, capsys, "tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == ["speak"]
    assert "kind" in tools[0]["inputSchema"]["properties"]


def test_speaking_while_voice_is_off_never_starts_the_daemon(srv, capsys, monkeypatch):
    started = []
    monkeypatch.setattr(srv.client, "alive", lambda *a, **k: False)
    monkeypatch.setattr(srv.client, "spawn_daemon", lambda *a, **k: started.append(1))
    r = rpc(srv, capsys, "tools/call",
            {"name": "speak", "arguments": {"text": "hello"}})
    assert "voice is off" in r["result"]["content"][0]["text"]
    assert not started


def test_a_spoken_line_reports_back_with_coaching(srv, capsys, monkeypatch):
    from core import state

    state.set_enabled(True, "s1", scope="session")
    monkeypatch.setattr(srv.client, "speak", lambda *a, **k: {
        "spoken": True, "reason": "ok", "issues": ["31 words — aim under 28"]})
    r = rpc(srv, capsys, "tools/call",
            {"name": "speak", "arguments": {"text": "hi", "kind": "done"}})
    text = r["result"]["content"][0]["text"]
    assert text.startswith("spoken (done)") and "aim under 28" in text


def test_suppression_reasons_come_back_as_guidance(srv, capsys, monkeypatch):
    from core import state

    state.set_enabled(True, "s1", scope="session")
    monkeypatch.setattr(srv.client, "speak",
                        lambda *a, **k: {"spoken": False, "reason": "rate-limited"})
    r = rpc(srv, capsys, "tools/call",
            {"name": "speak", "arguments": {"text": "hi"}})
    assert "Speak at moments" in r["result"]["content"][0]["text"]


def test_a_broken_voice_never_breaks_the_turn(srv, capsys, monkeypatch):
    from core import state

    state.set_enabled(True, "s1", scope="session")

    def boom(*a, **k):
        raise RuntimeError("audio device on fire")

    monkeypatch.setattr(srv.client, "speak", boom)
    r = rpc(srv, capsys, "tools/call", {"name": "speak", "arguments": {"text": "hi"}})
    assert r["result"]["isError"] is False
    assert "RuntimeError" in r["result"]["content"][0]["text"]


def test_unknown_tool_is_an_error_not_a_crash(srv, capsys):
    r = rpc(srv, capsys, "tools/call", {"name": "sing", "arguments": {}})
    assert r["error"]["code"] == -32602
