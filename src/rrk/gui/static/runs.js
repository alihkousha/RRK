// The scraper page: start main.py with its options grouped by what they do,
// follow a run live, stop it, and look back at earlier runs.
import {
  api, confirmDialog, copyButton, dateTime, digits, duration, empty, fmt, h, icon, query, runState, setChildren, shorten,
  skeleton, stat, state, storeTypes, t, toast, typeLabel,
} from "./ui.js";

let form = null; // what the form holds: kept across a language switch or a visit elsewhere

// main.py's options as the form's fields
const NUMBERS = { "--sessions-per-type": "sessions", "--workers": "workers", "--detail-delay": "delay", "--page-delay": "pageDelay",
  "--types-at-once": "typesAtOnce", "--detail-backlog": "backlog", "--timeout": "timeout" };
const SWITCHES = { "--no-detail-backlog": ["noBacklog", true], "--verify-ssl": ["verify", true], "--debug": ["debug", true] };
// --years: letter-date years, 1300 the placeholder of notices without one; this Jalali year (from 21 March)
const FIRST_YEAR = 1300, NOW = new Date();
const LAST_YEAR = NOW.getFullYear() - (NOW >= new Date(NOW.getFullYear(), 2, 21) ? 621 : 622);

function freshForm(defaults) {
  const f = { types: new Set(), mode: "full", detailLimit: "", years: null, verify: false, noBacklog: false, debug: false };
  for (const [flag, key] of Object.entries(NUMBERS)) f[key] = String(defaults[flag]);
  return f;
}

function fromArgs(args, defaults, codes) {
  const f = freshForm(defaults);
  f.types = new Set([defaults["--type"]]);
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    if (flag === "--type") f.types = new Set(args[++i] === "all" ? codes : args[i].split(","));
    else if (flag === "--missing-only" || flag === "--resume") f.mode = "finish";
    else if (flag === "--new-only") f.mode = "new";
    else if (flag === "--years") f.years = args[++i].split("-").map(Number);
    else if (flag === "--detail-limit") f.detailLimit = args[++i] === "0" ? "" : args[i]; // "list only" is gone
    else if (NUMBERS[flag]) f[NUMBERS[flag]] = args[++i];
    else if (SWITCHES[flag]) f[SWITCHES[flag][0]] = SWITCHES[flag][1];
  }
  return f;
}

/** main.py's arguments for the form: only what differs from its defaults. */
function toArgs(f, defaults, codes) {
  const args = [], chosen = codes.filter((code) => f.types.has(code));
  const type = chosen.length === codes.length ? "all" : chosen.join(",");
  if (chosen.length && type !== defaults["--type"]) args.push("--type", type);
  if (f.mode === "finish") args.push("--resume", "--missing-only");
  if (f.mode === "new") args.push("--new-only");
  if (f.years) args.push("--years", f.years.join("-"));
  if (f.detailLimit !== "") args.push("--detail-limit", String(Number(f.detailLimit)));
  for (const [flag, key] of Object.entries(NUMBERS)) {
    if (key === "backlog" && f.noBacklog) continue;
    if (Number(f[key]) !== Number(defaults[flag])) args.push(flag, String(Number(f[key])));
  }
  for (const [flag, [key, on]] of Object.entries(SWITCHES)) {
    if (f[key] === on) args.push(flag);
  }
  return args;
}

/** Detail pages a minute at most. */
function load(f) {
  const parallel = Math.min(Number(f.typesAtOnce) || 1, Math.max(1, f.types.size));
  // shortcut: ~0.7 s per detail page is a guess from the type-9 run; the pause dominates at the defaults
  return Math.round((parallel * Number(f.sessions) * Number(f.workers) * 60) / (Number(f.delay) + 0.7));
}

