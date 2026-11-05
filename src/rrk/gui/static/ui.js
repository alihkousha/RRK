// What every page uses: building the DOM, the API, the two languages, and the
// small components (entity links, notice rows, pickers, dialogs).
import { STRINGS, TYPE_EN } from "./i18n.js";

export const state = { lang: "fa", types: new Map() }; // types: code -> the site's Persian label

// Language and formatting --------------------------------------------------

export function t(key, vars) {
  const text = STRINGS[state.lang][key] ?? STRINGS.en[key] ?? key;
  return vars ? text.replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? "") : text;
}

/** t(key), or null when there is no such string. */
export const maybe = (key, vars) => (key in STRINGS.en ? t(key, vars) : null);

export function storeTypes(types) {
  for (const type of types) state.types.set(type.code, normalize(type.label));
}

const locale = () => (state.lang === "fa" ? "fa-IR" : "en-US");
export const fmt = (n) => (n == null ? "–" : new Intl.NumberFormat(locale()).format(n));
/** Persian digits in Persian, for dates and other numbers that are read, not copied. */
export const digits = (text) =>
  text == null ? "" : state.lang === "fa" ? String(text).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[d]) : String(text);
export const typeLabel = (code) =>
  state.lang === "en" ? TYPE_EN[code] ?? state.types.get(code) ?? code : state.types.get(code) ?? TYPE_EN[code] ?? code;
export const roleLabel = (role) => t(`role.${role}`);
export const nodeName = (node) => node.name || node.id;
export const shorten = (text, most = 40) => (text.length > most ? text.slice(0, most - 1) + "…" : text);
export const period = (first, last) => (first ? (first === last ? digits(first) : `${digits(first)} – ${digits(last)}`) : "–");

export function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const [hh, mm, ss] = [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60];
  return hh ? t("hm", { h: fmt(hh), m: fmt(mm) }) : mm ? t("ms", { m: fmt(mm), s: fmt(ss) }) : t("sec", { s: fmt(ss) });
}

export const dateTime = (iso) =>
  new Date(iso).toLocaleString(state.lang === "fa" ? "fa-IR" : "en-GB", { dateStyle: "medium", timeStyle: "short" });

// The gazette mixes Arabic and Persian letters: compare texts the way the server does (parsing/entities.py).
const LETTERS = { "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "أ": "ا", "إ": "ا",
  "‌": " ", "‎": " ", "‏": " ", " ": " " };
export const normalize = (text) =>
  text.replace(/[يىكةۀأإ‌‎‏ ۰-۹٠-٩]/g, (ch) => {
    const code = ch.charCodeAt(0);
    return LETTERS[ch] ?? String(code - (code >= 0x06f0 ? 0x06f0 : 0x0660));
  });
export const words = (text) => normalize(text).match(/[\p{L}\p{N}_]+/gu) ?? [];

// DOM --------------------------------------------------------------------

const PROPS = new Set(["value", "checked", "indeterminate", "selected"]);

function fill(el, attrs, children, setAttribute) {
  if (attrs == null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs)) {
    children.unshift(attrs);
    attrs = {};
  }
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.classList.add(...String(v).split(" ").filter(Boolean));
    else if (PROPS.has(k)) el[k] = v;
    else setAttribute(k, v === true ? "" : String(v));
  }
  el.append(...nodes(children));
  return el;
}

/** Children as h() takes them: nested lists flattened, null and false left out, the rest as text. */
const nodes = (children) => children.flat(Infinity).filter((c) => c != null && c !== false).map((c) => (c instanceof Node ? c : String(c)));
export const setChildren = (el, ...children) => el.replaceChildren(...nodes(children));

/** h("div.card.wide", {attrs}, ...children): attributes and children are both optional. */
export function h(spec, attrs, ...children) {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");
  return fill(el, attrs, children, (k, v) => el.setAttribute(k, v));
}

export function svg(spec, attrs, ...children) {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  if (classes.length) el.setAttribute("class", classes.join(" "));
  return fill(el, attrs, children, (k, v) => el.setAttribute(k, v));
}

const ICONS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  home: '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/>',
  file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  network: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/><circle cx="10" cy="18" r="2.5"/><path d="M8.4 6.4l7.2 1.2M7 8.3l2.2 7.3M16.5 10l-4.7 6.3"/>',
  bulb: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  play: '<path d="M7 4v16l13-8z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 9.5v4.5M12 17v.5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  route: '<circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><path d="M8.5 19H16a3 3 0 0 0 0-6H8a3 3 0 0 1 0-6h7.5"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
  tag: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8" r="1.2"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  open: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  person: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  building: '<path d="M4 21V4h10v17M14 9h6v12M2 21h20M8 8h2M8 12h2M8 16h2"/>',
  pin: '<path d="M12 21s7-6.5 7-12a7 7 0 0 0-14 0c0 5.5 7 12 7 12z"/><circle cx="12" cy="9" r="2.5"/>',
  minus: '<path d="M5 12h14"/>',
  expand: '<circle cx="12" cy="12" r="3"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/>',
  filter: '<path d="M4 5h16l-6 7.5V18l-4 2v-7.5z"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
};

