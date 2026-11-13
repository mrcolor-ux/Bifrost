"""Registers Bifrost in Claude Code and the Claude desktop app. Used by install.command and install.ps1.

usage: python register.py PYTHON SERVER [DESKTOP_CONFIG_DIR ...]

Each DESKTOP_CONFIG_DIR is a folder where the desktop app keeps claude_desktop_config.json;
it is created if missing. Every file is backed up as *.bak-bifrost before it changes.
"""
import json
import os
import shutil
import sys


def register(label, path, value):
    data = {}
    if os.path.exists(path):
        shutil.copy(path, path + ".bak-bifrost")
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except ValueError:
            print(f"x {label}: {path} isn't valid JSON, left it untouched")
            return False
    data.setdefault("mcpServers", {})["bifrost"] = value
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"OK {label}: registered")
    return True


def main():
    py, server, desktop_dirs = sys.argv[1], sys.argv[2], sys.argv[3:]
    entry = {"command": py, "args": [server]}
    code_json = os.path.expanduser("~/.claude.json")
    done = 0
    # The desktop app's Code tab reads ~/.claude.json too, so Code is registered whenever either app exists.
    if os.path.exists(code_json) or os.path.isdir(os.path.expanduser("~/.claude")) or desktop_dirs:
        done += register("Claude Code", code_json, {"type": "stdio", **entry})
    else:
        print("- Claude Code: not installed, skipped")
    for d in desktop_dirs:
        os.makedirs(d, exist_ok=True)
        done += register("Claude desktop (Chat)", os.path.join(d, "claude_desktop_config.json"), entry)
    if not desktop_dirs:
        print("- Claude desktop (Chat): not installed, skipped")
    if not done:
        print("x Didn't find Claude. Install the Claude desktop app (claude.ai/download), "
              "open it once, and run this installer again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
