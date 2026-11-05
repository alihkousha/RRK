// The network map: people, companies and addresses laid out by a small force
// simulation on a canvas. Positions live on the model's nodes, so the map keeps
// its shape while the user reads a profile and comes back.

const ALPHA_MIN = 0.003;
const LINK_DISTANCE = 70;
const REPULSION = 240;
const MAX_REPULSION_D2 = 520 * 520;
const FADED = 0.16;

// The role groups the map can hide; "ended" hides the links whose latest notice records them leaving.
export const ROLE_GROUPS = {
  board: ["chair", "vice_chair", "ceo", "board", "alternate_board", "secretary", "treasurer"],
  owners: ["partner", "shareholder"],
  inspectors: ["inspector", "alternate_inspector"],
  liquidation: ["liquidator", "liquidation_supervisor"],
  representative: ["representative"],
  signatory: ["signatory"],
  named: [], // no role given
};
const GROUP_OF = new Map(Object.entries(ROLE_GROUPS).flatMap(([group, roles]) => roles.map((r) => [r, group])));

export const edgeKey = (a, b) => (a < b ? `${a}|${b}` : `${b}|${a}`);
export const isService = (edge) => !!edge.service; // only inspectors: auditors link unrelated companies
export const newFilter = () => ({ hide: new Set(["inspectors"]), from: "", to: "" });
export const newModel = () => ({ nodes: new Map(), edges: new Map(), expanded: new Map(), picked: new Set(),
  filter: newFilter(), view: { x: 0, y: 0, k: 1 } });

/** Whether *edge* shows under *filter*: one of its roles in a group not hidden, and active in its years. */
export function passes(edge, { hide, from, to }) {
  if (hide.has("ended") && edge.ended) return false;
  if (edge.first && ((from && edge.last < from) || (to && edge.first > `${to}~`))) return false;
  const groups = edge.roles.filter((r) => GROUP_OF.has(r)).map((r) => GROUP_OF.get(r));
  return (groups.length ? groups : ["named"]).some((g) => !hide.has(g));
}
const radius = (node) => Math.min(26, 5 + 2 * Math.sqrt(node.links || 1));
const lineWidth = (edge) => Math.min(5, 1 + Math.log2(edge.ads || 1) * 0.9);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const label = (node) => {
  const text = node.name || node.id;
  return text.length > 32 ? text.slice(0, 31) + "…" : text;
};

function segmentDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const t = clamp(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy || 1), 0, 1);
  return Math.hypot(px - ax - t * dx, py - ay - t * dy);
}

export class NetworkView {
  // options: onSelect(selection), onExpand(key), onPick(key) (shift-click), onHover(hit, event),
  // insets() -> {top, right, bottom, left}
  constructor(canvas, model, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.model = model;
    this.options = options;
    this.selected = null; // {node: key} | {edge: key}
    this.hover = null;
    this.path = null; // {nodes: Set, edges: Set}
    this.labels = false;
    this.alpha = 0;
    this.alphaTarget = 0;
    this.autoFit = false;
    this.widths = new Map();
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.dark = matchMedia("(prefers-color-scheme: dark)");
    this.dark.addEventListener("change", this.onTheme);
    this.readColors();
    this.changed();
    canvas.addEventListener("pointerdown", this.down);
    canvas.addEventListener("pointermove", this.move);
    canvas.addEventListener("pointerup", this.up);
    canvas.addEventListener("pointercancel", this.up);
    canvas.addEventListener("pointerleave", this.leave);
    canvas.addEventListener("dblclick", this.dblclick);
    canvas.addEventListener("wheel", this.wheel, { passive: false });
    canvas.addEventListener("keydown", this.key);
    this.resizer = new ResizeObserver(() => this.resize());
    this.resizer.observe(canvas);
    document.fonts?.ready.then(() => { this.widths.clear(); this.draw(); });
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.resizer.disconnect();
    this.dark.removeEventListener("change", this.onTheme);
  }

