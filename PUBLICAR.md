# Cómo publicar Bifrost en Figma Community

## Antes de empezar

- [ ] Activa la **verificación en dos pasos** en tu cuenta de Figma: **Settings → Security**.
- [ ] Sube este repo a **GitHub** como público. Ahí la gente descarga el servidor y el instalador.
- [ ] Pega el link del repo en `README.md`, en el lugar de `[Figma Community](#)`, y en el campo **Support contact** del formulario de Figma.

## Publicar

1. **Abre Figma desktop** y ve a **Plugins → Manage plugins → Development**.
2. **Busca Bifrost**, abre el menú **⋯** y elige **Publish**.
3. **Llena el formulario** con los datos que vienen abajo.
4. **Cambia el `id` del manifest.** Al publicar, Figma te da un **ID de plugin nuevo** y te pide ponerlo en `figma-plugin/manifest.json`, en lugar de `claude-bridge-local-dev`. Cámbialo, haz commit y vuelve a darle Publish si te lo pide.
5. **Espera la revisión** de Figma, que tarda de 5 a 10 días hábiles. Si te piden cambios, me pasas el mensaje y lo ajustamos.

## Datos del formulario

**Name:** Bifrost

**Tagline** (máx. ~100 caracteres):
> Connect Claude to Figma: let it read, create and edit your designs.

**Description:**
> Bifrost is a bridge between Claude (the AI assistant by Anthropic) and Figma. Once connected, you can ask Claude to inspect your selection, rename layers, swap components from your design system, build frames with auto layout, update text and colors, or export frames for review — all inside the files you already work in.
>
> **Requires a small local server** (Python, included) that you install once from GitHub: https://github.com/mrcolor-ux/Bifrost. The plugin only talks to that server on localhost:3055. Bifrost itself has no servers, analytics or accounts.
>
> How to use:
> 1. Run the installer from the GitHub repo (macOS).
> 2. Open Figma and run Plugins → Bifrost → Conectar a Claude.
> 3. Ask Claude what you want to do in Figma.
>
> Works on any Figma plan. Everything Claude does can be undone with Cmd+Z.
>
> Bifrost is an independent project and is not affiliated with or endorsed by Anthropic or Figma.

**Tags:** AI, Claude, automation, design systems, productivity, MCP

**Icon:** `assets/bifrost-icon-128.png`

**Cover:** `assets/bifrost-cover-1920x1080.png`

**Support contact:** https://github.com/mrcolor-ux/Bifrost/issues, o un correo que quieras hacer público.

**Network access / Data security:**
- *Does your plugin make network requests?* Yes, only to `localhost:3055`, the user's own computer.
- *Does it collect or store user data?* No. The plugin doesn't store or send data to any server owned by the developer. File content is relayed locally to the Claude app the user runs, under the user's own Claude account.

## Notas

- **No uses el logo de Claude ni de Anthropic** en el icono ni en la portada. Decir "for Claude" para describir con qué funciona está bien; por eso viene el aviso de *not affiliated*.
- **Actualizaciones:** cada vez que cambies `code.js`, `ui.html` o `manifest.json`, vuelve a publicar desde **Manage plugins → Bifrost → Publish new version**. Los cambios en `server.py` solo necesitan subirse a GitHub.
- **El menú del plugin está en español** ("Conectar a Claude", "Mostrar panel"). Si quieres llegar a más gente, lo puedo pasar a inglés o hacerlo bilingüe.
