# Bifrost · puente Claude ↔ Figma

Conecta Claude con Figma sin el conector oficial. Funciona con cualquier plan de Figma.

```
Claude  ──(MCP / stdio)──►  server/server.py  ──(WebSocket :3055)──►  Plugin "Bifrost" en Figma
```

No hay que instalar nada: el servidor usa solo Python 3, que ya viene en la Mac.

## 1. Instalar el plugin en Figma (una sola vez)

1. Abre la **app de escritorio de Figma**. Los plugins de desarrollo no se pueden importar desde el navegador.
2. Abre cualquier archivo de diseño.
3. Menú **Plugins → Development → Import plugin from manifest…**
4. Elige `figma-plugin/manifest.json` de esta carpeta.

## 2. Registrar el servidor en Claude (una sola vez)

Lo más fácil es darle **doble clic a `install.command`**: registra Bifrost en Claude Code y en el Chat de la app de escritorio, y hace respaldo de tu configuración antes.

Si prefieres hacerlo a mano, agrega esto en `~/.claude.json`, dentro de `"mcpServers"`, con la ruta real de esta carpeta:

```json
"bifrost": {
  "type": "stdio",
  "command": "/usr/bin/python3",
  "args": ["/RUTA/A/claude-figma-bridge/server/server.py"]
}
```

Si tienes el CLI de `claude`, también lo puedes registrar así:

```bash
claude mcp add --scope user bifrost -- /usr/bin/python3 /RUTA/A/claude-figma-bridge/server/server.py
```

Después reinicia la sesión de Claude.

## 3. Usarlo

1. En Figma: **Plugins → Development → Bifrost → Conectar a Claude (en segundo plano)**.
   No se abre ninguna ventana; solo sale el aviso “🟢 Claude conectado”.
2. Pídele cosas a Claude: “¿qué tengo seleccionado?”, “crea un banner de 1080×1080 con fondo naranja y el texto…”, “exporta este frame para que lo veas”.

**Se enciende solo.** Si le pides algo a Claude y no hay ningún archivo conectado, el servidor pone Figma al frente y corre Bifrost desde el menú **Plugins** (si tienes la versión en desarrollo, usa esa). Funciona aunque acabes de reiniciar Figma. Si no lo encuentra en el menú, presiona **Cmd+Option+P** (*Run last plugin*).
- macOS tiene que darle permiso a la app de Claude: **Ajustes del Sistema → Privacidad y seguridad → Accesibilidad → Claude ✓**.
- Para desactivarlo, pon `FIGMA_BRIDGE_AUTOLAUNCH=0` en el `env` del servidor.

**Varios archivos a la vez.** Cada archivo donde corre el plugin queda conectado. Por default, Claude trabaja en el último archivo donde hiciste algo (seleccionar o cambiar de página). También le puedes pedir un archivo en particular, por ejemplo: “trae el componente X del DS y ponlo en el flujo”. La herramienta `list_files` muestra cuáles están conectados.

**Varias sesiones de Claude a la vez.** La primera sesión que arranca se queda con el puente y las demás trabajan a través de ella, así que todas ven y editan los mismos archivos. Si cierras esa sesión, otra toma su lugar en un par de segundos y el plugin se reconecta solo.

**Atajos**
- **Cmd+Option+P** vuelve a correr el último plugin, así que reconecta en un segundo.
- Después de la primera vez, el archivo muestra un botón **“Conectar Claude”** en el panel derecho, cuando no tienes nada seleccionado.
- **Mostrar panel** abre la ventanita con el log, por si quieres ver qué está haciendo Claude.
- **Desconectar**, o el botón del aviso, apaga el puente.

Figma no deja que un plugin corra sin que lo lances, así que se apaga si cambias de archivo o recargas, y hay que volver a lanzarlo. Todo lo que hace Claude se puede deshacer con **Cmd+Z**.

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `get_document_info`, `get_selection`, `get_node_info`, `find_nodes` | Leer el archivo, la selección o un nodo, y buscar nodos |
| `set_current_page`, `select_nodes` | Cambiar de página y seleccionar o hacer zoom |
| `create_frame`, `create_rectangle`, `create_ellipse`, `create_text` | Crear elementos |
| `set_text`, `set_fill`, `set_stroke`, `set_corner_radius`, `set_auto_layout` | Editar estilos |
| `move_node`, `resize_node`, `rename_node`, `clone_node`, `delete_node` | Mover, redimensionar, renombrar, duplicar y borrar |
| `export_image` | Exportar a PNG, JPG o SVG para que Claude vea el diseño, opcionalmente sobre un color de fondo (`background`) para diseños blancos sobre transparente |
| `index_library`, `find_components`, `import_component` | Indexar un Design System una vez y después buscar sus componentes e insertarlos como instancias de la librería por nombre y variante, desde cualquier archivo |
| `run_code` | Ejecutar JavaScript con la API completa de Figma (componentes, variables, efectos…). Los errores dicen la línea. Para trabajos largos, sube `timeout` o llama `progress()` dentro del loop |
| `notify` | Mostrar un aviso en Figma |

## Gastar menos tokens

Claude lee todo lo que regresa Bifrost, así que las respuestas van cortas:
- **Las herramientas de lectura** (`get_selection`, `get_node_info`…) listan máximo 20 hijos por nodo y recortan textos y nombres de capa largos.
- **Cualquier respuesta de más de 20,000 caracteres** se recorta, con un aviso de cómo pedir menos. Se cambia con `FIGMA_BRIDGE_MAX_CHARS`.
- **Cada llamada se anota** en `~/.bifrost/usage.log` con su tamaño y los tokens aproximados, para ver qué consume más.

## Problemas comunes

- **“El plugin de Figma no está conectado”**: abre el plugin en Figma. Si ya estaba abierto, pulsa Conectar.
- **El punto se queda amarillo**: Claude no tiene el servidor corriendo. Revisa el paso 2 y reinicia la sesión.
- **“Otra sesión de Claude… con una versión vieja de Bifrost”**: una sesión que arrancó antes de actualizar Bifrost tiene el puente y no sabe compartirlo. Ciérrala (o reiníciala) y en unos segundos esta toma la conexión.
- **“Bifrost está desactualizado en este archivo”**: esa pestaña trae una versión vieja del plugin. Ciérralo y vuelve a abrirlo en esa pestaña.
- **Error de fuente en `create_text`**: la fuente tiene que estar instalada o disponible en Figma, y el estilo tiene que existir. Por ejemplo, `"Bold"` y no `"bold"`.
- **Cambiar el puerto**: define la variable de entorno `FIGMA_BRIDGE_PORT` en el servidor, cámbialo en la ventana del plugin y actualiza `allowedDomains` en `manifest.json`.
