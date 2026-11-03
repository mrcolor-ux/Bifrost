// Bifrost: recibe comandos del servidor local (vía ui.html) y los ejecuta en Figma.

// Por default corre oculto: la UI (que tiene el WebSocket) vive en un iframe invisible.
// "Mostrar panel" la abre visible para ver el log.

if (figma.command === 'disconnect') {
  figma.closePlugin('Claude desconectado');
} else {
  var showPanel = figma.command === 'panel';
  figma.showUI(__html__, { width: 300, height: 250, title: 'Bifrost', visible: showPanel });
  figma.ui.postMessage({ type: 'init', fileName: figma.root.name, fileKey: figma.fileKey, page: figma.currentPage.name });
  // Avisa al servidor cuando trabajas en este archivo, para que Claude sepa cuál es el activo
  var reportActive = function () {
    figma.ui.postMessage({ type: 'active', fileName: figma.root.name, page: figma.currentPage.name });
  };
  figma.on('selectionchange', reportActive);
  figma.on('currentpagechange', reportActive);
  // Botón "Conectar Claude" en el panel derecho de este archivo, para relanzar en un clic.
  try { figma.root.setRelaunchData({ connect: 'Conecta este archivo con Claude' }); } catch (e) {}
}

var lastState = null;
var stopButton = { text: 'Desconectar', action: function () { figma.closePlugin('Claude desconectado'); } };

function onStatus(state) {
  if (state === lastState) return;
  var prev = lastState;
  lastState = state;
  if (state === 'on') {
    figma.notify('🟢 Claude conectado', { timeout: 3000, button: stopButton });
  } else if (state === 'wait' && prev !== 'wait') {
    figma.notify(prev === 'on' ? '🟡 Se perdió Claude, reconectando…' : '🟡 Esperando a Claude… (abre una sesión de Claude)',
      { timeout: 5000, button: stopButton });
  }
}

// ---------- utilidades ----------

