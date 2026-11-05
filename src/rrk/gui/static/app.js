// The GUI's shell (navigation, search box, live status, languages, routing)
// and its reading pages: overview, search, profiles, the lists (notices, people,
// companies, addresses, links) and leads.
import { ROLE_GROUPS } from "./graph.js";
import { networkView } from "./network.js";
import { runsView } from "./runs.js";
import {
  adItem, api, attachPicker, card, copyButton, digits, downloadCsv, duration, empty, entityLink, fmt, h, highlight,
  icon, kindMark, maybe, nodeName, normalize, period, query, roleChips, roleLabel, setChildren, shorten, skeleton, stat,
  state, storeTypes, svg, t, toast, tooltip, typeLabel, words,
} from "./ui.js";

const main = document.getElementById("main");
const nav = document.getElementById("nav");
const searchInput = document.getElementById("q");
const status = { run: null, index: null };
const go = (hash) => (location.hash = hash);
let cleanups = [];
let routeSeq = 0;

// Shell ----------------------------------------------------------------------

const NAV = [
  ["navExplore", [["/", "home", "navHome"], ["/network", "network", "navNetwork"], ["/leads", "bulb", "navLeads"]]],
  ["navLists", [["/notices", "file", "navNotices"], ["/people", "person", "navPeople"], ["/companies", "building", "navCompanies"],
    ["/addresses", "pin", "navAddresses"], ["/links", "link", "navLinks"]]],
  ["navOperate", [["/runs", "activity", "navRuns"]]],
];

const currentPath = () => (location.hash.slice(1) || "/").split("?")[0];

function renderNav() {
  const path = currentPath();
  const current = (href) => (href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`)
    || (href === "/notices" && path.startsWith("/ad/")));
  nav.setAttribute("aria-label", t("brand"));
  nav.replaceChildren(
    ...NAV.map(([title, items]) => h("div.nav-group", h("div.nav-title", t(title)),
      items.map(([href, glyph, label]) => h("a.nav-item", { href: `#${href}`, "aria-current": current(href) ? "page" : null },
        icon(glyph), h("span", t(label)),
        href === "/runs" && status.run ? h("span.pulse.badge", { title: t("state.running") }) : null)))),
    h("p.nav-foot", t("navFoot")));
}

function renderStatus() {
  const { index, run } = status, pills = [];
  if (index?.total && index.read < index.total) {
    const p = Math.floor((100 * index.read) / index.total);
    pills.push(h("span.status-pill", { title: t("indexNote", { read: fmt(index.read), total: fmt(index.total) }) },
      h("span.ring", { style: `--p: ${p}` }), t("indexReading", { p: fmt(p) })));
  }
  if (run) {
    pills.push(h("a.status-pill", { href: `#/runs/${run.id}` }, h("span.pulse"),
      t("pillRun", { t: duration((Date.now() - new Date(run.started)) / 1000) })));
  }
  document.getElementById("status").replaceChildren(...pills);
}

async function pollStatus() {
  try {
    if (!document.hidden) {
      const running = !!status.run;
      Object.assign(status, await api("/api/status"));
      renderStatus();
      if (running !== !!status.run) renderNav();
    }
  } catch {
    // the GUI server may be restarting: the next poll tells
  }
  setTimeout(pollStatus, 5000);
}

function applyLang(lang) {
  state.lang = lang;
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === "fa" ? "rtl" : "ltr";
  for (const el of document.querySelectorAll("[data-t]")) el.textContent = t(el.dataset.t);
  searchInput.placeholder = t("searchPlaceholder");
  searchInput.setAttribute("aria-label", t("searchPlaceholder"));
  document.querySelector(".skip").textContent = t("skip");
  document.querySelector(".brand").setAttribute("aria-label", t("brand"));
  for (const b of document.querySelectorAll("#lang button")) b.setAttribute("aria-pressed", String(b.dataset.lang === lang));
}

const ROUTES = [
  [/^\/$/, overview],
  [/^\/search$/, search],
  [/^\/e\/([pca]:[^/]+)$/, entity],
  [/^\/ad\/(\w+)$/, notice],
  [/^\/notices$/, (page) => listPage(page, NOTICES)],
  [/^\/people$/, (page) => listPage(page, nodeList("person"))],
  [/^\/companies$/, (page) => listPage(page, nodeList("company"))],
  [/^\/addresses$/, (page) => listPage(page, nodeList("address"))],
  [/^\/links$/, (page) => listPage(page, LINKS)],
  [/^\/network$/, networkView],
  [/^\/leads$/, leads],
  [/^\/runs(?:\/(\d{8}-\d{6}))?$/, runsView],
];

async function route() {
  for (const fn of cleanups.splice(0)) fn();
  tooltip(null);
  const [path, search = ""] = (location.hash.slice(1) || "/").split("?");
  const mine = ++routeSeq;
  const found = ROUTES.find(([pattern]) => pattern.test(path));
  const arg = found && path.match(found[0])[1];
  const page = {
    main, params: Object.fromEntries(new URLSearchParams(search)), arg: arg ? decodeURIComponent(arg) : null,
    alive: () => mine === routeSeq, onLeave: (fn) => cleanups.push(fn), refresh: route,
  };
  main.classList.remove("full");
  if (path !== "/search") searchInput.value = "";
  renderNav();
  try {
    await (found ? found[1] : notFound)(page);
  } catch (error) {
    if (page.alive()) main.replaceChildren(h("div.page", empty("alert", h("p", t("errGeneric", { e: error.message })))));
  }
}

function notFound() {
  document.title = t("brand");
  main.replaceChildren(h("div.page", empty("search", h("p", t("notFound")), h("a.btn", { href: "#/" }, t("navHome")))));
}

const title = (text) => (document.title = `${text} · ${t("brand")}`);
const pageHead = (heading, lede, ...actions) =>
  h("header.page-head", h("div", h("h1", heading), lede ? h("p", lede) : null), actions.length ? h("div.actions", actions) : null);
const sum = (rows, key) => rows.reduce((total, row) => total + (row[key] ?? 0), 0);

// Overview -------------------------------------------------------------------

async function overview(page) {
  document.title = t("brand");
  const input = h("input.input", { type: "search", placeholder: t("searchPlaceholder"), "aria-label": t("homeSearch"), autocomplete: "off", spellcheck: "false" });
  const tryRow = h("div.try");
  const body = h("div.stack", skeleton(8));
  main.replaceChildren(h("div.page",
    h("section.hero",
      h("h1", t("homeTitle")), h("p", t("homeLede")),
      h("form", { role: "search", onsubmit: (e) => { e.preventDefault(); if (input.value.trim()) go(`#/search${query({ q: input.value.trim() })}`); } },
        h("div.picker.grow", input), h("button.btn.primary", { type: "submit" }, icon("search"), t("homeSearch"))),
      tryRow),
    body));
  attachPicker(input, { onPick: (node) => go(`#/e/${node.key}`) });
  input.focus();

  const [data, leadsData] = await Promise.all([api("/api/overview"), api("/api/leads").catch(() => null)]);
  if (!page.alive()) return;
  storeTypes(data.types);
  const tries = [leadsData?.hubs?.[0], leadsData?.seats?.[0], leadsData?.busiest?.[0], leadsData?.addresses?.[0]]
    .filter((n, i, all) => n && all.findIndex((m) => m?.key === n.key) === i);
  if (tries.length) {
    tryRow.replaceChildren(h("span", t("homeTry")), ...tries.map((node) =>
      h("button", { type: "button", onclick: () => go(`#/e/${node.key}`) }, kindMark(node.kind), h("span", { dir: "auto" }, shorten(nodeName(node), 34)))));
  }

  const { types, entities, index } = data;
  setChildren(body,
    index.read < index.total ? h("p.note", h("span.ring", { style: `--p: ${Math.floor((100 * index.read) / index.total)}` }),
      h("span", t("indexNote", { read: fmt(index.read), total: fmt(index.total) }))) : null,
    h("div.stats",
      stat(t("statNotices"), fmt(sum(types, "ads")), t("statText", { n: fmt(sum(types, "details")) }), null, "#/notices"),
      stat(t("statPeople"), fmt(entities.person ?? 0),
        data.unnumbered ? t("statUncoded", { n: fmt(data.unnumbered) }) : t("statById"), "person", "#/people"),
      stat(t("statCompanies"), fmt(entities.company ?? 0), t("statById"), "company", "#/companies"),
      stat(t("statAddresses"), fmt(entities.address ?? 0), t("statById"), "address", "#/addresses"),
      stat(t("statLinks"), fmt(data.links), t("statLinksSub"), null, "#/links")),
    h("div.cols",
      leadsData ? startHere(leadsData) : null,
      card(t("yearsTitle"), t("yearsLede"), yearChart(data.years, types, page))),
    coverage(data));
}

function leadRow(kindTitle, who, metric, href) {
  return h("div.list-item",
    h("div.grow", h("div.small.muted", kindTitle), h("div.row", who)),
    h("span.metric.small", metric),
    h("a.btn.sm.ghost.icon-only", { href, title: t("openNetwork"), "aria-label": t("openNetwork") }, icon("network")));
}

