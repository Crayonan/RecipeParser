// App shell: hash router, sidebar, live connection indicator, notifications.
//
// View contract (static/js/views/*.js):
//   export async function render(root, params, query) -> optional cleanup function
// `root` is an empty element to fill; `params` holds route parameters (e.g. {id});
// `query` is a URLSearchParams of the hash's query string. The cleanup function is
// called when the user navigates away (unsubscribe from live events, clear timers).
import { h, clear, icon, toast, fmt } from "./ui.js";
import { live } from "./live.js";
import { api } from "./api.js";

const ROUTES = [
  { re: /^\/$/, view: "dashboard", title: "Runs", nav: "runs" },
  { re: /^\/new$/, view: "newrun", title: "New run", nav: "new" },
  { re: /^\/recipes$/, view: "recipes", title: "Recipes", nav: "recipes" },
  { re: /^\/recipes\/([^/]+)$/, keys: ["id"], view: "recipe", title: "Recipe", nav: "recipes" },
  { re: /^\/runs\/([^/]+)$/, keys: ["id"], view: "run", title: "Run", nav: "runs" },
  { re: /^\/compare$/, view: "compare", title: "Compare runs", nav: "compare" },
  { re: /^\/errors$/, view: "errors", title: "Errors & alerts", nav: "errors" },
  { re: /^\/prompts$/, view: "prompts", title: "Prompts", nav: "prompts" },
  { re: /^\/settings$/, view: "settings", title: "Settings", nav: "settings" },
];

const NAV = [
  { id: "runs", href: "#/", label: "Runs", icon: "home" },
  { id: "new", href: "#/new", label: "New run", icon: "plus" },
  { id: "recipes", href: "#/recipes", label: "Recipes", icon: "utensils" },
  { id: "compare", href: "#/compare", label: "Compare", icon: "compare" },
  { id: "errors", href: "#/errors", label: "Errors & alerts", icon: "alert" },
  { id: "prompts", href: "#/prompts", label: "Prompts", icon: "prompt" },
  { id: "settings", href: "#/settings", label: "Settings", icon: "settings" },
];

let cleanup = null;
let activeRuns = 0;
let unresolvedErrors = 0;

function renderNav(current) {
  clear(document.getElementById("nav"), NAV.map((n) => {
    const count = n.id === "runs" && activeRuns ? h("span", { class: "count live", title: "Active runs" }, activeRuns)
      : n.id === "errors" && unresolvedErrors ? h("span", { class: "count", title: "Unresolved errors" }, unresolvedErrors) : null;
    return h("a", { href: n.href, class: n.id === current ? "active" : "" }, icon(n.icon), n.label, count);
  }));
}

let currentNav = "runs";

async function route() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, qs] = raw.split("?");
  const query = new URLSearchParams(qs || "");
  let match = null, params = {};
  for (const r of ROUTES) {
    const m = path.match(r.re);
    if (m) { match = r; (r.keys || []).forEach((k, i) => (params[k] = decodeURIComponent(m[i + 1]))); break; }
  }
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
  const view = document.getElementById("view");
  if (!match) { clear(view, h("div", { class: "page empty" }, h("h3", {}, "Page not found"), h("a", { class: "link", href: "#/" }, "Back to runs"))); return; }
  currentNav = match.nav;
  renderNav(currentNav);
  document.getElementById("topbar-title").textContent = match.title;
  document.title = `${match.title} · RecipeParser`;
  const root = h("div", { class: "page" });
  clear(view, root);
  view.scrollTop = 0;
  try {
    const mod = await import(`./views/${match.view}.js`);
    cleanup = (await mod.render(root, params, query)) || null;
  } catch (e) {
    console.error(e);
    clear(root, h("div", { class: "alert red" }, icon("alert"), h("div", {}, h("div", { class: "alert-title" }, "This page failed to load"), h("div", { class: "mono small" }, String(e.stack || e)))));
  }
}

// ── notifications ───────────────────────────────────────────────────────────
const NOTIFY = {
  "run.finished": (e) => [e.data?.status === "completed" || e.data?.status === "limit_reached" ? "green" : "amber", "Run finished"],
  "run.failed": () => ["red", "Run failed"],
  "run.crashed": () => ["red", "Run crashed"],
  "run.killed": () => ["red", "Run killed"],
  "run.paused": () => ["amber", "Run paused"],
  "run.resumed": () => ["blue", "Run resumed"],
  "run.unresponsive": () => ["red", "Run unresponsive"],
  "limit.reached": () => ["blue", "Recipe limit reached"],
  "chunk.failed": () => ["red", "Chunk failed"],
};
const store = JSON.parse(localStorage.getItem("rp.notifications") || "[]");
let unread = Number(localStorage.getItem("rp.unread") || 0);
let lastErrorToast = 0;

