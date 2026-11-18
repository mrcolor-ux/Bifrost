#!/usr/bin/env python3
"""
Claude <-> Figma bridge (MCP server, sin dependencias).

- Habla MCP por stdio (JSON-RPC, una línea por mensaje) con Claude.
- Abre un WebSocket en ws://localhost:3055 al que se conecta el plugin de Figma.
- Cada herramienta que Claude llama se reenvía al plugin y se espera su respuesta.
"""
import asyncio
import base64
import contextvars
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from urllib.parse import urlsplit

HOST = "127.0.0.1"
PORT = int(os.environ.get("FIGMA_BRIDGE_PORT", "3055"))
TIMEOUT = float(os.environ.get("FIGMA_BRIDGE_TIMEOUT", "60"))
MAX_TIMEOUT = 600
# Para gastar menos tokens: respuestas más largas se recortan, y las herramientas de lectura resumen listas largas
MAX_CHARS = int(os.environ.get("FIGMA_BRIDGE_MAX_CHARS", "20000"))
MAX_CHILDREN = 20
MAX_TEXT = 300
MAX_NAME = 120
READ_TOOLS = {"get_document_info", "get_selection", "get_node_info", "find_nodes"}
# Una línea por llamada (herramienta, tamaño, tokens aproximados) para ver qué consume más
USAGE_LOG = os.path.expanduser(os.environ.get("FIGMA_BRIDGE_USAGE_LOG", "~/.bifrost/usage.log"))
current_tool = contextvars.ContextVar("current_tool", default=None)
# Comandos que traen componentes de librerías: Figma solo los termina en la pestaña al frente
FOREGROUND_COMMANDS = {"import_component", "import_nodes"}
# Versión del plugin que espera este servidor (BIFROST_VERSION en code.js; los plugins viejos no la mandan)
PLUGIN_VERSION = 8
ALLOW_RUN_CODE = os.environ.get("FIGMA_BRIDGE_ALLOW_RUN_CODE") == "1"
TOKEN_PATH = os.path.expanduser(os.environ.get("FIGMA_BRIDGE_TOKEN_FILE", "~/.bifrost/token"))


def load_token(path):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if not os.path.exists(path):
        fd, temporary = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(secrets.token_hex(32) + "\n")
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
    with open(path, encoding="ascii") as f:
        if os.fstat(f.fileno()).st_mode & 0o077:
            raise RuntimeError("El archivo de vinculación debe tener permisos 600")
        token = f.read().strip()
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise RuntimeError("Código de vinculación inválido")
    return token


def auth_proof(token, side, role, client_nonce, server_nonce):
    message = f"bifrost-v5:{side}:{role}:{client_nonce}:{server_nonce}"
    return hmac.new(token.encode("ascii"), message.encode("ascii"), hashlib.sha256).hexdigest()


