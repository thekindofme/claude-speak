from core.policy import Runtime, decide, min_gap

ON = {"enabled": True, "min_gap_s": 6.0, "source": "session"}
OFF = {"enabled": False, "min_gap_s": 6.0, "source": "global"}


def rt(**kw):
    base = dict(now=1000.0, active_session="s1")
    base.update(kw)
    return Runtime(**base)


def test_silent_when_voice_is_off():
    d = decide("done", "all set", "s1", OFF, rt())
    assert not d.accept and d.reason == "voice-off"


def test_muted_reports_itself_as_muted():
    eff = {**OFF, "source": "muted"}
    assert decide("done", "hi", "s1", eff, rt()).reason == "muted"


def test_only_the_session_the_user_is_in_may_speak():
    assert decide("done", "hi", "s2", ON, rt()).reason == "inactive-session"
    assert decide("done", "hi", "s1", ON, rt()).accept


def test_progress_is_rate_limited():
    r = rt(last_utterance_at=998.0)      # 2s ago, gap is 6s
    assert decide("progress", "still going", "s1", ON, r).reason == "rate-limited"


def test_questions_jump_the_rate_limit_and_the_queue():
    r = rt(last_utterance_at=999.5)
    d = decide("question", "postgres or sqlite?", "s1", ON, r)
    assert d.accept and d.priority == 0


def test_stale_progress_is_replaced_not_stacked():
    r = rt(last_utterance_at=990.0, queued_progress=True)
    d = decide("progress", "now on step 3", "s1", ON, r)
    assert d.accept and d.coalesce and d.reason == "coalesced"


def test_the_same_line_is_not_said_twice():
    r = rt(last_utterance_at=900.0, last_text="tests are green", last_text_at=999.0)
    assert decide("done", "tests are green", "s1", ON, r).reason == "repeat"


def test_manual_cli_speech_bypasses_every_gate():
    d = decide("alert", "testing", "", OFF, rt(), source="cli")
    assert d.accept and d.reason == "manual"


def test_retries_do_not_double_speak():
    r = rt(seen_ids={"abc"})
    assert decide("done", "hi", "s1", ON, r, request_id="abc").reason == "duplicate-request"


def test_empty_text_is_dropped():
    assert decide("done", "   ", "s1", ON, rt()).reason == "empty"


def test_unknown_kind_degrades_to_progress():
    d = decide("banter", "hey", "s1", ON, rt(last_utterance_at=0))
    assert d.accept and d.priority == 2


def test_style_sets_the_gap_when_no_explicit_floor():
    """`style` has to move the machine, not just the wording of the brief."""
    assert min_gap({"style": "narrate", "min_gap_s": None}) == 2.5
    assert min_gap({"style": "companion", "min_gap_s": None}) == 6.0
    assert min_gap({"style": "terse", "min_gap_s": None}) == 15.0


def test_an_explicit_floor_beats_the_style():
    assert min_gap({"style": "terse", "min_gap_s": 1.0}) == 1.0


def test_narrate_lets_a_walkthrough_through():
    eff = {"enabled": True, "min_gap_s": None, "style": "narrate", "source": "session"}
    r = rt(last_utterance_at=996.0)      # 4s ago
    assert decide("progress", "next part", "s1", eff, r).accept
    assert decide("progress", "next part", "s1", {**eff, "style": "terse"},
                  r).reason == "rate-limited"