  onTheme = () => { this.readColors(); this.draw(); };

  readColors() {
    const css = getComputedStyle(document.documentElement);
    const v = (name) => css.getPropertyValue(name).trim();
    this.colors = { bg: v("--bg"), text: v("--text"), muted: v("--text-3"), edge: v("--edge"),
      strong: v("--edge-strong"), person: v("--person"), company: v("--company"), address: v("--address") };
    this.font = v("--font");
  }

  resize() {
    const { width, height } = this.canvas.getBoundingClientRect();
    this.w = width;
    this.h = height;
    this.dpr = devicePixelRatio || 1;
    this.canvas.width = Math.round(width * this.dpr);
    this.canvas.height = Math.round(height * this.dpr);
    this.paint();
  }

  // The model ------------------------------------------------------------

  /** Call after changing the model or its filter: what shows follows from them. */
  changed() {
    const m = this.model, linked = new Set();
    this.neighbours = new Map([...m.nodes.keys()].map((key) => [key, new Set()]));
    this.shownEdges = new Map();
    for (const [key, e] of m.edges) {
      this.neighbours.get(e.a).add(e.b);
      this.neighbours.get(e.b).add(e.a);
      if (passes(e, m.filter)) {
        this.shownEdges.set(key, e);
        linked.add(e.a).add(e.b);
      }
    }
    // A node shows with one of its lines, or when nothing on the map links it, the user expanded or picked it.
    this.ranked = [...m.nodes.values()].filter((n) => linked.has(n.key) || !this.neighbours.get(n.key).size
      || m.expanded.has(n.key) || m.picked.has(n.key) || this.path?.nodes.has(n.key)).sort((a, b) => b.links - a.links);
    this.shown = new Set(this.ranked.map((n) => n.key));
    this.draw();
  }

  /** Adds *nodes* and *edges*; new nodes start next to a node they link to (or *anchor*). */
  add({ nodes, edges }, anchor = null) {
    const m = this.model;
    const fresh = nodes.filter((n) => !m.nodes.has(n.key));
    for (const n of nodes) if (m.nodes.has(n.key)) Object.assign(m.nodes.get(n.key), n);
    const near = new Map();
    for (const e of edges) {
      if (!near.has(e.a) && m.nodes.has(e.b)) near.set(e.a, e.b);
      if (!near.has(e.b) && m.nodes.has(e.a)) near.set(e.b, e.a);
    }
    fresh.forEach((n, i) => {
      const at = m.nodes.get(anchor) ?? m.nodes.get(near.get(n.key));
      const angle = at ? Math.random() * 2 * Math.PI : i * Math.PI * (3 - Math.sqrt(5));
      const r = at ? LINK_DISTANCE * (0.5 + Math.random() * 0.7) : 12 * Math.sqrt(0.5 + i);
      m.nodes.set(n.key, { ...n, x: (at?.x ?? 0) + r * Math.cos(angle), y: (at?.y ?? 0) + r * Math.sin(angle), vx: 0, vy: 0 });
    });
    for (const e of edges) if (m.nodes.has(e.a) && m.nodes.has(e.b)) m.edges.set(edgeKey(e.a, e.b), e);
    this.changed();
    if (fresh.length) this.reheat(fresh.length === m.nodes.size ? 1 : 0.5);
    return fresh.length;
  }

  remove(key) {
    this.model.nodes.delete(key);
    this.model.expanded.delete(key);
    this.model.picked.delete(key);
    for (const [k, e] of this.model.edges) if (e.a === key || e.b === key) this.model.edges.delete(k);
    if (this.path?.nodes.has(key)) this.path = null;
    if (this.selected?.node === key || this.selected?.edge?.split("|").includes(key)) this.select(null);
    this.changed();
    this.reheat(0.3);
  }

  clear() {
    this.model.nodes.clear();
    this.model.edges.clear();
    this.model.expanded.clear();
    this.model.picked.clear();
    Object.assign(this.model.view, { x: 0, y: 0, k: 1 });
    this.path = null;
    this.select(null);
    this.changed();
  }

