#!/bin/bash
# Bifrost installer (macOS): registers the local server in Claude Code and the Claude desktop app.
# Double-click this file, or run: ./install.command
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER="$DIR/server/server.py"
PY="$(command -v python3 || true)"

echo ""
echo "🌈  Bifrost · Claude ↔ Figma"
echo ""

if [ -z "$PY" ]; then
  echo "✕ Python 3 not found. Run:  xcode-select --install   and try again."
  exit 1
fi
if [ ! -f "$SERVER" ]; then
  echo "✕ Can't find $SERVER"
  exit 1
fi

"$PY" -I - "$PY" "$SERVER" <<'EOF'
import json, os, shutil, sys
py, server = sys.argv[1], sys.argv[2]
entry = {"command": py, "args": [server]}
targets = [
    ("Claude Code", os.path.expanduser("~/.claude.json"), {"type": "stdio", **entry}),
    ("Claude desktop (Chat)", os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json"), entry),
]
for label, path, value in targets:
    if not os.path.isdir(os.path.dirname(path)):
        print(f"– {label}: not installed, skipped")
        continue
    data = {}
    if os.path.exists(path):
        shutil.copy(path, path + ".bak-bifrost")
        with open(path) as f:
            data = json.load(f)
    data.setdefault("mcpServers", {})["bifrost"] = value
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✓ {label}: registered")
EOF

cat <<EOF

Next steps
  1. Quit Claude completely (Cmd+Q) and open it again.
  2. In the Figma desktop app, install Bifrost from Community
     (or for development: Plugins → Development → Import plugin from manifest →
      $DIR/figma-plugin/manifest.json)
  3. Run  Plugins → Bifrost → Conectar a Claude
  4. Optional, so Claude can start the plugin for you:
     System Settings → Privacy & Security → Accessibility → enable Claude

Done. Ask Claude: "what do I have selected in Figma?"
EOF