function summary(args, defaults) {
  if (args.includes("--export")) return t("runExport");
  const value = (flag) => (args.includes(flag) ? args[args.indexOf(flag) + 1] : undefined);
  const type = value("--type") ?? defaults["--type"];
  const codes = type === "all" ? null : type.split(",");
  const what = !codes ? t("typesAll") : codes.length > 3 ? t("fNTypes", { n: fmt(codes.length) })
    : codes.map(typeLabel).join(state.lang === "fa" ? "، " : ", ");
  const missing = args.includes("--missing-only"), resume = args.includes("--resume");
  const mode = missing && resume ? t("modeFinish") : missing ? t("modeMissing") : resume ? t("modeResume")
    : args.includes("--new-only") ? t("modeNew") : value("--detail-limit") === "0" ? t("modeList") : t("modeFull");
  const years = value("--years");
  return `${what} · ${mode}${years ? ` · ${digits(years.replace("-", " – "))}` : ""}`;
}

export async function runsView(page) {
  document.title = `${t("runsTitle")} · ${t("brand")}`;
  page.main.replaceChildren(h("div.page", skeleton(10)));
  const [{ runs, defaults }, { types }] = await Promise.all([api("/api/runs"), api("/api/types")]);
  if (!page.alive()) return;
  storeTypes(types);
  const codes = types.map((type) => type.code);
  form ??= freshForm(defaults);
  if (page.params.types) { // "plan a run for the samples"
    form.types = new Set(page.params.types.split(",").filter((code) => codes.includes(code)));
    history.replaceState(null, "", "#/runs");
  }
  const active = runs.find((run) => run.state === "running" || run.state === "stopping");
  const shown = page.arg ?? active?.id;
  const reuse = (run) => {
    form = fromArgs(run.args, defaults, codes);
    if (page.arg) location.hash = "#/runs";
    else page.refresh();
    setTimeout(() => document.getElementById("new-run")?.scrollIntoView({ block: "start" }), 300);
  };
  const exportButton = h("button.btn", {
    type: "button", disabled: !!active, title: t("runExportHint"),
    onclick: async () => {
      if (!await confirmDialog({ title: t("runExport"), text: t("runExportHint"), ok: t("runExport"), cancel: t("close") })) return;
      try {
        location.hash = `#/runs/${(await api("/api/runs", { args: ["--export"] })).id}`;
      } catch (error) {
        toast(t("errGeneric", { e: error.message }));
      }
    },
  }, icon("download"), t("runExport"));
  page.main.replaceChildren(h("div.page",
    h("header.page-head", h("div", h("h1", t("runsTitle")), h("p", t("runsLede"))),
      h("div.actions", page.arg ? h("a.btn", { href: "#/runs" }, t("allRuns")) : null, exportButton)),
    shown ? runCard(shown, page, defaults) : null,
    formCard(types, defaults, codes, active),
    historyCard(runs, defaults, reuse)));
}

// The run being watched --------------------------------------------------------

const ROWS = /type (\S+) \((.*)\): stored rows (\d+)-(\d+) of (\S+)/;
const SPLIT = /type (\S+): .* hits the \d+-row cap; splitting it in (\d+)/;
const DONE = /^type (\S+) \(.*\): (\d+) ads listed, (\d+) detail pages stored/;
const FAILED = /type (\S+) stopped: (.*)/;

