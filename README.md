<p align="center"><img src="assets/bifrost-icon-128.png" width="96" alt="Bifrost"></p>

# Bifrost · Claude ↔ Figma

Let Claude read, create and edit your Figma files: frames, text, colors, auto layout, components and variables. Works on **any Figma plan**, with no API key and no extra cloud service.

*[Versión en español → LEEME.md](LEEME.md)*

```
Claude (Code or desktop Chat) ──MCP/stdio──► server/server.py ──WebSocket localhost:3055──► Bifrost plugin in Figma
```

The server uses only the Python 3 standard library, so there's nothing else to install.

## Install (macOS)

1. **Download** [this repo](https://github.com/mrcolor-ux/Bifrost/archive/refs/heads/main.zip) and put it somewhere permanent, like `~/Documents/bifrost`.
2. **Double-click `install.command`.** It registers Bifrost in Claude Code and in the Claude desktop app, and backs up your config files first.
3. **Quit Claude** (Cmd+Q) and open it again.
4. **Install the plugin in Figma desktop.** Get it from [Figma Community](#) or, for development, use **Plugins → Development → Import plugin from manifest…** and pick `figma-plugin/manifest.json`.
5. **Run Plugins → Bifrost → Conectar a Claude.** You'll see the toast "🟢 Claude conectado".

### Optional: let Claude start the plugin for you

If no file is connected, Bifrost brings Figma to the front and runs itself from the **Plugins** menu (it prefers your development copy if you have one). This works even right after you restart Figma. If it can't find Bifrost in the menu, it presses **Cmd+Option+P** (*Run last plugin*) instead.

For this to work, allow it in **System Settings → Privacy & Security → Accessibility → Claude**.

It also works for a file that isn't connected yet: name it (`file: "Flujos"`) or paste its figma.com link, and Bifrost opens its tab in Figma desktop and starts the plugin there. It finds files by name among the tabs open in Figma desktop and remembers every file it has connected in `~/.bifrost/files.json`.

To turn it off, set `FIGMA_BRIDGE_AUTOLAUNCH=0` in the server's `env`.

## Using it

Ask Claude things like:
- "What do I have selected in Figma?"
- "Swap this illustration for the *Movilidad* one from our design system."
- "Create a 1080×1080 banner with an orange background and the headline…"
- "Export this frame so you can review it."

Some tips:
- **Multiple files at once.** Every file running the plugin stays connected. Claude works on the file you touched last, or on whichever file you name (`list_files` shows them).
- **Multiple Claude sessions at once.** The first session to start owns the bridge and the others work through it, so they all see the same files. If you close that session, another one takes over within a couple of seconds and the plugin reconnects on its own.
- **Everything is undoable** with Cmd+Z in Figma.
- **Other menu options:** **Mostrar panel** shows a small log window, and **Desconectar** turns the bridge off.
- **Figma doesn't run plugins automatically on file open.** Claude starts Bifrost from the Plugins menu when it needs it. You can also use Cmd+Option+P or the **Conectar Claude** button in the right panel.

## Tools

| Tool | What it does |
|---|---|
| `list_files` | Shows connected files, which one is active, and the ones Bifrost can open and connect on its own |
| `get_document_info`, `get_selection`, `get_node_info`, `find_nodes` | Read the file, the selection or a node, and search nodes |
| `set_current_page`, `select_nodes` | Navigate pages, select and zoom |
| `create_frame`, `create_rectangle`, `create_ellipse`, `create_text` | Create elements |
| `set_text`, `set_fill`, `set_stroke`, `set_corner_radius`, `set_auto_layout` | Edit styles |
| `move_node`, `resize_node`, `rename_node`, `clone_node`, `delete_node` | Move, resize, rename, duplicate and delete |
| `export_image` | Export PNG, JPG or SVG so Claude can see the design, optionally over a `background` color (for white-on-transparent designs) |
| `run_code` | Run JavaScript with the full Figma Plugin API (components, variables, effects…). Errors include the failing line. For long jobs, raise `timeout` or call `progress()` inside the loop |
| `notify` | Show a toast in Figma |

Every tool accepts an optional `file` argument to target a specific connected file.

## Privacy

- **The plugin only talks to `localhost:3055`** on your own computer.
- **Bifrost has no servers, analytics or accounts.**
- **What Claude reads from your file goes to Claude** as part of your conversation, just like anything else you share with it. Follow your company's rules for confidential designs.

## Troubleshooting

- **"El plugin de Figma no está conectado"**: run Bifrost in Figma.
- **The status dot stays yellow**: Claude isn't running the server. Re-run `install.command` and restart Claude.
- **"Otra sesión de Claude… con una versión vieja de Bifrost"**: a session started before you updated Bifrost owns the bridge and can't share it. Close or restart it, and this one takes over within a few seconds.
- **"Bifrost está desactualizado en este archivo"**: that tab is running an older copy of the plugin. Close it and run Bifrost again in that tab.
- **Font errors in `create_text`**: the font and style must exist in Figma. For example, use `"Bold"`, not `"bold"`.

## License

MIT, see [LICENSE](LICENSE).
