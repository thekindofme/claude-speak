#!/bin/zsh
# Remove claude-speak from Claude Code. Leaves ~/.claude-speak (config, logs)
# unless you pass --purge.
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"

pkill -f "daemon/speakd.py" 2>/dev/null || true
rm -f "$HOME/.local/bin/speak"
rm -f "$HOME/.claude/skills/speak"
claude mcp remove claude-speak >/dev/null 2>&1 || true

python3 - <<'EOF'
import json
from pathlib import Path

p = Path.home() / ".claude/settings.json"
if p.exists():
    s = json.loads(p.read_text())
    for event, groups in list(s.get("hooks", {}).items()):
        kept = []
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", [])
                          if "claude_speak_hook.py" not in h.get("command", "")]
            if g["hooks"]:
                kept.append(g)
        if kept:
            s["hooks"][event] = kept
        else:
            s["hooks"].pop(event, None)
    allow = s.get("permissions", {}).get("allow", [])
    if "mcp__claude-speak__speak" in allow:
        allow.remove("mcp__claude-speak__speak")
    p.write_text(json.dumps(s, indent=2) + "\n")
    print("hooks + permission removed from ~/.claude/settings.json")
EOF

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$HOME/.claude-speak"
  echo "purged ~/.claude-speak"
fi
echo "uninstalled (repo left at $ROOT)"
