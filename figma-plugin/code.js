// Bifrost: recibe comandos del servidor local (vía ui.html) y los ejecuta en Figma.

// Por default corre oculto: la UI (que tiene el WebSocket) vive en un iframe invisible.
// "Mostrar panel" la abre visible para ver el log.

// Súbelo junto con PLUGIN_VERSION de server.py cuando agregues o cambies comandos,
// para que el servidor avise si una pestaña trae una versión vieja.
var BIFROST_VERSION = 2;

if (figma.command === 'disconnect') {
  figma.closePlugin('Claude desconectado');
} else {
  var showPanel = figma.command === 'panel';
  figma.showUI(__html__, { width: 300, height: 250, title: 'Bifrost', visible: showPanel });
  figma.ui.postMessage({ type: 'init', fileName: figma.root.name, fileKey: figma.fileKey, page: figma.currentPage.name, version: BIFROST_VERSION });
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
    if (p.type === 'SOLID') {
      var hex = toHex(p.color);
      return p.opacity !== undefined && p.opacity < 1 ? hex + ' ' + Math.round(p.opacity * 100) + '%' : hex;
    }
    return p.type;
  });
}

function r1(v) { return Math.round(v * 10) / 10; }

// Respuesta mínima para acciones de edición
function brief(n) { return { ok: true, id: n.id, name: n.name }; }

