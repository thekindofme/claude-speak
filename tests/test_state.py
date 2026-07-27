import time

from core import state


def test_voice_is_off_until_asked_for():
    assert state.load_config()["enabled"] is False
    assert state.is_on("sess-a") is False


def test_session_override_beats_global_default():
    state.set_enabled(True, "sess-a", scope="session")
    assert state.is_on("sess-a") is True
    assert state.is_on("sess-b") is False, "voice must not leak into other sessions"
    assert state.resolve("sess-a")["source"] == "session"


def test_global_flip_clears_stale_session_overrides():
    state.set_enabled(False, "sess-a", scope="session")
    state.set_enabled(True, None, scope="global")
    assert state.is_on("sess-a") is True, "a global ON must not stay shadowed"
    assert state.resolve("sess-a")["source"] == "global"


def test_turning_on_queues_the_voice_brief():
    state.set_enabled(True, "sess-a", scope="session")
    assert state.resolve("sess-a")["needs_brief"] is True
    state.save_session("sess-a", {"needs_brief": False})
    assert state.resolve("sess-a")["needs_brief"] is False


def test_mute_wins_over_enabled_and_expires():
    state.set_enabled(True, "sess-a", scope="session")
    state.save_session("sess-a", {"muted_until": time.time() + 60})
    eff = state.resolve("sess-a")
    assert eff["enabled"] is False and eff["source"] == "muted"

    state.save_session("sess-a", {"muted_until": time.time() - 1})
    assert state.resolve("sess-a")["enabled"] is True


def test_v1_mode_config_migrates_to_fallback(isolated_home):
    (isolated_home / "config.json").write_text('{"mode": "summary", "speed": 1.2}')
    cfg = state.load_config()
    assert cfg["fallback"] == "off" and cfg["speed"] == 1.2 and "mode" not in cfg


def test_prune_drops_only_stale_sessions():
    state.save_session("fresh", {"enabled": True})
    state.save_session("stale", {"enabled": True})
    p = state.sessions_dir() / "stale.json"
    p.write_text(p.read_text().replace(
        str(state.load_session("stale")["updated"]), "1.0"))
    assert state.prune_sessions(ttl_h=1) == 1
    assert state.is_on("fresh") is True


def test_surface_breadcrumb_finds_the_session(monkeypatch):
    monkeypatch.setenv("CMUX_PANEL_ID", "panel-1")
    state.bind_surface("sess-xyz")
    assert state.current_session() == "sess-xyz"
    monkeypatch.setenv("CMUX_PANEL_ID", "panel-2")
    assert state.current_session() is None, "another terminal is another session"


def test_any_enabled_reports_session_level_opt_in():
    assert state.any_enabled() is False
    state.set_enabled(True, "sess-a", scope="session")
    assert state.any_enabled() is True