function startHere({ hubs = [], groups = [], seats = [], addresses = [], clusters = [] }) {
  const rows = [
    hubs[0] && leadRow(t("leadsHubs"), entityLink(hubs[0]), t("nCompanies", { n: fmt(hubs[0].companies) }), `#/network?add=${hubs[0].key}`),
    groups[0] && leadRow(t("leadsGroups"), groups[0].members.slice(0, 2).map((n) => entityLink(n)),
      t("nTogether", { n: fmt(groups[0].companies) }), `#/network?keys=${groups[0].keys.join(",")}`),
    seats[0] && leadRow(t("leadsSeats"), entityLink(seats[0]), t("nCompanies", { n: fmt(seats[0].companies) }), `#/network?add=${seats[0].key}`),
    addresses[0] && leadRow(t("leadsAddresses"), entityLink(addresses[0]), t("nCompanies", { n: fmt(addresses[0].companies) }),
      `#/network?add=${addresses[0].key}`),
    clusters[0] && leadRow(t("leadsClusters"), clusters[0].top.slice(0, 2).map((n) => entityLink(n)),
      t("nCompanies", { n: fmt(clusters[0].count.company) }), `#/network?keys=${clusters[0].keys.join(",")}`),
  ].filter(Boolean);
  if (!rows.length) return card(t("startHere"), null, empty("bulb", h("p", t("leadsEmpty"))));
  return card(t("startHere"), null, h("div.list", rows), h("a.btn.sm.more", { href: "#/leads" }, icon("bulb"), t("seeLeads")));
}

function coverage({ types, placeholder_dates: placeholder }) {
  const capped = types.filter((type) => type.capped);
  const listing = (type) => !type.ads ? h("span.muted", t("covNone"))
    : type.capped ? h("span.status.warn", icon("alert"), t("covCapped")) : h("span.status.good", icon("check"), t("covFull"));
  const href = (type) => `#/notices?types=${type.code}`;
  return h("section.card",
    h("div.card-head", h("h2", t("covTitle")),
      capped.length ? h("a.btn.sm", { href: `#/runs?types=${capped.map((type) => type.code).join(",")}` }, icon("play"), t("covPlan")) : null),
    h("p.soft.small", t("covLede")),
    h("div.table-wrap", h("table",
      h("thead", h("tr", h("th", t("covType")), h("th.num", t("covCount")), h("th.num", t("covText")), h("th.num", t("covSpan")), h("th", t("covState")))),
      h("tbody", types.map((type) => h(`tr${type.ads ? ".link" : ""}`, { onclick: (e) => type.ads && !e.target.closest("a") && go(href(type)) },
        h("td", type.ads ? h("a", { href: href(type) }, typeLabel(type.code)) : h("span.muted", typeLabel(type.code))),
        h("td.num", fmt(type.ads)),
        h("td.num.nowrap", h("span.meter", { title: `${fmt(Math.round((100 * type.details) / (type.ads || 1)))}%` },
          h("i", { style: `width: ${(100 * type.details) / (type.ads || 1)}%` })), " ", fmt(type.details)),
        h("td.num.nowrap", type.first ? `${digits(type.first.slice(0, 4))}–${digits(type.last.slice(0, 4))}` : "–"),
        h("td", listing(type))))))),
    h("div.stack",
      capped.length ? h("p.note.warn", icon("alert"), h("span", t("covSamples", { n: fmt(capped.length) }))) : null,
      h("p.note", icon("info"), h("span", t("qualityDates", { n: fmt(placeholder) }))),
      h("p.note", icon("info"), h("span", t("qualityNames")))));
}

/** [label, count, tooltip label] per year; the few stray early years (typos) share one bar. */
function yearData(rows, type) {
  const counts = new Map();
  for (const [year, adType, n] of rows) {
    if (/^\d{4}$/.test(year ?? "") && (!type || adType === type)) counts.set(+year, (counts.get(+year) ?? 0) + n);
  }
  if (!counts.size) return [];
  const years = [...counts.keys()].sort((a, b) => a - b), total = [...counts.values()].reduce((a, b) => a + b);
  let start = years[0], early = 0;
  for (const year of years) {
    if (early + counts.get(year) >= total * 0.005) {
      start = year;
      break;
    }
    early += counts.get(year);
  }
  const data = early ? [[`<${digits(start)}`, early, t("yearsBefore", { y: digits(start) })]] : [];
  for (let year = start; year <= years.at(-1); year++) data.push([digits(year), counts.get(year) ?? 0, digits(year)]);
  return data;
}

const niceStep = (x) => {
  const p = 10 ** Math.floor(Math.log10(x || 1));
  return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= x);
};
// Persian compact numbers ("۸۰ هزار") read backwards on a left-to-right axis: there, the full number.
const tick = (n) => (state.lang === "fa" ? fmt(n) : new Intl.NumberFormat("en-US", { notation: "compact" }).format(n));
const roundedTop = (x, y, w, height) => {
  const r = Math.min(4, w / 2, height);
  return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + height}Z`;
};

function yearChart(rows, types, page) {
  let type = "", asTable = false;
  const box = h("div.chart-box");
  const select = h("select.input", { "aria-label": t("covType"), onchange: () => { type = select.value; draw(); } },
    h("option", { value: "" }, t("yearsAll")),
    types.filter((ty) => ty.ads).map((ty) => h("option", { value: ty.code }, typeLabel(ty.code))));
  const views = [["asChart", false], ["asTable", true]].map(([label, table]) =>
    h("button", { type: "button", "aria-pressed": String(table === asTable), onclick: () => { asTable = table; draw(); } }, t(label)));

  function draw() {
    views.forEach((b, i) => b.setAttribute("aria-pressed", String(!!i === asTable)));
    const data = yearData(rows, type);
    if (!data.length) return box.replaceChildren(empty("info", h("p", t("noneFound"))));
    if (asTable) {
      return box.replaceChildren(h("div.table-wrap", h("table",
        h("thead", h("tr", h("th", t("year")), h("th.num", t("covCount")))),
        h("tbody", data.map(([, n, label]) => h("tr", h("td.num", label), h("td.num", fmt(n))))))));
    }
    const max = Math.max(1, ...data.map((d) => d[1])), step = niceStep(max / 4), top = Math.ceil(max / step) * step;
    const W = Math.max(280, box.clientWidth || 600), H = 230, M = { top: 22, right: 6, bottom: 24, left: 16 + 7 * tick(top).length };
    const iw = W - M.left - M.right, ih = H - M.top - M.bottom;
    const y = (v) => M.top + ih - (v / top) * ih, bw = iw / data.length;
    const peak = data.reduce((best, d, i) => (d[1] > data[best][1] ? i : best), 0);
    const chart = svg("svg.chart", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
      "aria-label": t("yearsSummary", { a: data[0][2], b: data.at(-1)[2], max: data[peak][2] }) });
    for (let v = 0; v <= top; v += step) {
      chart.append(svg(`line.${v ? "gridline" : "baseline"}`, { x1: M.left, x2: W - M.right, y1: y(v), y2: y(v) }),
        svg("text.tick", { x: M.left - 8, y: y(v) + 4, "text-anchor": "end" }, tick(v)));
    }
    const every = Math.ceil(data.length / Math.max(1, Math.floor(iw / 46)));
    data.forEach(([label, n, long], i) => {
      const x = M.left + i * bw;
      chart.append(svg("g.col", {
        onpointermove: (e) => tooltip(e, [h("strong", long), " · ", t("nNotices", { n: fmt(n) })]), onpointerleave: () => tooltip(null),
      }, svg("rect.hit", { x, y: M.top, width: bw, height: ih }),
      n ? svg("path.bar", { d: roundedTop(x + 1, y(n), Math.max(1, bw - 2), y(0) - y(n)) }) : null));
      if (i % every === 0) chart.append(svg("text.tick", { x: x + bw / 2, y: H - 6, "text-anchor": "middle" }, label));
      if (i === peak) chart.append(svg("text.value", { x: x + bw / 2, y: y(n) - 7, "text-anchor": "middle" }, fmt(n)));
    });
    box.replaceChildren(chart);
  }
  const resizer = new ResizeObserver(() => !asTable && draw());
  resizer.observe(box);
  page.onLeave(() => resizer.disconnect());
  return h("div.stack", h("div.row", select, h("div.seg", { role: "group", "aria-label": t("yearsTitle") }, views)), box);
}

// Search ---------------------------------------------------------------------

function entityRow(node, extra) {
  return h("div.list-item",
    h("div.grow", h("div.row", entityLink(node), h("span.id.muted.small", node.id || t("noCode"))), extra ?? null),
    h("span.small.soft.nowrap", t("nNotices", { n: fmt(node.ads) }), " · ", t("nLinks", { n: fmt(node.links) })));
}

async function search(page) {
  const q = page.params.q ?? "";
  title(q);
  searchInput.value = q;
  const entityBox = h("div", skeleton(5)), textBox = h("div", skeleton(5));
  main.replaceChildren(h("div.page",
    pageHead(t("searchTitle", { q }), t("searchHint")),
    h("section.card", h("h2", t("searchEntities")), entityBox),
    h("section.card", textBox)));
  const [found, text, inLeads] = await Promise.all([api(`/api/search${query({ q })}`), api(`/api/search/text${query({ q })}`),
    api(`/api/leads${query({ q })}`).catch(() => null)]);
  if (!page.alive()) return;

  let kind = null, order = "links";
  const counts = {};
  for (const n of found.entities) counts[n.kind] = (counts[n.kind] ?? 0) + 1;
  const filters = h("div.chips.filter-chips", { role: "group" });
  const sorts = h("div.seg", { role: "group", "aria-label": t("sortBy") });
  const list = h("div.list");
  const compare = { links: (a, b) => b.links - a.links || b.ads - a.ads, notices: (a, b) => b.ads - a.ads,
    name: (a, b) => nodeName(a).localeCompare(nodeName(b), state.lang) };
  const drawEntities = () => {
    filters.replaceChildren(...[null, "person", "company", "address"].filter((k) => !k || counts[k]).map((k) =>
      h("button.chip", { type: "button", "aria-pressed": String(kind === k), onclick: () => { kind = k; drawEntities(); } },
        k ? kindMark(k) : null, k ? t(`kinds.${k}`) : t("all"), h("span.muted", fmt(k ? counts[k] : found.entities.length)))));
    sorts.replaceChildren(...[["links", "colLinks"], ["notices", "colNotices"], ["name", "sortName"]].map(([key, label]) =>
      h("button", { type: "button", "aria-pressed": String(order === key), onclick: () => { order = key; drawEntities(); } }, t(label))));
    list.replaceChildren(...found.entities.filter((n) => !kind || n.kind === kind).sort(compare[order]).map((n) => entityRow(n)));
  };
  let last = entityBox.closest(".card");
  if (found.entities.length) {
    drawEntities();
    setChildren(entityBox, h("div.spread", filters, h("label.row.small.soft", t("sortBy"), sorts)), list,
      found.more ? h("p.small.muted", t("searchMore", { n: fmt(found.more) })) : null);
  } else {
    entityBox.replaceChildren(empty("search", h("p", t("searchNone"))));
  }
  if (found.ads.length) {
    last.after(last = h("section.card", h("h2", t("searchNumber")), h("div.list", found.ads.map((ad) => adItem(ad)))));
  }
  const hits = inLeads && leadsFound(inLeads, q);
  if (hits) last.after(hits);

  const shown = h("div.list");
  let limit = 30;
  const more = h("button.btn.sm.more", { type: "button", onclick: () => { limit += 60; drawText(); } }, t("showAll", { n: fmt(text.ads.length) }));
  const drawText = () => {
    shown.replaceChildren(...text.ads.slice(0, limit).map((ad) => adItem(ad, { hl: q })));
    more.hidden = limit >= text.ads.length;
  };
  drawText();
  setChildren(textBox,
    h("div.card-head", h("h2", t("searchText")), h("span.soft.small", t("nNotices", { n: fmt(text.total) }),
      text.total > text.ads.length ? [" · ", t("nShown", { n: fmt(text.ads.length) })] : null),
    text.total ? h("a.btn.sm", { href: `#/notices${query({ q })}` }, icon("file"), t("searchAll", { n: fmt(text.total) })) : null),
    text.total ? [shown, more] : empty("file", h("p", t("searchNoneText"))));
}