  select(selection) {
    this.selected = selection;
    this.options.onSelect?.(selection);
    this.draw();
  }

  /** Highlights *keys* and the *edges* between them (by default a chain: each key to the next). */
  setPath(keys, edges = keys?.slice(1).map((key, i) => edgeKey(keys[i], key))) {
    this.path = keys && { nodes: new Set(keys), edges: new Set(edges) };
    this.changed();
  }

  unpin(key) {
    const n = this.model.nodes.get(key);
    n.fx = n.fy = null;
    this.reheat(0.3);
  }

  // The simulation ---------------------------------------------------------

  reheat(alpha) {
    this.alpha = Math.max(this.alpha, alpha);
    if (this.reduced) { // no animation: settle at once
      for (let i = 0; i < 300 && this.alpha > ALPHA_MIN; i++) this.tick();
      if (this.autoFit) this.fit();
      this.autoFit = false;
    }
    this.draw();
  }

  tick() {
    // shortcut: all-pairs repulsion, fine up to the ~500 nodes one map shows; Barnes-Hut beyond that
    const nodes = this.ranked, alpha = this.alpha, byKey = this.model.nodes;
    const degree = (key) => this.neighbours.get(key).size || 1;
    for (const e of this.shownEdges.values()) {
      const s = byKey.get(e.a), t = byKey.get(e.b);
      let dx = t.x + t.vx - s.x - s.vx || 1e-6, dy = t.y + t.vy - s.y - s.vy || 1e-6;
      const l = Math.hypot(dx, dy), ds = degree(e.a), dt = degree(e.b);
      const f = ((l - LINK_DISTANCE) / l) * alpha / Math.min(ds, dt), bias = ds / (ds + dt);
      dx *= f;
      dy *= f;
      t.vx -= dx * bias;
      t.vy -= dy * bias;
      s.vx += dx * (1 - bias);
      s.vy += dy * (1 - bias);
    }
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
        if (d2 > MAX_REPULSION_D2) continue;
        if (d2 < 1) {
          dx = Math.random() - 0.5;
          dy = Math.random() - 0.5;
          d2 = 1;
        }
        const f = (REPULSION * alpha) / d2;
        a.vx -= dx * f;
        a.vy -= dy * f;
        b.vx += dx * f;
        b.vy += dy * f;
      }
    }
    for (const n of nodes) {
      n.vx -= n.x * 0.03 * alpha; // weak gravity keeps separate groups in view
      n.vy -= n.y * 0.03 * alpha;
      if (n.fx != null) {
        n.x = n.fx;
        n.y = n.fy;
        n.vx = n.vy = 0;
      } else {
        n.x += n.vx *= 0.6;
        n.y += n.vy *= 0.6;
      }
    }
    this.alpha += (this.alphaTarget - this.alpha) * 0.0228;
  }

  draw() {
    if (!this.raf && !this.destroyed) this.raf = requestAnimationFrame(this.frame);
  }

  frame = () => {
    this.raf = 0;
    if (!this.reduced && (this.alpha > ALPHA_MIN || this.alphaTarget)) {
      this.tick();
      this.tick();
      if (this.autoFit) {
        this.fit();
        if (this.alpha < 0.05) this.autoFit = false;
      }
      this.draw();
    }
    this.paint();
  };

  // Camera -----------------------------------------------------------------

  screen(n) {
    const v = this.model.view;
    return [this.w / 2 + v.x + n.x * v.k, this.h / 2 + v.y + n.y * v.k];
  }

  world(x, y) {
    const v = this.model.view;
    return [(x - this.w / 2 - v.x) / v.k, (y - this.h / 2 - v.y) / v.k];
  }

  r(n) {
    return Math.max(2.5, radius(n) * Math.sqrt(this.model.view.k));
  }

  insets() {
    return this.options.insets?.() ?? { top: 0, right: 0, bottom: 0, left: 0 };
  }

  fit() {
    const v = this.model.view, nodes = this.ranked;
    if (!nodes.length) return this.draw();
    const pad = 48, ins = this.insets();
    const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const aw = Math.max(80, this.w - ins.left - ins.right - 2 * pad);
    const ah = Math.max(80, this.h - ins.top - ins.bottom - 2 * pad);
    v.k = clamp(Math.min(aw / (x1 - x0 || 1), ah / (y1 - y0 || 1)), 0.15, 2);
    v.x = ins.left + pad + aw / 2 - this.w / 2 - ((x0 + x1) / 2) * v.k;
    v.y = ins.top + pad + ah / 2 - this.h / 2 - ((y0 + y1) / 2) * v.k;
    this.draw();
  }

  center(key) {
    const n = this.model.nodes.get(key), v = this.model.view, ins = this.insets();
    if (!n) return;
    v.x = (ins.left + this.w - ins.right) / 2 - this.w / 2 - n.x * v.k;
    v.y = (ins.top + this.h - ins.bottom) / 2 - this.h / 2 - n.y * v.k;
    this.autoFit = false;
    this.draw();
  }

  zoomAt(x, y, factor) {
    const v = this.model.view, [wx, wy] = this.world(x, y);
    v.k = clamp(v.k * factor, 0.08, 6);
    v.x = x - this.w / 2 - wx * v.k;
    v.y = y - this.h / 2 - wy * v.k;
    this.autoFit = false;
    this.draw();
  }

  zoom(factor) {
    this.zoomAt(this.w / 2, this.h / 2, factor);
  }

  png() {
    this.paint();
    return new Promise((resolve) => this.canvas.toBlob(resolve, "image/png"));
  }

  // Painting ---------------------------------------------------------------

  focus() {
    if (this.path) return this.path;
    const key = this.hover?.node ?? (this.hover?.edge ? null : this.selected?.node);
    if (key) {
      const around = this.neighbours.get(key) ?? new Set();
      return { nodes: new Set([key, ...around]), edges: new Set([...around].map((o) => edgeKey(key, o))) };
    }
    const e = this.model.edges.get(this.hover?.edge ?? this.selected?.edge);
    return e ? { nodes: new Set([e.a, e.b]), edges: new Set([edgeKey(e.a, e.b)]) } : null;
  }

  shape(kind, x, y, r) {
    const ctx = this.ctx;
    ctx.beginPath();
    if (kind === "person") {
      ctx.arc(x, y, r, 0, 2 * Math.PI);
    } else if (kind === "company") { // a square of the circle's area
      const s = r * 0.886;
      ctx.roundRect(x - s, y - s, 2 * s, 2 * s, r * 0.22);
    } else { // a diamond of the circle's area
      const d = r * 1.253;
      ctx.moveTo(x, y - d);
      ctx.lineTo(x + d, y);
      ctx.lineTo(x, y + d);
      ctx.lineTo(x - d, y);
      ctx.closePath();
    }
  }

  measure(text) {
    if (!this.widths.has(text)) this.widths.set(text, this.ctx.measureText(text).width);
    return this.widths.get(text);
  }

  paint() {
    const { ctx, w, h, colors: c } = this;
    if (!w || this.destroyed) return;
    const nodes = this.model.nodes, scale = Math.sqrt(this.model.view.k), focus = this.focus();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.globalAlpha = 1;
    ctx.fillStyle = c.bg;
    ctx.fillRect(0, 0, w, h);

    ctx.lineCap = "round";
    for (const [key, e] of this.shownEdges) {
      const on = !focus || focus.edges.has(key);
      const hot = this.hover?.edge === key || this.selected?.edge === key || this.path?.edges.has(key);
      const [ax, ay] = this.screen(nodes.get(e.a)), [bx, by] = this.screen(nodes.get(e.b));
      ctx.globalAlpha = on ? ((isService(e) || e.ended) && !hot ? 0.55 : 1) : FADED;
      ctx.strokeStyle = hot || (focus && on) ? c.strong : c.edge;
      ctx.lineWidth = lineWidth(e) * scale * (hot ? 1.5 : 1);
      // dashed: inspectors only; dotted: the latest notice records them leaving
      ctx.setLineDash(isService(e) ? [4 * scale, 4 * scale] : e.ended ? [0.1, 2.5 * ctx.lineWidth + 2] : []);
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    for (const n of nodes.values()) {
      const [x, y] = this.screen(n), r = this.r(n);
      if (!this.shown.has(n.key) || x < -r - 8 || y < -r - 8 || x > w + r + 8 || y > h + r + 8) continue;
      ctx.globalAlpha = !focus || focus.nodes.has(n.key) ? 1 : FADED;
      this.shape(n.kind, x, y, r);
      ctx.fillStyle = c[n.kind];
      ctx.fill();
      ctx.lineWidth = 2; // a ring of the background keeps touching marks apart
      ctx.strokeStyle = c.bg;
      ctx.stroke();
      if (this.model.picked.has(n.key)) { // a double ring: picked to compare
        for (const gap of [4, 7.5]) {
          this.shape(n.kind, x, y, r + gap);
          ctx.lineWidth = 1.6;
          ctx.strokeStyle = c.text;
          ctx.stroke();
        }
      } else if (this.selected?.node === n.key || this.hover?.node === n.key) {
        this.shape(n.kind, x, y, r + 4);
        ctx.lineWidth = 2;
        ctx.strokeStyle = c.text;
        ctx.stroke();
      } else if (n.links > this.neighbours.get(n.key).size) { // links not on the map yet
        this.shape(n.kind, x, y, r + 3.5);
        ctx.setLineDash([2, 3]);
        ctx.lineWidth = 1.2;
        ctx.strokeStyle = c.muted;
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // Labels: the selected, hovered and path nodes always; the rest, most linked
    // first, wherever they don't cover a label already drawn.
    ctx.font = `500 12px ${this.font}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.direction = "rtl"; // the gazette's names are Persian
    ctx.lineJoin = "round";
    const forced = new Set([this.selected?.node, this.hover?.node, ...(this.path?.nodes ?? [])]);
    const many = this.labels || this.model.view.k > 1.4;
    const candidates = [...forced].filter((k) => nodes.has(k)).map((k) => nodes.get(k));
    for (const k of focus?.nodes ?? []) candidates.push(nodes.get(k));
    candidates.push(...(many ? this.ranked : this.ranked.slice(0, 24)));
    const placed = [], done = new Set();
    for (const n of candidates) {
      if (!n || done.has(n.key) || !this.shown.has(n.key)) continue;
      done.add(n.key);
      const [x, y] = this.screen(n), r = this.r(n);
      if (x < -100 || y < -40 || x > w + 100 || y > h + 40) continue;
      const text = label(n), half = this.measure(text) / 2 + 3;
      const free = (top) => !placed.some((p) => x - half < p[2] && x + half > p[0] && top < p[3] && top + 17 > p[1]);
      // under the node, else above it; a label that fits nowhere waits for zooming in
      const below = y + r + 3, above = y - r - 20;
      const top = free(below) ? below : free(above) ? above : forced.has(n.key) ? below : null;
      if (top == null) continue;
      placed.push([x - half, top, x + half, top + 17]);
      ctx.globalAlpha = !focus || focus.nodes.has(n.key) ? 1 : FADED;
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = c.bg;
      ctx.strokeText(text, x, top + 1);
      ctx.fillStyle = c.text;
      ctx.fillText(text, x, top + 1);
    }
    ctx.globalAlpha = 1;
  }

  // Pointer and keys -------------------------------------------------------

  nodeAt(x, y) {
    let hit = null;
    for (const n of this.model.nodes.values()) { // the last drawn is on top
      if (!this.shown.has(n.key)) continue;
      const [sx, sy] = this.screen(n), r = this.r(n) + 3;
      if ((sx - x) ** 2 + (sy - y) ** 2 <= r * r) hit = n;
    }
    return hit;
  }

  edgeAt(x, y) {
    let hit = null, best = 6;
    for (const [key, e] of this.shownEdges) {
      const d = segmentDistance(x, y, ...this.screen(this.model.nodes.get(e.a)), ...this.screen(this.model.nodes.get(e.b)));
      if (d < best) [hit, best] = [key, d];
    }
    return hit;
  }

  hitAt(x, y) {
    const node = this.nodeAt(x, y);
    if (node) return { node: node.key };
    const edge = this.edgeAt(x, y);
    return edge ? { edge } : null;
  }

  down = (event) => {
    if (event.button !== 0) return;
    this.canvas.setPointerCapture(event.pointerId);
    const node = this.nodeAt(event.offsetX, event.offsetY);
    this.drag = { node, x: event.offsetX, y: event.offsetY, view: { ...this.model.view }, moved: false,
      pick: event.shiftKey || event.ctrlKey || event.metaKey };
  };

  move = (event) => {
    const x = event.offsetX, y = event.offsetY, d = this.drag;
    if (d) {
      if (!d.moved && Math.hypot(x - d.x, y - d.y) < 4) return;
      d.moved = true;
      this.autoFit = false;
      this.options.onHover?.(null, event);
      if (d.node) { // dragging pins the node where it is let go
        [d.node.fx, d.node.fy] = this.world(x, y);
        if (this.reduced) {
          [d.node.x, d.node.y] = [d.node.fx, d.node.fy];
        } else {
          this.alphaTarget = 0.25;
          this.alpha = Math.max(this.alpha, 0.25);
        }
      } else {
        this.model.view.x = d.view.x + x - d.x;
        this.model.view.y = d.view.y + y - d.y;
        this.canvas.classList.add("dragging");
      }
      this.draw();
      return;
    }
    const hit = this.hitAt(x, y);
    if ((hit?.node ?? hit?.edge) !== (this.hover?.node ?? this.hover?.edge)) {
      this.hover = hit;
      this.canvas.classList.toggle("pointing", !!hit);
      this.draw();
    }
    this.options.onHover?.(hit, event);
  };

  up = () => {
    const d = this.drag;
    this.drag = null;
    this.alphaTarget = 0;
    this.canvas.classList.remove("dragging");
    if (!d || d.moved) return;
    const hit = this.hitAt(d.x, d.y);
    if (d.pick && hit?.node && this.options.onPick) this.options.onPick(hit.node);
    else this.select(hit);
  };

  leave = () => {
    if (this.drag) return;
    this.hover = null;
    this.options.onHover?.(null);
    this.draw();
  };

  dblclick = (event) => {
    const node = this.nodeAt(event.offsetX, event.offsetY);
    if (node) this.options.onExpand?.(node.key);
  };

  wheel = (event) => {
    event.preventDefault();
    this.zoomAt(event.offsetX, event.offsetY, Math.exp(-event.deltaY * (event.deltaMode ? 0.05 : 0.0015)));
  };

  key = (event) => {
    const pan = (dx, dy) => {
      this.model.view.x += dx;
      this.model.view.y += dy;
      this.draw();
    };
    const actions = {
      "+": () => this.zoom(1.25), "=": () => this.zoom(1.25), "-": () => this.zoom(0.8), "0": () => this.fit(),
      ArrowLeft: () => pan(60, 0), ArrowRight: () => pan(-60, 0), ArrowUp: () => pan(0, 60), ArrowDown: () => pan(0, -60),
      Escape: () => this.select(null),
      Enter: () => this.selected?.node && this.options.onExpand?.(this.selected.node),
    };
    if (actions[event.key]) {
      event.preventDefault();
      actions[event.key]();
    }
  };
}