export function icon(name) {
  const el = svg("svg.icon", { viewBox: "0 0 24 24", "aria-hidden": "true" });
  el.innerHTML = ICONS[name]; // our own constant markup, never data
  return el;
}

// The API ----------------------------------------------------------------

export async function api(path, body) {
  let response;
  try {
    response = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  } catch {
    throw new Error(t("errOffline"));
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || response.statusText);
    error.status = response.status;
    throw error;
  }
  return data;
}

/** A query string from *params*, leaving out the empty ones. */
export function query(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v != null && v !== "" && !(Array.isArray(v) && !v.length)) q.set(k, v);
  const s = q.toString();
  return s ? `?${s}` : "";
}

// Components ---------------------------------------------------------------

export const kindMark = (kind, large) =>
  h(`span.kind.${kind}${large ? ".lg" : ""}`, { role: "img", "aria-label": t(`kind.${kind}`), title: t(`kind.${kind}`) });

export function entityLink(node, { id = false } = {}) {
  return h("a.entity", { href: `#/e/${node.key}` }, kindMark(node.kind),
    h("span.name", { dir: "auto" }, node.name || h("span.id", node.id)),
    id && node.name ? h("span.id.muted.small", node.id) : null);
}

/** Role chips; *roles* is a list of role keys, or of [key, count]. */
export function roleChips(roles, { empty = true } = {}) {
  const items = roles.map((r) => (Array.isArray(r) ? r : [r]));
  if (!items.length) return empty ? h("span.chip", t("role.named")) : null;
  return h("div.chips", items.map(([role, n]) => h("span.chip", roleLabel(role), n > 1 ? h("span.muted", `×${fmt(n)}`) : null)));
}

const STATES = { running: ["live", null], stopping: ["warn", "clock"], finished: ["good", "check"],
  failed: ["bad", "alert"], stopped: ["warn", "stop"] };

export function runState(name) {
  const [tone, glyph] = STATES[name];
  return h(`span.status.${tone}`, glyph ? icon(glyph) : h("span.pulse"), t(`state.${name}`));
}

export const empty = (glyph, ...content) => h("div.empty", { role: "status" }, icon(glyph), ...content);

export const skeleton = (lines = 4) =>
  h("div", { "aria-busy": "true", "aria-label": t("loading") },
    Array.from({ length: lines }, (_, i) => h("div.skeleton.sk-line", { style: `width: ${92 - ((i * 23) % 40)}%` })));

export const stat = (label, value, sub, kind, href) =>
  h(href ? "a.stat" : "div.stat", { href }, h("div.stat-label", kind ? kindMark(kind) : null, label), h("div.stat-value.num", value),
    sub ? h("div.stat-sub", sub) : null);

export function card(title, hint, ...content) {
  return h("section.card", title && h("h2", title), hint && h("p", hint), ...content);
}

let tip;
/** The one hover tooltip, next to the pointer; no *content* hides it. */
export function tooltip(event, content) {
  tip ??= document.body.appendChild(h("div.tooltip", { role: "tooltip", hidden: true }));
  if (!content || !event) return void (tip.hidden = true);
  setChildren(tip, content);
  tip.hidden = false;
  const box = tip.getBoundingClientRect();
  tip.style.left = `${Math.max(8, Math.min(event.clientX + 14, innerWidth - box.width - 8))}px`;
  tip.style.top = `${event.clientY - box.height - 12 < 8 ? event.clientY + 18 : event.clientY - box.height - 12}px`;
}

let toastTimer;
export function toast(text) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 5000);
}

export function confirmDialog({ title, text, ok, cancel, danger }) {
  const dialog = document.getElementById("dialog");
  return new Promise((resolve) => {
    const done = (value) => {
      dialog.close();
      resolve(value);
    };
    dialog.replaceChildren(
      h("h2", title), h("p", text),
      h("div.actions",
        h("button.btn", { type: "button", onclick: () => done(false) }, cancel),
        h(`button.btn${danger ? ".danger" : ".primary"}`, { type: "button", onclick: () => done(true) }, ok)));
    dialog.onclose = () => resolve(false);
    dialog.showModal();
    dialog.querySelector(".actions .btn").focus(); // the safe choice first
  });
}