const LEAD_LISTS = [["hubs", "leadsHubs"], ["groups", "leadsGroups"], ["seats", "leadsSeats"], ["addresses", "leadsAddresses"],
  ["busiest", "leadsBusiest"], ["clusters", "leadsClusters"]];

/** The leads with someone matching the search (the first few of each list), or null. */
function leadsFound(found, q) {
  const rows = LEAD_LISTS.flatMap(([name, label]) => (found[name] ?? []).slice(0, 3).map((item) => h("div.list-item",
    h("span.rank", { title: t("leadsRank") }, fmt(item.rank)),
    h("div.grow", h("div.small.muted", t(label)), h("div.row", item.matched.map((n) => entityLink(n)))))));
  return rows.length ? h("section.card", h("div.card-head", h("h2", t("searchLeads")),
    h("a.btn.sm", { href: `#/leads${query({ q })}` }, icon("bulb"), t("seeLeads"))), h("div.list", rows)) : null;
}

// Entity profile -------------------------------------------------------------

const LINK_ORDER = { person: ["company"], company: ["person", "company", "address"], address: ["company"] };
// A profile's links grouped in the browser: the values of a link, their label, and whether the newest value goes first.
const LINK_GROUPS = {
  role: [(link) => (link.roles.length ? link.roles : ["named"]), roleLabel],
  status: [(link) => [link.ended ? "ended" : "current"], (v) => t(`status.${v}`)],
  first: [(link) => [link.first?.slice(0, 4) ?? ""], (v) => digits(v) || "–", true],
};

/** Under *row*, the notices behind the link between *a* and *b*; *button* shows and hides them. */
async function evidence(a, b, row, button, colspan) {
  const open = button.getAttribute("aria-expanded") === "true";
  button.setAttribute("aria-expanded", String(!open));
  if (open) return row.nextElementSibling?.classList.contains("evidence") && row.nextElementSibling.remove();
  const cell = h("td", { colspan }, skeleton(2));
  row.after(h("tr.evidence", cell));
  try {
    const found = await api(`/api/link${query({ a: a.key, b: b.key })}`);
    cell.replaceChildren(h("div.list", found.ads.map((ad) => adItem(ad, { hl: b.name || a.name, extra: roleChips(ad.roles, { empty: false }) }))));
  } catch (error) {
    cell.replaceChildren(h("p.small", t("errGeneric", { e: error.message })));
  }
}

/** *items* under each value *valuesOf* gives them (an item with several under each), the biggest group first or the newest value. */
function groupItems(items, valuesOf, newest) {
  const groups = new Map();
  for (const item of items) {
    for (const value of valuesOf(item)) {
      if (!groups.has(value)) groups.set(value, []);
      groups.get(value).push(item);
    }
  }
  return [...groups].sort(newest ? (a, b) => (a[0] < b[0] ? 1 : -1) : (a, b) => b[1].length - a[1].length);
}

/** A heading that sorts a table in the browser by *key* (none: it doesn't); clicked again, the order turns. */
function sortHead(label, key, cls, view, draw) {
  if (!key) return h(`th${cls}`, label);
  const active = view.sort === key;
  return h(`th${cls}`, { "aria-sort": active ? (view.desc ? "descending" : "ascending") : null },
    h("button.sort", { type: "button", onclick: () => { view.desc = active ? !view.desc : key !== "name"; view.sort = key; draw(); } },
      label, h("span.arrow", { "aria-hidden": "true" }, active ? (view.desc ? "▼" : "▲") : "")));
}

function linksCard(self, kind, links) {
  const hintKey = `links.${self.kind}.${kind}Hint`;
  const view = { q: "", role: "", group: "", sort: "ads", desc: true, all: false };
  const roles = [...new Set(links.flatMap((link) => link.roles))].sort();
  const head = h("tr"), body = h("tbody");
  const toggle = h("button.btn.sm.more", { type: "button", onclick: () => { view.all = !view.all; draw(); } });
  const watch = (el, key) => {
    el.addEventListener(el.matches("input") ? "input" : "change", () => { view[key] = el.value; view.all = false; draw(); });
    return el;
  };
  const tools = links.length > 6 ? h("div.row.table-tools",
    watch(h("input.input.sm", { type: "search", placeholder: t("filterPh"), "aria-label": t("filterPh") }), "q"),
    roles.length > 1 ? watch(h("select.input.sm", { "aria-label": t("fRole") }, h("option", { value: "" }, t("fAnyRole")),
      roles.map((r) => h("option", { value: r }, roleLabel(r)))), "role") : null,
    watch(h("select.input.sm", { "aria-label": t("groupBy") }, h("option", { value: "" }, t("gNone")),
      Object.keys(LINK_GROUPS).map((g) => h("option", { value: g }, t(`g.${g}`)))), "group")) : null;
  const row = (link) => {
    const button = h("button.btn.sm.ghost", { type: "button", "aria-expanded": "false", title: t("netWhy") }, icon("file"), fmt(link.ads));
    const tr = h("tr",
      h("td", entityLink(link.node, { id: true })),
      h("td", roleChips(link.roles)),
      h("td.nowrap.num.small", period(link.first, link.last), link.ended ? h("div.muted", t("ended")) : null),
      h("td.num", button));
    button.addEventListener("click", () => evidence(self, link.node, tr, button, 4));
    return tr;
  };
  const compare = { name: (a, b) => nodeName(a.node).localeCompare(nodeName(b.node), state.lang),
    first: (a, b) => (a.first ?? "").localeCompare(b.first ?? ""), ads: (a, b) => a.ads - b.ads };

  const draw = () => {
    const words = normalize(view.q).trim();
    const rows = links.filter((link) => (!view.role || link.roles.includes(view.role))
      && (!words || normalize(`${link.node.name ?? ""} ${link.node.id}`).includes(words)))
      .sort((a, b) => (view.desc ? -1 : 1) * compare[view.sort](a, b));
    head.replaceChildren(...[["name", t(`kind.${kind}`)], [null, t("entityRoles")], ["first", t("entityPeriod"), ".num"],
      ["ads", t("entityNotices"), ".num"]].map(([key, label, cls = ""]) => sortHead(label, key, cls, view, draw)));
    if (!rows.length) {
      body.replaceChildren(h("tr", h("td.muted", { colspan: 4 }, t("noneFound"))));
    } else if (view.group) {
      const [valuesOf, show, newest] = LINK_GROUPS[view.group];
      body.replaceChildren(...groupItems(rows, valuesOf, newest).flatMap(([value, items]) => [
        h("tr.group-head", h("th", { colspan: 4, scope: "colgroup" }, show(value), h("span.muted", ` · ${fmt(items.length)}`))),
        ...items.map(row)]));
    } else {
      body.replaceChildren(...rows.slice(0, view.all ? rows.length : 12).map(row));
    }
    toggle.hidden = !!view.group || rows.length <= 12;
    toggle.textContent = view.all ? t("showLess") : t("showAll", { n: fmt(rows.length) });
  };
  draw();
  return h("section.card",
    h("div.card-head", h("h2", t(`links.${self.kind}.${kind}`)), h("span.soft.small", fmt(links.length))),
    maybe(hintKey) ? h("p.soft.small", maybe(hintKey)) : null,
    tools,
    h("div.table-wrap", h("table", h("thead", head), body)),
    toggle);
}