function runCard(id, page, defaults) {
  const head = h("div.card-head"), stats = h("div.stats"), perTypeBox = h("div"), commandBox = h("div");
  const pre = h("pre", { tabindex: "0", "aria-label": t("log") });
  const perType = new Map();
  let offset = -1, follow = true, live = null, timer = 0, busy = false, last = "";

  const followButton = h("button.btn.sm", { type: "button", "aria-pressed": "true" }, t("logFollow"));
  const setFollow = (on) => {
    follow = on;
    followButton.setAttribute("aria-pressed", String(on));
    if (on) pre.scrollTop = pre.scrollHeight;
  };
  followButton.addEventListener("click", () => setFollow(!follow));
  pre.addEventListener("scroll", () => {
    const atEnd = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 30;
    if (atEnd !== follow) setFollow(atEnd);
  });
  const filters = [["logAll", false], ["logProblems", true]].map(([label, only]) => h("button.btn.sm", {
    type: "button", "aria-pressed": String(!only),
    onclick: () => {
      pre.classList.toggle("problems", only);
      filters.forEach((b, i) => b.setAttribute("aria-pressed", String(!!i === only)));
    },
  }, t(label)));

  const lineClass = (line) => {
    const cls = / ERROR |^Traceback|^\S*(Error|Exception)\b/.test(line) || (/^\s/.test(line) && last === "l-error") ? "l-error"
      : / WARNING /.test(line) ? "l-warn" : / DEBUG /.test(line) ? "l-debug"
        : DONE.test(line) || /^exported /.test(line) ? "l-result" : "";
    last = cls;
    return cls;
  };
  const of = (code) => perType.get(code) ?? perType.set(code, {}).get(code);
  const parse = (line) => {
    let m;
    if ((m = DONE.exec(line))) Object.assign(of(m[1]), { listed: +m[2], stored: +m[3], done: true });
    else if ((m = FAILED.exec(line))) Object.assign(of(m[1]), { error: m[2] });
    else if ((m = SPLIT.exec(line))) Object.assign(of(m[1]), { split: +m[2] });
    else if ((m = ROWS.exec(line))) Object.assign(of(m[1]), { window: m[2], a: +m[3], b: +m[4], n: m[5], split: null });
  };
  const append = (text) => {
    const lines = text.split("\n");
    if (lines.at(-1) === "") lines.pop();
    pre.append(...lines.map((line) => {
      parse(line);
      const cls = lineClass(line);
      return h(cls ? `span.${cls}` : "span", `${line}\n`);
    }));
    while (pre.childElementCount > 4000) pre.firstElementChild.remove();
    if (follow) pre.scrollTop = pre.scrollHeight;
  };

  const windowText = (w) => (w.match(/\d{4}\/\d{2}\/\d{2}/g) ?? [w]).join(" – ");
  const perTypeTable = (isLive) => (!perType.size ? null : h("div.stack", h("h3", t("runTypes")), h("div.table-wrap", h("table",
    h("thead", h("tr", h("th", t("covType")), h("th", t("runProgress")), h("th", t("hResult")))),
    h("tbody", [...perType].map(([code, s]) => h("tr",
      h("td", typeLabel(code)),
      h("td.small", s.a ? [t("runRows", { a: fmt(s.a), b: fmt(s.b), n: /^\d+$/.test(s.n) ? fmt(+s.n) : s.n }), h("div.id.muted", windowText(s.window))] : "–",
        s.split ? h("div.muted", t("runSplit", { n: fmt(s.split) })) : null),
      h("td", s.error ? h("span.status.bad", { title: s.error }, icon("alert"), shorten(s.error, 70))
        : s.done ? h("span.status.good", icon("check"), t("runTypeDone", { listed: fmt(s.listed), stored: fmt(s.stored) }))
          : isLive ? h("span.status.live", h("span.pulse"), t("runListing")) : "–"))))))));

  const stop = async (run) => {
    const ok = await confirmDialog({ title: t("runStopTitle"), text: t("runStopText"), ok: t("runStop"), cancel: t("runKeep"), danger: true });
    if (!ok) return;
    try {
      await api("/api/run/stop", { id: run.id });
    } catch (error) {
      return toast(t("errGeneric", { e: error.message }));
    }
    clearTimeout(timer);
    poll();
  };

  const render = (run, isLive) => {
    const seconds = ((run.ended ? new Date(run.ended) : new Date()) - new Date(run.started)) / 1000;
    const minutes = Math.max(seconds / 60, 1 / 60);
    const rate = (n) => (n && seconds > 30 ? t("runRate", { n: fmt(Math.round(n / minutes)) }) : null);
    setChildren(head,
      h("h2", isLive ? t("runCurrent") : dateTime(run.started)), runState(run.state),
      h("span.soft.small", t("runFor", { t: duration(seconds) })),
      run.exit ? h("span.small.muted", t("runExit", { n: run.exit })) : null,
      run.state === "running" ? h("button.btn.sm.danger", { type: "button", onclick: () => stop(run) }, icon("stop"), t("runStop")) : null);
    stats.replaceChildren(
      stat(t("runListed"), fmt(run.listed ?? 0), rate(run.listed)),
      stat(t("runTexts"), fmt(run.details ?? 0), rate(run.details)),
      stat(t("hStarted"), dateTime(run.started), summary(run.args, defaults)));
    commandBox.replaceChildren(h("div.command", h("code", run.command), copyButton(run.command, { label: "" })));
    setChildren(perTypeBox, perTypeTable(isLive));
  };

  const poll = async () => {
    if (busy) return;
    busy = true;
    try {
      const run = await api(`/api/run${query({ id, offset })}`);
      if (!page.alive()) return;
      if (run.log) append(run.log);
      if (run.offset != null) offset = run.offset;
      const isLive = run.state === "running" || run.state === "stopping";
      render(run, isLive);
      if (isLive) timer = setTimeout(poll, 2000);
      else if (live) setTimeout(() => page.alive() && page.refresh(), 800); // it just ended: history and form catch up
      live = isLive;
    } catch (error) {
      if (!page.alive()) return;
      if (error.status === 404) return card.replaceChildren(empty("search", h("p", t("notFound"))));
      toast(t("errGeneric", { e: error.message }));
      timer = setTimeout(poll, 5000);
    } finally {
      busy = false;
    }
  };
  page.onLeave(() => clearTimeout(timer));

  const card = h("section.card.stack", head, stats, perTypeBox, commandBox,
    h("div.console",
      h("div.console-bar", h("strong", t("log")), h("span.grow"), filters, followButton),
      pre));
  poll();
  return card;
}