function serialize(node, depth) {
  if (depth === undefined) depth = 1;
  var out = { id: node.id, name: node.name, type: node.type };
  if ('x' in node) { out.x = r1(node.x); out.y = r1(node.y); }
  if ('width' in node) { out.w = r1(node.width); out.h = r1(node.height); }
  if ('visible' in node && !node.visible) out.visible = false;
  if ('fills' in node) { var f = describePaints(node.fills); if (f) out.fills = f; }
  if ('strokes' in node) { var s = describePaints(node.strokes); if (s) out.strokes = s; }
  if ('cornerRadius' in node && node.cornerRadius && node.cornerRadius !== figma.mixed) out.cornerRadius = node.cornerRadius;
  if ('layoutMode' in node && node.layoutMode !== 'NONE') {
    out.layout = node.layoutMode + ' gap ' + node.itemSpacing + ' pad ' +
      [node.paddingTop, node.paddingRight, node.paddingBottom, node.paddingLeft].join(',');
  }
  if (node.type === 'TEXT') {
    out.characters = node.characters;
    if (node.fontSize !== figma.mixed) out.fontSize = node.fontSize;
    if (node.fontName !== figma.mixed) out.font = node.fontName.family + ' ' + node.fontName.style;
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
  get_document_info: async function (p) {
    var out = {
      fileName: figma.root.name,
      currentPage: { id: figma.currentPage.id, name: figma.currentPage.name },
      pageCount: figma.root.children.length,
      topLevelNodes: figma.currentPage.children.slice(0, p.limit || 60).map(function (c) { return serialize(c, 0); })
    };
    if (figma.currentPage.children.length > (p.limit || 60)) out.moreNodes = figma.currentPage.children.length - (p.limit || 60);
    if (p.allPages) out.pages = figma.root.children.map(function (pg) { return { id: pg.id, name: pg.name }; });
    return out;
  },

  get_selection: async function (p) {
    var d = p.depth === undefined ? 1 : p.depth;
    return figma.currentPage.selection.map(function (n) { return serialize(n, d); });
  },

  get_node_info: async function (p) {
    return serialize(await getNode(p.nodeId), p.depth === undefined ? 1 : p.depth);
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
    return brief(n);
  },

  set_fill: async function (p) {
    var n = await getNode(p.nodeId);
    if (!('fills' in n)) throw new Error('Ese nodo no tiene rellenos');
    n.fills = solid(p.color);
    return brief(n);
  },

  set_stroke: async function (p) {
    var n = await getNode(p.nodeId);
    if (!('strokes' in n)) throw new Error('Ese nodo no tiene bordes');
    n.strokes = solid(p.color);
    if (p.weight !== undefined) n.strokeWeight = p.weight;
    return brief(n);
  },

  set_corner_radius: async function (p) {
    var n = await getNode(p.nodeId);
    if (!('cornerRadius' in n)) throw new Error('Ese nodo no tiene esquinas');
    n.cornerRadius = p.radius;
    return brief(n);
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
    return brief(n);
  },

  move_node: async function (p) {
    var n = await getNode(p.nodeId);
    if (p.parentId) (await getParent(p.parentId)).appendChild(n);
    if (p.x !== undefined) n.x = p.x;
    if (p.y !== undefined) n.y = p.y;
    return brief(n);
  },

  resize_node: async function (p) {
    var n = await getNode(p.nodeId);
    if (!('resize' in n)) throw new Error('Ese nodo no se puede redimensionar');
    n.resize(p.width, p.height);
    return brief(n);
  },

  rename_node: async function (p) {
    var n = await getNode(p.nodeId);
    n.name = p.name;
    return brief(n);
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
    var maxSide = Math.max(n.width, n.height) || 1;
    var scale = Math.min(p.scale || 1, (p.maxSize || 800) / maxSide);
    var settings = format === 'SVG'
      ? { format: 'SVG' }
      : { format: format, constraint: { type: 'SCALE', value: Math.max(scale, 0.01) } };
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
  },

  // Copiar entre archivos: el servidor llama export_nodes en el origen e import_nodes en el destino
  export_nodes: async function (p) {
    var nodes = [];
    if (p.nodeIds && p.nodeIds.length) {
      for (var i = 0; i < p.nodeIds.length; i++) nodes.push(await getNode(p.nodeIds[i]));
    } else {
      nodes = figma.currentPage.selection.slice();
    }
    if (!nodes.length) throw new Error('No hay nada seleccionado para copiar en ' + figma.root.name);
    var ctx = { images: {}, count: 0, warnings: [] };
    var out = [];
    for (var j = 0; j < nodes.length; j++) {
      var d = await exportNode(nodes[j], ctx);
      var box = nodes[j].absoluteBoundingBox;
      if (box) { d.absX = box.x; d.absY = box.y; }
      out.push(d);
    }
    return { sourceFile: figma.root.name, nodes: out, images: ctx.images, count: ctx.count, warnings: ctx.warnings };
  },

  import_nodes: async function (p) {
    var data = p.data;
    if (!data || !data.nodes) throw new Error('No llegaron nodos para pegar');
    var parent = await getParent(p.parentId);
    var ctx = { images: data.images || {}, imageCache: {}, count: 0, detached: [], missingFonts: {}, styleMisses: 0, varMisses: 0, warnings: [], made: [] };
    var created = [];
    try {
      for (var i = 0; i < data.nodes.length; i++) {
        var n = await importNode(data.nodes[i], parent, ctx);
        if (n) created.push(n);
      }
    } catch (e) {
      // No dejar copias a medias en el destino
      ctx.made.forEach(function (r) { try { if (!r.removed) r.remove(); } catch (e2) {} });
      throw e;
    }
    // Colocar el grupo pegado conservando la posición relativa entre nodos
    var minX = Infinity, minY = Infinity;
    data.nodes.forEach(function (d) {
      if (d.absX !== undefined) { minX = Math.min(minX, d.absX); minY = Math.min(minY, d.absY); }
    });
    var baseX = p.x, baseY = p.y;
    if (p.nextToId) {
      var ref = await getNode(p.nextToId);
      var rb = ref.absoluteBoundingBox;
      baseX = rb.x + rb.width + (p.gap === undefined ? 200 : p.gap);
      baseY = rb.y;
    }
    if (baseX === undefined && parent.type === 'PAGE') {
      var c = figma.viewport.center;
      baseX = Math.round(c.x); baseY = Math.round(c.y);
    }
    if (baseX !== undefined) {
      created.forEach(function (n, k) {
        var d = data.nodes[k];
        var dx = d.absX !== undefined && minX !== Infinity ? d.absX - minX : 0;
        var dy = d.absY !== undefined && minY !== Infinity ? d.absY - minY : 0;
        n.x = Math.round(baseX + dx);
        n.y = Math.round(baseY + dy);
      });
    }
    figma.currentPage.selection = created;
    figma.viewport.scrollAndZoomIntoView(created);
    return {
      created: created.map(function (n) { return serialize(n, 0); }),
      layersCopied: ctx.count,
      detachedInstances: ctx.detached,
      missingFonts: Object.keys(ctx.missingFonts),
      stylesNotLinked: ctx.styleMisses,
      variablesNotLinked: ctx.varMisses,
      warnings: ctx.warnings.concat(data.warnings || [])
    };
  }
};

// ---------- copiar entre archivos: exportar ----------

var COPY_PROPS = [
  'visible', 'locked', 'opacity', 'blendMode', 'isMask', 'maskType', 'clipsContent',
  'strokeWeight', 'strokeAlign', 'strokeCap', 'strokeJoin', 'strokeMiterLimit', 'dashPattern',
  'strokeTopWeight', 'strokeBottomWeight', 'strokeLeftWeight', 'strokeRightWeight',
  'cornerRadius', 'cornerSmoothing', 'topLeftRadius', 'topRightRadius', 'bottomLeftRadius', 'bottomRightRadius',
  'constraints', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight', 'arcData', 'pointCount', 'innerRadius',
  'effects', 'layoutGrids', 'exportSettings',
  'layoutMode', 'layoutWrap', 'primaryAxisSizingMode', 'counterAxisSizingMode',
  'primaryAxisAlignItems', 'counterAxisAlignItems', 'counterAxisAlignContent',
  'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'itemSpacing', 'counterAxisSpacing',
  'itemReverseZIndex', 'strokesIncludedInLayout',
  'layoutAlign', 'layoutGrow', 'layoutPositioning'
];
var SIZING_PROPS = ['layoutSizingHorizontal', 'layoutSizingVertical'];
var TEXT_NODE_PROPS = ['textAlignHorizontal', 'textAlignVertical', 'paragraphSpacing', 'paragraphIndent', 'textTruncation', 'maxLines', 'leadingTrim'];
var TEXT_SEG_FIELDS = ['fontName', 'fontSize', 'fills', 'letterSpacing', 'lineHeight', 'textDecoration', 'textCase', 'textStyleId', 'fillStyleId'];
var STYLE_FIELDS = ['fillStyleId', 'strokeStyleId', 'effectStyleId', 'gridStyleId', 'textStyleId'];

function clone(v) { return v === undefined ? undefined : JSON.parse(JSON.stringify(v)); }

// Para comparar rellenos sin que cuenten las referencias a variables
function samePaints(a, b) {
  function norm(list) {
    return JSON.stringify((list || []).map(function (p) {
      var c = clone(p); delete c.boundVariables; delete c._colorVarKey; return c;
    }));
  }
  if (a === figma.mixed || b === figma.mixed) return false;
  return norm(a) === norm(b);
}

async function styleKey(id) {
  if (!id || id === figma.mixed) return null;
  try { var s = await figma.getStyleByIdAsync(id); return s ? s.key : null; } catch (e) { return null; }
}

async function variableKey(alias) {
  if (!alias || !alias.id) return null;
  try { var v = await figma.variables.getVariableByIdAsync(alias.id); return v ? v.key : null; } catch (e) { return null; }
}

async function exportPaints(paints, ctx) {
  if (!paints || paints === figma.mixed) return undefined;
  var out = [];
  for (var i = 0; i < paints.length; i++) {
    var p = clone(paints[i]);
    if (p.type === 'IMAGE' && p.imageHash && !ctx.images[p.imageHash]) {
      try {
        var img = figma.getImageByHash(p.imageHash);
        ctx.images[p.imageHash] = figma.base64Encode(await img.getBytesAsync());
      } catch (e) { ctx.warnings.push('No pude leer una imagen'); }
    }
    if (p.boundVariables && p.boundVariables.color) {
      p._colorVarKey = await variableKey(p.boundVariables.color);
    }
    delete p.boundVariables;
    out.push(p);
  }
  return out;
}

async function exportNode(n, ctx) {
  ctx.count++;
  var d = { type: n.type, name: n.name };
  COPY_PROPS.concat(SIZING_PROPS).forEach(function (k) {
    if (!(k in n)) return;
    var v;
    try { v = n[k]; } catch (e) { return; }
    if (v === figma.mixed || v === undefined || typeof v === 'symbol') return;
    d[k] = clone(v);
  });
  if ('width' in n) { d.width = n.width; d.height = n.height; }
  if ('relativeTransform' in n) d.relativeTransform = clone(n.relativeTransform);
  if ('fills' in n) d.fills = await exportPaints(n.fills, ctx);
  if ('strokes' in n) d.strokes = await exportPaints(n.strokes, ctx);

  d.styles = {};
  for (var s = 0; s < STYLE_FIELDS.length; s++) {
    var f = STYLE_FIELDS[s];
    if (f in n && n.type !== 'TEXT') { var key = await styleKey(n[f]); if (key) d.styles[f] = key; }
  }
  if (n.type === 'TEXT' && n.textStyleId && n.textStyleId !== figma.mixed) {
    var tk = await styleKey(n.textStyleId); if (tk) d.styles.textStyleId = tk;
  }

  // Variables ligadas a propiedades numéricas (padding, spacing, radios, tamaños…)
  d.vars = {};
  if (n.boundVariables) {
    var bv = n.boundVariables;
    for (var field in bv) {
      if (field === 'fills' || field === 'strokes' || field === 'componentProperties' || field === 'textRangeFills') continue;
      var alias = bv[field];
      if (alias && !Array.isArray(alias) && alias.id) {
        var vk = await variableKey(alias); if (vk) d.vars[field] = vk;
      }
    }
  }

  if (n.type === 'TEXT') {
    d.characters = n.characters;
    d.textAutoResize = n.textAutoResize;
    TEXT_NODE_PROPS.forEach(function (k) { if (k in n && n[k] !== figma.mixed) d[k] = clone(n[k]); });
    var segs = n.getStyledTextSegments(TEXT_SEG_FIELDS);
    d.segments = [];
    for (var g = 0; g < segs.length; g++) {
      var seg = segs[g];
      var sd = { start: seg.start, end: seg.end, fontName: seg.fontName, fontSize: seg.fontSize,
        letterSpacing: seg.letterSpacing, lineHeight: seg.lineHeight, textDecoration: seg.textDecoration, textCase: seg.textCase };
      sd.fills = await exportPaints(seg.fills, ctx);
      sd.textStyleKey = await styleKey(seg.textStyleId);
      sd.fillStyleKey = await styleKey(seg.fillStyleId);
      d.segments.push(sd);
    }
  } else if (n.type === 'VECTOR') {
    try { d.vectorNetwork = clone(n.vectorNetwork); } catch (e) {}
    try { d.vectorPaths = clone(n.vectorPaths); } catch (e) {}
  } else if (n.type === 'BOOLEAN_OPERATION') {
    // Se reconstruye como SVG (queda como vector editable, sin la operación booleana)
    try { d.svg = await n.exportAsync({ format: 'SVG_STRING' }); } catch (e) { ctx.warnings.push('No pude copiar la forma ' + n.name); }
  }

  if (n.type === 'INSTANCE') {
    var main = await n.getMainComponentAsync();
    if (main) {
      d.componentKey = main.key;
      d.componentName = main.name;
      d.componentRemote = main.remote;
    }
    try {
      var props = n.componentProperties, values = {};
      for (var pk in props) if (props[pk].type !== 'INSTANCE_SWAP') values[pk] = props[pk].value;
      d.componentProps = values;
    } catch (e) {}
  }

  if ('children' in n && n.type !== 'BOOLEAN_OPERATION') {
    d.children = [];
    for (var c = 0; c < n.children.length; c++) d.children.push(await exportNode(n.children[c], ctx));
  }
  return d;
}

// ---------- copiar entre archivos: reconstruir ----------

async function importPaints(paints, ctx) {
  if (!paints) return undefined;
  var out = [];
  for (var i = 0; i < paints.length; i++) {
    var p = clone(paints[i]);
    var varKey = p._colorVarKey;
    delete p._colorVarKey;
    if (p.type === 'IMAGE' && p.imageHash) {
      if (!ctx.imageCache[p.imageHash] && ctx.images[p.imageHash]) {
        ctx.imageCache[p.imageHash] = figma.createImage(figma.base64Decode(ctx.images[p.imageHash])).hash;
      }
      if (ctx.imageCache[p.imageHash]) p.imageHash = ctx.imageCache[p.imageHash];
    }
    if (varKey) {
      try {
        var v = await figma.variables.importVariableByKeyAsync(varKey);
        p = figma.variables.setBoundVariableForPaint(p, 'color', v);
      } catch (e) { ctx.varMisses++; }
    }
    out.push(p);
  }
  return out;
}

async function loadFontSafe(font, ctx) {
  try { await figma.loadFontAsync(font); return font; }
  catch (e) {
    ctx.missingFonts[font.family + ' ' + font.style] = true;
    var fallback = { family: 'Inter', style: /bold/i.test(font.style) ? 'Bold' : 'Regular' };
    await figma.loadFontAsync(fallback);
    return fallback;
  }
}

// Figma no deja mover una instancia con textos sin tener sus fuentes cargadas
async function loadNodeFonts(node, ctx) {
  var texts = node.type === 'TEXT' ? [node] : ('findAllWithCriteria' in node ? node.findAllWithCriteria({ types: ['TEXT'] }) : []);
  var seen = {};
  for (var i = 0; i < texts.length; i++) {
    var t = texts[i];
    var fonts = t.characters.length ? t.getRangeAllFontNames(0, t.characters.length) : [t.fontName];
    for (var j = 0; j < fonts.length; j++) {
      var f = fonts[j];
      if (!f || f === figma.mixed) continue;
      var key = f.family + ' ' + f.style;
      if (seen[key]) continue;
      seen[key] = true;
      try { await figma.loadFontAsync(f); } catch (e) { ctx.missingFonts[key] = true; }
    }
  }
}

function setSafe(node, key, value) {
  if (value === undefined || !(key in node)) return;
  try { node[key] = value; } catch (e) {}
}

async function applyStyle(node, field, key, ctx) {
  try {
    var st = await figma.importStyleByKeyAsync(key);
    var setter = { fillStyleId: 'setFillStyleIdAsync', strokeStyleId: 'setStrokeStyleIdAsync', effectStyleId: 'setEffectStyleIdAsync',
      gridStyleId: 'setGridStyleIdAsync', textStyleId: 'setTextStyleIdAsync' }[field];
    if (setter && node[setter]) await node[setter](st.id);
    else node[field] = st.id;
  } catch (e) { ctx.styleMisses++; }
}

async function applyCommon(node, d, ctx, isInstanceChild) {
  COPY_PROPS.forEach(function (k) {
    if (k === 'layoutAlign' || k === 'layoutGrow' || k === 'layoutPositioning') return; // se aplican ya dentro del padre
    if (isInstanceChild && (k === 'layoutMode' || k.indexOf('padding') === 0 || k === 'itemSpacing')) return;
    setSafe(node, k, d[k]);
  });
  if (d.fills && 'fills' in node) { try { node.fills = await importPaints(d.fills, ctx); } catch (e) {} }
  if (d.strokes && 'strokes' in node) { try { node.strokes = await importPaints(d.strokes, ctx); } catch (e) {} }
  for (var f in (d.styles || {})) await applyStyle(node, f, d.styles[f], ctx);
  for (var vf in (d.vars || {})) {
    try { node.setBoundVariable(vf, await figma.variables.importVariableByKeyAsync(d.vars[vf])); } catch (e) { ctx.varMisses++; }
  }
}

async function applyText(node, d, ctx) {
  var first = d.segments && d.segments[0];
  node.fontName = await loadFontSafe(first ? first.fontName : { family: 'Inter', style: 'Regular' }, ctx);
  node.characters = d.characters || '';
  if (d.textAutoResize) setSafe(node, 'textAutoResize', d.textAutoResize);
  TEXT_NODE_PROPS.forEach(function (k) { setSafe(node, k, d[k]); });
  for (var i = 0; i < (d.segments || []).length; i++) {
    var s = d.segments[i];
    if (s.end <= s.start || s.end > node.characters.length) continue;
    try {
      node.setRangeFontName(s.start, s.end, await loadFontSafe(s.fontName, ctx));
      if (s.fontSize) node.setRangeFontSize(s.start, s.end, s.fontSize);
      if (s.letterSpacing) node.setRangeLetterSpacing(s.start, s.end, s.letterSpacing);
      if (s.lineHeight) node.setRangeLineHeight(s.start, s.end, s.lineHeight);
      if (s.textDecoration) node.setRangeTextDecoration(s.start, s.end, s.textDecoration);
      if (s.textCase) node.setRangeTextCase(s.start, s.end, s.textCase);
      if (s.fills) node.setRangeFills(s.start, s.end, await importPaints(s.fills, ctx));
      if (s.textStyleKey) {
        try { var ts = await figma.importStyleByKeyAsync(s.textStyleKey); await node.setRangeTextStyleIdAsync(s.start, s.end, ts.id); }
        catch (e) { ctx.styleMisses++; }
      }
      if (s.fillStyleKey) {
        try { var fs = await figma.importStyleByKeyAsync(s.fillStyleKey); await node.setRangeFillStyleIdAsync(s.start, s.end, fs.id); }
        catch (e) { ctx.styleMisses++; }
      }
    } catch (e) {}
  }
}

function placeInParent(node, d, parent) {
  var autoParent = parent && 'layoutMode' in parent && parent.layoutMode !== 'NONE';
  if (autoParent) {
    setSafe(node, 'layoutPositioning', d.layoutPositioning);
    setSafe(node, 'layoutAlign', d.layoutAlign);
    setSafe(node, 'layoutGrow', d.layoutGrow);
  }
  if ((!autoParent || d.layoutPositioning === 'ABSOLUTE') && d.relativeTransform) {
    try { node.relativeTransform = d.relativeTransform; } catch (e) {}
  }
  SIZING_PROPS.forEach(function (k) { setSafe(node, k, d[k]); });
}

// Lleva textos, colores, visibilidad y swaps de una instancia original a la recién creada
async function applyOverrides(node, d, ctx) {
  if (!d.children || !('children' in node)) return;
  var kids = node.children;
  for (var i = 0; i < d.children.length && i < kids.length; i++) {
    var cd = d.children[i], kid = kids[i];
    if (cd.name !== kid.name && cd.type !== kid.type) continue;
    setSafe(kid, 'visible', cd.visible);
    if (cd.type === 'INSTANCE' && kid.type === 'INSTANCE' && cd.componentKey) {
      var km = await kid.getMainComponentAsync();
      if (km && km.key !== cd.componentKey) {
        try { kid.swapComponent(await figma.importComponentByKeyAsync(cd.componentKey)); } catch (e) {}
      }
      if (cd.componentProps) { try { kid.setProperties(cd.componentProps); } catch (e) {} }
    }
    if (cd.fills && 'fills' in kid) {
      try {
        if (!samePaints(kid.fills, cd.fills)) kid.fills = await importPaints(cd.fills, ctx);
      } catch (e) {}
    }
    if (cd.type === 'TEXT' && kid.type === 'TEXT' && kid.characters !== cd.characters) {
      try { await applyText(kid, cd, ctx); } catch (e) {}
    }
    await applyOverrides(kid, cd, ctx);
  }
}

async function importNode(d, parent, ctx) {
  ctx.count++;
  var node = null;
  var t = d.type;

  if (t === 'GROUP') {
    var kids = [];
    for (var g = 0; g < (d.children || []).length; g++) {
      var k = await importNode(d.children[g], parent, ctx);
      if (k) kids.push(k);
    }
    if (!kids.length) return null;
    var group = figma.group(kids, parent);
    group.name = d.name;
    setSafe(group, 'opacity', d.opacity);
    setSafe(group, 'visible', d.visible);
    setSafe(group, 'blendMode', d.blendMode);
    if (d.effects) setSafe(group, 'effects', d.effects);
    return group;
  }

  if (t === 'INSTANCE' && d.componentKey) {
    try {
      var comp = await figma.importComponentByKeyAsync(d.componentKey);
      node = comp.createInstance();
    } catch (e) {
      ctx.detached.push(d.componentName || d.name);
    }
  }

  if (!node) {
    if (t === 'RECTANGLE') node = figma.createRectangle();
    else if (t === 'ELLIPSE') node = figma.createEllipse();
    else if (t === 'STAR') node = figma.createStar();
    else if (t === 'POLYGON') node = figma.createPolygon();
    else if (t === 'LINE') node = figma.createLine();
    else if (t === 'VECTOR') node = figma.createVector();
    else if (t === 'TEXT') node = figma.createText();
    else if (t === 'SECTION') node = figma.createSection();
    else if (t === 'BOOLEAN_OPERATION' && d.svg) node = figma.createNodeFromSvg(d.svg);
    else node = figma.createFrame(); // FRAME, COMPONENT, COMPONENT_SET, INSTANCE sin librería
    if (t === 'FRAME' || t === 'COMPONENT' || t === 'COMPONENT_SET' || t === 'INSTANCE') node.fills = [];
  }

  ctx.made.push(node);
  if (node.type === 'INSTANCE') await loadNodeFonts(node, ctx);
  // Los nodos nuevos ya nacen en la página actual; moverlos otra vez falla si traen fuentes faltantes
  if (node.parent !== parent) parent.appendChild(node);
  node.name = d.name;
  var isInstance = node.type === 'INSTANCE';

  if (t === 'TEXT') await applyText(node, d, ctx);
  if (t === 'VECTOR') {
    try { await node.setVectorNetworkAsync(d.vectorNetwork); }
    catch (e) { try { node.vectorPaths = d.vectorPaths; } catch (e2) {} }
  }

  if (isInstance) {
    if (d.componentProps) { try { node.setProperties(d.componentProps); } catch (e) {} }
  } else {
    if (d.layoutMode) setSafe(node, 'layoutMode', d.layoutMode);
  }

  var autoResizeText = t === 'TEXT' && d.textAutoResize && d.textAutoResize !== 'NONE';
  if ('resize' in node && d.width !== undefined && !autoResizeText && t !== 'BOOLEAN_OPERATION') {
    try { node.resize(Math.max(d.width, 0.01), Math.max(d.height, 0.01)); } catch (e) {}
  } else if (t === 'TEXT' && d.textAutoResize === 'HEIGHT') {
    try { node.resize(Math.max(d.width, 1), node.height); } catch (e) {}
  } else if (t === 'BOOLEAN_OPERATION') {
    try { node.resize(Math.max(d.width, 0.01), Math.max(d.height, 0.01)); } catch (e) {}
  }

  if (isInstance) {
    // Una instancia trae su propio contenido: solo se aplican los cambios que tenía el original
    setSafe(node, 'visible', d.visible);
    setSafe(node, 'opacity', d.opacity);
    if (d.fills) { try { if (!samePaints(node.fills, d.fills)) node.fills = await importPaints(d.fills, ctx); } catch (e) {} }
    await applyOverrides(node, d, ctx);
  } else {
    await applyCommon(node, d, ctx, false);
    if (t !== 'BOOLEAN_OPERATION' && d.children && 'appendChild' in node) {
      for (var c = 0; c < d.children.length; c++) await importNode(d.children[c], node, ctx);
    }
    // Re-aplicar modos de tamaño una vez que hay hijos (HUG/FIXED)
    setSafe(node, 'primaryAxisSizingMode', d.primaryAxisSizingMode);
    setSafe(node, 'counterAxisSizingMode', d.counterAxisSizingMode);
  }

  placeInParent(node, d, parent);
  return node;
}

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