function timeline(self, ads) {
  const view = { type: "", role: "", newest: false };
  const hl = self.name ?? self.id;
  const box = h("div.stack"), count = h("span.soft.small"), order = h("div.seg", { role: "group", "aria-label": t("order") });
  const types = [...new Set(ads.map((ad) => ad.type))], roles = [...new Set(ads.flatMap((ad) => ad.roles))].sort();
  const choose = (key, all, options) => {
    const el = h("select.input.sm", { "aria-label": all, onchange: () => { view[key] = el.value; draw(); } }, h("option", { value: "" }, all), options);
    return el;
  };
  const draw = () => {
    order.replaceChildren(...[[false, "oldestFirst"], [true, "newestFirst"]].map(([newest, label]) =>
      h("button", { type: "button", "aria-pressed": String(view.newest === newest), onclick: () => { view.newest = newest; draw(); } }, t(label))));
    const shown = ads.filter((ad) => (!view.type || ad.type === view.type) && (!view.role || ad.roles.includes(view.role)));
    if (view.newest) shown.reverse(); // they come oldest first
    count.textContent = t("nNotices", { n: fmt(shown.length) });
    if (!shown.length) return box.replaceChildren(h("p.muted", t("noneFound")));
    const years = new Map();
    for (const ad of shown) {
      const year = ad.day?.slice(0, 4) ?? "–";
      if (!years.has(year)) years.set(year, []);
      years.get(year).push(ad);
    }
    box.replaceChildren(...[...years].map(([year, items]) => h("div.timeline-year",
      h("div.year", digits(year)),
      h("div.list", items.map((ad) => adItem(ad, {
        hl, extra: h("div.chips", ad.subject ? h("span.chip.strong", t("aboutIt")) : null,
          ad.issues > 1 ? h("span.chip", t("nIssues", { n: fmt(ad.issues) })) : null,
          ad.roles.map((r) => h("span.chip", roleLabel(r))),
          ad.stake ? h("span.chip", t("stakeOf", { n: fmt(ad.stake) })) : null),
      }))))));
  };
  draw();
  return h("section.card",
    h("div.card-head", h("h2", t("timeline")), count),
    h("p.soft.small", t("timelineHint")),
    ads.length > 5 ? h("div.row.table-tools",
      types.length > 1 ? choose("type", t("fAllTypes"), types.map((type) => h("option", { value: type }, typeLabel(type)))) : null,
      roles.length > 1 ? choose("role", t("fAnyRole"), roles.map((r) => h("option", { value: r }, roleLabel(r)))) : null,
      order) : null,
    box);
}

/** A person named without a national code: known by that name within one company only. */
function uncodedNote(key, links) {
  const id = key.split("~")[1], company = links.find((link) => link.node.key === `c:${id}`)?.node;
  return h("p.note", icon("info"), h("span", t("uncodedNote", { company: company ? nodeName(company) : id })));
}

async function entity(page) {
  main.replaceChildren(h("div.page", skeleton(8)));
  let data;
  try {
    data = await api(`/api/entity${query({ key: page.arg })}`);
  } catch (error) {
    if (error.status !== 404) throw error;
  }
  if (!page.alive()) return;
  if (!data) {
    title(page.arg);
    return main.replaceChildren(h("div.page", empty("search", h("p", t("entityNotFound")), h("p.id", page.arg.slice(2)))));
  }
  const { node, names, ads, links, co, registry } = data;
  title(nodeName(node));
  const days = ads.map((ad) => ad.day).filter(Boolean);
  const byKind = new Map();
  for (const link of links) {
    if (!byKind.has(link.node.kind)) byKind.set(link.node.kind, []);
    byKind.get(link.node.kind).push(link);
  }
  const side = [
    co.length ? h("section.card", h("h2", t(`co.${node.kind}`)), h("p", t(`co.${node.kind}Hint`)),
      h("div.list", co.slice(0, 25).map((other) => h("div.list-item",
        h("div.grow", entityLink(other), h("div.small.muted", t("via"), " ",
          h("span", { dir: "auto" }, other.via.map((m) => shorten(nodeName(m), 30)).join(state.lang === "fa" ? "، " : ", ")))),
        h("span.metric.small", t("nShared", { n: fmt(other.shared) })))))) : null,
    names.length > 1 ? h("section.card", h("h2", t("names")),
      h("div.list", names.map(([name, n]) => h("div.list-item", h("span.grow", { dir: "auto" }, name), h("span.small.muted", fmt(n)))))) : null,
    registry.length ? h("section.card", h("h2", t("registry")), h("div.table-wrap", h("table",
      h("thead", h("tr", h("th", t("regNo")), h("th", t("office")), h("th.num", t("entityNotices")))),
      h("tbody", registry.map((r) => h("tr", h("td.id", r.registration_number ?? "–"), h("td", { dir: "auto" }, r.issuer ?? "–"), h("td.num", fmt(r.ads)))))))) : null,
  ];
  main.replaceChildren(h("div.page",
    h("header.page-head",
      h("div",
        h("div.eyebrow", kindMark(node.kind, true), t(`kind.${node.kind}`), h("span.muted", "·"),
          node.id ? [t(`id.${node.kind}`), h("span.id", node.id), copyButton(node.id, { label: "" })] : t("noCode")),
        h("h1", { dir: "auto" }, nodeName(node)),
        h("p", t("nNotices", { n: fmt(node.ads) }), " · ", t("nLinks", { n: fmt(node.links) }),
          days.length ? [" · ", period(days[0], days.at(-1))] : null),
        node.id ? null : uncodedNote(node.key, links)),
      h("div.actions", h("a.btn.primary", { href: `#/network?add=${node.key}` }, icon("network"), t("entityNetwork")),
        h("a.btn", { href: `#/notices${query({ named: node.key, named_name: nodeName(node) })}` }, t("entityNoticeList")))),
    h("div.cols.wide-start",
      h("div.stack",
        links.length ? LINK_ORDER[node.kind].filter((k) => byKind.has(k)).map((k) => linksCard(node, k, byKind.get(k)))
          : card(null, null, empty("network", h("p", t("noLinks")))),
        data.more ? h("p.small.muted", t("linksMore", { n: fmt(data.more) })) : null,
        timeline(node, ads)),
      h("div.stack", side))));
}

// Notice reader --------------------------------------------------------------

const TAG = /<\/?([a-zA-Z]+)[^>]*>/g;
/** A stored text as plain text: the gazette's tags as line breaks. */
const plain = (raw) => (raw ?? "").replace(TAG, "\n").replace(/\n{2,}/g, "\n").trim();

/** raw[a:b] as DOM: the gazette's <br>/<p> as line breaks, other tags dropped, *marks* highlighted. */
function textPiece(raw, a, b, marks) {
  const out = [], plain = (s, e) => {
    let pos = s;
    for (const [m0, m1] of marks) {
      if (m1 <= pos || m0 >= e) continue;
      if (m0 > pos) out.push(raw.slice(pos, m0));
      out.push(h("mark", raw.slice(Math.max(m0, pos), Math.min(m1, e))));
      pos = Math.min(m1, e);
    }
    if (pos < e) out.push(raw.slice(pos, e));
  };
  let pos = a;
  for (const tag of raw.slice(a, b).matchAll(TAG)) {
    plain(pos, a + tag.index);
    if (/^(br|p)$/i.test(tag[1]) && out.length) out.push(h("br"));
    pos = a + tag.index + tag[0].length;
  }
  plain(pos, b);
  return out;
}

function noticeText(raw, mentions, hl) {
  const norm = normalize(raw), marks = [];
  for (const w of words(hl ?? "").filter((w) => w.length > 1)) {
    for (let at = norm.indexOf(w); at >= 0; at = norm.indexOf(w, at + w.length)) marks.push([at, at + w.length]);
  }
  marks.sort((x, y) => x[0] - y[0]);
  const out = [];
  let pos = 0;
  for (const m of mentions.filter((m) => m.text_start != null)) {
    if (m.text_start < pos) continue;
    out.push(...textPiece(raw, pos, m.text_start, marks));
    const roles = (m.role ?? "").split(",").filter(Boolean).map(roleLabel).join("، ");
    const title = [t(`kind.${m.kind}`), m.label, roles, m.valid === 0 ? t("codeInvalid") : null].filter(Boolean).join(" · ");
    out.push(h(`a.m.${m.kind}`, { href: `#/e/${m.key}`, title },
      textPiece(raw, m.text_start, m.text_end, marks)));
    pos = m.text_end;
  }
  out.push(...textPiece(raw, pos, raw.length, marks));
  return out;
}

