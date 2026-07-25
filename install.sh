#!/bin/zsh
# Install claude-speak into Claude Code: CLI on PATH, /speak skill, hooks.
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"

# 1. CLI
mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/bin/speak" "$HOME/.local/bin/speak"
echo "CLI: ~/.local/bin/speak -> $ROOT/bin/speak"

# 2. Skill
mkdir -p "$HOME/.claude/skills"
ln -sfn "$ROOT/skill" "$HOME/.claude/skills/speak"
echo "Skill: ~/.claude/skills/speak"

# 3. Hooks (idempotent merge into ~/.claude/settings.json)
python3 - "$ROOT" <<'EOF'
import json, sys
from pathlib import Path

root = sys.argv[1]
p = Path.home() / ".claude/settings.json"
settings = json.loads(p.read_text()) if p.exists() else {}
hooks = settings.setdefault("hooks", {})

def ensure(event, command, timeout):
    arr = hooks.setdefault(event, [])
    for grp in arr:
        for h in grp.get("hooks", []):
            if "claude_speak_hook.py" in h.get("command", ""):
                h["command"] = command  # refresh path if repo moved
                h["timeout"] = timeout
                return
    arr.append({"hooks": [{"type": "command", "command": command, "timeout": timeout}]})

ensure("Stop", f"python3 {root}/hooks/claude_speak_hook.py stop", 10)
ensure("UserPromptSubmit", f"python3 {root}/hooks/claude_speak_hook.py prompt", 5)
p.write_text(json.dumps(settings, indent=2) + "\n")
print("Hooks: Stop + UserPromptSubmit registered in ~/.claude/settings.json")
EOF

# 4. Default config (never clobber an existing one)
mkdir -p "$HOME/.claude-speak"
if [ ! -f "$HOME/.claude-speak/config.json" ]; then
  cat > "$HOME/.claude-speak/config.json" <<'JSON'
{
  "enabled": true,
  "engine": "kokoro-mlx",
  "voice": "af_heart",
  "speed": 1.0,
  "mode": "auto",
  "max_chars": 2200,
  "port": 7865
}
JSON
  echo "Config: ~/.claude-speak/config.json created"
fi

echo "Done. Try: speak on && speak say testing one two three"