/** A copy button; *text* may be a function. An empty *label* leaves only the icon. */
export function copyButton(text, { label = t("copy") } = {}) {
  const caption = label ? h("span", label) : null;
  const button = h("button.btn.ghost.sm", { type: "button", title: t("copy"), "aria-label": label ? null : t("copy") },
    icon("copy"), caption);
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(typeof text === "function" ? text() : text);
    } catch {
      return toast(t("errGeneric", { e: "clipboard" }));
    }
    button.firstChild.replaceWith(icon("check"));
    if (caption) caption.textContent = t("copied");
    setTimeout(() => {
      button.firstChild.replaceWith(icon("copy"));
      if (caption) caption.textContent = label;
    }, 1500);
  });
  return button;
}

export function downloadCsv(name, rows) {
  const cell = (v) => {
    const text = v == null ? "" : String(v).replace(/^[=+\-@\t\r]/, "'$&"); // untrusted text, never a spreadsheet formula
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const blob = new Blob(["﻿" + rows.map((r) => r.map(cell).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/** *text* with the words of *q* marked. */
export function highlight(text, q) {
  const wanted = words(q || "").filter((w) => w.length > 1);
  if (!text || !wanted.length) return text ?? "";
  const norm = normalize(text), marks = [];
  for (const w of wanted) for (let at = norm.indexOf(w); at >= 0; at = norm.indexOf(w, at + w.length)) marks.push([at, at + w.length]);
  marks.sort((a, b) => a[0] - b[0]);
  const out = [];
  let pos = 0;
  for (const [a, b] of marks) {
    if (a < pos) continue;
    out.push(text.slice(pos, a), h("mark", text.slice(a, b)));
    pos = b;
  }
  out.push(text.slice(pos));
  return out;
}

/** A notice in a list: date, type, title, and what it says about the entity or the query. */
export function adItem(ad, { hl, extra } = {}) {
  return h("a.list-item", { href: `#/ad/${ad.code}${query({ hl })}` },
    h("div.grow",
      h("div.row.small.muted", h("span.num", digits(ad.day) || "–"), h("span.chip", typeLabel(ad.type))),
      h("div", { dir: "auto" }, highlight(ad.title, hl)),
      ad.snippet ? h("div.small.soft", { dir: "auto" }, highlight(ad.snippet, hl)) : null,
      extra ?? null));
}

/** A search-as-you-type list under *input* for people, companies and addresses. */
export function attachPicker(input, { onPick, footer, kinds } = {}) {
  const list = h("div.picker-list", { role: "listbox", id: `picker-${Math.random().toString(36).slice(2)}`, hidden: true });
  input.after(list);
  Object.entries({ role: "combobox", "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": list.id })
    .forEach(([k, v]) => input.setAttribute(k, v));
  let items = [], active = -1, timer, seq = 0;

  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  };
  const render = () => {
    list.replaceChildren(...items.map((item, i) => h("button", {
      type: "button", role: "option", id: `${list.id}-${i}`, "aria-selected": String(i === active),
      onmousedown: (e) => e.preventDefault(), onclick: () => pick(i),
    }, item.node
      ? [kindMark(item.node.kind), h("span.grow", { dir: "auto" }, nodeName(item.node)), h("span.id.muted.small", item.node.id)]
      : [icon(item.icon || "search"), h("span.grow", item.label)])));
    list.hidden = !items.length;
    input.setAttribute("aria-expanded", String(!list.hidden));
    if (active >= 0) input.setAttribute("aria-activedescendant", `${list.id}-${active}`);
  };
  const pick = (i) => {
    const item = items[i];
    close();
    if (item.node) onPick?.(item.node);
    else item.action();
  };
  const load = async () => {
    const q = input.value.trim(), mine = ++seq;
    if (!q) return close();
    try {
      const found = await api(`/api/search${query({ q, ads: 0 })}`); // the picker shows people and companies only
      if (mine !== seq) return;
      items = found.entities.filter((n) => !kinds || kinds.includes(n.kind)).slice(0, 8).map((node) => ({ node }));
      items.push(...(footer?.(q) ?? []));
      active = -1;
      render();
    } catch (error) {
      toast(error.message);
    }
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(load, 180);
  });
  input.addEventListener("keydown", (event) => {
    if (list.hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      active = event.key === "ArrowDown" ? (active + 1) % items.length : active <= 0 ? items.length - 1 : active - 1;
      render();
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      pick(active);
    } else if (event.key === "Escape") {
      close();
    }
  });
  input.addEventListener("blur", () => setTimeout(close, 150));
  return { close };
}