function namedCard(mentions) {
  const byKey = new Map();
  for (const m of mentions) {
    const seen = byKey.get(m.key) ?? { ...m, roles: new Set() };
    (m.role ?? "").split(",").filter(Boolean).forEach((r) => seen.roles.add(r));
    seen.subject ||= m.subject;
    seen.represents ||= m.represents;
    seen.stake ||= m.stake;
    byKey.set(m.key, seen);
  }
  const companyName = (id) => {
    const company = byKey.get(`c:${id}`);
    return company ? nodeName({ name: company.label, id }) : id;
  };
  const items = [...byKey.values()];
  return h("section.card", h("h2", t("adNamed")),
    items.length ? h("div.list", items.map((m) => h("div.list-item", h("div.grow",
      entityLink({ key: m.key, kind: m.kind, name: m.label, id: m.entity_id.startsWith("~") ? "" : m.entity_id }, { id: true }),
      h("div.chips",
        m.subject ? h("span.chip.strong", t("adSubject")) : null,
        m.kind === "person" && m.written == null ? h("span.chip", t("noCode")) : null,
        m.valid === 0 ? h("span.chip.warn", { title: t("codeInvalid") }, icon("alert"),
          m.written !== m.entity_id ? t("writtenAs", { n: m.written }) : t("codeInvalidShort")) : null,
        [...m.roles].map((r) => h("span.chip", roleLabel(r))),
        m.stake ? h("span.chip", t("stakeOf", { n: fmt(m.stake) })) : null,
        m.represents ? h("a.chip", { href: `#/e/c:${m.represents}` }, t("adRepresents", { name: shorten(companyName(m.represents), 40) })) : null)))))
      : empty("info", h("p", t("adNoneNamed"))));
}

async function notice(page) {
  const hl = page.params.hl;
  main.replaceChildren(h("div.page", skeleton(10)));
  let data;
  try {
    data = await api(`/api/ad${query({ code: page.arg })}`);
  } catch (error) {
    if (error.status !== 404) throw error;
  }
  if (!page.alive()) return;
  if (!data) return notFound();
  const { ad, mentions, siblings } = data;
  title(ad.title);
  const plainText = () => plain(ad.full_text ?? ad.summary);
  const citation = t("adCiteText", { no: digits(ad.newspaper_number ?? "–"), date: digits(ad.newspaper_date ?? "–"),
    page: digits(ad.page_number ?? "–"), code: ad.tracking_code });
  const company = /^\d{10,11}$/.test(ad.company_national_id ?? "")
    ? h("a.entity", { href: `#/e/c:${ad.company_national_id}` }, kindMark("company"), h("span.name", ad.company_name || ad.company_national_id))
    : ad.company_name;
  const facts = [
    ["adLetterDate", digits(ad.letter_date)], ["adIssue", digits(ad.newspaper_number)], ["adPublished", digits(ad.newspaper_date)],
    ["adPage", digits(ad.page_number)], ["adTracking", ad.tracking_code && h("span.row", h("span.id", ad.tracking_code), copyButton(ad.tracking_code, { label: "" }))],
    ["adIndicator", ad.indicator_number && h("span.id", ad.indicator_number)], ["adCompany", company],
    ["adEvents", eventList(ad.events)], ["adCapital", capitalText(ad.capital_from, ad.capital_to)],
    ["regNo", ad.registration_number && h("span.id", ad.registration_number)], ["adIssuer", ad.issuer],
    ["adTimes", digits(ad.publication_count)], ["adStatus", ad.status], ["adEdition", ad.newspaper_type],
  ].filter(([, value]) => value && value !== "-");

  main.replaceChildren(h("div.page",
    h("header.page-head", h("div",
      h("div.eyebrow", h("span.chip", typeLabel(ad.ad_type)), h("span.num", digits(ad.day))),
      h("h1", { dir: "auto" }, ad.full_title || ad.title))),
    h("div.cols.wide-start",
      h("div.stack",
        h("section.card",
          h("div.card-head", h("h2", t("adText")), copyButton(plainText)),
          ad.full_text
            ? h("div.notice-text", { dir: "auto" }, noticeText(ad.full_text, mentions, hl))
            : [h("div.notice-text", { dir: "auto" }, highlight(ad.summary, hl)), h("p.note", icon("info"), h("span", t("adSummaryOnly")))],
          ad.full_text ? h("p.note.small-gap", icon("info"), h("span", t("adCheck"))) : null)),
      h("div.stack",
        namedCard(mentions),
        h("section.card", h("h2", t("adNotice")), h("dl.kv", facts.map(([key, value]) => [h("dt", t(key)), h("dd", h("span", { dir: "auto" }, value))]))),
        h("section.card", h("div.card-head", h("h2", t("adCite")), copyButton(citation)), h("p.cite", citation)),
        siblings.length ? h("section.card", h("h2", t("adSiblings")), h("div.list", siblings.map((s) =>
          h("a.list-item", { href: `#/ad/${s.code}${query({ hl })}` },
            h("span.grow", t("adIssue"), " ", digits(s.newspaper_number ?? "–")), h("span.small.muted.num", digits(s.newspaper_date ?? ""))))))
          : null))));
  main.querySelector(".notice-text mark")?.scrollIntoView({ block: "center" });
}

// Lists ----------------------------------------------------------------------
// Notices, people, companies, addresses and links: each a spec that listPage() shows.

const PAGE = 50;
const ROLES = [...Object.values(ROLE_GROUPS).flat(), "left"];
// What a notice records (parsing/events.py EVENTS, in its order).
const EVENTS = ["establish", "board", "inspector", "resign", "signatory", "capital_up", "capital_down", "address", "name",
  "activity", "accounts", "newspaper", "transfer", "dissolve", "liquidate_end", "branch", "merger", "duration",
  "invitation", "articles", "other"];
const eventList = (events) => (events ?? []).map((e) => t(`event.${e}`)).join("، ");
const capitalText = (from, to) => (to == null ? null
  : from == null ? t("capitalSet", { b: fmt(to) }) : t("capitalChange", { a: fmt(from), b: fmt(to) }));
/** A Jalali date as stored ("1395/06/01"), from what was typed ("۱۳۹۵/6/1"). */
const storedDate = (text) =>
  normalize(text.trim()).split(/[/\-.\s]+/).filter(Boolean).map((part, i) => (i ? part.padStart(2, "0") : part)).join("/");

/** A list form's filters: each named control's value (dates and numbers as stored), the empty ones
 *  left out, checkboxes of one name joined by commas. */
function formParams(form) {
  const params = {};
  for (const [name, raw] of new FormData(form)) {
    const as = form.elements[name]?.dataset?.as;
    const value = as === "date" ? storedDate(raw) : as === "number" ? normalize(raw).replace(/\D/g, "") : raw.trim();
    if (value) params[name] = params[name] ? `${params[name]},${value}` : value;
  }
  return params;
}

const textInput = (p, name, extra) => h("input.input", { name, value: p[name] ?? "", autocomplete: "off", ...extra });
const numberInput = (p, name, extra) => textInput(p, name, { inputmode: "numeric", "data-as": "number", ...extra });
const field = (label, control) => h("label.field", h("span", label), control);
const selectField = (p, name, label, options) => field(label, h("select.input", { name },
  options.map(([value, text]) => h("option", { value, selected: (p[name] ?? "") === value }, text))));
/** The role someone is named in; *none*: also "named without one". */
const roleField = (p, none = true) => selectField(p, "role", t("fRole"),
  [["", t("fAnyRole")], ...ROLES.map((r) => [r, roleLabel(r)]), none ? ["none", t("role.named")] : null].filter(Boolean));
/** Two inputs for a span of numbers: *lo* and *hi*, either may stay empty (*wide*: twice the width, for rials). */
const spanField = (p, label, lo, hi, extra = {}, wide = false) => h(`div.field${wide ? ".wide" : ""}`, h("span", label), h("div.pair",
  numberInput(p, lo, { placeholder: t("fMinPh"), ...extra, "aria-label": `${label}: ${t("fFrom")}` }),
  h("span.muted", "–"),
  numberInput(p, hi, { placeholder: t("fMaxPh"), ...extra, "aria-label": `${label}: ${t("fTo")}` })));
/** The years from and to of *name*'s span ("first": first_from and first_to). */
const yearsField = (p, label, name) => spanField(p, label, `${name}_from`, `${name}_to`, { maxlength: 4, placeholder: t("fYearPh") });

/** A dropdown of checkboxes named *name* (sent joined by commas), one per value shown by *show*; its
 *  summary: *none* while none is ticked, the one ticked, else how many (the string *many*, with {n}). */