function saveNotifs() {
  localStorage.setItem("rp.notifications", JSON.stringify(store.slice(0, 150)));
  localStorage.setItem("rp.unread", String(unread));
  renderBell();
}

function renderBell() {
  clear(document.getElementById("bell"), icon("bell"), unread ? h("span", { class: "dot" }, unread > 99 ? "99+" : unread) : null);
}

function notify(e) {
  let spec = NOTIFY[e.kind];
  if (!spec && (e.level === "ERROR" || e.level === "CRITICAL") && e.kind === "log") spec = () => ["red", "Error"];
  if (!spec) return;
  const [color, title] = spec(e);
  store.unshift({ id: e.id, run_id: e.run_id, ts: e.ts, color, title, message: e.message, chunk: e.chunk });
  unread++;
  saveNotifs();
  const noisy = e.kind === "chunk.failed" || e.kind === "log";
  if (noisy) { if (Date.now() - lastErrorToast < 4000) return; lastErrorToast = Date.now(); }
  toast(`${title} · ${e.run_id}`, e.message, color, { timeout: color === "red" ? 12000 : 6000, onClick: () => (location.hash = `#/runs/${e.run_id}`) });
}

function toggleNotifPanel() {
  const panel = document.getElementById("notif-panel");
  if (!panel.hidden) { panel.hidden = true; return; }
  unread = 0; saveNotifs();
  clear(panel,
    h("div", { class: "card-head" }, h("h3", {}, "Notifications"), h("button", { class: "btn btn-xs", onclick: () => { store.length = 0; saveNotifs(); panel.hidden = true; } }, "Clear")),
    store.length ? store.map((n) => h("div", { class: "notif", onclick: () => { panel.hidden = true; location.hash = `#/runs/${n.run_id}`; } },
      h("span", { class: `badge ${n.color}` }, h("span", { class: "dot" })),
      h("div", { style: { minWidth: 0 } }, h("div", { style: { fontWeight: 600 } }, n.title, h("span", { class: "faint small" }, ` · ${fmt.ago(n.ts)}`)), h("div", { class: "small muted", style: { overflowWrap: "anywhere" } }, n.message), h("div", { class: "faint small mono" }, n.run_id + (n.chunk != null ? ` · chunk ${n.chunk}` : "")))))
      : h("div", { class: "empty" }, "Nothing yet. Run outcomes, failures, pauses and crashes appear here."));
  panel.hidden = false;
}

async function refreshCounts() {
  try {
    const [errs, runs] = await Promise.all([api.get("/api/errors?unresolved=1&limit=1", { quiet: true }), api.get("/api/runs?limit=50", { quiet: true })]);
    unresolvedErrors = (errs.summary || []).reduce((a, s) => a + (s.unresolved || 0), 0);
    activeRuns = runs.runs.filter((r) => r.active).length;
    renderNav(currentNav);
  } catch { /* server down: the connection dot says so */ }
}

function init() {
  document.getElementById("bell").onclick = toggleNotifPanel;
  renderBell();
  live.on("status", (ok) => {
    const c = document.getElementById("conn");
    c.className = `conn ${ok ? "on" : "off"}`;
    c.title = ok ? "Live: connected to the server" : "Disconnected from the server — reconnecting…";
  });
  live.on("events", (events) => {
    let refresh = false;
    for (const e of events) {
      notify(e);
      if (e.kind.startsWith("run.") || e.level === "ERROR" || e.level === "CRITICAL" || e.kind === "chunk.failed") refresh = true;
    }
    if (refresh) refreshCounts();
  });
  live.on("runs", (runs) => {
    const n = runs.filter((r) => r.active).length;
    if (n !== activeRuns) { activeRuns = n; renderNav(currentNav); }
  });
  live.start();
  api.get("/api/meta", { quiet: true }).then((m) => clear(document.getElementById("sidebar-foot"), h("div", {}, `v${m.version}`), h("div", { class: "ellipsis", title: m.data_dir }, m.data_dir))).catch(() => {});
  refreshCounts();
  window.addEventListener("hashchange", route);
  route();
}

init();
