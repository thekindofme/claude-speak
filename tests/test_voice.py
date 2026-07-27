import json

from core import voice


# ------------------------------------------------------------- voice channel

def test_paths_are_spoken_as_names_not_addresses():
    clean, issues = voice.lint("Fixed it in src/api/auth/middleware.ts:42.")
    assert "src/api" not in clean and "middleware" in clean
    assert any("file path" in i for i in issues)


def test_line_references_keep_the_number_but_lose_the_path():
    clean, _ = voice.lint("See auth.ts:42")
    assert clean == "See auth, line 42"


def test_code_blocks_never_reach_the_speakers():
    clean, issues = voice.lint("Done.\n```python\nprint(1)\n```")
    assert "print" not in clean
    assert any("code block" in i for i in issues)


def test_rambling_is_flagged_with_a_word_count():
    long = " ".join(["word"] * 40)
    _, issues = voice.lint(long)
    assert any("40 words" in i for i in issues)


def test_pointing_at_the_screen_is_flagged():
    _, issues = voice.lint("The details are in the table above.")
    assert any("screen" in i for i in issues)


def test_lists_read_aloud_are_flagged():
    _, issues = voice.lint("- first\n- second")
    assert any("list" in i for i in issues)


def test_markdown_and_symbols_become_speech():
    clean, _ = voice.lint("**Done** — 50% faster, auth -> cache")
    assert "**" not in clean and "50 percent" in clean and " then " in clean


def test_a_clean_line_passes_untouched():
    clean, issues = voice.lint("Tests are green. Opening the PR now.")
    assert issues == [] or all("word" not in i for i in issues)
    assert clean.startswith("Tests are green")


def test_lexicon_fixes_words_tts_gets_wrong():
    clean, _ = voice.lint("The handler is idempotent.")
    assert "eye-dem-potent" in clean


def test_user_lexicon_overrides_the_defaults(isolated_home):
    (isolated_home / "lexicon.json").write_text(json.dumps({"kubectl": "koob cuttle"}))
    clean, _ = voice.lint("Run kubectl now.")
    assert "koob cuttle" in clean


# ---------------------------------------------------------- fallback channel

REPLY = """## Result

Fixed the **token check** in `src/auth.ts:11`.

| case | before | after |
|---|---|---|
| expiry | fails | passes |

```ts
if (exp <= now) reject()
```

Next: deploy to dev, then prod.
"""


def test_fallback_lead_says_the_headline_only():
    spoken = voice.speechify(REPLY, lead_only=True)
    assert "|" not in spoken and "```" not in spoken and "src/auth" not in spoken
    assert len(spoken) < 220


def test_fallback_full_keeps_prose_and_flags_the_rest():
    spoken = voice.speechify(REPLY, max_chars=60)
    assert spoken.endswith("The rest is on screen.")


def test_legacy_speak_block_still_wins():
    spoken = voice.speechify("Big reply.\n<speak>Short version.</speak>")
    assert spoken == "Short version."


def test_chunking_splits_on_sentences_for_early_playback():
    text = "One sentence here. " * 20
    chunks = voice.sentences(text, chunk_chars=100)
    assert len(chunks) > 1 and all(len(c) <= 120 for c in chunks)
