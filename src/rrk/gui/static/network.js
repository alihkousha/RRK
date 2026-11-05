// The network page: a map the journalist grows from a person or company, the
// shortest chain between two of them, what a few of them share, and the notices
// behind every line. The map and its filters live in the URL: a reload or a
// bookmark brings them back.
import { NetworkView, ROLE_GROUPS, edgeKey, isService, newFilter, newModel } from "./graph.js";
import {
  adItem, api, attachPicker, confirmDialog, digits, entityLink, fmt, h, icon, kindMark, nodeName, period, query, roleChips,
  setChildren, shorten, t, toast, tooltip,
} from "./ui.js";

const model = newModel(); // lives as long as the GUI tab: leaving the page keeps the map
const path = { open: false, from: null, to: null, avoid: [], result: null };
const common = { result: null }; // what the picked nodes share
let suggestions = null; // a few leads to start an empty map from

export async function networkView(page) {
  page.main.classList.add("full");
  document.title = `${t("navNetwork")} · ${t("brand")}`;
  const canvas = h("canvas", { tabindex: "0" });
  const panel = h("aside.net-panel.card", { "aria-label": t("netInView") });
  const emptyBox = h("div.net-empty");
  const addInput = h("input.input", { type: "search", placeholder: t("netAdd"), "aria-label": t("netAdd"), autocomplete: "off", spellcheck: "false" });
  const labelsButton = h("button.btn.sm", { type: "button", "aria-pressed": String(!!model.labels) }, icon("tag"), t("netLabels"));
  const filterSummary = h("summary.btn.sm");
  const filterBody = h("div.net-filter-body.card");
  const toolbar = h("div.net-toolbar",
    h("div.picker", addInput),
    h("button.btn.sm", { type: "button", onclick: () => view.fit() }, icon("fit"), t("netFit")),
    h("button.btn.sm", { type: "button", onclick: () => togglePath() }, icon("route"), t("netPath")),
    h("details.net-filter", filterSummary, filterBody),
    labelsButton,
    h("button.btn.sm.icon-only", { type: "button", title: t("netLinkHint"), "aria-label": t("netLinkHint"), onclick: copyLink }, icon("link")),
    h("button.btn.sm.icon-only", { type: "button", title: t("netPng"), "aria-label": t("netPng"), onclick: savePng }, icon("image")),
    h("button.btn.sm.icon-only", { type: "button", title: t("netClear"), "aria-label": t("netClear"), onclick: clearMap }, icon("trash")));
  const legend = h("div.net-legend", { title: t("netKeys") },
    ["person", "company", "address"].map((kind) => h("span", kindMark(kind), t(`kinds.${kind}`))),
    h("span.muted", t("netSize")), h("span.muted", t("netLine")), h("span.muted", t("netService")), h("span.muted", t("netEnded")),
    h("span.muted", t("netMore")));
  page.main.replaceChildren(h("div.net", canvas, toolbar, panel, legend, emptyBox));

  const view = new NetworkView(canvas, model, { onSelect: () => renderPanel(), onExpand: (key) => expand(key),
    onPick: (key) => togglePick(key), onHover: hover, insets });
  view.labels = !!model.labels;
  page.onLeave(() => view.destroy());
  attachPicker(addInput, { onPick: (node) => { addInput.value = ""; expand(node.key, { fit: true }); } });
  labelsButton.addEventListener("click", () => {
    view.labels = model.labels = !view.labels;
    labelsButton.setAttribute("aria-pressed", String(view.labels));
    view.draw();
  });

  function refresh() {
    canvas.setAttribute("aria-label", t("netCanvas", { n: fmt(model.nodes.size) }));
    legend.hidden = model.nodes.size === 0;
    emptyBox.hidden = model.nodes.size > 0;
    renderFilters();
    renderPanel();
    saveInUrl();
  }

  /** The map in the URL: its nodes and filters, so that a reload or a bookmark redraws it. */
  function saveInUrl() {
    const f = model.filter;
    const params = model.nodes.size
      ? { keys: [...model.nodes.keys()].join(","), hide: [...f.hide].join(",") || "none", from: f.from, to: f.to } : {};
    history.replaceState(null, "", `#/network${query(params)}`);
  }

  function insets() {
    const c = canvas.getBoundingClientRect(), bar = toolbar.getBoundingClientRect();
    const ins = { top: bar.bottom - c.top + 4, right: 0, bottom: legend.hidden ? 8 : 48, left: 0 };
    if (!panel.hidden) {
      const p = panel.getBoundingClientRect();
      if (p.width > c.width * 0.7) ins.bottom = c.bottom - p.top + 8; // narrow screens: the panel is a bottom sheet
      else if (p.left - c.left < c.right - p.right) ins.left = p.right - c.left + 8;
      else ins.right = c.right - p.left + 8;
    }
    return ins;
  }

  async function expand(key, { fit = false } = {}) {
    const times = (model.expanded.get(key) ?? 0) + 1; // each expansion brings 30 more links
    try {
      const data = await api(`/api/expand${query({ key, have: [...model.nodes.keys()].filter((k) => k !== key).join(","), limit: 30 * times })}`);
      if (!page.alive()) return;
      if (!data.nodes.length) return toast(t("entityNotFound"));
      if (fit || !model.nodes.size) view.autoFit = true;
      model.expanded.set(key, times);
      view.add(data, key);
      view.select({ node: key });
      refresh();
    } catch (error) {
      toast(t("errGeneric", { e: error.message }));
    }
  }

  async function addKeys(keys, { fit = false } = {}) {
    const data = await api(`/api/subgraph${query({ keys: keys.join(","), have: [...model.nodes.keys()].join(",") })}`);
    if (!page.alive()) return;
    if (fit || !model.nodes.size) view.autoFit = true;
    view.add(data);
    refresh();
  }

  function hover(hit, event) {
    if (!hit) return tooltip(null);
    if (hit.node) {
      const n = model.nodes.get(hit.node);
      return tooltip(event, [h("strong", shorten(nodeName(n), 50)), " · ", t(`kind.${n.kind}`), " · ", t("nLinks", { n: fmt(n.links) })]);
    }
    const e = model.edges.get(hit.edge);
    tooltip(event, [shorten(nodeName(model.nodes.get(e.a)), 30), " — ", shorten(nodeName(model.nodes.get(e.b)), 30), " · ",
      t("nNotices", { n: fmt(e.ads) }), e.ended ? [" · ", t("netEndedShort")] : null]);
  }

  // Filters --------------------------------------------------------------------

  function renderFilters() {
    const f = model.filter;
    const chip = (group) => h("button.chip", { type: "button", "aria-pressed": String(!f.hide.has(group)), onclick: () => {
      if (!f.hide.delete(group)) f.hide.add(group);
      filtered();
    } }, t(`netGroup.${group}`));
    const years = [...model.edges.values()].flatMap((e) => [e.first, e.last]).filter(Boolean).map((day) => +day.slice(0, 4));
    const [lo, hi] = [Math.min(...years), Math.max(...years)];
    setChildren(filterBody,
      h("div.field", h("span", t("netShowRoles")), h("div.chips.filter-chips", Object.keys(ROLE_GROUPS).map(chip))),
      h("div.field", h("span", t("netShowEnded")), h("div.chips.filter-chips", chip("ended"))),
      years.length && lo < hi ? periodField(lo, hi) : null,
      h("div.spread", h("span.small.muted", t("netFilterNote")),
        h("button.btn.sm.ghost", { type: "button", onclick: () => { model.filter = newFilter(); filtered(); } }, t("netReset"))));
    renderSummary();
  }

  function renderSummary() {
    const hidden = model.edges.size - view.shownEdges.size;
    setChildren(filterSummary, icon("filter"), t("netFilters"), hidden ? h("span.badge", fmt(hidden)) : null);
    filterSummary.title = hidden ? t("netFilteredOut", { n: fmt(hidden) }) : "";
  }

  function periodField(lo, hi) {
    const f = model.filter;
    const from = h("input", { type: "range", min: lo, max: hi, step: 1, value: f.from || lo, "aria-label": t("netFromYear") });
    const to = h("input", { type: "range", min: lo, max: hi, step: 1, value: f.to || hi, "aria-label": t("netToYear") });
    const years = h("output.small");
    const show = () => (years.textContent = `${digits(from.value)} – ${digits(to.value)}`);
    const set = (moved) => {
      if (+from.value > +to.value) (moved === from ? to : from).value = moved.value;
      f.from = +from.value > lo ? from.value : "";
      f.to = +to.value < hi ? to.value : "";
      show();
      filtered({ dragging: true });
    };
    from.addEventListener("input", () => set(from));
    to.addEventListener("input", () => set(to));
    show();
    return h("div.field", h("span.spread", t("netPeriod"), years), h("div.period", from, to));
  }

  function filtered({ dragging = false } = {}) {
    view.changed();
    view.reheat(dragging ? 0.08 : 0.3);
    if (dragging) renderSummary(); // keeps the slider being dragged
    else renderFilters();
    renderPanel();
    saveInUrl();
  }

  // The inspector ------------------------------------------------------------

  function renderPanel() {
    const sel = view.selected, sections = [];
    if (path.open) sections.push(pathSection());
    if (model.picked.size) sections.push(commonSection());
    if (sel?.node && model.nodes.has(sel.node)) sections.push(...nodeSections(sel.node));
    else if (sel?.edge && model.edges.has(sel.edge)) sections.push(...edgeSections(sel.edge));
    else if (model.nodes.size) sections.push(listSection());
    setChildren(panel, sections);
    panel.hidden = !sections.length;
  }

  function nodeSections(key) {
    const n = model.nodes.get(key), hidden = n.links - view.neighbours.get(key).size;
    const around = [...view.neighbours.get(key)].filter((o) => view.shownEdges.has(edgeKey(key, o)));
    const picked = model.picked.has(key);
    return [
      h("div.panel-section",
        h("div.eyebrow", kindMark(n.kind, true), t(`kind.${n.kind}`), h("span.id", n.id || t("noCode"))),
        h("h2", { dir: "auto" }, nodeName(n)),
        h("p.small.soft", t("nNotices", { n: fmt(n.ads) }), " · ", t("netAllLinks", { n: fmt(n.links) })),
        hidden > 0 ? h("p.small", t("netHidden", { n: fmt(hidden) })) : null,
        h("div.actions",
          h("button.btn.sm.primary", { type: "button", disabled: hidden <= 0, onclick: () => expand(key) },
            icon("expand"), hidden > 0 ? t("netExpand") : t("netExpanded")),
          h("a.btn.sm", { href: `#/e/${key}` }, icon("open"), t("netProfile")),
          h("button.btn.sm", { type: "button", onclick: () => openPath(n) }, icon("route"), t("netPath")),
          h("button.btn.sm", { type: "button", "aria-pressed": String(picked), title: t("netPickHint"), onclick: () => togglePick(key) },
            icon(picked ? "check" : "plus"), t("netPick")),
          n.fx != null ? h("button.btn.sm", { type: "button", onclick: () => { view.unpin(key); renderPanel(); } }, t("netUnpin")) : null,
          h("button.btn.sm.ghost", { type: "button", onclick: () => { view.remove(key); refresh(); } }, icon("trash"), t("netRemove")))),
      around.length ? h("div.panel-section", h("h3", t("netInView")),
        h("div.list", around.map((o) => [model.nodes.get(o), model.edges.get(edgeKey(key, o))])
          .sort((a, b) => b[1].ads - a[1].ads)
          .map(([other, e]) => h("button.list-item.plain", { type: "button", onclick: () => view.select({ edge: edgeKey(key, other.key) }) },
            kindMark(other.kind),
            h("span.grow", h("span.name", { dir: "auto" }, shorten(nodeName(other), 40)), roleChips(e.roles)),
            h("span.small.muted.nowrap", t("nNotices", { n: fmt(e.ads) })))))) : null,
    ];
  }

  function edgeSections(key) {
    const e = model.edges.get(key), a = model.nodes.get(e.a), b = model.nodes.get(e.b);
    const list = h("div.list", h("div.skeleton.sk-line"), h("div.skeleton.sk-line"));
    api(`/api/link${query({ a: e.a, b: e.b })}`).then((found) => {
      if (view.selected?.edge === key) {
        list.replaceChildren(...found.ads.map((ad) =>
          adItem(ad, { hl: [a.name, b.name].filter(Boolean).join(" "), extra: roleChips(ad.roles, { empty: false }) })));
      }
    }).catch((error) => list.replaceChildren(h("p.small", t("errGeneric", { e: error.message }))));
    return [
      h("div.panel-section", h("h3", t("netWhy")),
        [a, b].map((n) => h("div.row", entityLink(n), h("span.id.small.muted", n.id))),
        roleChips(e.roles),
        h("p.small.soft", t("entityPeriod"), ": ", period(e.first, e.last)),
        e.ended ? h("p.note", icon("info"), h("span", t("netEndedNote"))) : null,
        isService(e) ? h("p.note", icon("info"), h("span", t("netService"))) : null),
      h("div.panel-section", h("h3", t("netEvidence", { n: fmt(e.ads) })), list),
    ];
  }

  function listSection() {
    return h("div.panel-section", h("h3", t("netInView")), h("p.small.muted", t("netInViewHint")),
      h("div.list", view.ranked.slice(0, 300).map((n) => h("button.list-item.plain", {
        type: "button", onclick: () => { view.select({ node: n.key }); view.center(n.key); },
      }, kindMark(n.kind), h("span.grow.name", { dir: "auto" }, shorten(nodeName(n), 44)), h("span.small.muted.nowrap", t("nLinks", { n: fmt(n.links) }))))));
  }

  // What a few nodes have in common ----------------------------------------------

  function togglePick(key) {
    if (!model.picked.delete(key)) {
      if (model.picked.size >= 5) return toast(t("netPickMost"));
      model.picked.add(key);
    }
    forgetCommon();
    renderPanel();
  }

  function forgetCommon() {
    if (common.result) {
      common.result = null;
      view.setPath(null);
    } else {
      view.changed();
    }
  }

  async function findCommon() {
    const picked = new Set(model.picked);
    try {
      const found = await api(`/api/common${query({ keys: [...picked].join(","), have: [...model.nodes.keys()].join(","), service: viaInspectors() })}`);
      if (!page.alive()) return;
      view.add(found);
      common.result = found;
      const shared = new Set(Object.keys(found.shared));
      const lines = found.edges.filter((e) => (picked.has(e.a) && (shared.has(e.b) || picked.has(e.b))) || (picked.has(e.b) && shared.has(e.a)));
      view.setPath([...picked, ...shared], lines.map((e) => edgeKey(e.a, e.b)));
      refresh();
    } catch (error) {
      toast(t("errGeneric", { e: error.message }));
    }
  }

  function commonSection() {
    const picked = [...model.picked].map((key) => model.nodes.get(key)).filter(Boolean);
    return h("div.panel-section",
      h("div.spread", h("h3", t("netCommon")),
        h("button.btn.sm.ghost.icon-only", { type: "button", title: t("close"), "aria-label": t("close"), onclick: () => {
          model.picked.clear();
          forgetCommon();
          renderPanel();
        } }, icon("x"))),
      h("p.small.soft", t("netCommonHint"), viaInspectors() ? null : [" ", t("netNoInspectors")]),
      h("div.chips", picked.map((n) => h("button.chip", { type: "button", title: t("netRemove"), onclick: () => togglePick(n.key) },
        kindMark(n.kind), shorten(nodeName(n), 24), icon("x")))),
      h("div", h("button.btn.sm.primary", { type: "button", disabled: picked.length < 2, onclick: findCommon }, icon("network"), t("netFindCommon"))),
      common.result ? commonResult(picked.length) : null);
  }

  function commonResult(of) {
    const shared = Object.entries(common.result.shared).filter(([key]) => model.nodes.has(key));
    if (!shared.length) return h("p.note", icon("info"), h("span", t("netCommonNone")));
    return h("div.list", shared.map(([key, n]) => {
      const node = model.nodes.get(key);
      return h("button.list-item.plain", { type: "button", onclick: () => { view.select({ node: key }); view.center(key); } },
        kindMark(node.kind), h("span.grow.name", { dir: "auto" }, shorten(nodeName(node), 40)),
        h("span.small.muted.nowrap", t("netSharedBy", { n: fmt(n), of: fmt(of) })));
    }));
  }

  // Shortest connection --------------------------------------------------------

  const viaInspectors = () => (model.filter.hide.has("inspectors") ? null : 1); // as the map shows them

  function togglePath(open = !path.open) {
    path.open = open;
    if (!open) {
      path.result = null;
      view.setPath(null);
    }
    renderPanel();
  }

  function openPath(from) {
    Object.assign(path, { open: true, from, result: null });
    renderPanel();
    panel.querySelectorAll(".path input")[path.to ? 0 : 1]?.focus();
  }

  async function findPath() {
    if (!path.from || !path.to) return toast(t("netPathHint"));
    try {
      const found = await api(`/api/path${query({ from: path.from.key, to: path.to.key, avoid: path.avoid.map((n) => n.key).join(","), service: viaInspectors() })}`);
      if (!page.alive()) return;
      path.result = found;
      common.result = null;
      if (found.nodes.length) {
        await addKeys(found.nodes.map((n) => n.key), { fit: true });
        view.setPath(found.nodes.map((n) => n.key));
      } else {
        view.setPath(null);
      }
      renderPanel();
    } catch (error) {
      toast(t("errGeneric", { e: error.message }));
    }
  }

  function pathSection() {
    const ends = ["from", "to"].map((end) => {
      const label = t(end === "from" ? "netFrom" : "netTo");
      const input = h("input.input", { type: "search", placeholder: label, "aria-label": label, autocomplete: "off",
        value: path[end] ? nodeName(path[end]) : "" });
      input.addEventListener("input", () => (path[end] = null));
      const box = h("div.picker", input);
      attachPicker(input, { onPick: (node) => { path[end] = node; input.value = nodeName(node); } });
      return h("div.field", h("span", label), box);
    });
    return h("div.panel-section.path",
      h("div.spread", h("h3", t("netPath")),
        h("button.btn.sm.ghost.icon-only", { type: "button", title: t("close"), "aria-label": t("close"), onclick: () => togglePath(false) }, icon("x"))),
      h("p.small.soft", t("netPathHint"), viaInspectors() ? null : [" ", t("netNoInspectors")]),
      ends,
      h("div", h("button.btn.sm.primary", { type: "button", onclick: findPath }, icon("route"), t("netFindPath"))),
      path.avoid.length ? h("div.chips", h("span.small.muted", t("netAvoided")), path.avoid.map((n) =>
        h("button.chip", { type: "button", title: t("netRemove"), onclick: () => { path.avoid = path.avoid.filter((m) => m.key !== n.key); findPath(); } },
          shorten(nodeName(n), 24), icon("x")))) : null,
      path.result ? pathResult() : null);
  }

  function pathResult() {
    const { nodes, edges } = path.result;
    if (!nodes.length) {
      return h("p.note", icon("info"), h("span", t("netNoPath", { avoid: path.avoid.length ? t("netAvoiding", { n: fmt(path.avoid.length) }) : "" })));
    }
    return h("div.stack", h("strong", t("netPathFound", { n: fmt(edges.length) })),
      h("ol.path-steps", nodes.map((n, i) => h("li",
        h("div.row", entityLink(n), i > 0 && i < nodes.length - 1
          ? h("button.btn.sm", { type: "button", title: t("netAvoidHint"), onclick: () => { path.avoid.push(n); findPath(); } }, t("netAvoid"))
          : null),
        edges[i] ? h("button.path-link", { type: "button", title: t("netWhy"), onclick: () => view.select({ edge: edgeKey(edges[i].a, edges[i].b) }) },
          roleChips(edges[i].roles), h("span.small.muted", t("nNotices", { n: fmt(edges[i].ads) }))) : null))));
  }

  // Toolbar actions ------------------------------------------------------------

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(location.href);
      toast(t("netLinkCopied"));
    } catch {
      toast(t("errGeneric", { e: "clipboard" }));
    }
  }

  async function savePng() {
    const a = h("a", { href: URL.createObjectURL(await view.png()), download: "rrk-network.png" });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  async function clearMap() {
    if (!model.nodes.size) return;
    if (!(await confirmDialog({ title: t("netClearTitle"), text: t("netClearText"), ok: t("netClear"), cancel: t("close"), danger: true }))) return;
    view.clear();
    Object.assign(path, { from: null, to: null, avoid: [], result: null });
    common.result = null;
    refresh();
  }

  function renderEmpty() {
    emptyBox.replaceChildren(h("div", icon("network"), h("h2", t("netEmptyTitle")), h("p.soft", t("netEmptyText")),
      suggestions?.length ? h("div.try", h("span", t("homeTry")), suggestions.map((n) =>
        h("button", { type: "button", onclick: () => expand(n.key, { fit: true }) }, kindMark(n.kind), h("span", { dir: "auto" }, shorten(nodeName(n), 30))))) : null));
  }

  const { add, keys, hide, from, to } = page.params; // read before refresh() puts the map's own in the URL
  if (hide != null) model.filter.hide = new Set(hide === "none" ? [] : hide.split(","));
  if (from != null) model.filter.from = from;
  if (to != null) model.filter.to = to;
  view.changed();
  refresh();
  renderEmpty();
  if (!suggestions) {
    api("/api/leads").then((leads) => {
      suggestions = [...(leads.hubs ?? []).slice(0, 3), ...(leads.seats ?? []).slice(0, 2)];
      if (page.alive()) renderEmpty();
    }).catch(() => {});
  }
  if (keys) await addKeys(keys.split(","), { fit: true }); // a saved map, or a group from the leads
  for (const key of add?.split(",") ?? []) await expand(key, { fit: true });
}
