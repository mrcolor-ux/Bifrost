#!/bin/bash
# Bifrost installer (macOS): registers the local server in Claude Code and the Claude desktop app.
# Double-click this file, or run:  bash install.command
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || true)"

echo ""
echo "🌈  Bifrost · Claude ↔ Figma"
echo ""

# On a Mac without developer tools, python3 exists but is only a stub that can't run anything.
if [ -x /usr/bin/python3 ] && /usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  PY=/usr/bin/python3   # Apple's copy: it doesn't disappear when Homebrew or conda change
elif [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "✕ Python 3.9+ isn't available yet."
  echo "  Accept Apple's developer tools dialog (or run:  xcode-select --install),"
  echo "  wait for it to finish, and run this installer again."
  exit 1
fi
if [ ! -f "$DIR/server/server.py" ]; then
  echo "✕ Can't find $DIR/server/server.py"
  echo "  Unzip Bifrost.zip first and run install.command from the unzipped folder."
  exit 1
fi

# Claude keeps pointing at this folder, so it gets a permanent home outside Documents/Downloads:
# macOS asks for permission before Claude can read those, and iCloud can offload them.
# A git checkout (development) stays where it is.
if [ ! -d "$DIR/.git" ]; then
  TARGET="$HOME/Library/Application Support/Bifrost"
  if [ "$DIR" != "$TARGET" ]; then
    if [ -e "$TARGET" ] && [ ! -f "$TARGET/server/server.py" ]; then
      echo "✕ $TARGET already exists and isn't Bifrost. Move it away and run this again."
      exit 1
    fi
    mkdir -p "$TARGET"
    ditto "$DIR" "$TARGET"
    DIR="$TARGET"
    echo "✓ Installed in $DIR"
  fi
  xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true
fi
SERVER="$DIR/server/server.py"
MANIFEST="$DIR/figma-plugin/manifest.json"

# The desktop app creates its folder on first launch; register it too if it's installed but never opened.
DESKTOP=()
if [ -d "$HOME/Library/Application Support/Claude" ] || [ -d /Applications/Claude.app ] || [ -d "$HOME/Applications/Claude.app" ]; then
  DESKTOP=("$HOME/Library/Application Support/Claude")
fi
"$PY" -I "$DIR/server/register.py" "$PY" "$SERVER" "${DESKTOP[@]}"

printf '%s' "$MANIFEST" | pbcopy 2>/dev/null || true

cat <<EOF

Next steps
  1. In the Figma desktop app, open a design file and use
     Plugins → Development → Import plugin from manifest…
     In the file window press Cmd+Shift+G, paste (Cmd+V) and press Enter.
     The path is already copied:
     $MANIFEST
  2. Run  Plugins → Development → Bifrost → Conectar a Claude
  3. Quit Claude completely (Cmd+Q) and open it again.
  4. Optional, so Claude can start the plugin for you:
     System Settings → Privacy & Security → Accessibility → enable Claude
EOF
if [ -f "$HOME/Documents/Bifrost/server/server.py" ] && [ "$DIR" != "$HOME/Documents/Bifrost" ]; then
  echo ""
  echo "  An older copy is in ~/Documents/Bifrost. Claude no longer uses it: if you imported"
  echo "  the plugin from there, import it again from the path above and delete that folder."
fi
echo ""
echo "Done. Ask Claude: \"what do I have selected in Figma?\""
