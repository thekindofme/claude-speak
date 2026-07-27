import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own ~/.claude-speak so nothing touches the real one."""
    monkeypatch.setenv("CLAUDE_SPEAK_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return tmp_path / "state"
