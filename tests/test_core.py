import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "daemon"))
sys.path.insert(0, str(ROOT / "hooks"))

from speakd import speechify, sentences  # noqa: E402
from claude_speak_hook import last_assistant_text  # noqa: E402


def test_speechify_strips_code_blocks():
    text = "Fixed the bug.\n```python\nprint('hi')\n```\nAll tests pass."
    out = speechify(text, 2000)
    assert "print" not in out
    assert "Code block omitted" in out
    assert "All tests pass." in out


def test_speechify_prefers_speak_block():
    text = "Long analysis here.\n<speak>Short summary for the ear.</speak>"
    assert speechify(text, 2000) == "Short summary for the ear."


def test_speechify_strips_markdown():
    text = "## Result\n- **bold** item\n- [link](https://x.com/y)\n`inline` code"
    out = speechify(text, 2000)
    assert "#" not in out and "**" not in out and "](" not in out and "`" not in out
    assert "bold item" in out and "link" in out


def test_speechify_tables_summarised():
    text = "Compare:\n| a | b |\n|---|---|\n| 1 | 2 |\nDone."
    out = speechify(text, 2000)
    assert "|" not in out
    assert "table" in out.lower()


def test_speechify_caps_length():
    text = ("This is a sentence. " * 300).strip()
    out = speechify(text, 500)
    assert len(out) < 560
    assert out.endswith("The rest is on screen.")


def test_speechify_shortens_paths():
    out = speechify("Edited src/components/deep/Button.tsx today.", 2000)
    assert "src/components" not in out
    assert "Button.tsx" in out


def test_sentences_chunking():
    text = "One. Two. " + "Three is a much longer sentence that goes on. " * 10
    chunks = sentences(text, chunk_chars=100)
    assert all(len(c) <= 160 for c in chunks)
    assert " ".join(chunks).startswith("One. Two.")


def test_last_assistant_text(tmp_path):
    t = tmp_path / "t.jsonl"
    lines = [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "x"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "final answer"}]}},
    ]
    t.write_text("\n".join(json.dumps(l) for l in lines))
    assert last_assistant_text(str(t)) == "final answer"


def test_last_assistant_text_missing_file():
    assert last_assistant_text("/nonexistent/x.jsonl") == ""


def test_hook_never_crashes_on_garbage_stdin():
    p = subprocess.run(
        [sys.executable, str(ROOT / "hooks/claude_speak_hook.py"), "stop"],
        input="not json", capture_output=True, text=True,
    )
    assert p.returncode == 0