function checksField(p, name, label, values, show, none, many, extra = null) {
  const chosen = new Set((p[name] ?? "").split(",").filter(Boolean));
  const text = () => (chosen.size > 1 ? t(many, { n: fmt(chosen.size) }) : chosen.size ? show([...chosen][0]) : none);
  const summary = h("span.grow.clip", text());
  const panel = h("div.dropdown-panel", values.map((value) =>
    h("label.check", h("input", { type: "checkbox", name, value, checked: chosen.has(value) }), show(value))), extra);
  const box = h("details.dropdown", h("summary.input", summary), panel);
  box.addEventListener("change", (e) => {
    if (e.target.name !== name) return;
    e.target.checked ? chosen.add(e.target.value) : chosen.delete(e.target.value);
    summary.textContent = text();
  });
  box.addEventListener("toggle", () => { // near the form's far side: open toward the field's other end
    panel.classList.remove("flip");
    const at = panel.getBoundingClientRect(), form = box.closest("form").getBoundingClientRect();
    if (box.open && (at.left < form.left || at.right > form.right)) panel.classList.add("flip");
  });
  return h("div.field", h("span", label), box);
}
const typesField = (p) => checksField(p, "types", t("fTypes"), [...state.types.keys()], typeLabel, t("fAllTypes"), "fNTypes");
/** What a notice records: any of the ticked events, or (*every*, a box under them) all of them. */
const eventsField = (p, label, every = false) => checksField(p, "events", label, EVENTS, (e) => t(`event.${e}`), t("fAnyEvent"),
  "fNEvents", every ? h("label.check.every", h("input", { type: "checkbox", name: "every", value: "1", checked: p.every === "1" }), t("fEvery")) : null);

/** A list: its filters in a form, then a page of rows under headings that sort it, or (a "group
 *  by" chosen) how many rows each value has, each opening its rows. All of it lives in the URL. */
async function listPage(page, spec) {
  const p = page.params;
  title(t(spec.title));
  const view = { sort: p.sort, dir: p.dir, group: p.group }; // as the URL has them: the defaults stay out of it
  const filters = Object.fromEntries(spec.filters.filter((k) => p[k]).map((k) => [k, p[k]]));
  const href = (params) => `#${spec.path}${query(params)}`;
  const more = spec.more && h("div.filters", spec.more(p));
  const form = h("form.filters", { onsubmit: (e) => { e.preventDefault(); go(href({ ...formParams(form), ...view })); } },
    spec.fields(p),
    h("div.actions.filters-actions", h("button.btn.primary", { type: "submit" }, t("fApply")), h("a.btn.ghost", { href: href(view) }, t("fReset"))),
    more && moreFilters(more));
  const results = h("div", skeleton(10));
  main.replaceChildren(h("div.page", pageHead(t(spec.title), t(spec.lede)), h("section.card", form), h("section.card", results)));
  const offset = Math.max(0, parseInt(p.offset ?? "0", 10) || 0);
  const data = await api(`${spec.api}${query({ ...spec.fixed, ...filters, ...view, offset })}`);
  if (!page.alive()) return;

  const groupBy = h("select.input.sm", { "aria-label": t("groupBy"),
    onchange: () => go(href({ ...filters, sort: view.sort, dir: view.dir, group: groupBy.value })) },
  h("option", { value: "" }, t("gNone")),
  Object.keys(spec.groups).map((g) => h("option", { value: g, selected: g === view.group }, t(`g.${g}`))));
  const grouping = h("label.row.small.soft.nowrap", t("groupBy"), groupBy);
  if (!data.total) return results.replaceChildren(h("div.spread", h("span"), grouping), empty("file", h("p", t("noneFound"))));
  if (data.groups) {
    return setChildren(results, groupTable(spec.groups[view.group], view.group, data, grouping,
      (drill) => href({ ...filters, ...drill, sort: view.sort, dir: view.dir })));
  }
  const pager = (delta, label) => h("button.btn.sm", {
    type: "button", disabled: offset + delta < 0 || offset + delta >= data.total,
    onclick: () => go(href({ ...filters, ...view, offset: offset + delta || null })),
  }, label);
  const sort = view.sort ?? spec.sort, desc = view.dir !== "asc";
  const heading = ([label, key, cls = ""]) => {
    if (!key) return h(`th${cls}`, label);
    const active = sort === key, dir = active ? (desc ? "asc" : null) : key === "name" ? "asc" : null;
    return h(`th${cls}`, { "aria-sort": active ? (desc ? "descending" : "ascending") : null },
      h("a.sort", { href: href({ ...filters, group: view.group, sort: key === spec.sort ? null : key, dir }) },
        label, h("span.arrow", { "aria-hidden": "true" }, active ? (desc ? "▼" : "▲") : "")));
  };
  results.replaceChildren(
    h("div.spread",
      h("span.soft.small", t("range", { a: fmt(offset + 1), b: fmt(Math.min(offset + PAGE, data.total)), n: fmt(data.total) })),
      h("div.actions", grouping, pager(-PAGE, t("prev")), pager(PAGE, t("next")),
        csvButton(spec, { ...filters, sort: view.sort, dir: view.dir }))),
    h("div.table-wrap", h("table",
      h("thead", h("tr", spec.columns().map(heading))),
      h("tbody", data.rows.map((row) => spec.row(row, p))))));
}

/** The filters in *grid* folded away, open while any of them is set (and saying how many are). */
function moreFilters(grid) {
  const used = (field) => [...field.querySelectorAll("input, select")].some((el) => (el.type === "checkbox" ? el.checked : el.value));
  const set = [...grid.children].filter(used).length;
  return h("details.more-options.filters-more", { open: set > 0 },
    h("summary", set ? t("fMoreSet", { n: fmt(set) }) : t("fMore")), grid);
}

/** How many rows each value of a list's *group* has (the most first; years: the newest), each
 *  opening its rows (*open*: the URL of a drill-down's filters). */
function groupTable(group, name, data, grouping, open) {
  let byValue = !!group.newest;
  const most = Math.max(...data.groups.map((g) => g.n));
  const head = h("tr"), body = h("tbody");
  const draw = () => {
    const order = !byValue ? (a, b) => b.n - a.n : group.newest ? (a, b) => (a.value < b.value ? 1 : -1)
      : (a, b) => group.show(a.value).localeCompare(group.show(b.value), state.lang);
    const by = (value, label, cls = "") => h(`th${cls}`, { "aria-sort": byValue === value ? (value && !group.newest ? "ascending" : "descending") : null },
      h("button.sort", { type: "button", onclick: () => { byValue = value; draw(); } }, label));
    head.replaceChildren(by(true, t(`g.${name}`)), by(false, t("colCount"), ".num"), h("th", h("span.sr-only", t("colShare"))));
    body.replaceChildren(...[...data.groups].sort(order).map(({ value, n }) => {
      const drill = group.drill(value), to = drill && open(drill);
      return h(`tr${to ? ".link" : ""}`, { onclick: (e) => to && !e.target.closest("a") && go(to) },
        h("td", to ? h("a", { href: to, dir: "auto" }, group.show(value)) : h("span.muted", group.show(value))),
        h("td.num", fmt(n)),
        h("td.share", h("span.meter", h("i", { style: `width: ${(100 * n) / most}%` }))));
    }));
  };
  draw();
  return [
    h("div.spread", h("span.soft.small", t("nGroups", { n: fmt(data.groups.length), total: fmt(data.total) })), grouping),
    group.many ? h("p.note.small-gap", icon("info"), h("span", t("groupMany"))) : null,
    h("div.table-wrap", h("table", h("thead", head), body)),
  ];
}

