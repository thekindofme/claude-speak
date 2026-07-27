from datetime import datetime

from core import presence


def at(h, m=0):
    return datetime(2026, 7, 26, h, m)


def test_quiet_hours_covers_a_normal_window():
    assert presence.in_quiet_hours("09:00-17:00", at(12)) is True
    assert presence.in_quiet_hours("09:00-17:00", at(8)) is False
    assert presence.in_quiet_hours("09:00-17:00", at(17)) is False, "end is exclusive"


def test_quiet_hours_wraps_past_midnight():
    assert presence.in_quiet_hours("22:00-08:00", at(23)) is True
    assert presence.in_quiet_hours("22:00-08:00", at(3)) is True
    assert presence.in_quiet_hours("22:00-08:00", at(9)) is False


def test_no_window_means_never_quiet():
    assert presence.in_quiet_hours("", at(3)) is False
    assert presence.in_quiet_hours("garbage", at(3)) is False


def test_locked_screen_wins_over_everything(monkeypatch):
    presence.invalidate()
    monkeypatch.setattr(presence, "screen_locked", lambda: True)
    assert presence.check({}, use_cache=False) == "screen-locked"


def test_a_muted_mac_is_taken_as_no(monkeypatch):
    presence.invalidate()
    monkeypatch.setattr(presence, "screen_locked", lambda: False)
    monkeypatch.setattr(presence, "screensaver_running", lambda: False)
    monkeypatch.setattr(presence, "output_muted", lambda: True)
    assert presence.check({}, use_cache=False) == "output-muted"


def test_a_running_call_app_names_itself(monkeypatch):
    presence.invalidate()
    monkeypatch.setattr(presence, "screen_locked", lambda: False)
    monkeypatch.setattr(presence, "screensaver_running", lambda: False)
    monkeypatch.setattr(presence, "output_muted", lambda: False)
    monkeypatch.setattr(presence, "call_app_running", lambda apps: "zoom.us")
    assert presence.check({}, use_cache=False) == "call-app:zoom.us"


def test_an_empty_room_is_fine(monkeypatch):
    presence.invalidate()
    for name in ("screen_locked", "screensaver_running", "output_muted"):
        monkeypatch.setattr(presence, name, lambda: False)
    monkeypatch.setattr(presence, "call_app_running", lambda apps: None)
    assert presence.check({}, use_cache=False) is None


def test_probes_are_cached_so_a_burst_costs_one_check(monkeypatch):
    presence.invalidate()
    calls = []
    monkeypatch.setattr(presence, "screen_locked", lambda: calls.append(1) or False)
    monkeypatch.setattr(presence, "screensaver_running", lambda: False)
    monkeypatch.setattr(presence, "output_muted", lambda: False)
    monkeypatch.setattr(presence, "call_app_running", lambda apps: None)
    for _ in range(5):
        presence.check({})
    assert len(calls) == 1


def test_an_unreadable_probe_never_silences_by_accident(monkeypatch):
    monkeypatch.setattr(presence, "_run", lambda *a, **k: (1, ""))
    assert presence.screen_locked() is False
    assert presence.output_muted() is False