// The form -------------------------------------------------------------------

function formCard(types, defaults, codes, active) {
  const preview = h("code"), loadLine = h("div.load"), problem = h("span.small.muted", { role: "status" });
  const startButton = h("button.btn.primary", { type: "button" }, icon("play"), t("runStart"));
  const typeInputs = new Map(), numbers = [];

  const field = (label, control, hint) => h("label.field", h("span", label), control, hint ? h("span.hint", hint) : null);
  const numberInput = (key, min, step, extra = {}) => {
    const input = h("input.input", { type: "number", min, step, value: form[key], required: key !== "detailLimit", inputmode: "decimal", ...extra });
    input.addEventListener("input", () => {
      form[key] = input.value;
      update();
    });
    numbers.push(input);
    return input;
  };
  const check = (key, label, hint) => {
    const input = h("input", { type: "checkbox", checked: form[key] });
    input.addEventListener("change", () => {
      form[key] = input.checked;
      update();
    });
    return h("label.check", input, h("span", label, hint ? h("span.hint", hint) : null));
  };
  const section = (n, heading, ...content) => h("div.form-section", h("h3", h("span.step", fmt(n)), heading), ...content);

  const typeGrid = h("div.type-grid", types.map((type) => {
    const input = h("input", { type: "checkbox", checked: form.types.has(type.code) });
    input.addEventListener("change", () => {
      input.checked ? form.types.add(type.code) : form.types.delete(type.code);
      update();
    });
    typeInputs.set(type.code, input);
    return h("label.type-option", input, h("span",
      h("span.label", typeLabel(type.code)),
      h("span.sub", type.ads ? t("typesStored", { n: fmt(type.ads) }) : t("typesNew"),
        type.capped ? [" · ", h("span.status.warn", icon("alert"), t("covCapped"))] : null,
        type.left ? [" · ", h("span.status.warn", t("windowsLeft", { n: fmt(type.left) }))] : null,
        type.missing ? [" · ", h("span.status.warn", t("textsMissing", { n: fmt(type.missing) }))] : null)));
  }));
  const pick = (chosen) => {
    form.types = new Set(chosen);
    update();
  };
  const capped = types.filter((type) => type.capped).map((type) => type.code);
  const unfinished = types.filter((type) => type.left || type.missing).map((type) => type.code);

  const modes = h("div.choices", { role: "radiogroup", "aria-label": t("stepMode") },
    [["full", "modeFull", "modeFullHint"], ["finish", "modeFinish", "modeFinishHint"], ["new", "modeNew", "modeNewHint"]]
      .map(([value, label, hint]) => {
        const input = h("input", { type: "radio", name: "mode", value, checked: form.mode === value });
        input.addEventListener("change", () => {
          form.mode = value;
          update();
        });
        return h("label.choice", input, h("strong", t(label)), h("span", t(hint)));
      }));
  const limitField = field(t("fDetailLimit"), numberInput("detailLimit", 1, 1, { placeholder: t("all") }), t("fDetailLimitHint"));
  const yearsField = () => { // like the map's period: two handles, the whole span sends no --years
    const [a, b] = form.years ?? [FIRST_YEAR, LAST_YEAR];
    const range = (value, label) => h("input", { type: "range", min: FIRST_YEAR, max: LAST_YEAR, step: 1, value, "aria-label": t(label) });
    const from = range(a, "netFromYear"), to = range(b, "netToYear"), shown = h("output.small");
    const show = () => (shown.textContent = form.years ? `${digits(from.value)} – ${digits(to.value)}` : t("all"));
    const set = (moved) => {
      if (+from.value > +to.value) (moved === from ? to : from).value = moved.value;
      form.years = +from.value > FIRST_YEAR || +to.value < LAST_YEAR ? [+from.value, +to.value] : null;
      show();
      update();
    };
    from.addEventListener("input", () => set(from));
    to.addEventListener("input", () => set(to));
    show();
    return h("div.field.wide", h("span.spread", t("fYears"), shown), h("div.period", from, to), h("span.hint", t("fYearsHint")));
  };
  const workers = numberInput("workers", 1, 1), delay = numberInput("delay", 0, "any"), backlog = numberInput("backlog", 0, 1);
  const advancedOpen = form.noBacklog || form.verify || form.debug
    || Number(form.backlog) !== Number(defaults["--detail-backlog"]) || Number(form.timeout) !== Number(defaults["--timeout"]);

  function update() {
    const args = toArgs(form, defaults, codes);
    preview.textContent = ["python3 -W ignore main.py", ...args].join(" ");
    const perMinute = load(form);
    loadLine.replaceChildren(...(perMinute > 200
      ? [h("span.status.warn", icon("alert"), t("loadEstimate", { n: fmt(perMinute) })), h("span.soft", t("loadHigh"))]
      : [icon("info"), h("span", t("loadEstimate", { n: fmt(perMinute) }))]));
    for (const [code, input] of typeInputs) input.checked = form.types.has(code);
    backlog.disabled = form.noBacklog;
    const invalid = numbers.find((input) => !input.disabled && !input.checkValidity());
    const chosen = types.filter((type) => form.types.has(type.code));
    // An unknown count of missing texts (the overview isn't counted yet) doesn't block a finish run.
    const nothingLeft = form.mode === "finish" && !chosen.some((type) => type.left || type.missing !== 0);
    const samples = form.mode === "new" ? chosen.filter((type) => type.capped).map((type) => typeLabel(type.code)) : [];
    problem.textContent = !form.types.size ? t("runNoTypes") : nothingLeft ? t("runNothingLeft")
      : samples.length ? t("runSamplesFirst", { types: samples.join(state.lang === "fa" ? "، " : ", ") })
        : invalid ? invalidText(invalid) : "";
    startButton.disabled = !!active || !form.types.size || nothingLeft || samples.length > 0 || !!invalid;
  }
  const invalidText = (input) => `${input.closest(".field").firstChild.textContent}: ${input.validationMessage}`;

  startButton.addEventListener("click", async () => {
    startButton.disabled = true;
    try {
      const run = await api("/api/runs", { args: toArgs(form, defaults, codes) });
      location.hash = `#/runs/${run.id}`;
    } catch (error) {
      toast(t("errGeneric", { e: error.message }));
      update();
    }
  });

  const body = h("fieldset.plain", { disabled: !!active },
    section(1, t("stepTypes"),
      h("div.actions",
        h("button.btn.sm", { type: "button", onclick: () => pick(codes) }, t("typesAll")),
        h("button.btn.sm", { type: "button", onclick: () => pick([]) }, t("typesNone")),
        capped.length ? h("button.btn.sm", { type: "button", onclick: () => pick(capped) }, icon("alert"), t("typesCapped")) : null,
        unfinished.length ? h("button.btn.sm", { type: "button", onclick: () => pick(unfinished) }, t("typesLeft")) : null),
      typeGrid),
    section(2, t("stepMode"), modes, h("div.fields", yearsField(), limitField)),
    section(3, t("stepSpeed"),
      h("div.fields",
        field(t("fSessions"), numberInput("sessions", 1, 1), t("fSessionsHint")),
        field(t("fWorkers"), workers, t("fWorkersHint")),
        field(t("fDelay"), delay, t("fDelayHint")),
        field(t("fPageDelay"), numberInput("pageDelay", 0, "any"), t("fPageDelayHint")),
        field(t("fTypesAtOnce"), numberInput("typesAtOnce", 1, 1), t("fTypesAtOnceHint"))),
      loadLine),
    h("div.form-section",
      h("details.more-options", { open: advancedOpen }, h("summary", t("advTitle")), h("div",
        h("div.fields", field(t("fBacklog"), backlog, t("fBacklogHint")), field(t("fTimeout"), numberInput("timeout", 1, "any"), t("fTimeoutHint"))),
        check("noBacklog", t("fNoBacklog")),
        check("verify", t("fVerify"), t("fVerifyHint")),
        check("debug", t("fDebug"), t("fDebugHint"))))),
    h("div.form-section",
      h("h3", t("command")),
      h("div.command", preview, copyButton(() => preview.textContent, { label: "" })),
      h("p.note.warn", icon("alert"), h("span", t("runNetwork"))),
      h("div.row", startButton, problem)));
  update();
  return h("section.card", { id: "new-run" }, h("h2", t("runsNew")),
    active ? h("p.note.warn", icon("clock"), h("span", t("runsBusy"))) : null, body);
}

// History --------------------------------------------------------------------

function historyCard(runs, defaults, reuse) {
  if (!runs.length) return h("section.card", h("h2", t("historyTitle")), empty("clock", h("p", t("historyEmpty"))));
  return h("section.card", h("h2", t("historyTitle")), h("div.table-wrap", h("table",
    h("thead", h("tr", h("th", t("hStarted")), h("th", t("hWhat")), h("th", t("hTook")), h("th", t("hResult")), h("th"))),
    h("tbody", runs.map((run) => h("tr.link", { onclick: (e) => !e.target.closest("a, button") && (location.hash = `#/runs/${run.id}`) },
      h("td.nowrap", h("a", { href: `#/runs/${run.id}` }, dateTime(run.started))),
      h("td", summary(run.args, defaults)),
      h("td.nowrap", run.ended ? duration((new Date(run.ended) - new Date(run.started)) / 1000) : "–"),
      h("td", runState(run.state), run.listed != null
        ? h("div.small.muted", t("runTypeDone", { listed: fmt(run.listed), stored: fmt(run.details ?? 0) })) : null),
      h("td", h("button.btn.sm.ghost", { type: "button", onclick: () => reuse(run) }, t("reuse")))))))));
}
