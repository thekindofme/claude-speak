#!/bin/zsh
# Install claude-speak: CLI on PATH, /speak skill, hooks, MCP voice channel.
# Idempotent. Voice stays OFF until you run `speak on`.
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"

# 1. CLI
mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/bin/speak" "$HOME/.local/bin/speak"
chmod +x "$ROOT/bin/speak" "$ROOT/hooks/claude_speak_hook.py" "$ROOT/mcp/server.py"
echo "CLI        ~/.local/bin/speak"

# 2. Skill
mkdir -p "$HOME/.claude/skills"
ln -sfn "$ROOT/skill" "$HOME/.claude/skills/speak"
echo "Skill      ~/.claude/skills/speak"

# 3. Hooks + permissions (idempotent merge into ~/.claude/settings.json)
python3 - "$ROOT" <<'EOF'
import json, sys
from pathlib import Path

root = sys.argv[1]
p = Path.home() / ".claude/settings.json"
settings = json.loads(p.read_text()) if p.exists() else {}
hooks = settings.setdefault("hooks", {})

def ensure(event, arg, timeout):
    command = f"python3 {root}/hooks/claude_speak_hook.py {arg}"
    for grp in hooks.setdefault(event, []):
        for h in grp.get("hooks", []):
            if "claude_speak_hook.py" in h.get("command", ""):
                h.update(type="command", command=command, timeout=timeout)
                return
    hooks[event].append({"hooks": [
        {"type": "command", "command": command, "timeout": timeout}]})

ensure("SessionStart",     "session-start", 10)
ensure("UserPromptSubmit", "prompt",         5)
ensure("Notification",     "notification",   5)
ensure("Stop",             "stop",          10)
ensure("SessionEnd",       "end",            5)

allow = settings.setdefault("permissions", {}).setdefault("allow", [])
if "mcp__claude-speak__speak" not in allow:
    allow.append("mcp__claude-speak__speak")

p.write_text(json.dumps(settings, indent=2) + "\n")
print("Hooks      SessionStart, UserPromptSubmit, Notification, Stop, SessionEnd")
print("Permission mcp__claude-speak__speak pre-approved")
EOF

# 4. MCP voice channel
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
if claude mcp get claude-speak >/dev/null 2>&1; then
  claude mcp remove claude-speak >/dev/null 2>&1 || true
fi
claude mcp add --scope user claude-speak -- "$PY" "$ROOT/mcp/server.py" >/dev/null
echo "MCP        claude-speak (tool: speak)"

# 5. State — never clobber real settings, but v1 defaulted to ON; v2 does not.
python3 - <<'EOF'
import json
from pathlib import Path

home = Path.home() / ".claude-speak"
home.mkdir(exist_ok=True)
(home / "sessions").mkdir(exist_ok=True)
cfg_path = home / "config.json"
cfg = {}
if cfg_path.exists():
    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception:
        cfg = {}
migrated = "mode" in cfg
if migrated:
    cfg["fallback"] = {"full": "full", "summary": "off"}.get(cfg.pop("mode"), "lead")
    cfg["enabled"] = False          # v2 is opt-in; do not inherit v1's always-on
defaults = {"enabled": False, "engine": "kokoro-mlx", "voice": "af_heart",
            "speed": 1.05, "style": "companion", "fallback": "lead",
            "min_gap_s": None, "notify": True, "port": 7865}
cfg = {**defaults, **cfg}
cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
lex = home / "lexicon.json"
if not lex.exists():
    lex.write_text('{\n  "_comment": "your own pronunciations, word: how-to-say-it"\n}\n')
print(f"State      ~/.claude-speak (voice {'OFF (migrated from v1)' if migrated else 'OFF'})")
EOF

cat <<'DONE'

Installed. Voice is off by default.

  speak on           turn it on for the session you're in
  speak on --global  turn it on everywhere
  speak say hi       hear it right now

Restart Claude Code once so it picks up the MCP voice channel.
DONE