function hexToRgba(hex) {
  var h = String(hex || '').replace('#', '').trim();
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  if (!/^[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(h)) throw new Error('Color inválido: ' + hex);
  return {
    r: parseInt(h.slice(0, 2), 16) / 255,
    g: parseInt(h.slice(2, 4), 16) / 255,
    b: parseInt(h.slice(4, 6), 16) / 255,
    a: h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1
  };
}

function solid(hex) {
  var c = hexToRgba(hex);
  return [{ type: 'SOLID', color: { r: c.r, g: c.g, b: c.b }, opacity: c.a }];
}

function toHex(c) {
  function p(v) { var s = Math.round(v * 255).toString(16); return s.length === 1 ? '0' + s : s; }
  return '#' + p(c.r) + p(c.g) + p(c.b);
}

function describePaints(paints) {
  if (!paints || paints === figma.mixed || !paints.length) return undefined;
  return paints.map(function (p) {
    if (p.type === 'SOLID') return { type: 'SOLID', color: toHex(p.color), opacity: p.opacity };
    return { type: p.type };
  });
}

function serialize(node, depth) {
  if (depth === undefined) depth = 1;
  var out = { id: node.id, name: node.name, type: node.type };
  if ('x' in node) { out.x = node.x; out.y = node.y; }
  if ('width' in node) { out.width = node.width; out.height = node.height; }
  if ('visible' in node && !node.visible) out.visible = false;
  if ('fills' in node) { var f = describePaints(node.fills); if (f) out.fills = f; }
  if ('strokes' in node) { var s = describePaints(node.strokes); if (s) out.strokes = s; }
  if ('cornerRadius' in node && node.cornerRadius && node.cornerRadius !== figma.mixed) out.cornerRadius = node.cornerRadius;
  if ('layoutMode' in node && node.layoutMode !== 'NONE') {
    out.layout = { mode: node.layoutMode, spacing: node.itemSpacing,
      padding: [node.paddingTop, node.paddingRight, node.paddingBottom, node.paddingLeft] };
  }
  if (node.type === 'TEXT') {
    out.characters = node.characters;
    if (node.fontSize !== figma.mixed) out.fontSize = node.fontSize;
    if (node.fontName !== figma.mixed) out.fontName = node.fontName;
  }
  if ('children' in node) {
    if (depth > 0) out.children = node.children.map(function (c) { return serialize(c, depth - 1); });
    else out.childCount = node.children.length;
  }
  return out;
}

async function getNode(id) {
  var node = await figma.getNodeByIdAsync(id);
  if (!node) throw new Error('No existe el nodo ' + id);
  return node;
}

async function getParent(parentId) {
  if (!parentId) return figma.currentPage;
  var p = await getNode(parentId);
  if (!('appendChild' in p)) throw new Error('El nodo ' + parentId + ' no puede tener hijos');
  return p;
}

async function place(node, p) {
  var parent = await getParent(p.parentId);
  parent.appendChild(node);
  if (p.x !== undefined) node.x = p.x;
  if (p.y !== undefined) node.y = p.y;
  if (p.name) node.name = p.name;
  if (!p.parentId && p.x === undefined && p.y === undefined) {
    var c = figma.viewport.center;
    node.x = Math.round(c.x - node.width / 2);
    node.y = Math.round(c.y - node.height / 2);
  }
  return node;
}

async function loadFontsOf(textNode) {
  if (textNode.fontName !== figma.mixed) {
    await figma.loadFontAsync(textNode.fontName);
    return;
  }
  var fonts = textNode.getRangeAllFontNames(0, textNode.characters.length);
  for (var i = 0; i < fonts.length; i++) await figma.loadFontAsync(fonts[i]);
}

// ---------- comandos ----------

var commands = {
  get_document_info: async function () {
    return {
      fileName: figma.root.name,
      editorType: figma.editorType,
      currentPage: { id: figma.currentPage.id, name: figma.currentPage.name },
      pages: figma.root.children.map(function (p) { return { id: p.id, name: p.name }; }),
      topLevelNodes: figma.currentPage.children.map(function (c) { return serialize(c, 0); })
    };
  },

  get_selection: async function (p) {
    var d = p.depth === undefined ? 1 : p.depth;
    return figma.currentPage.selection.map(function (n) { return serialize(n, d); });
  },

  get_node_info: async function (p) {
    return serialize(await getNode(p.nodeId), p.depth === undefined ? 2 : p.depth);
  },

  find_nodes: async function (p) {
    var root = p.withinId ? await getNode(p.withinId) : figma.currentPage;
    if (!('findAll' in root)) throw new Error('Ese nodo no tiene hijos');
    var q = p.name ? String(p.name).toLowerCase() : null;
    var found = root.findAll(function (n) {
      if (p.type && n.type !== p.type) return false;
      if (q && n.name.toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
    var limit = p.limit || 50;
    return { total: found.length, nodes: found.slice(0, limit).map(function (n) { return serialize(n, 0); }) };
  },

  set_current_page: async function (p) {
    var page = null;
    if (p.pageId) page = await getNode(p.pageId);
    else if (p.name) page = figma.root.children.filter(function (x) { return x.name === p.name; })[0];
    if (!page || page.type !== 'PAGE') throw new Error('Página no encontrada');
    await figma.setCurrentPageAsync(page);
    return { id: page.id, name: page.name };
  },

  create_frame: async function (p) {
    var n = figma.createFrame();
    n.resize(p.width, p.height);
    if (p.fillColor) n.fills = solid(p.fillColor);
    if (p.cornerRadius !== undefined) n.cornerRadius = p.cornerRadius;
    return serialize(await place(n, p), 0);
  },

  create_rectangle: async function (p) {
    var n = figma.createRectangle();
    n.resize(p.width, p.height);
    if (p.fillColor) n.fills = solid(p.fillColor);
    if (p.cornerRadius !== undefined) n.cornerRadius = p.cornerRadius;
    return serialize(await place(n, p), 0);
  },

  create_ellipse: async function (p) {
    var n = figma.createEllipse();
    n.resize(p.width, p.height);
    if (p.fillColor) n.fills = solid(p.fillColor);
    return serialize(await place(n, p), 0);
  },

  create_text: async function (p) {
    var font = { family: p.fontFamily || 'Inter', style: p.fontStyle || 'Regular' };
    await figma.loadFontAsync(font);
    var n = figma.createText();
    n.fontName = font;
    n.characters = String(p.text);
    if (p.fontSize) n.fontSize = p.fontSize;
    if (p.color) n.fills = solid(p.color);
    if (p.align) n.textAlignHorizontal = p.align;
    if (p.width) { n.textAutoResize = 'HEIGHT'; n.resize(p.width, n.height); }
    if (!p.name) n.name = String(p.text).slice(0, 40);
    return serialize(await place(n, p), 0);
  },

  set_text: async function (p) {
    var n = await getNode(p.nodeId);
    if (n.type !== 'TEXT') throw new Error('El nodo no es de texto');
    await loadFontsOf(n);
    if (p.fontFamily || p.fontStyle) {
      var cur = n.fontName !== figma.mixed ? n.fontName : { family: 'Inter', style: 'Regular' };
      var font = { family: p.fontFamily || cur.family, style: p.fontStyle || cur.style };
      await figma.loadFontAsync(font);
      n.fontName = font;
    }
    n.characters = String(p.text);
    if (p.fontSize) n.fontSize = p.fontSize;
    return serialize(n, 0);
  },

  set_fill: async function (p) {
    var n = await getNode(p.nodeId);
    if (!('fills' in n)) throw new Error('Ese nodo no tiene rellenos');
    n.fills = solid(p.color);
    return serialize(n, 0);
  },

  set_stroke: async function (p) {
    var n = await getNode(p.nodeId);
    if (!('strokes' in n)) throw new Error('Ese nodo no tiene bordes');
    n.strokes = solid(p.color);
    if (p.weight !== undefined) n.strokeWeight = p.weight;
    return serialize(n, 0);
  },

  set_corner_radius: async function (p) {
    var n = await getNode(p.nodeId);
    if (!('cornerRadius' in n)) throw new Error('Ese nodo no tiene esquinas');
    n.cornerRadius = p.radius;
    return serialize(n, 0);
  },

  set_auto_layout: async function (p) {
    var n = await getNode(p.nodeId);
    if (!('layoutMode' in n)) throw new Error('Auto layout solo aplica a frames/componentes');
    if (p.mode) n.layoutMode = p.mode;
    if (p.spacing !== undefined) n.itemSpacing = p.spacing;
    if (p.padding !== undefined) { n.paddingTop = n.paddingRight = n.paddingBottom = n.paddingLeft = p.padding; }
    if (p.paddingTop !== undefined) n.paddingTop = p.paddingTop;
    if (p.paddingRight !== undefined) n.paddingRight = p.paddingRight;
    if (p.paddingBottom !== undefined) n.paddingBottom = p.paddingBottom;
    if (p.paddingLeft !== undefined) n.paddingLeft = p.paddingLeft;
    if (p.primaryAlign) n.primaryAxisAlignItems = p.primaryAlign;
    if (p.counterAlign) n.counterAxisAlignItems = p.counterAlign;
    if (p.hug !== undefined && n.layoutMode !== 'NONE') {
      n.primaryAxisSizingMode = p.hug ? 'AUTO' : 'FIXED';
      n.counterAxisSizingMode = p.hug ? 'AUTO' : 'FIXED';
    }
    return serialize(n, 0);
  },

  move_node: async function (p) {
    var n = await getNode(p.nodeId);
    if (p.parentId) (await getParent(p.parentId)).appendChild(n);
    if (p.x !== undefined) n.x = p.x;
    if (p.y !== undefined) n.y = p.y;
    return serialize(n, 0);
  },

  resize_node: async function (p) {
    var n = await getNode(p.nodeId);
    if (!('resize' in n)) throw new Error('Ese nodo no se puede redimensionar');
    n.resize(p.width, p.height);
    return serialize(n, 0);
  },

  rename_node: async function (p) {
    var n = await getNode(p.nodeId);
    n.name = p.name;
    return serialize(n, 0);
  },

  clone_node: async function (p) {
    var n = await getNode(p.nodeId);
    var c = n.clone();
    if (p.x !== undefined) c.x = p.x;
    if (p.y !== undefined) c.y = p.y;
    return serialize(c, 0);
  },

  delete_node: async function (p) {
    var n = await getNode(p.nodeId);
    var info = { id: n.id, name: n.name, deleted: true };
    n.remove();
    return info;
  },

  select_nodes: async function (p) {
    var nodes = [];
    for (var i = 0; i < p.nodeIds.length; i++) nodes.push(await getNode(p.nodeIds[i]));
    figma.currentPage.selection = nodes;
    figma.viewport.scrollAndZoomIntoView(nodes);
    return { selected: nodes.length };
  },

  export_image: async function (p) {
    var n = await getNode(p.nodeId);
    if (!('exportAsync' in n)) throw new Error('Ese nodo no se puede exportar');
    var format = p.format || 'PNG';
    var settings = format === 'SVG'
      ? { format: 'SVG' }
      : { format: format, constraint: { type: 'SCALE', value: p.scale || 1 } };
    var bytes = await n.exportAsync(settings);
    return { format: format, base64: figma.base64Encode(bytes) };
  },

  run_code: async function (p) {
    var fn = new Function('figma', 'return (async function () {\n' + p.code + '\n})();');
    var result = await fn(figma);
    if (result && typeof result === 'object' && 'type' in result && 'id' in result) return serialize(result, 1);
    return result === undefined ? null : result;
  },

  notify: async function (p) {
    figma.notify(String(p.message));
    return { ok: true };
  }
};

figma.ui.onmessage = async function (msg) {
  if (msg && msg.type === 'status') { onStatus(msg.state); return; }
  if (msg && msg.type === 'close') { figma.closePlugin('Claude desconectado'); return; }
  if (!msg || !msg.id || !msg.command) return;
  var handler = commands[msg.command];
  try {
    if (!handler) throw new Error('Comando desconocido: ' + msg.command);
    var result = await handler(msg.params || {});
    figma.ui.postMessage({ id: msg.id, result: result });
  } catch (e) {
    figma.ui.postMessage({ id: msg.id, error: (e && e.message) ? e.message : String(e) });
  }
};