/** Up to 5,000 rows of the list, as a CSV file. */
function csvButton(spec, params) {
  const button = h("button.btn.sm", { type: "button" }, icon("download"), t("exportMax"));
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const all = await api(`${spec.api}${query({ ...spec.fixed, ...params, limit: 5000, full: spec.csv.full ? 1 : null })}`);
      downloadCsv(spec.csv.name, [spec.csv.head(), ...all.rows.map(spec.csv.row)]);
    } catch (error) {
      toast(t("errGeneric", { e: error.message }));
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

/** Whom a notice names, as links: people first, then companies, the rest counted. */
function namedChips(named, most = 6) {
  const shown = named.filter((m) => m.kind !== "address").sort((a, b) => (a.kind === "person" ? 0 : 1) - (b.kind === "person" ? 0 : 1));
  if (!shown.length) return null;
  return h("div.chips.named", shown.slice(0, most).map((m) => {
    const roles = m.roles.map(roleLabel).join("، ");
    return h(`a.chip${m.roles.includes("left") ? ".left" : ""}`, { href: `#/e/${m.key}` },
      kindMark(m.kind), h("span", { dir: "auto" }, shorten(m.name || m.id, 32)), roles ? h("span.muted", roles) : null);
  }), shown.length > most ? h("span.chip", t("nMore", { n: fmt(shown.length - most) })) : null);
}

/** Each named person, company or address of *kind*: "name [number] (roles)". */
const namedList = (named, kind) => named.filter((m) => m.kind === kind).map((m) =>
  [m.name, m.id && `[${m.id}]`, m.roles.length && `(${m.roles.map(roleLabel).join("، ")})`,
    m.stake && t("stakeOf", { n: m.stake })].filter(Boolean).join(" ")).join("؛ ");

/** The company a notice is about: the listing's, else (a notice of the Notices list) the one its text names first. */
const companyId = (ad) => (/^\d{10,11}$/.test(ad.company_national_id ?? "") ? ad.company_national_id : ad.subject);
function companyCell(ad) {
  const id = companyId(ad);
  return id ? h("a.entity", { href: `#/e/c:${id}` }, kindMark("company"), h("span.name", shorten(ad.company_name || ad.subject_name || id, 48)))
    : h("span", { dir: "auto" }, ad.company_name ?? "–");
}
const yearGroup = (name) => ({ show: (v) => digits(v) || "–", drill: (v) => v && { [`${name}_from`]: v, [`${name}_to`]: v }, newest: true });
const ROLE_GROUP = { show: (v) => roleLabel(v || "named"), drill: (v) => ({ role: v || "none" }), many: true };

const NOTICES = {
  path: "/notices", api: "/api/ads", title: "noticesTitle", lede: "noticesLede", sort: "day",
  filters: ["types", "from", "to", "title", "company", "issue", "issues_min", "issues_max", "q", "who", "role", "events",
    "every", "office", "capital_min", "capital_max", "named", "named_name"],
  fields: (p) => [p.named ? h("div.field", h("span", t("fNamed")), h("div.row",
    h("input", { type: "hidden", name: "named", value: p.named }), h("input", { type: "hidden", name: "named_name", value: p.named_name ?? "" }),
    h("a.entity", { href: `#/e/${p.named}`, dir: "auto" }, p.named_name || p.named.slice(2)),
    h("a.btn.ghost.sm", { href: `#/notices${query({ ...p, named: null, named_name: null, offset: null })}`, "aria-label": t("fNamedClear") }, "×"))) : null,
    typesField(p),
    field(t("fFrom"), textInput(p, "from", { placeholder: t("fDatePh"), inputmode: "numeric", "data-as": "date" })),
    field(t("fTo"), textInput(p, "to", { placeholder: t("fDatePh"), inputmode: "numeric", "data-as": "date" })),
    // the table's columns, then what the notice says
    field(t("colTitle"), textInput(p, "title", { type: "search" })),
    field(t("colCompany"), textInput(p, "company", { type: "search", placeholder: t("fNamePh.company") })),
    field(t("colIssue"), numberInput(p, "issue")), spanField(p, t("csvIssues"), "issues_min", "issues_max"),
    field(t("fWho"), textInput(p, "who", { type: "search", placeholder: t("fWhoPh") })),
    roleField(p, false), eventsField(p, t("fEvent"), true), field(t("fWords"), textInput(p, "q", { type: "search" }))],
  more: (p) => [field(t("fOffice"), textInput(p, "office", { placeholder: t("fOfficePh") })),
    spanField(p, t("fCapital"), "capital_min", "capital_max", {}, true)],
  groups: {
    type: { show: (v) => (v ? typeLabel(v) : "–"), drill: (v) => v && { types: v }, many: true },
    year: { show: (v) => digits(v) || "–", drill: (v) => v && { from: v, to: v }, newest: true },
    month: { show: (v) => digits(v) || "–", drill: (v) => v && { from: v, to: v }, newest: true },
    office: { show: (v) => (v?.startsWith("~") ? t(`office.${v.slice(1)}`) : v || "–"), drill: (v) => v && { office: v } },
    event: { show: (v) => (v ? t(`event.${v}`) : "–"), drill: (v) => v && { events: v }, many: true },
    role: { show: (v) => (v ? roleLabel(v) : "–"), drill: (v) => v && { role: v }, many: true },
  },
  columns: () => [[t("colDate"), "day"], [t("colType")], [t("colTitle")], [t("colCompany")], [t("colIssue"), null, ".num"],
    [t("csvIssues"), "issues", ".num"]],
  row: (ad, p) => {
    const open = `#/ad/${ad.code}${query({ hl: p.q })}`;
    return h("tr.link", { onclick: (e) => !e.target.closest("a, button") && go(open) },
      h("td.nowrap.num", digits(ad.day) || "–"),
      h("td", h("div.chips", ad.types.map((type) => h("span.chip", typeLabel(type))),
        ad.events.filter((e) => e !== "other").map((e) => h("span.chip.event", t(`event.${e}`))))),
      h("td", h("a.title", { href: open, dir: "auto" }, highlight(ad.title, p.q)), namedChips(ad.named)),
      h("td", companyCell(ad)),
      h("td.num.nowrap", digits(ad.newspaper_number) || "–"),
      h("td.num", fmt(ad.issues)));
  },
  csv: {
    name: "rrk-notices.csv", full: true,
    head: () => [t("colDate"), t("colType"), t("colTitle"), t("colCompany"), t("id.company"), t("regNo"), t("office"), t("adTracking"),
      t("adIndicator"), t("adLetterDate"), t("adIssue"), t("adPublished"), t("adPage"), t("csvIssues"), t("csvEvents"),
      t("csvCapitalFrom"), t("csvCapitalTo"), t("csvPeople"),
      t("csvCompanies"), t("csvAddresses"), t("adText"), t("csvSummary"), "detail_code"],
    row: (ad) => [ad.day, ad.types.map(typeLabel).join("، "), ad.full_title || ad.title, ad.company_name || ad.subject_name, companyId(ad),
      ad.registration_number, ad.issuer, ad.tracking_code, ad.indicator_number, ad.letter_date, ad.newspaper_number,
      ad.newspaper_date, ad.page_number, ad.issues, eventList(ad.events), ad.capital_from, ad.capital_to,
      ...["person", "company", "address"].map((kind) => namedList(ad.named, kind)),
      plain(ad.full_text), ad.full_text ? "" : ad.summary, ad.code],
  },
};

const NODE_LISTS = { person: ["/people", "peopleTitle", "peopleLede"], company: ["/companies", "companiesTitle", "companiesLede"],
  address: ["/addresses", "addressesTitle", "addressesLede"] };

function nodeList(kind) {
  const [path, title, lede] = NODE_LISTS[kind];
  const linksLabel = () => t(kind === "company" ? "colLinks" : "colCompanies");
  return {
    path, title, lede, api: "/api/nodes", fixed: { kind }, sort: "links",
    filters: ["q", "role", "code", "first_from", "first_to", "last_from", "last_to", "active_from", "active_to", "min", "max",
      "notices_min", "notices_max", "current_min", "current_max", "status", "office", "events", "capital_min", "capital_max"],
    fields: (p) => [
      field(t("fName"), textInput(p, "q", { type: "search", placeholder: t(`fNamePh.${kind}`) })),
      kind === "address" ? null : roleField(p),
      yearsField(p, t("fActive"), "active"),
      spanField(p, linksLabel(), "min", "max"),
      kind === "company" ? eventsField(p, t("fAboutEvents")) : null,
    ],
    more: (p) => [
      kind === "person" ? selectField(p, "code", t("fCode"), [["", t("all")], ["yes", t("fCodeYes")], ["no", t("fCodeNo")]]) : null,
      kind === "company" ? field(t("fOffice"), textInput(p, "office", { placeholder: t("fOfficePh") })) : null,
      kind === "company" ? spanField(p, t("fAboutCapital"), "capital_min", "capital_max", {}, true) : null,
      selectField(p, "status", t("fLinkStatus"), [["", t("all")], ...["current", "ended", "gone"].map((s) => [s, t(`nodeStatus.${s}`)])]),
      spanField(p, t("colCurrent"), "current_min", "current_max"), spanField(p, t("colNotices"), "notices_min", "notices_max"),
      yearsField(p, t("fFirst"), "first"), yearsField(p, t("fLast"), "last"),
    ],
    groups: { ...(kind === "address" ? {} : { role: ROLE_GROUP }), first: yearGroup("first"), last: yearGroup("last") },
    columns: () => [[t(`kind.${kind}`), "name"], [linksLabel(), "links", ".num"], [t("colCurrent"), "current", ".num"],
      [t("colNotices"), "notices", ".num"], [t("colFirst"), "first", ".num"], [t("colLast"), "last", ".num"]],
    row: (n) => h("tr.link", { onclick: (e) => !e.target.closest("a") && go(`#/e/${n.key}`) },
      h("td", entityLink(n, { id: true }), roleChips(n.roles, { empty: false })),
      h("td.num", fmt(n.links)), h("td.num", fmt(n.current)), h("td.num", fmt(n.ads)),
      h("td.num.nowrap", digits(n.first) || "–"), h("td.num.nowrap", digits(n.last) || "–")),
    csv: {
      name: `rrk-${path.slice(1)}.csv`,
      head: () => [t("csvName"), t(`id.${kind}`), linksLabel(), t("colCurrent"), t("colNotices"), t("colFirst"), t("colLast"),
        t("entityRoles"), "key"],
      row: (n) => [n.name, n.id, n.links, n.current, n.ads, n.first, n.last, n.roles.map(roleLabel).join("، "), n.key],
    },
  };
}

const LINKS = {
  path: "/links", api: "/api/links", title: "linksTitle", lede: "linksLede", sort: "notices",
  filters: ["q", "q2", "kind", "role", "status", "service", "first_from", "first_to", "last_from", "last_to", "active_from",
    "active_to", "min", "max", "years"],
  fields: (p) => [
    field(t("fName"), textInput(p, "q", { type: "search", placeholder: t("fLinkNamePh") })),
    field(t("fOther"), textInput(p, "q2", { type: "search", placeholder: t("fOtherPh") })),
    selectField(p, "kind", t("g.kind"), [["", t("all")], ...["person", "company", "address"].map((k) => [k, t(`kinds.${k}`)])]),
    roleField(p),
    selectField(p, "status", t("g.status"), [["", t("all")], ["current", t("status.current")], ["ended", t("status.ended")]]),
    h("label.check.filters-check", h("input", { type: "checkbox", name: "service", value: "0", checked: p.service === "0" }), t("fNoService")),
  ],
  more: (p) => [
    yearsField(p, t("fActive"), "active"), spanField(p, t("colNotices"), "min", "max"),
    field(t("fYears"), numberInput(p, "years", { maxlength: 2 })),
    yearsField(p, t("fFirst"), "first"), yearsField(p, t("fLast"), "last"),
  ],
  groups: {
    role: ROLE_GROUP,
    kind: { show: (v) => (v ? t(`kinds.${v}`) : "–"), drill: (v) => v && { kind: v } },
    status: { show: (v) => t(`status.${v}`), drill: (v) => ({ status: v }) },
    first: yearGroup("first"), last: yearGroup("last"),
  },
  columns: () => [[t("colWho")], [t("entityRoles")], [t("colIn")], [t("colNotices"), "notices", ".num"],
    [t("colFirst"), "first", ".num"], [t("colLast"), "last", ".num"], [h("span.sr-only", t("openNetwork"))]],
  row: (link) => {
    const button = h("button.btn.sm.ghost", { type: "button", "aria-expanded": "false", title: t("netWhy") }, icon("file"), fmt(link.ads));
    const tr = h("tr",
      h("td", entityLink(link.a, { id: true })),
      h("td", roleChips(link.roles), link.ended ? h("div.small.muted", t("ended")) : null),
      h("td", entityLink(link.b, { id: true })),
      h("td.num", button),
      h("td.num.nowrap", digits(link.first) || "–"), h("td.num.nowrap", digits(link.last) || "–"),
      h("td", openIn(`#/network?keys=${link.a.key},${link.b.key}`)));
    button.addEventListener("click", () => evidence(link.a, link.b, tr, button, 7));
    return tr;
  },
  csv: {
    name: "rrk-links.csv",
    head: () => [t("colWho"), t("csvNumber"), t("csvKind"), t("entityRoles"), t("colIn"), t("id.company"), t("colNotices"),
      t("colFirst"), t("colLast"), t("status.ended"), t("csvService")],
    row: (link) => [link.a.name, link.a.id, t(`kind.${link.a.kind}`), link.roles.map(roleLabel).join("، "), link.b.name, link.b.id,
      link.ads, link.first, link.last, link.ended ? 1 : 0, link.service ? 1 : 0],
  },
};

// Leads ----------------------------------------------------------------------

function leadsCard(titleKey, whyKey, items, row) {
  let shown = 10;
  const list = h("div.list");
  const toggle = h("button.btn.sm.more", { type: "button" });
  toggle.addEventListener("click", () => {
    shown = shown >= items.length ? 10 : items.length;
    draw();
  });
  const draw = () => {
    list.replaceChildren(...items.slice(0, shown).map((item, i) => h("div.list-item", h("span.rank", fmt(item.rank ?? i + 1)), ...row(item))));
    toggle.hidden = items.length <= 10;
    toggle.textContent = shown >= items.length ? t("showLess") : t("showAll", { n: fmt(items.length) });
  };
  draw();
  return h("section.card", h("h2", t(titleKey)), h("p", t(whyKey)), list, toggle);
}

const openIn = (href) => h("a.btn.sm.ghost.icon-only", { href, title: t("openNetwork"), "aria-label": t("openNetwork") }, icon("network"));
const topRoles = (roles) => roleChips(roles.slice(0, 3), { empty: false });

async function leads(page) {
  const p = page.params;
  title(t("leadsTitle"));
  const year = (name) => numberInput(p, name, { maxlength: 4, placeholder: t("leadsYearPh") });
  const form = h("form.filters", { onsubmit: (e) => { e.preventDefault(); go(`#/leads${query(formParams(form))}`); } },
  field(t("leadsSearch"), textInput(p, "q", { type: "search", placeholder: t("leadsSearchPh") })),
  field(t("leadsFrom"), year("from")), field(t("leadsTo"), year("to")),
  h("label.check.filters-check", h("input", { type: "checkbox", name: "current", value: "1", checked: p.current === "1" }), t("leadsCurrent")),
  h("div.actions.filters-actions", h("button.btn.primary", { type: "submit" }, t("fApply")), h("a.btn.ghost", { href: "#/leads" }, t("fReset"))));
  const head = [pageHead(t("leadsTitle"), t("leadsLede")),
    h("section.card", form, h("p.note.small-gap", icon("info"), h("span", t("leadsPeriodHint"))))];
  main.replaceChildren(h("div.page", ...head, skeleton(10)));
  const data = await api(`/api/leads${query({ from: p.from, to: p.to, current: p.current, q: p.q })}`);
  if (!page.alive()) return;
  const list = (name) => data[name] ?? [];
  // Whom a group or cluster matched the search by: they may be past its first names.
  const matched = (item) => (p.q && item.matched ? h("div.row.small", h("span.muted", t("leadsMatched")), item.matched.map((n) => entityLink(n))) : null);
  const cards = [
    list("hubs").length && leadsCard("leadsHubs", "leadsHubsWhy", data.hubs, (n) => [
      h("div.grow", entityLink(n), topRoles(n.roles)), h("span.metric.small", t("nCompanies", { n: fmt(n.companies) })), openIn(`#/network?add=${n.key}`)]),
    list("groups").length && leadsCard("leadsGroups", "leadsGroupsWhy", data.groups, (g) => [
      h("div.grow", h("div.row", g.members.map((n) => entityLink(n))),
        g.size > g.members.length ? h("div.small.muted", t("nMorePeople", { n: fmt(g.size - g.members.length) })) : null, matched(g)),
      h("span.metric.small", t("nTogether", { n: fmt(g.companies) })), openIn(`#/network?keys=${g.keys.join(",")}`)]),
    list("seats").length && leadsCard("leadsSeats", "leadsSeatsWhy", data.seats, (n) => [
      h("div.grow", entityLink(n), topRoles(n.roles)), h("span.metric.small", t("nCompanies", { n: fmt(n.companies) })), openIn(`#/network?add=${n.key}`)]),
    list("addresses").length && leadsCard("leadsAddresses", "leadsAddressesWhy", data.addresses, (n) => [
      h("div.grow", entityLink(n), h("div.id.small.muted", n.id)), h("span.metric.small", t("nCompanies", { n: fmt(n.companies) })), openIn(`#/network?add=${n.key}`)]),
    list("busiest").length && leadsCard("leadsBusiest", "leadsBusiestWhy", data.busiest, (n) => [
      h("div.grow", entityLink(n)), h("span.metric.small", t("nNotices", { n: fmt(n.about) })), openIn(`#/network?add=${n.key}`)]),
    list("clusters").length && leadsCard("leadsClusters", "leadsClustersWhy", data.clusters, (c) => [
      h("div.grow", h("div.row", c.top.map((n) => entityLink(n))),
        h("div.small.muted", t("nCompanies", { n: fmt(c.count.company ?? 0) }), " · ", t("nPeople", { n: fmt(c.count.person ?? 0) })),
        matched(c)),
      openIn(`#/network?keys=${c.keys.join(",")}`)]),
  ].filter(Boolean);
  main.replaceChildren(h("div.page", ...head,
    cards.length ? h("div.cols.leads", cards) : card(null, null, empty("bulb", h("p", t(p.q ? "leadsNoMatch" : "leadsEmpty"))))));
}

// Start ----------------------------------------------------------------------

async function boot() {
  let lang = "fa";
  try {
    lang = localStorage.getItem("rrk-lang") === "en" ? "en" : "fa";
  } catch {
    // storage blocked: Persian it is
  }
  applyLang(lang);
  document.getElementById("lang").addEventListener("click", (e) => {
    const chosen = e.target.closest("button")?.dataset.lang;
    if (!chosen || chosen === state.lang) return;
    try {
      localStorage.setItem("rrk-lang", chosen);
    } catch {
      // the choice lasts until the page is closed
    }
    applyLang(chosen);
    renderStatus();
    route();
  });
  document.getElementById("global-search").addEventListener("submit", (e) => {
    e.preventDefault();
    if (searchInput.value.trim()) go(`#/search${query({ q: searchInput.value.trim() })}`);
  });
  attachPicker(searchInput, {
    onPick: (node) => go(`#/e/${node.key}`),
    footer: (q) => [{ icon: "file", label: t("searchTitle", { q }), action: () => go(`#/search${query({ q })}`) }],
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !e.target.closest("input, textarea, select, [contenteditable]")) {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
  });
  window.addEventListener("hashchange", async () => {
    const typing = document.activeElement === searchInput;
    window.scrollTo(0, 0);
    await route();
    if (!typing && !main.contains(document.activeElement)) main.focus({ preventScroll: true });
  });
  try {
    storeTypes((await api("/api/types")).types);
  } catch (error) {
    toast(error.message);
  }
  route();
  pollStatus();
}

boot();
