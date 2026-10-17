// #/runs/:id — one run: controls, progress, milestones, chunks, log, errors, calls, recipes, analytics, config.
import { h, clear, icon, fmt, statusBadge, severityBadge, levelColor, toast, openDrawer, popMenu, confirmDialog, barChart, hbars, diffView, debounce } from "../ui.js";
import { api } from "../api.js";
import { live } from "../live.js";

const STAGES = ["PREFLIGHT", "READING", "EXTRACTING", "REFINING", "EMBEDDING", "CATEGORIZING"];
const TIMELINE_KINDS = /^(run\.|preflight\.|book\.|chunks\.selected|pipeline\.start|milestone|control\.|limit\.reached|output\.)/;
const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const TABS = [["log", "Live log"], ["errors", "Errors"], ["calls", "API calls"], ["recipes", "Recipes"], ["analytics", "Analytics"], ["config", "Configuration"], ["raw", "Raw log"]];

export async function render(root, params, query) {
  const id = params.id;
  const S = { detail: null, events: [], timeline: [], tab: query.get("tab") || "log", logFilter: { level: "INFO", kind: "", chunk: "", q: "" }, autoscroll: true, lastMilestone: null };
  const offs = [];
  const timers = [];

  const bar = h("div", { class: "control-bar" });
  const banner = h("div", { class: "mb-16" });
  const progress = h("div", { class: "card card-pad mb-16" });
  const middle = h("div", { class: "grid cols-2 mb-16" });
  const timelineCard = h("div", { class: "card" });
  const chunksCard = h("div", { class: "card" });
  middle.append(timelineCard, chunksCard);
  const tabsEl = h("div", { class: "tabs" });
  const tabBody = h("div");
  clear(root, bar, banner, progress, middle, tabsEl, tabBody);

  // ── data ────────────────────────────────────────────────────────────────
  async function loadDetail() {
    try {
      S.detail = await api.get(`/api/runs/${id}`, { quiet: true });
    } catch (e) {
      clear(root, h("div", { class: "alert red" }, icon("alert"), h("div", {}, h("div", { class: "alert-title" }, "Run not available"), h("div", {}, e.message), h("a", { class: "link", href: "#/" }, "Back to runs"))));
      return false;
    }
    renderBar(); renderBanner(); renderProgress(); renderChunks();
    return true;
  }
  const refresh = throttle(loadDetail, 1000);

  async function loadTimeline() {
    const evs = await api.get(`/api/runs/${id}/events?limit=5000&level=INFO`, { quiet: true });
    S.timeline = evs.events.filter((e) => TIMELINE_KINDS.test(e.kind));
    S.lastMilestone = [...S.timeline].reverse().find((e) => e.kind === "milestone") || null;
    renderTimeline();
  }

  if (!(await loadDetail())) return;
  await loadTimeline();
  renderTabs();

  offs.push(live.on("events", (events) => {
    const mine = events.filter((e) => e.run_id === id);
    if (!mine.length) return;
    let tl = false;
    for (const e of mine) {
      if (TIMELINE_KINDS.test(e.kind) && e.level !== "DEBUG") { S.timeline.push(e); tl = true; if (e.kind === "milestone") S.lastMilestone = e; }
    }
    if (tl) renderTimeline();
    if (S.tab === "log") appendLog(mine);
    if (S.tab === "errors" && mine.some((e) => e.level === "ERROR" || e.level === "WARNING" || e.kind === "chunk.failed")) debouncedTab();
    if ((S.tab === "recipes" && mine.some((e) => e.kind === "recipe")) || (S.tab === "calls")) debouncedTab();
    refresh();
  }));
  offs.push(live.on("runs", (runs) => { if (runs.some((r) => r.id === id)) refresh(); }));
  timers.push(setInterval(() => { if (S.detail?.run?.active) renderBarMeta(); }, 1000));
  const debouncedTab = debounce(() => renderTab(), 1500);

  // ── control bar ─────────────────────────────────────────────────────────
  let metaEl;
  function renderBarMeta() {
    if (!metaEl) return;
    const r = S.detail.run;
    const started = r.started_at || r.created_at;
    const elapsed = (r.ended_at || Date.now() / 1000) - started;
    clear(metaEl, [r.provider, r.gen_model, r.embed_model].filter(Boolean).join(" · "), " · started ", fmt.datetime(started), " · ", h("span", { class: "mono" }, fmt.dur(elapsed)), r.pid ? ` · pid ${r.pid}` : "");
  }

  function renderBar() {
    const r = S.detail.run, can = S.detail.can;
    metaEl = h("div", { class: "small muted" });
    const restartBtn = h("button", { class: "btn btn-sm", title: "Start a new run from this one" }, icon("restart"), "Restart ▾");
    restartBtn.onclick = () => popMenu(restartBtn, [
      { label: "Restart (same settings)", hint: r.active ? "Kills this run first" : "Whole selection again", onClick: () => restart("same") },
      { label: `Retry failed chunks (${S.detail.failed_chunks.length})`, hint: "Only chunks that failed", disabled: !can.retry_failed || !S.detail.failed_chunks.length, onClick: () => restart("failed") },
      { label: `Continue remaining (${S.detail.remaining_chunks ?? "—"})`, hint: "Chunks never processed", disabled: !can.continue_remaining || !S.detail.remaining_chunks, onClick: () => restart("remaining") },
    ]);
    const files = [["paprika", "Paprika archive"], ["cayenne", "Cayenne zip"], ["json", "recipes.json"], ["run.log", "run.log"], ["crash.log", "crash.log"], ["prompts.json", "prompts.json"]];
    const dlBtn = h("button", { class: "btn btn-sm" }, icon("download"), "Files ▾");
    dlBtn.onclick = () => popMenu(dlBtn, files.map(([k, label]) => ({ label, disabled: !["run.log", "crash.log", "prompts.json"].includes(k) && !(r.outputs || {})[k], onClick: () => window.open(`/api/runs/${id}/files/${k}`) })));
    clear(bar,
      h("div", { class: "row between wrap" },
        h("div", { style: { minWidth: 0 } },
          h("div", { class: "row" }, h("a", { href: "#/", class: "faint small" }, "← Runs"), r.parent_run_id ? h("a", { class: "faint small", href: `#/runs/${r.parent_run_id}` }, ` · from ${r.parent_run_id}`) : null),
          h("div", { class: "row", style: { marginTop: "4px" } }, h("h1", { class: "ellipsis", style: { fontSize: "20px" } }, r.label || r.book_name), statusBadge(r.status), h("span", { class: "faint small mono" }, r.id)),
          metaEl),
        h("div", { class: "btn-group" },
          can.resume ? h("button", { class: "btn btn-sm btn-primary", onclick: () => control("resume") }, icon("play"), "Resume")
            : h("button", { class: "btn btn-sm", disabled: !can.pause, onclick: () => control("pause"), title: "Hold every chunk before its next model call" }, icon("pause"), "Pause"),
          h("button", { class: "btn btn-sm btn-danger", disabled: !can.stop, onclick: () => control("stop"), title: "Finish calls in flight, then write what was extracted" }, icon("stop"), "Stop"),
          h("button", { class: "btn btn-sm btn-kill", disabled: !can.kill, onclick: kill, title: "End the run's process immediately" }, icon("kill"), "Kill"),
          restartBtn,
          r.parent_run_id ? h("a", { class: "btn btn-sm", href: `#/compare?ids=${r.parent_run_id},${id}` }, icon("compare"), "Compare with parent") : h("a", { class: "btn btn-sm", href: `#/compare?ids=${id}` }, icon("compare"), "Compare"),
          dlBtn,
          h("button", { class: "btn btn-sm btn-ghost", disabled: !can.delete, title: "Delete run", onclick: del }, icon("trash")))));
    renderBarMeta();
  }

  async function control(cmd) {
    try { await api.post(`/api/runs/${id}/${cmd}`); toast(`${cmd[0].toUpperCase() + cmd.slice(1)} requested`, cmd === "pause" ? "Chunks hold before their next model call; calls in flight finish." : cmd === "stop" ? "Calls in flight finish, then the outputs are written." : "", "blue"); refresh(); } catch { /* toasted */ }
  }
  async function kill() {
    if (!confirmDialog("Kill this run now? Its process ends immediately; calls in flight are lost and no archive is written. Recipes recorded so far stay visible here.")) return;
    try { await api.post(`/api/runs/${id}/kill`); refresh(); } catch { /* toasted */ }
  }
  async function restart(mode) {
    if (mode === "same" && S.detail.run.active && !confirmDialog("This run is active: restarting kills it first. Continue?")) return;
    try { const res = await api.post(`/api/runs/${id}/restart`, { mode }); toast("New run started", res.id, "green"); location.hash = `#/runs/${res.id}`; } catch { /* toasted */ }
  }
  async function del() {
    if (!confirmDialog("Delete this run, its events, errors, recipes and output files?")) return;
    try { await api.del(`/api/runs/${id}`); location.hash = "#/"; } catch { /* toasted */ }
  }

  // ── banner ──────────────────────────────────────────────────────────────
  async function renderBanner() {
    const r = S.detail.run;
    if (r.active) {
      const stages = Object.entries(S.detail.active_stages || {});
      const what = r.status === "paused" ? "Paused — no new model calls start until you resume." : r.status === "pausing" ? "Pausing — waiting for chunks to reach their next call." : r.status === "stopping" ? "Stopping — finishing calls in flight, then writing outputs." : r.status === "starting" ? "Starting — preflight checks and reading the book." : stages.length ? `Working: ${stages.map(([s, n]) => `${n} chunk${n > 1 ? "s" : ""} ${s.toLowerCase()}`).join(", ")}.` : `Current stage: ${r.stage || "—"}.`;
      clear(banner, h("div", { class: `alert ${r.status.startsWith("paus") ? "amber" : "blue"}` }, h("span", { class: "spinner" }), h("div", {}, h("div", { class: "alert-title" }, "What is happening now"), h("div", {}, what))));
      return;
    }
    const color = { completed: "green", limit_reached: "green", completed_with_errors: "amber", cancelled: "amber" }[r.status] || "red";
    const body = h("div", { style: { flex: 1, minWidth: 0 } }, h("div", { class: "alert-title" }, { completed: "Completed", limit_reached: "Recipe limit reached", completed_with_errors: "Completed with errors", cancelled: "Stopped by you", failed: "Failed", killed: "Killed", crashed: "Crashed" }[r.status] || r.status), h("div", {}, r.outcome || ""));
    clear(banner, h("div", { class: `alert ${color}` }, icon(color === "green" ? "check" : "alert"), body));
    if (color !== "green" || r.errors) {
      const errs = (await api.get(`/api/runs/${id}/errors`, { quiet: true })).errors;
      const byCode = new Map();
      for (const e of errs) if (e.severity !== "warning" || !byCode.size) if (!byCode.has(e.code)) byCode.set(e.code, { ...e, n: errs.filter((x) => x.code === e.code).length });
      const top = [...byCode.values()].slice(0, 3);
      if (top.length) body.append(h("div", { class: "col mt-8" }, top.map((e) => h("div", { class: "small" }, h("b", {}, `${e.code} · ${e.title}`), ` (×${e.n})`, h("div", { class: "muted" }, e.cause || ""), h("div", {}, h("b", {}, "Fix: "), e.fix || "—"))), h("a", { class: "link small", href: "javascript:void 0", onclick: () => switchTab("errors") }, "All errors →")));
    }
  }

  // ── progress ────────────────────────────────────────────────────────────
  function renderProgress() {
    const r = S.detail.run;
    const color = r.status === "failed" || r.status === "crashed" || r.status === "killed" ? "red" : r.status.startsWith("completed") || r.status === "limit_reached" ? "green" : r.status.startsWith("paus") ? "amber" : "";
    const ms = S.lastMilestone?.data;
    const eta = r.active && ms && ms.eta ? `about ${fmt.dur(ms.eta - (Date.now() / 1000 - S.lastMilestone.ts))} left` : "";
    const stat = (label, value, hint) => h("div", { class: "stat", style: { padding: "0" } }, h("div", { class: "label" }, label), h("div", { class: "value", style: { fontSize: "20px" } }, value), hint ? h("div", { class: "hint" }, hint) : null);
    const st = S.detail.active_stages || {};
    clear(progress,
      h("div", { class: "row between" }, h("div", { class: "row" }, h("b", {}, fmt.pct(r.progress)), h("span", { class: "muted" }, `${r.chunks_done || 0} of ${r.chunks_total || "?"} chunks`, r.chunks_in_book ? ` (book has ${r.chunks_in_book})` : "")), h("span", { class: "muted small" }, eta)),
      h("div", { class: `progress mt-8 ${color}` }, h("div", { style: { width: `${r.progress || 0}%` } })),
      h("div", { class: "grid cols-6 mt-16" },
        stat("Recipes", fmt.num(r.recipes_done), r.options?.max_recipes ? `limit ${r.options.max_recipes}` : null),
        stat("Errors", h("span", { style: { color: r.errors ? "var(--danger)" : "" } }, fmt.num(r.errors))),
        stat("Warnings", h("span", { style: { color: r.warnings ? "var(--warning)" : "" } }, fmt.num(r.warnings))),
        stat("Skipped", fmt.num(r.skips)),
        stat("Concurrency", fmt.num(r.options?.concurrency)),
        stat("Requests/min limit", fmt.num(r.options?.rpm))),
      h("div", { class: "pipeline mt-16" }, STAGES.map((s) => h("div", { class: `pipe-step ${st[s] || r.stage === s ? "on" : ""}` }, h("div", { class: "n" }, st[s] || (r.stage === s && !["EXTRACTING", "REFINING", "EMBEDDING"].includes(s) ? "●" : 0)), h("div", { class: "t" }, s.toLowerCase())))));
  }

  // ── timeline ────────────────────────────────────────────────────────────
  function renderTimeline() {
    const items = S.timeline.slice(-60).reverse();
    const cls = (e) => e.level === "CRITICAL" || e.level === "ERROR" ? "bad" : e.level === "WARNING" ? "warn" : e.kind === "milestone" || e.kind === "run.finished" || e.kind === "output.written" ? "ok" : "info";
    const ic = (e) => ({ milestone: "flag", "output.written": "download", "book.read": "book", "run.finished": "check" }[e.kind] || (e.level === "WARNING" || e.level === "ERROR" || e.level === "CRITICAL" ? "alert" : "info"));
    clear(timelineCard,
      h("div", { class: "card-head" }, h("h3", {}, "Milestones & lifecycle"), h("span", { class: "faint small" }, `${S.timeline.length} events`)),
      h("div", { class: "card-body", style: { maxHeight: "360px", overflow: "auto" } },
        items.length ? h("div", { class: "timeline" }, items.map((e) => h("div", { class: `tl-item ${cls(e)}` }, h("span", { class: "tl-dot" }, icon(ic(e))), h("div", { class: "small", style: { minWidth: 0, overflowWrap: "anywhere" } }, e.message), h("span", { class: "faint small nowrap" }, fmt.time(e.ts))))) : h("div", { class: "empty" }, "Nothing yet.")));
  }

  // ── chunks ──────────────────────────────────────────────────────────────
  function renderChunks() {
    const chunks = S.detail.chunks || [];
    const counts = {};
    for (const c of chunks) counts[c.state] = (counts[c.state] || 0) + 1;
    const legend = [["queued", "var(--bg-muted)"], ["active", "#84caff"], ["done", "#47cd89"], ["empty", "#e4e7ec"], ["failed", "#f97066"], ["interrupted", "#fec84b"], ["not processed", "#fff"]];
    clear(chunksCard,
      h("div", { class: "card-head" }, h("h3", {}, "Chunks"), h("span", { class: "faint small" }, "click a chunk for its events and calls")),
      h("div", { class: "card-body" },
        chunks.length ? h("div", { class: "chunks" }, chunks.map((c) => h("div", { class: `chunk ${c.state.replace(" ", "-")}`, title: `Chunk ${c.chunk} (book chunk ${c.book_chunk ?? "?"}) · ${c.state}${c.stage ? " · " + c.stage : ""} · ${c.recipes} recipe(s)${c.skips ? ` · ${c.skips} skipped` : ""}`, onclick: () => chunkDrawer(c) }))) : h("div", { class: "empty" }, "Chunks appear once the book is read."),
        h("div", { class: "legend mt-16" }, legend.filter(([k]) => counts[k]).map(([k, c]) => h("span", { style: { "--c": c } }, `${k} ${counts[k]}`)))));
  }

  async function chunkDrawer(c) {
    const [evs, calls] = await Promise.all([api.get(`/api/runs/${id}/events?chunk=${c.chunk}&limit=2000&level=DEBUG`), api.get(`/api/runs/${id}/calls?chunk=${c.chunk}`)]);
    openDrawer(`Chunk ${c.chunk}`, [
      h("dl", { class: "kv" }, h("dt", {}, "Book chunk"), h("dd", {}, c.book_chunk ?? "—"), h("dt", {}, "State"), h("dd", {}, c.state), h("dt", {}, "Last stage"), h("dd", {}, c.stage || "—"), h("dt", {}, "Recipes"), h("dd", {}, c.recipes), h("dt", {}, "Skipped"), h("dd", {}, c.skips)),
      h("h3", {}, "API calls"), callsTable(calls.calls),
      h("h3", {}, "Events"), h("div", { class: "log" }, evs.events.map(logLine))]);
  }

  // ── tabs ────────────────────────────────────────────────────────────────
  function renderTabs() {
    clear(tabsEl, TABS.map(([k, label]) => h("button", { class: S.tab === k ? "on" : "", onclick: () => switchTab(k) }, label)));
    renderTab();
  }
  function switchTab(k) { S.tab = k; history.replaceState(null, "", `#/runs/${id}?tab=${k}`); renderTabs(); }

  async function renderTab() {
    const fn = { log: tabLog, errors: tabErrors, calls: tabCalls, recipes: tabRecipes, analytics: tabAnalytics, config: tabConfig, raw: tabRaw }[S.tab];
    try { await fn(); } catch (e) { clear(tabBody, h("div", { class: "alert red" }, icon("alert"), h("div", {}, "This tab failed to load: ", e.message))); }
  }

  // Live log
  let logBox = null;
  function logLine(e) {
    return h("div", { class: `log-line ${e.level} ${e.kind === "milestone" ? "milestone" : ""}`, style: { cursor: e.data ? "pointer" : "" }, onclick: e.data ? () => openDrawer(`${e.kind} · ${fmt.time(e.ts)}`, [h("p", {}, e.message), h("pre", { class: "block" }, JSON.stringify(e.data, null, 2))]) : null },
      h("span", { class: "faint" }, fmt.time(e.ts)), h("span", { class: "lvl" }, e.level), h("span", { class: "faint" }, e.chunk != null ? `c${e.chunk}` : ""), h("span", { class: "kind", title: e.kind }, e.stage ? `${e.kind} · ${e.stage.toLowerCase()}` : e.kind), h("span", { class: "msg" }, e.message));
  }
  function matches(e) {
    const f = S.logFilter;
    if (LEVELS.indexOf(e.level) < LEVELS.indexOf(f.level)) return false;
    if (f.kind && !e.kind.startsWith(f.kind)) return false;
    if (f.chunk !== "" && String(e.chunk) !== String(f.chunk)) return false;
    if (f.q && !e.message.toLowerCase().includes(f.q.toLowerCase())) return false;
    return true;
  }
  function appendLog(events) {
    if (!logBox) return;
    const lines = events.filter(matches).map(logLine);
    if (!lines.length) return;
    logBox.append(...lines);
    while (logBox.children.length > 4000) logBox.firstChild.remove();
    if (S.autoscroll) logBox.scrollTop = logBox.scrollHeight;
  }
  async function tabLog() {
    const f = S.logFilter;
    const qs = new URLSearchParams({ limit: "1000", level: f.level });
    if (f.kind) qs.set("kind", f.kind + "*");
    if (f.chunk !== "") qs.set("chunk", f.chunk);
    if (f.q) qs.set("q", f.q);
    const res = await api.get(`/api/runs/${id}/events?${qs}`);
    logBox = h("div", { class: "log", style: { maxHeight: "620px" } }, res.events.map(logLine));
    let oldest = res.events[0]?.id;
    const set = (k) => (ev) => { S.logFilter[k] = ev.target.value; tabLog(); };
    const older = h("button", { class: "btn btn-xs", disabled: res.events.length < 1000, onclick: async () => {
      qs.set("before", oldest);
      const more = await api.get(`/api/runs/${id}/events?${qs}`);
      if (more.events.length) { oldest = more.events[0].id; logBox.prepend(...more.events.map(logLine)); } else older.disabled = true;
    } }, "Load older");
    clear(tabBody,
      h("div", { class: "row wrap mb-16" },
        h("select", { class: "input input-sm", style: { width: "130px" }, onchange: set("level") }, LEVELS.map((l) => h("option", { value: l, selected: l === f.level }, `${l}+`))),
        h("select", { class: "input input-sm", style: { width: "170px" }, onchange: set("kind") }, [["", "All kinds"], ["log", "Log lines"], ["stage", "Stage changes"], ["chunk", "Chunks"], ["recipe", "Recipes"], ["milestone", "Milestones"], ["control", "Controls"], ["run", "Run lifecycle"], ["preflight", "Preflight"]].map(([v, l]) => h("option", { value: v, selected: v === f.kind }, l))),
        h("input", { class: "input input-sm", style: { width: "110px" }, placeholder: "Chunk #", value: f.chunk, onchange: set("chunk") }),
        h("input", { class: "input input-sm", style: { width: "240px" }, placeholder: "Search messages", value: f.q, onchange: set("q") }),
        h("label", { class: "check small" }, h("input", { type: "checkbox", checked: S.autoscroll, onchange: (e) => (S.autoscroll = e.target.checked) }), "Follow"),
        h("span", { class: "spacer" }), older),
      logBox);
    logBox.scrollTop = logBox.scrollHeight;
  }

  // Errors
  async function tabErrors() {
    logBox = null;
    const { errors } = await api.get(`/api/runs/${id}/errors`);
    if (!errors.length) { clear(tabBody, h("div", { class: "card empty" }, h("h3", {}, "No errors or warnings"), "Every model call and chunk of this run succeeded so far.")); return; }
    const groups = new Map();
    for (const e of errors) { if (!groups.has(e.code)) groups.set(e.code, []); groups.get(e.code).push(e); }
    clear(tabBody,
      h("p", { class: "muted small mb-16" }, "Warning = handled automatically (retried or skipped, the run went on) · Error = work was lost (a chunk or recipe) · Fatal = the run could not continue."),
      h("div", { class: "col" }, [...groups.entries()].map(([code, list]) => {
        const e0 = list[0];
        return h("div", { class: "card" },
          h("div", { class: "card-head" }, h("div", { class: "row" }, h("span", { class: "badge dark mono" }, code), severityBadge(e0.severity), h("b", {}, e0.title), h("span", { class: "faint" }, `×${list.length}`)), h("span", { class: "faint small" }, `last ${fmt.time(list[0].ts)}`)),
          h("div", { class: "card-body col" },
            h("dl", { class: "kv" }, h("dt", {}, "What happened"), h("dd", {}, e0.message), h("dt", {}, "Why"), h("dd", {}, e0.cause || "—"), h("dt", {}, "Fix"), h("dd", {}, h("b", {}, e0.fix || "—"))),
            h("details", {}, h("summary", { class: "small muted" }, `${list.length} occurrence(s)`),
              h("div", { class: "table-wrap mt-8" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, ["Time", "Chunk", "Stage", "Message", ""].map((t) => h("th", {}, t)))),
                h("tbody", {}, list.map((e) => h("tr", {}, h("td", { class: "nowrap" }, fmt.time(e.ts)), h("td", {}, e.chunk ?? "—"), h("td", {}, e.stage || "—"), h("td", { class: "small", style: { maxWidth: "520px", overflowWrap: "anywhere" } }, e.message.slice(0, 400)),
                  h("td", { class: "nowrap" },
                    e.api_call_id ? h("button", { class: "btn btn-xs", onclick: () => callDrawer(e.api_call_id) }, "API call") : null, " ",
                    e.traceback ? h("button", { class: "btn btn-xs", onclick: () => openDrawer(`${e.code} traceback`, h("pre", { class: "block" }, e.traceback)) }, "Traceback") : null, " ",
                    h("button", { class: "btn btn-xs", onclick: () => resolve(e) }, e.resolved ? "Reopen" : "Resolve"),
                    e.resolution_note ? h("div", { class: "faint small" }, e.resolution_note) : null)))))))));
      })));
  }
  async function resolve(e) {
    const note = e.resolved ? null : window.prompt("How was it fixed? (optional note)", e.resolution_note || "");
    if (note === null && !e.resolved) return;
    await api.patch(`/api/errors/${e.id}`, { resolved: !e.resolved, note });
    tabErrors();
  }

  // API calls
  function callsTable(calls) {
    if (!calls.length) return h("div", { class: "empty" }, "No API calls.");
    return h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, ["Time", "Chunk", "Stage", "Kind", "Model", "Status", "Duration", "Tokens in/out", "Finish"].map((t) => h("th", { class: ["Duration", "Tokens in/out"].includes(t) ? "num" : "" }, t)))),
      h("tbody", {}, calls.map((c) => h("tr", { class: "click", onclick: () => callDrawer(c.id) },
        h("td", { class: "nowrap" }, fmt.time(c.ts)), h("td", {}, c.chunk ?? "—"), h("td", {}, c.stage || "—"), h("td", {}, c.kind), h("td", { class: "small" }, c.model),
        h("td", {}, h("span", { class: `badge ${c.ok ? "green" : c.status_code >= 500 || c.status_code === 429 ? "amber" : "red"}` }, c.status_code || c.status || (c.ok ? "200" : "error"))),
        h("td", { class: "num" }, fmt.ms(c.duration_ms)), h("td", { class: "num" }, `${fmt.num(c.prompt_tokens)} / ${fmt.num(c.output_tokens)}`), h("td", { class: "small" }, c.finish_reason || (c.ok ? "" : (c.error || "").slice(0, 60))))))));
  }
  async function callDrawer(callId) {
    const c = await api.get(`/api/calls/${callId}`);
    openDrawer(`API call #${c.id} · ${c.kind} · ${c.ok ? "OK" : "FAILED"}`, [
      h("dl", { class: "kv" }, ...[["Model", c.model], ["Chunk", c.chunk ?? "—"], ["Stage", c.stage || "—"], ["HTTP status", `${c.status_code ?? "—"} ${c.status || ""}`], ["Duration", fmt.ms(c.duration_ms)], ["Tokens", `prompt ${fmt.num(c.prompt_tokens)} · output ${fmt.num(c.output_tokens)} · thinking ${fmt.num(c.thought_tokens)} · total ${fmt.num(c.total_tokens)}`], ["Finish reason", c.finish_reason || "—"], ["Prompt / reply size", `${fmt.num(c.prompt_chars)} / ${fmt.num(c.response_chars)} chars`]].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      c.error ? h("div", { class: "alert red" }, icon("alert"), h("div", { class: "mono small" }, c.error)) : null,
      h("h3", {}, "Prompt"), h("pre", { class: "block" }, c.prompt_text ?? "(not stored — RP_STORE_PROMPTS is off)"),
      h("h3", {}, "Reply"), h("pre", { class: "block" }, c.response_text ?? (c.ok ? "(not stored)" : "(no reply)"))]);
  }
  async function tabCalls() {
    logBox = null;
    const sel = tabBody.querySelector("select[data-f=ok]")?.value ?? "";
    const stage = tabBody.querySelector("select[data-f=stage]")?.value ?? "";
    const qs = new URLSearchParams({ limit: "1000" });
    if (sel !== "") qs.set("ok", sel);
    if (stage) qs.set("stage", stage);
    const { calls } = await api.get(`/api/runs/${id}/calls?${qs}`);
    clear(tabBody,
      h("div", { class: "row mb-16" },
        h("select", { class: "input input-sm", style: { width: "150px" }, "data-f": "ok", onchange: tabCalls }, [["", "All calls"], ["1", "Succeeded"], ["0", "Failed"]].map(([v, l]) => h("option", { value: v, selected: v === sel }, l))),
        h("select", { class: "input input-sm", style: { width: "170px" }, "data-f": "stage", onchange: tabCalls }, [["", "All stages"], ...STAGES.map((s) => [s, s.toLowerCase()])].map(([v, l]) => h("option", { value: v, selected: v === stage }, l))),
        h("span", { class: "faint small" }, `${calls.length} shown (newest first)`)),
      h("div", { class: "card" }, callsTable(calls)));
  }

  // Recipes
  async function tabRecipes() {
    logBox = null;
    const only = tabBody.querySelector("input[data-f=review]")?.checked || false;
    const { recipes } = await api.get(`/api/runs/${id}/recipes${only ? "?needs_review=1" : ""}`);
    const needs = recipes.filter((r) => r.needs_review).length;
    clear(tabBody,
      h("div", { class: "row mb-16" }, h("label", { class: "check" }, h("input", { type: "checkbox", "data-f": "review", checked: only, onchange: tabRecipes }), "Only recipes that need review"), h("span", { class: "faint small" }, `${recipes.length} shown${only ? "" : ` · ${needs} need review`}`)),
      recipes.length ? h("div", { class: "card" }, h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["#", "Recipe", "Improvements", "Categories", "Chunk", "Embedding"].map((t) => h("th", {}, t)))),
        h("tbody", {}, recipes.map((r) => {
          const kinds = {}; for (const n of r.review) kinds[n.kind] = (kinds[n.kind] || 0) + 1;
          return h("tr", { class: "click", onclick: () => recipeDrawer(r.id) },
            h("td", { class: "faint" }, r.position), h("td", {}, h("b", {}, r.title)),
            h("td", {}, h("div", { class: "row wrap", style: { gap: "4px" } }, kinds.needs_review ? h("span", { class: "badge amber" }, `needs review ${kinds.needs_review}`) : null, kinds.note ? h("span", { class: "badge blue" }, `notes ${kinds.note}`) : null, kinds.diet ? h("span", { class: "badge green" }, `diet ${kinds.diet}`) : null, !r.review.length ? h("span", { class: "faint small" }, "—") : null)),
            h("td", { class: "small muted" }, r.categories.join(", ") || "—"), h("td", {}, r.chunk ?? "—"), h("td", { class: "small mono" }, r.embedding ? `${r.embedding.dims}d · ‖v‖ ${r.embedding.norm}` : "—"));
        }))))) : h("div", { class: "card empty" }, h("h3", {}, "No recipes yet"), "Recipes appear here as soon as each one is assembled."));
  }
  async function recipeDrawer(recipeId) {
    const r = await api.get(`/api/recipes/${recipeId}`);
    const d = r.data || {};
    const groups = [["needs_review", "Needs review"], ["note", "Notes"], ["diet", "Diet"]];
    const needs = r.review.some((n) => n.kind === "needs_review");
    const review = h("div", { class: "review" },
      h("div", { class: `review-head ${needs ? "needs" : ""}` }, icon(needs ? "alert" : "check"), needs ? "Needs review" : "Improvements"),
      r.review.length ? groups.map(([k, label]) => { const items = r.review.filter((n) => n.kind === k); return items.length ? [h("div", { class: "small faint", style: { padding: "8px 14px 0" } }, label), h("ul", {}, items.map((n) => h("li", { class: k }, n.text)))] : null; }) : h("div", { class: "muted small", style: { padding: "12px 14px" } }, "No review notes: the refiner found nothing to flag."));
    const ings = (d.structured_ingredients || []).map((i) => i.fallback_string);
    const steps = d.direction_steps?.length ? d.direction_steps : (d.tokenized_directions || []).map((t) => t.text);
    const e = r.embedding;
    openDrawer(r.title, [
      review,
      h("div", { class: "grid cols-2" },
        h("div", {}, h("h3", { class: "mb-16" }, `Ingredients (${(d.ingredient_lines?.length ? d.ingredient_lines : ings).length})`), h("ul", { style: { margin: 0, paddingLeft: "18px" } }, (d.ingredient_lines?.length ? d.ingredient_lines : ings).map((l) => h("li", {}, l)))),
        h("div", {}, h("h3", { class: "mb-16" }, "Details"), h("dl", { class: "kv" }, ...[["Servings", d.base_servings ?? d.servings_note ?? "—"], ["Prep", d.prep_time || "—"], ["Cook", d.cook_time || "—"], ["Source", d.source || d.source_title || "—"], ["Measures", d.source_uom_system_detected ? `${d.source_uom_system_detected} (“${d.source_uom_system_evidence}”)` : "—"], ["Categories", r.categories.join(", ") || "—"], ["Chunk", r.chunk ?? "—"]].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])))),
      h("div", {}, h("h3", { class: "mb-16" }, `Directions (${steps.length})`), h("ol", { style: { margin: 0, paddingLeft: "20px" }, class: "col" }, steps.map((s) => h("li", {}, s)))),
      h("div", {}, h("h3", { class: "mb-16" }, "Embedding"), e ? h("dl", { class: "kv" }, ...[["Model", e.model], ["Dimensions", e.dims], ["L2 norm", e.norm], ["Mean", e.mean], ["Min / max", `${e.min} / ${e.max}`], ["Zero values", e.zeros], ["First 12 values", h("span", { class: "mono small" }, (e.head || []).join(", "))]].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])) : h("p", { class: "muted" }, "No embedding recorded.")),
      h("div", {}, h("h3", { class: "mb-16" }, "API calls for this chunk"), callsTable(r.calls.map((c) => ({ ...c, ts: null })))),
      h("details", {}, h("summary", { class: "small muted" }, "Raw recipe JSON"), h("pre", { class: "block mt-8" }, JSON.stringify(d, null, 2)))]);
  }

  // Analytics
  async function tabAnalytics() {
    logBox = null;
    const m = await api.get(`/api/runs/${id}/analytics`);
    const card = (label, value, hint) => h("div", { class: "card stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), hint ? h("div", { class: "hint" }, hint) : null);
    const stageRows = Object.entries(m.api.by_stage);
    clear(tabBody,
      h("div", { class: "grid cols-4" },
        card("Duration", fmt.dur(m.duration_secs)), card("Recipes", fmt.num(m.recipes.total), `${fmt.num(m.recipes.per_minute, 1)}/min · ${fmt.num(m.recipes.per_chunk, 2)}/chunk`),
        card("Need review", fmt.num(m.recipes.needs_review), Object.entries(m.recipes.review_notes).map(([k, v]) => `${k} ${v}`).join(" · ") || "no notes"),
        card("API calls", fmt.num(m.api.calls), `${fmt.num(m.api.failed)} failed (${fmt.pct(m.api.failure_rate * 100, 1)})`),
        card("Latency", fmt.ms(m.api.avg_ms), `p50 ${fmt.ms(m.api.p50_ms)} · p95 ${fmt.ms(m.api.p95_ms)} · max ${fmt.ms(m.api.max_ms)}`),
        card("Requests/min", fmt.num(m.api.avg_rpm, 1), `peak ${fmt.num(m.api.peak_rpm)} · limit ${fmt.num(m.options.rpm)}`),
        card("Tokens", fmt.num(m.tokens.total), `in ${fmt.num(m.tokens.prompt)} · out ${fmt.num(m.tokens.output)} · thinking ${fmt.num(m.tokens.thought)}`),
        card("Est. cost", m.cost_usd === null ? "—" : fmt.usd(m.cost_usd), m.cost_usd === null ? "set prices in Settings → Analytics" : null)),
      h("div", { class: "card mt-16" }, h("div", { class: "card-head" }, h("h3", {}, "Per minute"), h("span", { class: "legend" }, h("span", { style: { "--c": "var(--text)" } }, "calls ok"), h("span", { style: { "--c": "var(--danger)" } }, "calls failed"), h("span", { style: { "--c": "var(--accent)" } }, "recipes"))),
        h("div", { class: "card-body" }, m.timeline.length ? h("div", { class: "grid cols-2" }, barChart(m.timeline.map((t) => ({ label: `${t.minute}m`, ...t })), [{ key: "ok", cls: "bar-ok" }, { key: "failed", cls: "bar-failed" }]), barChart(m.timeline.map((t) => ({ label: `${t.minute}m`, ...t })), [{ key: "recipes", cls: "bar-recipes" }])) : h("div", { class: "empty" }, "No calls yet."))),
      h("div", { class: "grid cols-2 mt-16" },
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "By stage")), h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, ["Stage", "Calls", "Failed", "Avg", "p95", "Tokens in", "Tokens out"].map((t, i) => h("th", { class: i ? "num" : "" }, t)))), h("tbody", {}, stageRows.map(([s, v]) => h("tr", {}, h("td", {}, s), h("td", { class: "num" }, v.calls), h("td", { class: "num" }, v.failed), h("td", { class: "num" }, fmt.ms(v.avg_ms)), h("td", { class: "num" }, fmt.ms(v.p95_ms)), h("td", { class: "num" }, fmt.num(v.prompt_tokens)), h("td", { class: "num" }, fmt.num(v.output_tokens)))))))),
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "HTTP status codes")), h("div", { class: "card-body" }, hbars(Object.entries(m.api.status_codes)), Object.keys(m.api.finish_reasons).length ? [h("div", { class: "small muted mt-16" }, "Finish reasons"), hbars(Object.entries(m.api.finish_reasons))] : null)),
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Errors by code"), h("span", { class: "faint small" }, `${m.errors.errors} errors · ${m.errors.warnings} warnings · ${m.errors.unresolved} unresolved`)), h("div", { class: "card-body" }, Object.keys(m.errors.by_code).length ? hbars(Object.entries(m.errors.by_code)) : h("div", { class: "muted" }, "None."))),
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Chunks")), h("div", { class: "card-body" }, hbars(Object.entries(m.chunks.states)))),
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Embeddings")), h("div", { class: "card-body" }, h("dl", { class: "kv" }, ...[["Model", m.embed_model], ["Vectors", m.embeddings.count], ["Dimensions", Object.entries(m.embeddings.dims).map(([d, n]) => `${d} (×${n})`).join(", ") || "—"], ["Norm min / avg / max", `${m.embeddings.norm_min ?? "—"} / ${m.embeddings.norm_avg ?? "—"} / ${m.embeddings.norm_max ?? "—"}`]].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])))),
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Categories")), h("div", { class: "card-body" }, Object.keys(m.recipes.categories).length ? hbars(Object.entries(m.recipes.categories)) : h("div", { class: "muted" }, "No tags.")))));
  }

  // Configuration
  async function tabConfig() {
    logBox = null;
    const r = S.detail.run;
    const settings = Object.entries(r.settings || {});
    const onlyChanged = tabBody.querySelector("input[data-f=changed]")?.checked ?? true;
    const filter = tabBody.querySelector("input[data-f=q]")?.value || "";
    const rows = settings.filter(([k, v]) => (!onlyChanged || v.source !== "default") && (!filter || k.toLowerCase().includes(filter.toLowerCase())));
    const prompts = Object.keys(r.prompts || {});
    let defaults = {};
    if (prompts.length) { const p = await api.get("/api/prompts", { quiet: true }); defaults = Object.fromEntries(p.prompts.map((x) => [x.name, x.default])); }
    clear(tabBody,
      h("div", { class: "grid cols-2" },
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Run options")), h("div", { class: "card-body" }, h("dl", { class: "kv" }, h("dt", {}, "Book"), h("dd", { class: "mono small" }, r.book_path), ...Object.entries(r.options || {}).flatMap(([k, v]) => [h("dt", {}, k), h("dd", { class: "mono small" }, typeof v === "object" ? JSON.stringify(v) : String(v))])))),
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Edited prompts used")), h("div", { class: "card-body col" }, prompts.length ? prompts.map((n) => h("details", {}, h("summary", {}, h("b", {}, n), h("span", { class: "faint small" }, " — diff against the built-in prompt")), h("div", { class: "mt-8" }, diffView(defaults[n] || "", r.prompts[n])))) : h("div", { class: "muted" }, "None: this run used the built-in prompts.")))),
      h("div", { class: "card mt-16" },
        h("div", { class: "card-head" }, h("h3", {}, `Settings snapshot (${rows.length} of ${settings.length})`), h("div", { class: "row" }, h("input", { class: "input input-sm", "data-f": "q", placeholder: "Filter keys", value: filter, style: { width: "200px" }, onchange: tabConfig }), h("label", { class: "check small" }, h("input", { type: "checkbox", "data-f": "changed", checked: onlyChanged, onchange: tabConfig }), "Only non-default"))),
        h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", {}, "Key"), h("th", {}, "Value"), h("th", {}, "Source"))), h("tbody", {}, rows.map(([k, v]) => h("tr", {}, h("td", { class: "mono small" }, k), h("td", { class: "mono small", style: { overflowWrap: "anywhere" } }, v.value ?? "—"), h("td", {}, h("span", { class: `badge ${v.source === "run" ? "purple" : v.source === "saved" ? "blue" : v.source === "default" ? "" : "amber"}` }, v.source)))))))));
  }

  // Raw log
  async function tabRaw() {
    logBox = null;
    const text = await api.get(`/api/runs/${id}/log?lines=3000`).catch(() => "");
    const pre = h("pre", { class: "block", style: { maxHeight: "640px" } }, typeof text === "string" ? text || "(empty)" : JSON.stringify(text));
    clear(tabBody, h("div", { class: "row mb-16" }, h("span", { class: "muted small" }, "The run's log file, exactly as written (last 3000 lines)."), h("span", { class: "spacer" }), h("button", { class: "btn btn-sm", onclick: tabRaw }, icon("restart"), "Refresh"), h("a", { class: "btn btn-sm", href: `/api/runs/${id}/files/run.log` }, icon("download"), "Download")), pre);
    pre.scrollTop = pre.scrollHeight;
  }

  return () => { offs.forEach((f) => f()); timers.forEach(clearInterval); };
}

function throttle(fn, ms) {
  let last = 0, timer = null;
  return () => {
    const now = Date.now();
    if (now - last >= ms) { last = now; fn(); return; }
    if (!timer) timer = setTimeout(() => { timer = null; last = Date.now(); fn(); }, ms - (now - last));
  };
}
