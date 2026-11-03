#!/usr/bin/env python3
"""
Claude <-> Figma bridge (MCP server, sin dependencias).

- Habla MCP por stdio (JSON-RPC, una línea por mensaje) con Claude.
- Abre un WebSocket en ws://localhost:3055 al que se conecta el plugin de Figma.
- Cada herramienta que Claude llama se reenvía al plugin y se espera su respuesta.
"""
import asyncio
import base64
import hashlib
import json
import os
import re
import struct
import sys
import threading
import time
import uuid

HOST = "127.0.0.1"
PORT = int(os.environ.get("FIGMA_BRIDGE_PORT", "3055"))
TIMEOUT = float(os.environ.get("FIGMA_BRIDGE_TIMEOUT", "60"))
# Si no hay plugin conectado, mandar Cmd+Option+P a Figma para encenderlo solo
AUTOLAUNCH = os.environ.get("FIGMA_BRIDGE_AUTOLAUNCH", "1") != "0"
# Archivos ya vistos (key -> nombre), para abrir su pestaña y conectarlos solos
KNOWN_FILES_PATH = os.path.expanduser(os.environ.get("FIGMA_BRIDGE_FILES", "~/.bifrost/files.json"))
FIGMA_URL_RE = re.compile(r"figma\.com/(?:design|file|board|proto|slides)/([A-Za-z0-9]+)")
# La app de escritorio guarda aquí sus pestañas abiertas (título + key)
FIGMA_SETTINGS_PATH = os.path.expanduser("~/Library/Application Support/Figma/settings.json")
TAB_PATH_RE = re.compile(r"^/(?:file|design|board|proto|slides)/([A-Za-z0-9]+)")


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
        }, ["nodeId"]),
    },
    {
        "name": "run_code",
        "description": (
            "Ejecuta JavaScript con la API de plugins de Figma (objeto `figma`). "
            "El código es el cuerpo de una función async: usa `await` y `return` para devolver datos (JSON). "
            "Para cosas que las demás herramientas no cubren (componentes, variables, efectos, estilos...). "
            "Usa las versiones async de la API (getNodeByIdAsync, loadFontAsync antes de editar texto)."
        ),
        "inputSchema": schema({"code": {"type": "string"}}, ["code"]),
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
TOOL_NAMES = {t["name"] for t in TOOLS}


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

    async def read_message(self):
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
        self.file_name = "?"
        self.file_key = None
        self.page = None
        self.last_active = time.monotonic()


class Bridge:
    def __init__(self):
        self.files = []          # FileSession, varios archivos a la vez
        self.pending = {}
        self.listening = False   # esta sesión tiene el puerto (es la central)
        self.hub = None          # conexión a la sesión central, si esta es cliente
        self.hub_pending = {}
        self.old_hub = False     # el puerto lo tiene una sesión con Bifrost viejo
        self.ready = asyncio.Event()
        self.connected = asyncio.Event()
        self.launch_lock = asyncio.Lock()
        self.known = self.load_known()  # key -> nombre del archivo
        self.awaiting_key = None        # key de la pestaña que se está abriendo

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
                    log(f"Puerto {PORT} ocupado por una sesión con Bifrost viejo; reintentando cada 3s…")
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
            await conn.send_text(json.dumps({"type": "peer_hello"}))
            try:
                first = await asyncio.wait_for(conn.read_message(), 3)
            except asyncio.TimeoutError:
                first = None
            if not first or json.loads(first).get("type") != "peer_ok":
                writer.close()
                return "old"
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError):
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

    async def ask_hub(self, payload, timeout):
        req_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.hub_pending[req_id] = fut
        await self.hub.send_text(json.dumps({"id": req_id, **payload}))
        try:
            # La central aplica el timeout real; aquí solo un margen por si se cuelga
            msg = await asyncio.wait_for(fut, timeout + 30)
        except asyncio.TimeoutError:
            self.hub_pending.pop(req_id, None)
            raise RuntimeError(f"La sesión central de Bifrost no respondió en {timeout + 30:.0f}s")
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
                if msg.get("type") == "peer_hello":
                    await conn.send_text(json.dumps({"type": "peer_ok"}))
                else:
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
            request = await reader.readuntil(b"\r\n\r\n")
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

        if headers.get("x-bifrost-peer"):
            await self.handle_peer(PluginConnection(reader, writer))
            writer.close()
            return

        session = FileSession(PluginConnection(reader, writer))
        self.files.append(session)
        try:
            while True:
                msg = await session.conn.read_message()
                if msg is None:
                    break
                self.on_plugin_message(session, msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if session in self.files:
                self.files.remove(session)
            if not self.files:
                self.connected.clear()
            log("Desconectado:", session.file_name)
            writer.close()

    def on_plugin_message(self, session, text):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        kind = msg.get("type")
        if kind in ("hello", "active"):
            if msg.get("fileName"):
                session.file_name = msg["fileName"]
            if msg.get("fileKey"):
                session.file_key = msg["fileKey"]
            if msg.get("page"):
                session.page = msg["page"]
            session.last_active = time.monotonic()
            if kind == "hello":
                log("Conectado:", session.file_name)
                # Si acabamos de abrir una pestaña por su key, este es ese archivo
                if not session.file_key and self.awaiting_key:
                    session.file_key = self.awaiting_key
                    self.awaiting_key = None
                if not session.file_key:
                    same = [k for k, n in figma_open_tabs().items() if n == session.file_name]
                    if len(same) == 1:
                        session.file_key = same[0]
                self.remember(session.file_key, session.file_name)
                self.connected.set()
            return
        fut = self.pending.pop(msg.get("id"), None)
        if fut and not fut.done():
            fut.set_result(msg)

    def ready_files(self):
        return [f for f in self.files if f.file_name != "?"]

    def list_files(self):
        active = self.pick(None) if self.ready_files() else None
        connected = [{
            "file": f.file_name,
            "page": f.page,
            "active": f is active,
            "lastActiveSecondsAgo": round(time.monotonic() - f.last_active),
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
        url = FIGMA_URL_RE.search(q)
        if url:
            key = url.group(1)
            name = self.candidates().get(key)
            return [f for f in files if f.file_key == key or (name and f.file_name == name)]
        q = q.lower()
        exact = [f for f in files if f.file_name.lower() == q]
        return exact or [f for f in files if q in f.file_name.lower()]

    def candidates(self):
        """Archivos que se pueden abrir y conectar: los ya vistos y las pestañas abiertas en Figma."""
        return {**self.known, **figma_open_tabs()}

    def known_key(self, file_query):
        """Key de un archivo no conectado: del link, o buscándolo por nombre."""
        q = str(file_query)
        url = FIGMA_URL_RE.search(q)
        if url:
            return url.group(1)
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
                    "Pásame el link del archivo (figma.com/design/…) y lo abro y conecto solo.")
        # El archivo donde el usuario tocó algo más recientemente
        return max(files, key=lambda f: f.last_active)

    async def open_and_connect(self, file_query):
        """Abre la pestaña del archivo en Figma y enciende Bifrost ahí (Cmd+Option+P)."""
        key = self.known_key(file_query)
        if not key or not AUTOLAUNCH or sys.platform != "darwin":
            return
        async with self.launch_lock:
            if self.match(file_query):
                return
            before = set(id(f) for f in self.files)
            self.awaiting_key = key
            log("Abriendo", self.candidates().get(key, key))
            proc = await asyncio.create_subprocess_exec(
                "open", "-a", "Figma", f"https://www.figma.com/design/{key}",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            await asyncio.sleep(2.5)  # a que la pestaña quede al frente
            # Si la pestaña tarda en cargar, el primer atajo se pierde: se intenta una segunda vez
            for wait in (6, 10):
                await self.press_launch_shortcut()
                for _ in range(wait * 4):
                    await asyncio.sleep(0.25)
                    if self.match(file_query) or [f for f in self.ready_files() if id(f) not in before]:
                        break
                else:
                    continue
                break
            self.awaiting_key = None

    async def press_launch_shortcut(self):
        script = [
            "-e", 'if application "Figma" is not running then error "Figma no está abierto"',
            "-e", 'tell application "Figma" to activate',
            "-e", "delay 0.4",
            "-e", 'tell application "System Events" to keystroke "p" using {command down, option down}',
        ]
        proc = await asyncio.create_subprocess_exec(
            "osascript", *script, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        return proc.returncode, err.decode("utf-8", "replace").strip()

    async def auto_launch(self):
        """Pulsa Cmd+Option+P en Figma (relanzar el último plugin) para encender el puente solo."""
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
                return ("lancé el atajo pero el plugin no respondió; puede que el último plugin que usaste "
                        "en Figma no sea Bifrost")

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
            return (await self.ask_hub({"op": "list_files"}, 30)).get("result")
        if self.listening:
            return await self.list_files_launching()
        return self.list_files()

    def not_connected_error(self):
        if self.old_hub:
            return RuntimeError(
                f"Otra sesión de Claude tiene el puente de Figma (puerto {PORT}) con una versión vieja de "
                "Bifrost que no se puede compartir. Ciérrala y en unos segundos esta toma la conexión.")
        return RuntimeError("Bifrost todavía está arrancando; vuelve a intentarlo en unos segundos.")

    async def call(self, command, params, timeout=None):
        """Ejecuta el comando aquí si esta sesión tiene el puerto, o se lo pasa a la sesión central."""
        timeout = timeout or TIMEOUT
        await self.wait_ready()
        if self.hub:
            msg = await self.ask_hub({"command": command, "params": params, "timeout": timeout}, timeout)
            return msg.get("file"), msg.get("result")
        if not self.listening:
            raise self.not_connected_error()
        return await self.call_local(command, params, timeout)

    async def call_local(self, command, params, timeout=None):
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
        req_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[req_id] = fut
        await session.conn.send_text(json.dumps({"id": req_id, "command": command, "params": params}))
        try:
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(req_id, None)
            raise RuntimeError(f"Figma ({session.file_name}) no respondió en {timeout:.0f}s")
        if "error" in msg:
            raise RuntimeError(f"[{session.file_name}] {msg['error']}")
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


def reply(req_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    send(msg)


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
    if name == "copy_nodes":
        await handle_copy(bridge, req_id, dict(args))
        return
    try:
        file_name, result = await bridge.call(name, dict(args))
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
                "un flujo). Empieza con get_document_info o get_selection. Para lo demás, usa run_code."
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