def valid_hex(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def verify_proof(value, expected):
    return valid_hex(value) and hmac.compare_digest(value, expected)


# Si no hay plugin conectado, correr Bifrost desde el menú Plugins de Figma para encenderlo solo
AUTOLAUNCH = os.environ.get("FIGMA_BRIDGE_AUTOLAUNCH", "1") != "0"
# Archivos ya vistos (key -> nombre), para abrir su pestaña y conectarlos solos
KNOWN_FILES_PATH = os.path.expanduser(os.environ.get("FIGMA_BRIDGE_FILES", "~/.bifrost/files.json"))
# Índices de librerías (Design Systems): un JSON por librería
LIBRARY_DIR = os.path.expanduser(os.environ.get("FIGMA_BRIDGE_LIBRARY", "~/.bifrost/library"))
# La app de escritorio guarda aquí sus pestañas abiertas (título + key)
if sys.platform == "win32":
    FIGMA_SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", ""), "Figma", "settings.json")
else:
    FIGMA_SETTINGS_PATH = os.path.expanduser("~/Library/Application Support/Figma/settings.json")
TAB_PATH_RE = re.compile(r"^/(?:file|design|board|proto|slides)/([A-Za-z0-9]+)")


def binding_key(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        url = urlsplit(value.strip())
    except ValueError:
        return None
    if url.scheme != "https" or url.netloc.lower() not in {"figma.com", "www.figma.com"}:
        return None
    match = re.fullmatch(r"/(?:file|design|board|proto|slides)/([A-Za-z0-9]+)(?:/[^/]*)?/?", url.path)
    return match.group(1) if match else None


def figma_open_tabs():
    """key -> título de las pestañas abiertas en Figma de escritorio."""
    try:
        with open(FIGMA_SETTINGS_PATH, encoding="utf-8") as f:
            windows = json.load(f).get("windows") or []
    except (OSError, ValueError, AttributeError):
        return {}
    tabs = {}
    for w in windows:
        for t in (w.get("tabs") or []) if isinstance(w, dict) else []:
            m = TAB_PATH_RE.match(str(t.get("path", "")))
            if m and t.get("title"):
                tabs[m.group(1)] = t["title"]
    return tabs
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Plugins > (Desarrollo | Desde <organización> | …) > Bifrost > primer comando (Conectar a Claude).
# Se prefiere la versión en desarrollo, que suele ser la más nueva.
LAUNCH_SCRIPT = '''
if application "Figma" is not running then error "Figma no está abierto"
tell application "Figma" to activate
delay 0.4
tell application "System Events" to tell process "Figma"
    set target to missing value
    try
        repeat with sec in menu items of menu 1 of menu bar item "Plugins" of menu bar 1
            try
                set b to menu item "Bifrost" of menu 1 of sec
                if target is missing value or (name of sec) is in {"Desarrollo", "Development"} then set target to b
            end try
        end repeat
    end try
    if target is missing value then
        keystroke "p" using {command down, option down}
    else
        click menu item 1 of menu 1 of target
    end if
end tell
'''


def log(*args):
    print("[bifrost]", *args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Herramientas expuestas a Claude
# ---------------------------------------------------------------------------

COLOR = {"type": "string", "description": "Color hex, ej. #FF5733 o #FF573380 (con alfa)"}
NODE_ID = {"type": "string", "description": "ID del nodo en Figma, ej. 12:34"}
PARENT_ID = {"type": "string", "description": "ID del padre (frame/grupo). Si se omite, va a la página actual"}


def schema(props=None, required=None):
    return {"type": "object", "properties": props or {}, "required": required or []}


TOOLS = [
    {
        "name": "get_document_info",
        "description": "Info del archivo abierto: nombre, página actual y sus nodos de primer nivel. allPages=true para listar todas las páginas.",
        "inputSchema": schema({"allPages": {"type": "boolean"}, "limit": {"type": "number", "description": "Máx. nodos (default 60)"}}),
    },
    {
        "name": "get_selection",
        "description": "Devuelve los nodos que el usuario tiene seleccionados en Figma.",
        "inputSchema": schema({"depth": {"type": "number", "description": "Niveles de hijos a incluir (default 1)"}}),
    },
    {
        "name": "get_node_info",
        "description": "Detalle de un nodo (posición, tamaño, rellenos, texto, hijos).",
        "inputSchema": schema({"nodeId": NODE_ID, "depth": {"type": "number", "description": "Niveles de hijos (default 1)"}}, ["nodeId"]),
    },
    {
        "name": "find_nodes",
        "description": "Busca nodos por nombre (contiene, sin distinguir mayúsculas) y/o tipo en la página actual o dentro de un nodo.",
        "inputSchema": schema({
            "name": {"type": "string"},
            "type": {"type": "string", "description": "FRAME, TEXT, RECTANGLE, COMPONENT, INSTANCE, GROUP..."},
            "withinId": {"type": "string", "description": "Buscar solo dentro de este nodo"},
            "limit": {"type": "number", "description": "Máximo de resultados (default 50)"},
        }),
    },
    {
        "name": "set_current_page",
        "description": "Cambia la página activa por ID o nombre.",
        "inputSchema": schema({"pageId": {"type": "string"}, "name": {"type": "string"}}),
    },
    {
        "name": "create_frame",
        "description": "Crea un frame.",
        "inputSchema": schema({
            "x": {"type": "number"}, "y": {"type": "number"},
            "width": {"type": "number"}, "height": {"type": "number"},
            "name": {"type": "string"}, "fillColor": COLOR, "cornerRadius": {"type": "number"},
            "parentId": PARENT_ID,
        }, ["width", "height"]),
    },
    {
        "name": "create_rectangle",
        "description": "Crea un rectángulo.",
        "inputSchema": schema({
            "x": {"type": "number"}, "y": {"type": "number"},
            "width": {"type": "number"}, "height": {"type": "number"},
            "name": {"type": "string"}, "fillColor": COLOR, "cornerRadius": {"type": "number"},
            "parentId": PARENT_ID,
        }, ["width", "height"]),
    },
    {
        "name": "create_ellipse",
        "description": "Crea una elipse/círculo.",
        "inputSchema": schema({
            "x": {"type": "number"}, "y": {"type": "number"},
            "width": {"type": "number"}, "height": {"type": "number"},
            "name": {"type": "string"}, "fillColor": COLOR, "parentId": PARENT_ID,
        }, ["width", "height"]),
    },
    {
        "name": "create_text",
        "description": "Crea un texto. La fuente debe existir en Figma (default Inter Regular).",
        "inputSchema": schema({
            "text": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
            "fontSize": {"type": "number"}, "fontFamily": {"type": "string"}, "fontStyle": {"type": "string", "description": "Regular, Bold, Medium..."},
            "color": COLOR, "width": {"type": "number", "description": "Si se da, el texto hace salto de línea a ese ancho"},
            "align": {"type": "string", "enum": ["LEFT", "CENTER", "RIGHT", "JUSTIFIED"]},
            "name": {"type": "string"}, "parentId": PARENT_ID,
        }, ["text"]),
    },
    {
        "name": "set_text",
        "description": "Cambia el contenido (y opcionalmente tamaño/fuente) de un nodo de texto.",
        "inputSchema": schema({
            "nodeId": NODE_ID, "text": {"type": "string"},
            "fontSize": {"type": "number"}, "fontFamily": {"type": "string"}, "fontStyle": {"type": "string"},
        }, ["nodeId", "text"]),
    },
    {
        "name": "set_fill",
        "description": "Pone un relleno sólido a un nodo (o al texto).",
        "inputSchema": schema({"nodeId": NODE_ID, "color": COLOR}, ["nodeId", "color"]),
    },
    {
        "name": "set_stroke",
        "description": "Pone un borde sólido a un nodo.",
        "inputSchema": schema({"nodeId": NODE_ID, "color": COLOR, "weight": {"type": "number"}}, ["nodeId", "color"]),
    },
    {
        "name": "set_corner_radius",
        "description": "Cambia el radio de las esquinas.",
        "inputSchema": schema({"nodeId": NODE_ID, "radius": {"type": "number"}}, ["nodeId", "radius"]),
    },
    {
        "name": "set_auto_layout",
        "description": "Activa/ajusta auto layout en un frame.",
        "inputSchema": schema({
            "nodeId": NODE_ID,
            "mode": {"type": "string", "enum": ["NONE", "HORIZONTAL", "VERTICAL"]},
            "spacing": {"type": "number"},
            "padding": {"type": "number", "description": "Padding igual en los 4 lados"},
            "paddingTop": {"type": "number"}, "paddingRight": {"type": "number"},
            "paddingBottom": {"type": "number"}, "paddingLeft": {"type": "number"},
            "primaryAlign": {"type": "string", "enum": ["MIN", "CENTER", "MAX", "SPACE_BETWEEN"]},
            "counterAlign": {"type": "string", "enum": ["MIN", "CENTER", "MAX"]},
            "hug": {"type": "boolean", "description": "Que el frame abrace su contenido"},
        }, ["nodeId"]),
    },
    {
        "name": "move_node",
        "description": "Mueve un nodo a x,y (y opcionalmente a otro padre).",
        "inputSchema": schema({"nodeId": NODE_ID, "x": {"type": "number"}, "y": {"type": "number"}, "parentId": PARENT_ID}, ["nodeId"]),
    },
    {
        "name": "resize_node",
        "description": "Cambia el tamaño de un nodo.",
        "inputSchema": schema({"nodeId": NODE_ID, "width": {"type": "number"}, "height": {"type": "number"}}, ["nodeId", "width", "height"]),
    },
    {
        "name": "rename_node",
        "description": "Renombra un nodo.",
        "inputSchema": schema({"nodeId": NODE_ID, "name": {"type": "string"}}, ["nodeId", "name"]),
    },
    {
        "name": "clone_node",
        "description": "Duplica un nodo, opcionalmente en otra posición.",
        "inputSchema": schema({"nodeId": NODE_ID, "x": {"type": "number"}, "y": {"type": "number"}}, ["nodeId"]),
    },
    {
        "name": "delete_node",
        "description": "Elimina un nodo (se puede deshacer con Cmd+Z en Figma).",
        "inputSchema": schema({"nodeId": NODE_ID}, ["nodeId"]),
    },
    {
        "name": "select_nodes",
        "description": "Selecciona nodos en Figma y hace zoom a ellos.",
        "inputSchema": schema({"nodeIds": {"type": "array", "items": {"type": "string"}}}, ["nodeIds"]),
    },
    {
        "name": "export_image",
        "description": "Exporta un nodo como imagen para que Claude lo vea.",
        "inputSchema": schema({
            "nodeId": NODE_ID,
            "format": {"type": "string", "enum": ["PNG", "JPG", "SVG"], "description": "Default PNG"},
            "scale": {"type": "number", "description": "Default 1"},
            "maxSize": {"type": "number", "description": "Lado mayor máximo en px (default 800, para ahorrar tokens)"},
            "background": {**COLOR, "description": "Color de fondo (PNG/JPG), para ver diseños blancos o transparentes. Ej. #1A6B47"},
        }, ["nodeId"]),
    },
    {
        "name": "run_code",
        "description": (
            "Ejecuta JavaScript con la API de plugins de Figma (objeto `figma`). "
            "El código es el cuerpo de una función async: usa `await` y `return` para devolver datos (JSON). "
            "Para cosas que las demás herramientas no cubren (componentes, variables, efectos, estilos...). "
            "Usa las versiones async de la API (getNodeByIdAsync, loadFontAsync antes de editar texto). "
            f"Regresa solo los datos que necesitas (más de {MAX_CHARS} caracteres se recortan). "
            "Si falla, el error dice la línea. Para trabajos largos llama `progress('texto')` dentro del loop: "
            "cada llamada reinicia el límite de tiempo."
        ),
        "inputSchema": schema({
            "code": {"type": "string"},
            "timeout": {"type": "number", "description": f"Segundos sin respuesta ni progress() antes de cortar (default {TIMEOUT:.0f}, máx {MAX_TIMEOUT})"},
        }, ["code"]),
    },
    {
        "name": "notify",
        "description": "Muestra un aviso (toast) en Figma.",
        "inputSchema": schema({"message": {"type": "string"}}, ["message"]),
    },
]
FILE_PARAM = {
    "type": "string",
    "description": (
        "Archivo de Figma (nombre o parte del nombre, o su link de figma.com). Si se omite, el último donde el "
        "usuario trabajó. Si no está conectado pero ya se conoce (o pasas el link), Bifrost abre su pestaña y se "
        "conecta solo. Ver list_files."
    ),
}
for _t in TOOLS:
    _t["inputSchema"]["properties"]["file"] = FILE_PARAM

TOOLS.append({
    "name": "copy_nodes",
    "description": (
        "Copia capas de un archivo de Figma a otro (Figma no deja copiar entre archivos desde un plugin, así que "
        "se leen completas en el origen y se reconstruyen en el destino). Conserva frames, auto layout, textos con "
        "estilos, colores, imágenes, efectos, y las instancias/estilos/variables de librerías publicadas. Los "
        "componentes locales del origen llegan como frames. Los datos van de archivo a archivo sin pasar por Claude. "
        "Ambos archivos deben tener Bifrost conectado (list_files)."
    ),
    "inputSchema": schema({
        "from": {"type": "string", "description": "Archivo de origen (parte del nombre o link)"},
        "to": {"type": "string", "description": "Archivo de destino (parte del nombre o link)"},
        "nodeIds": {"type": "array", "items": {"type": "string"}, "description": "Nodos a copiar; si se omite, la selección del origen"},
        "nextToId": {"type": "string", "description": "Pegar a la derecha de este nodo del destino"},
        "gap": {"type": "number", "description": "Separación con nextToId (default 200)"},
        "x": {"type": "number"}, "y": {"type": "number"},
        "parentId": {"type": "string", "description": "Frame del destino donde pegarlo (default: página actual)"},
    }, ["from", "to"]),
})
TOOLS.insert(0, {
    "name": "list_files",
    "description": (
        "Lista los archivos de Figma conectados al puente y cuál está activo (el último donde el usuario trabajó), "
        "y los que Bifrost ya conoce y puede abrir y conectar solo."
    ),
    "inputSchema": schema(),
})
PLACE_PARAMS = {
    "nextToId": {"type": "string", "description": "Ponerlo a la derecha de este nodo"},
    "gap": {"type": "number", "description": "Separación con nextToId (default 200)"},
    "x": {"type": "number"}, "y": {"type": "number"},
    "parentId": PARENT_ID,
}
TOOLS += [
    {
        "name": "index_library",
        "description": (
            "Lee todos los componentes de un Design System (sets con sus variantes y su clave de librería) y guarda "
            "el índice en este equipo, para buscarlos e importarlos después desde cualquier archivo sin abrir el DS. "
            "Vuelve a correrlo cuando el DS cambie."
        ),
        "inputSchema": schema({"file": {**FILE_PARAM, "description": "El archivo del Design System"}}),
    },
    {
        "name": "find_components",
        "description": (
            "Busca componentes en los Design Systems indexados (index_library), por nombre, página, descripción o "
            "nombre de variante. No necesita Figma. Devuelve nombre, librería y variantes que coinciden."
        ),
        "inputSchema": schema({
            "query": {"type": "string", "description": "Palabras a buscar (sin importar acentos ni mayúsculas)"},
            "library": {"type": "string", "description": "Solo en esta librería (parte del nombre)"},
            "limit": {"type": "number", "description": "Máximo de resultados (default 15)"},
        }, ["query"]),
    },
    {
        "name": "import_component",
        "description": (
            "Inserta una instancia de un componente de un Design System indexado (como instancia de la librería, no "
            "copia suelta), buscándolo por nombre y variante. La librería debe estar activada en el archivo destino."
        ),
        "inputSchema": schema({
            "name": {"type": "string", "description": "Nombre del componente o set (exacto o parte única)"},
            "variant": {"type": "string", "description": "Variante: su nombre completo ('Tipo=OUOF003 Movilidad') o una parte única ('Movilidad'). Default: la variante por default"},
            "key": {"type": "string", "description": "En vez de name: la clave del componente o variante"},
            "library": {"type": "string", "description": "Librería donde buscar (parte del nombre)"},
            "page": {"type": "string", "description": "Página del DS donde está, si hay nombres repetidos"},
            "file": {**FILE_PARAM, "description": "Archivo donde insertarlo"},
            **PLACE_PARAMS,
        }),
    },
]
if not ALLOW_RUN_CODE:
    TOOLS = [tool for tool in TOOLS if tool["name"] != "run_code"]

TOOL_NAMES = {t["name"] for t in TOOLS}


# ---------------------------------------------------------------------------
# Índice de Design Systems
# ---------------------------------------------------------------------------

def norm(text):
    """Minúsculas y sin acentos, para buscar."""
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def library_path(name):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", norm(name)).strip("_") or "libreria"
    return os.path.join(LIBRARY_DIR, safe + ".json")


def load_libraries(library=None):
    libs = []
    try:
        names = sorted(os.listdir(LIBRARY_DIR))
    except OSError:
        return libs
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(LIBRARY_DIR, fn), encoding="utf-8") as f:
                lib = json.load(f)
        except (OSError, ValueError):
            continue
        if library and norm(library) not in norm(lib.get("library")):
            continue
        libs.append(lib)
    return libs


def save_library(data, file_key):
    comps = data.get("components") or []
    lib = {
        "library": data.get("library"),
        "fileKey": file_key,
        "indexedAt": time.strftime("%Y-%m-%d %H:%M"),
        "components": comps,
    }
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    path = library_path(lib["library"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False)
    return {
        "library": lib["library"],
        "sets": sum(1 for c in comps if c["type"] == "SET"),
        "components": sum(1 for c in comps if c["type"] == "COMPONENT"),
        "variants": sum(len(c.get("variants") or []) for c in comps),
        "unpublished": sum(1 for c in comps if c.get("unpublished")),
        "savedTo": path,
    }


def find_components(query, library=None, limit=15):
    words = norm(query).split()
    results = []
    for lib in load_libraries(library):
        for c in lib.get("components") or []:
            own = norm(" ".join([c.get("name", ""), c.get("page", ""), c.get("description", "")]))
            variants = c.get("variants") or []
            if all(w in own for w in words):
                hits = variants
            else:
                hits = [v for v in variants if all(w in own + " " + norm(v["name"]) for w in words)]
                if not hits:
                    continue
            item = {"library": lib.get("library"), "name": c["name"], "type": c["type"], "page": c.get("page")}
            if c.get("unpublished"):
                item["unpublished"] = True
            if variants:
                item["variantCount"] = len(variants)
                item["variants"] = [v["name"] for v in hits[:12]]
            else:
                item["key"] = c["key"]
            results.append(item)
    # Primero los que coinciden por nombre exacto
    q = norm(query)
    results.sort(key=lambda r: (norm(r["name"]) != q, q not in norm(r["name"]), len(r["name"])))
    return results[:limit]


def describe(option):
    page = (option.get("page") or "").strip()
    return f"{option['name']} ({page})" if page else option["name"]


def pick_one(options, label, what):
    if len(options) == 1:
        return options[0]
    if not options:
        raise RuntimeError(f"No encontré {what} '{label}'. Búscalo con find_components.")
    names = ", ".join(sorted({describe(o) for o in options})[:15])
    raise RuntimeError(f"Hay varios {what}s que coinciden con '{label}': {names}. Sé más específico (o pasa page).")


def match_variants(variants, variant):
    vq = norm(variant)
    parts = [x.strip() for x in vq.split(",") if x.strip()]
    return ([v for v in variants if norm(v["name"]) == vq]
            or [v for v in variants if all(x in [y.strip() for y in norm(v["name"]).split(",")] for x in parts)]
            or [v for v in variants if vq in norm(v["name"])])


def resolve_component(name=None, variant=None, key=None, library=None, page=None):
    """Nombre (y variante) -> clave de componente para importComponentByKeyAsync."""
    libs = load_libraries(library)
    if not libs:
        raise RuntimeError("No hay Design Systems indexados. Corre index_library en el archivo del DS.")
    comps = [dict(c, library=lib.get("library")) for lib in libs for c in lib.get("components") or []]
    if key:
        for c in comps:
            if c["key"] == key:
                return c["key"] if c["type"] == "COMPONENT" else c["variants"][0]["key"], c["name"], None
            for v in c.get("variants") or []:
                if v["key"] == key:
                    return key, c["name"], v["name"]
        return key, None, None  # puede ser de una librería no indexada
    if not name:
        raise RuntimeError("Pasa name (o key) del componente.")
    if page:
        comps = [c for c in comps if norm(page) in norm(c.get("page"))]
    q = norm(name)
    options = [c for c in comps if norm(c["name"]) == q] or [c for c in comps if q in norm(c["name"])]
    # Desempatar nombres repetidos: por la variante pedida y luego por los publicados
    if len(options) > 1 and variant:
        options = [c for c in options if match_variants(c.get("variants") or [], variant)] or options
    if len(options) > 1:
        options = [c for c in options if not c.get("unpublished")] or options
    comp = pick_one(options, name, "componente")
    if comp.get("unpublished"):
        raise RuntimeError(f"'{describe(comp)}' no está publicado en {comp['library']}; publícalo para poder importarlo.")
    if comp["type"] == "COMPONENT":
        return comp["key"], comp["name"], None
    variants = comp.get("variants") or []
    if not variant:
        return variants[0]["key"], comp["name"], variants[0]["name"]
    v = pick_one(match_variants(variants, variant), variant, f"variante de {comp['name']}")
    return v["key"], comp["name"], v["name"]


# ---------------------------------------------------------------------------
# WebSocket mínimo (RFC 6455) para hablar con el plugin
# ---------------------------------------------------------------------------

class PluginConnection:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.lock = asyncio.Lock()

    async def send_text(self, text):
        data = text.encode("utf-8")
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header += struct.pack("!H", n)
        else:
            header.append(127)
            header += struct.pack("!Q", n)
        async with self.lock:
            self.writer.write(bytes(header) + data)
            await self.writer.drain()

    async def send_control(self, opcode, payload=b""):
        async with self.lock:
            self.writer.write(bytes([0x80 | opcode, len(payload)]) + payload)
            await self.writer.drain()

    async def read_message(self, max_size=None):
        """Devuelve el siguiente mensaje de texto, o None si se cerró."""
        buf = bytearray()
        while True:
            b1, b2 = await self.reader.readexactly(2)
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            n = b2 & 0x7F
            if n == 126:
                (n,) = struct.unpack("!H", await self.reader.readexactly(2))
            elif n == 127:
                (n,) = struct.unpack("!Q", await self.reader.readexactly(8))
            if max_size is not None and len(buf) + n > max_size:
                raise ValueError("Mensaje demasiado grande")
            mask = await self.reader.readexactly(4) if masked else None
            payload = bytearray(await self.reader.readexactly(n))
            if mask:
                for i in range(n):
                    payload[i] ^= mask[i % 4]
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                await self.send_control(0xA, bytes(payload))
                continue
            if opcode == 0xA:
                continue
            buf += payload
            if fin:
                return buf.decode("utf-8")


class FileSession:
    """Un archivo de Figma con el plugin corriendo."""

    def __init__(self, conn):
        self.conn = conn
        self.registered = False
        self.file_name = "?"
        self.file_key = None
        self.key_from_figma = False  # la clave vino de figma.fileKey y no se puede quitar
        self.stored_key_rejected = False
        self.page = None
        self.version = 1
        self.last_active = time.monotonic()

    @property
    def outdated(self):
        return self.version < PLUGIN_VERSION

    def outdated_hint(self):
        return (f"La pestaña '{self.file_name}' trae una versión vieja del plugin de Bifrost. "
                "Ciérralo y vuelve a abrirlo en esa pestaña (Plugins > Bifrost > Conectar a Claude); "
                "si lo usas desde Community, publica primero la versión nueva.")


class Bridge:
    def __init__(self):
        self.files = []          # FileSession, varios archivos a la vez
        self.pending = {}
        self.progress = {}       # id de la llamada -> última vez que el plugin avisó que sigue trabajando
        self.listening = False   # esta sesión tiene el puerto (es la central)
        self.hub = None          # conexión a la sesión central, si esta es cliente
        self.hub_pending = {}
        self.old_hub = False     # el puerto lo tiene una sesión con Bifrost viejo
        self.ready = asyncio.Event()
        self.connected = asyncio.Event()
        self.launch_lock = asyncio.Lock()
        self.known = self.load_known()  # key -> nombre del archivo
        self.plugin_token = load_token(TOKEN_PATH)
        self.peer_token = load_token(TOKEN_PATH + ".peer")

    @staticmethod
    def load_known():
        try:
            with open(KNOWN_FILES_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def remember(self, key, name):
        if not key or not name or name == "?" or self.known.get(key) == name:
            return
        self.known[key] = name
        try:
            os.makedirs(os.path.dirname(KNOWN_FILES_PATH), exist_ok=True)
            with open(KNOWN_FILES_PATH, "w", encoding="utf-8") as f:
                json.dump(self.known, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log("No pude guardar", KNOWN_FILES_PATH, e)

    async def run(self):
        """Abre el puerto (sesión central). Si otra sesión ya lo tiene, se le une como cliente
        y le pasa sus llamadas; si esa sesión se cierra, vuelve a intentar quedarse con el puerto."""
        warned = False
        while True:
            try:
                server = await asyncio.start_server(self.handle_client, HOST, PORT)
            except OSError:
                joined = await self.join_hub()
                if joined is True:
                    warned = False
                    continue
                self.old_hub = joined == "old"
                if self.old_hub and not warned:
                    log(f"Puerto {PORT} ocupado por Bifrost viejo u otro programa; reintentando cada 3s…")
                    warned = True
                await asyncio.sleep(3 if self.old_hub else 0.5)
                continue
            self.listening = True
            self.old_hub = False
            self.ready.set()
            log(f"Esperando al plugin en ws://localhost:{PORT}")
            async with server:
                await server.serve_forever()

    async def join_hub(self):
        """Se conecta a la sesión central como cliente (True al desconectarse de ella).
        Regresa "old" si quien tiene el puerto no sabe compartirlo, o False si no se pudo conectar."""
        writer = None
        try:
            reader, writer = await asyncio.open_connection(HOST, PORT)
            key = base64.b64encode(os.urandom(16)).decode()
            writer.write((
                f"GET / HTTP/1.1\r\nHost: {HOST}:{PORT}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nX-Bifrost-Peer: 1\r\n\r\n"
            ).encode())
            await writer.drain()
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            if b" 101 " not in head.split(b"\r\n", 1)[0]:
                writer.close()
                return False
            conn = PluginConnection(reader, writer)
            if not await self.authenticate_hub(conn):
                writer.close()
                return "old"
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError):
            if writer:
                writer.close()
            return False
        self.hub = conn
        self.old_hub = False
        self.ready.set()
        log("Otra sesión tiene el puerto; me uno a ella como cliente")
        try:
            while True:
                msg = await conn.read_message()
                if msg is None:
                    break
                try:
                    data = json.loads(msg)
                except ValueError:
                    continue
                fut = self.hub_pending.pop(data.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(data)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.hub = None
            self.ready.clear()
            for fut in self.hub_pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError(
                        "La sesión de Claude que tenía el puente se cerró. Vuelve a intentarlo; esta sesión toma el puerto."))
            self.hub_pending.clear()
            writer.close()
            log("La sesión central se cerró; intento quedarme con el puerto")
        return True

    async def authenticate_hub(self, conn):
        role = "peer"
        client = secrets.token_hex(32)
        await conn.send_text(json.dumps({"type": "auth_start", "nonce": client}))
        challenge = await self.read_auth(conn)
        server = challenge.get("nonce")
        if challenge.get("type") != "auth_challenge" or not valid_hex(server):
            return False
        if not verify_proof(challenge.get("proof"), auth_proof(self.peer_token, "server", role, client, server)):
            return False
        await conn.send_text(json.dumps({
            "type": "auth_proof", "proof": auth_proof(self.peer_token, "client", role, client, server)}))
        return (await self.read_auth(conn)).get("type") == "auth_ok"

    @staticmethod
    async def read_auth(conn, max_size=2048):
        raw = await asyncio.wait_for(conn.read_message(max_size=max_size), 5)
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}

    async def authenticate_client(self, conn, role):
        token = self.peer_token if role == "peer" else self.plugin_token
        start = await self.read_auth(conn)
        client = start.get("nonce")
        if start.get("type") != "auth_start" or not valid_hex(client):
            return False
        server = secrets.token_hex(32)
        await conn.send_text(json.dumps({
            "type": "auth_challenge", "nonce": server,
            "proof": auth_proof(token, "server", role, client, server)}))
        answer = await self.read_auth(conn)
        if answer.get("type") != "auth_proof" or not verify_proof(
                answer.get("proof"), auth_proof(token, "client", role, client, server)):
            return False
        await conn.send_text(json.dumps({"type": "auth_ok"}))
        return True

    async def ask_hub(self, payload):
        req_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.hub_pending[req_id] = fut
        await self.hub.send_text(json.dumps({"id": req_id, **payload}))
        # La central aplica el límite de tiempo (que progress() puede alargar), o se desconecta
        msg = await fut
        if "error" in msg:
            raise RuntimeError(msg["error"])
        return msg

    async def handle_peer(self, conn):
        """Otra sesión de Claude conectada a esta como cliente."""
        log("Se unió otra sesión de Claude")
        try:
            while True:
                text = await conn.read_message()
                if text is None:
                    break
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    asyncio.ensure_future(self.serve_peer(conn, msg))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        log("Se fue una sesión cliente")

    async def serve_peer(self, conn, msg):
        out = {"id": msg.get("id")}
        try:
            if msg.get("op") == "list_files":
                out["result"] = await self.list_files_launching()
            else:
                out["file"], out["result"] = await self.call_local(
                    msg["command"], msg.get("params") or {}, msg.get("timeout"))
        except Exception as e:
            out["error"] = str(e)
        try:
            await conn.send_text(json.dumps(out, ensure_ascii=False))
        except (ConnectionError, OSError):
            pass

    async def wait_ready(self):
        """Al arrancar, dar unos segundos a quedarse con el puerto o unirse a la central."""
        try:
            await asyncio.wait_for(self.ready.wait(), 5)
        except asyncio.TimeoutError:
            pass

    async def handle_client(self, reader, writer):
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        except Exception:
            writer.close()
            return
        headers = {}
        for line in request.decode("latin-1").split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        key = headers.get("sec-websocket-key")
        if not key:
            body = b"Bifrost corriendo. Conecta el plugin de Figma."
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
            writer.close()
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        writer.write((
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode())
        await writer.drain()

        conn = PluginConnection(reader, writer)
        role = "peer" if headers.get("x-bifrost-peer") else "plugin"
        try:
            authenticated = await self.authenticate_client(conn, role)
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError):
            authenticated = False
        if not authenticated:
            writer.close()
            return
        if role == "peer":
            try:
                await self.handle_peer(conn)
            finally:
                writer.close()
            return

        session = FileSession(conn)
        try:
            hello = await self.read_auth(conn, max_size=16384)
            if not self.register_plugin(session, hello):
                log("Figma autenticado, pero el registro del documento es inválido")
                await conn.send_text(json.dumps({"type": "registration_error"}))
                return
            await conn.send_text(json.dumps({
                "type": "registered", "version": PLUGIN_VERSION, "canBindLink": not session.file_key,
                "linkRemovable": bool(session.file_key and not session.key_from_figma),
                "storedKeyRejected": session.stored_key_rejected,
            }))
            session.registered = True
            self.files.append(session)
            self.connected.set()
            log("Conectado:", session.file_name, "(plugin viejo)" if session.outdated else "")
            if session.outdated:
                asyncio.ensure_future(self.warn_outdated(session))
            while True:
                msg = await conn.read_message()
                if msg is None:
                    break
                await self.on_plugin_message(session, msg)
        except (asyncio.IncompleteReadError, OSError, asyncio.TimeoutError, ValueError):
            if not session.registered:
                log("Figma autenticado, pero el registro no se completó")
        finally:
            if session in self.files:
                self.files.remove(session)
                log("Desconectado:", session.file_name)
            if not self.files:
                self.connected.clear()
            writer.close()

    def register_plugin(self, session, hello):
        name = hello.get("fileName")
        version = hello.get("version")
        if hello.get("type") != "hello" or not isinstance(name, str) or not name.strip():
            return False
        if type(version) is not int or version < 5:
            return False
        session.file_name = name
        session.version = version
        key = hello.get("fileKey")
        if isinstance(key, str) and key:
            if hello.get("fileKeySource") == "stored":
                # Un archivo duplicado hereda el enlace guardado: si ya está en uso, se descarta
                if any(other.file_key == key for other in self.ready_files()):
                    session.stored_key_rejected = True
                else:
                    session.file_key = key
            else:
                session.file_key = key
                session.key_from_figma = True
        if isinstance(hello.get("page"), str):
            session.page = hello["page"]
        self.remember(session.file_key, session.file_name)
        return True

    def bind_link(self, session, url):
        key = binding_key(url)
        if not key:
            return "Pega un enlace HTTPS a un archivo de figma.com"
        if session.file_key and session.file_key != key:
            return "Este documento ya tiene un enlace. Quítalo para asociar otro."
        if any(other is not session and other.file_key == key for other in self.ready_files()):
            return "Ese enlace ya está asociado a otra sesión de Figma"
        session.file_key = key
        return None

    async def on_plugin_message(self, session, text):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "bind_link":
            error = self.bind_link(session, msg.get("url"))
            await session.conn.send_text(json.dumps({
                "type": "link_binding", "error": error, "fileKey": None if error else session.file_key,
            }))
            return
        if kind == "unbind_link":
            if session.file_key and not session.key_from_figma:
                session.file_key = None
            await session.conn.send_text(json.dumps({"type": "link_unbound", "removed": not session.file_key}))
            return
        if kind == "active":
            if isinstance(msg.get("fileName"), str) and msg["fileName"].strip():
                session.file_name = msg["fileName"]
            if isinstance(msg.get("page"), str):
                session.page = msg["page"]
            session.last_active = time.monotonic()
            return
        if kind == "progress":
            if msg.get("id") in self.pending:
                self.progress[msg["id"]] = time.monotonic()
                if msg.get("message"):
                    log(f"[{session.file_name}]", msg["message"])
            return
        fut = self.pending.pop(msg.get("id"), None)
        if fut and not fut.done():
            fut.set_result(msg)

    async def warn_outdated(self, session):
        """Aviso en Figma; la respuesta del plugin no se espera."""
        try:
            await session.conn.send_text(json.dumps({"id": uuid.uuid4().hex, "command": "notify", "params": {
                "message": "⚠️ Bifrost está desactualizado en este archivo. Ciérralo y vuelve a abrirlo."}}))
        except (ConnectionError, OSError):
            pass

    def ready_files(self):
        return [f for f in self.files if f.registered]

    def list_files(self):
        active = self.pick(None) if self.ready_files() else None
        connected = [{
            "file": f.file_name,
            "page": f.page,
            "active": f is active,
            "lastActiveSecondsAgo": round(time.monotonic() - f.last_active),
            **({"pluginViejo": True} if f.outdated else {}),
        } for f in sorted(self.ready_files(), key=lambda f: -f.last_active)]
        names = {f.file_name for f in self.ready_files()}
        others = sorted(set(n for n in self.candidates().values() if n not in names))
        if not others:
            return connected
        return {"connected": connected, "canAutoConnect": others}

    def match(self, file_query):
        """Archivos conectados que coinciden con el nombre o link pedido."""
        files = self.ready_files()
        q = str(file_query)
        key = binding_key(q)
        if key:
            return [f for f in files if f.file_key == key]
        q = q.lower()
        exact = [f for f in files if f.file_name.lower() == q]
        return exact or [f for f in files if q in f.file_name.lower()]

    def candidates(self):
        """Archivos que se pueden abrir y conectar: los ya vistos y las pestañas abiertas en Figma."""
        return {**self.known, **figma_open_tabs()}

    def known_key(self, file_query):
        """Key de un archivo no conectado: del link, o buscándolo por nombre."""
        q = str(file_query)
        key = binding_key(q)
        if key:
            return key
        q = q.lower()
        files = self.candidates()
        exact = [k for k, n in files.items() if n.lower() == q]
        matches = exact or [k for k, n in files.items() if q in n.lower()]
        return matches[0] if len(matches) == 1 else None

    def pick(self, file_query):
        files = self.ready_files()
        if file_query:
            files = self.match(file_query)
            if not files:
                names = ", ".join(f.file_name for f in self.ready_files()) or "ninguno"
                raise RuntimeError(
                    f"No hay ningún archivo conectado que coincida con '{file_query}'. Conectados: {names}. "
                    "Abre Bifrost en ese documento y asocia su enlace en el panel, o usa un nombre único.")
            if len(files) > 1:
                raise RuntimeError(f"Varios archivos coinciden con '{file_query}'. Usa un enlace de Figma o deja conectado solo el archivo de destino.")
        # El archivo donde el usuario tocó algo más recientemente
        return max(files, key=lambda f: f.last_active)

    async def open_and_connect(self, file_query):
        """Abre la pestaña del archivo en Figma y enciende Bifrost ahí."""
        key = self.known_key(file_query)
        if not key or not AUTOLAUNCH or sys.platform != "darwin":
            return
        async with self.launch_lock:
            if self.match(file_query):
                return
            log("Abriendo", self.candidates().get(key, key))
            proc = await asyncio.create_subprocess_exec(
                "open", "-a", "Figma", f"https://www.figma.com/design/{key}",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            await asyncio.sleep(2.5)  # a que la pestaña quede al frente
            # Si la pestaña tarda en cargar, el primer intento se pierde: se intenta una segunda vez
            for wait in (6, 10):
                await self.press_launch_shortcut()
                for _ in range(wait * 4):
                    await asyncio.sleep(0.25)
                    if self.match(file_query):
                        break
                else:
                    continue
                break

    async def bring_to_front(self, session):
        """Figma solo importa componentes de librerías en la pestaña que está al frente."""
        if not session.file_key or sys.platform != "darwin":
            return
        proc = await asyncio.create_subprocess_exec(
            "open", "-a", "Figma", f"https://www.figma.com/design/{session.file_key}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
        await asyncio.sleep(1)

    async def press_launch_shortcut(self):
        """Corre Bifrost desde el menú Plugins de Figma (funciona aunque Figma esté recién abierto);
        si no lo encuentra en el menú, usa Cmd+Option+P (Run last plugin)."""
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", LAUNCH_SCRIPT, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        return proc.returncode, err.decode("utf-8", "replace").strip()

    async def auto_launch(self):
        """Corre Bifrost en Figma desde el menú Plugins para encender el puente solo."""
        if not AUTOLAUNCH or sys.platform != "darwin":
            return "desactivado"
        async with self.launch_lock:
            if self.ready_files():
                return "ok"
            code, err = await self.press_launch_shortcut()
            if code != 0:
                log("Autolanzado falló:", err)
                if "no está abierto" in err:
                    return "Figma no está abierto"
                return ("macOS no dejó mandar el atajo a Figma. Dale permiso de Accesibilidad a la app de "
                        "Claude en Ajustes del Sistema > Privacidad y seguridad > Accesibilidad")
            try:
                await asyncio.wait_for(self.connected.wait(), 8)
                return "ok"
            except asyncio.TimeoutError:
                return ("lancé Bifrost desde el menú Plugins pero no respondió; revisa que esté instalado y que "
                        "haya un archivo abierto al frente")

    async def list_files_launching(self):
        """list_files; si no hay nada conectado, intenta encender el plugin primero."""
        files = self.list_files()
        if not files:
            await self.auto_launch()
            files = self.list_files()
        return files

    async def list_files_any(self):
        await self.wait_ready()
        if self.hub:
            return (await self.ask_hub({"op": "list_files"})).get("result")
        if self.listening:
            return await self.list_files_launching()
        return self.list_files()

    def not_connected_error(self):
        if self.old_hub:
            return RuntimeError(
                f"El puerto {PORT} lo tiene otro programa: una sesión de Claude con una versión vieja de "
                "Bifrost, u otro conector de Figma que usa el mismo puerto (por ejemplo \"Talk to Figma\"). "
                "Ciérralo y en unos segundos esta sesión toma la conexión.")
        return RuntimeError("Bifrost todavía está arrancando; vuelve a intentarlo en unos segundos.")

    async def call(self, command, params, timeout=None):
        """Ejecuta el comando aquí si esta sesión tiene el puerto, o se lo pasa a la sesión central."""
        timeout = timeout or TIMEOUT
        await self.wait_ready()
        if self.hub:
            msg = await self.ask_hub({"command": command, "params": params, "timeout": timeout})
            return msg.get("file"), msg.get("result")
        if not self.listening:
            raise self.not_connected_error()
        return await self.call_local(command, params, timeout)

    async def call_local(self, command, params, timeout=None):
        if command == "run_code" and not ALLOW_RUN_CODE:
            raise RuntimeError("run_code está deshabilitado")
        timeout = timeout or TIMEOUT
        file_query = params.pop("file", None)
        if file_query and not self.match(file_query):
            await self.open_and_connect(file_query)
        if not self.ready_files():
            status = await self.auto_launch()
            if not self.ready_files():
                raise RuntimeError(
                    "El plugin de Figma no está conectado y no pude encenderlo solo (" + status + "). "
                    "En Figma: Plugins > Development > Bifrost > Conectar a Claude."
                )
        session = self.pick(file_query)
        if command in FOREGROUND_COMMANDS:
            await self.bring_to_front(session)
        req_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[req_id] = fut
        await session.conn.send_text(json.dumps({"id": req_id, "command": command, "params": params}))
        start = time.monotonic()
        try:
            while True:
                # Cada progress() del plugin vuelve a empezar la cuenta
                left = max(start, self.progress.get(req_id, 0)) + timeout - time.monotonic()
                if left <= 0:
                    self.pending.pop(req_id, None)
                    raise RuntimeError(
                        f"Figma ({session.file_name}) no respondió en {timeout:.0f}s. Para trabajos largos en "
                        "run_code sube `timeout` o llama progress() dentro del loop; Figma puede seguir trabajando.")
                try:
                    msg = await asyncio.wait_for(asyncio.shield(fut), left)
                    break
                except asyncio.TimeoutError:
                    continue
        finally:
            self.progress.pop(req_id, None)
        if "error" in msg:
            err = f"[{session.file_name}] {msg['error']}"
            if session.outdated:
                err += ". " + session.outdated_hint()
            raise RuntimeError(err)
        return session.file_name, msg.get("result")


# ---------------------------------------------------------------------------
# MCP por stdio
# ---------------------------------------------------------------------------

_out_lock = threading.Lock()


def send(obj):
    line = json.dumps(obj, ensure_ascii=False)
    with _out_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def compact(value):
    """Resume lo que regresan las herramientas de lectura: listas de hijos y textos largos."""
    if isinstance(value, list):
        return [compact(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for k, v in value.items():
        if k == "children" and isinstance(v, list) and len(v) > MAX_CHILDREN:
            out[k] = [compact(c) for c in v[:MAX_CHILDREN]]
            out["moreChildren"] = len(v) - MAX_CHILDREN
        elif k == "characters" and isinstance(v, str) and len(v) > MAX_TEXT:
            out[k] = v[:MAX_TEXT] + f"… ({len(v)} caracteres)"
        elif k == "name" and isinstance(v, str) and len(v) > MAX_NAME:
            # Las capas de texto se llaman igual que su contenido
            out[k] = v[:MAX_NAME] + "…"
        else:
            out[k] = compact(v)
    return out


def clip(text):
    if len(text) <= MAX_CHARS:
        return text
    return (text[:MAX_CHARS] + f"…\n[Recortado: la respuesta tenía {len(text)} caracteres. Pide menos datos: usa "
            "limit/depth, filtra, o en run_code regresa solo los campos que necesitas.]")


def image_size(data):
    """Ancho y alto de un PNG en base64 (lee solo el encabezado)."""
    try:
        head = base64.b64decode(data[:44])
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack("!II", head[16:24])
    except (ValueError, struct.error):
        pass
    return None


def log_usage(tool, content, is_error):
    chars = sum(len(c.get("text", "")) for c in content if c.get("type") == "text")
    tokens = chars / 3.5
    images = []
    for c in content:
        if c.get("type") == "image":
            size = image_size(c.get("data", ""))
            images.append(f"{size[0]}x{size[1]}" if size else "?")
            # Claude cobra una imagen por píxeles: ~ancho*alto/750 tokens
            tokens += size[0] * size[1] / 750 if size else 800
    line = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "tool": tool, "chars": chars, "tokens": round(tokens)}
    if images:
        line["images"] = images
    if is_error:
        line["error"] = True
    try:
        os.makedirs(os.path.dirname(USAGE_LOG), exist_ok=True)
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def reply(req_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": req_id}
    tool = current_tool.get()
    if tool and isinstance(result, dict) and isinstance(result.get("content"), list):
        for c in result["content"]:
            if c.get("type") == "text":
                c["text"] = clip(c["text"])
        log_usage(tool, result["content"], bool(result.get("isError")))
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    send(msg)


async def handle_library(bridge, req_id, name, args):
    try:
        if name == "find_components":
            result = find_components(args.get("query", ""), args.get("library"), int(args.get("limit") or 15))
            if not result and not load_libraries():
                result = "No hay Design Systems indexados. Corre index_library en el archivo del DS."
        elif name == "index_library":
            file_name, data = await bridge.call("index_library", {"file": args.get("file")}, timeout=300)
            session = bridge.pick(file_name) if bridge.listening else None
            result = save_library(data, session.file_key if session else None)
        else:
            key, comp, variant = resolve_component(
                args.get("name"), args.get("variant"), args.get("key"), args.get("library"), args.get("page"))
            params = {k: args[k] for k in PLACE_PARAMS if k in args}
            params.update(file=args.get("file"), key=key)
            file_name, node = await bridge.call("import_component", params)
            result = {"file": file_name, "component": comp, "variant": variant, "node": node}
    except Exception as e:
        reply(req_id, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        return
    reply(req_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, separators=(',', ':'))}]})


async def handle_copy(bridge, req_id, args):
    """Origen -> export_nodes, destino -> import_nodes. El JSON nunca sale del servidor."""
    try:
        src_name, data = await bridge.call(
            "export_nodes", {"file": args.get("from"), "nodeIds": args.get("nodeIds")}, timeout=180)
        dest_params = {k: args[k] for k in ("nextToId", "gap", "x", "y", "parentId") if k in args}
        dest_params["file"] = args.get("to")
        dest_params["data"] = data
        dst_name, result = await bridge.call("import_nodes", dest_params, timeout=300)
    except Exception as e:
        reply(req_id, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        return
    summary = {"from": src_name, "to": dst_name, **(result or {})}
    reply(req_id, {"content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False, separators=(',', ':'))}]})


async def handle_tool_call(bridge, req_id, params):
    name = params.get("name")
    current_tool.set(name)
    args = params.get("arguments") or {}
    if name not in TOOL_NAMES:
        reply(req_id, error={"code": -32602, "message": f"Herramienta desconocida: {name}"})
        return
    if name == "list_files":
        try:
            files = await bridge.list_files_any()
        except Exception as e:
            reply(req_id, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
            return
        reply(req_id, {"content": [{"type": "text", "text": json.dumps(files, ensure_ascii=False, separators=(',', ':'))}]})
        return
    if name in ("index_library", "find_components", "import_component"):
        await handle_library(bridge, req_id, name, dict(args))
        return
    if name == "copy_nodes":
        await handle_copy(bridge, req_id, dict(args))
        return
    args = dict(args)
    timeout = None
    if args.get("timeout"):
        timeout = min(max(float(args.pop("timeout")), 1), MAX_TIMEOUT)
    try:
        file_name, result = await bridge.call(name, args, timeout)
    except Exception as e:
        reply(req_id, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        return
    if name == "export_image" and isinstance(result, dict) and result.get("base64"):
        fmt = result.get("format", "PNG")
        if fmt == "SVG":
            svg = base64.b64decode(result["base64"]).decode("utf-8", "replace")
            content = [{"type": "text", "text": svg}]
        else:
            mime = "image/jpeg" if fmt == "JPG" else "image/png"
            content = [{"type": "image", "data": result["base64"], "mimeType": mime}]
    else:
        if name in READ_TOOLS:
            result = compact(result)
        content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, separators=(',', ':'))}]
    content.insert(0, {"type": "text", "text": f"[Archivo: {file_name}]"})
    reply(req_id, {"content": content})


async def handle_mcp(bridge, msg):
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        reply(req_id, {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "bifrost", "version": "1.0.0"},
            "instructions": (
                "Controla Figma a través del plugin local 'Bifrost'. Puede haber varios archivos "
                "conectados a la vez (list_files); por default cada herramienta actúa sobre el último archivo "
                "donde el usuario trabajó, o pasa `file` para elegir otro (ej. copiar un componente del DS a "
                "un flujo). Empieza con get_document_info o get_selection."
            ),
        })
    elif method == "tools/list":
        reply(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        await handle_tool_call(bridge, req_id, params)
    elif method == "ping":
        reply(req_id, {})
    elif req_id is not None:
        reply(req_id, error={"code": -32601, "message": f"Método no soportado: {method}"})
    # notificaciones (sin id) se ignoran


async def main():
    bridge = Bridge()
    server_task = asyncio.ensure_future(bridge.run())

    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        asyncio.ensure_future(handle_mcp(bridge, msg))

    server_task.cancel()


if __name__ == "__main__":
    # En Windows la consola no usa UTF-8 por default; MCP siempre habla UTF-8 con una línea por mensaje.
    for stream, opts in ((sys.stdin, {}), (sys.stdout, {"newline": "\n"}), (sys.stderr, {"newline": "\n"})):
        try:
            stream.reconfigure(encoding="utf-8", **opts)
        except (AttributeError, ValueError):
            pass
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
