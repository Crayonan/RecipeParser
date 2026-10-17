// Shared UI helpers: DOM building, formatting, icons, toasts, drawer, menus.
// Every view imports from here; no framework.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") el.innerHTML = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) { el.replaceChildren(); append(el, children); return el; }

const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
  play: '<path d="M7 4.5v15l13-7.5z"/>',
  pause: '<path d="M7 4h4v16H7zM13 4h4v16h-4z"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  kill: '<path d="M12 2v10M5.6 6.3a8 8 0 1 0 12.8 0"/>',
  restart: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  compare: '<path d="M8 3v18M16 3v18M3 8h5M16 16h5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  prompt: '<path d="M4 5h16v11H8l-4 4z"/><path d="M8 9h8M8 12h5"/>',
  alert: '<path d="M12 3 2 20h20zM12 10v4M12 17h.01"/>',
  bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  check: '<path d="m5 12 5 5L20 7"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
  flag: '<path d="M4 21V4h12l-2 4 2 4H4"/>',
  book: '<path d="M4 4h10a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4zM18 20H8"/>',
  upload: '<path d="M12 16V4M6 10l6-6 6 6M4 20h16"/>',
  download: '<path d="M12 4v12M6 10l6 6 6-6M4 20h16"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  dots: '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  leaf: '<path d="M5 19c8 0 14-6 14-15C10 4 5 9 5 19zM5 19l7-7"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  utensils: '<path d="M18 2v6a3 3 0 0 1-3 3 3 3 0 0 1-3-3V2M15 11v11M5 2v4a3 3 0 0 0 3 3 3 3 0 0 0 3-3V2M8 9v13"/>',
  sparkles: '<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3z"/>',
  image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
};

export function icon(name, cls = "icon") {
  const span = document.createElement("span");
  span.style.display = "inline-flex";
  span.innerHTML = `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ICONS.info}</svg>`;
  return span;
}

// ── formatting ──────────────────────────────────────────────────────────────
export const fmt = {
  num(n, d = 0) { if (n === null || n === undefined || Number.isNaN(n)) return "—"; return Number(n).toLocaleString(undefined, { maximumFractionDigits: d }); },
  pct(n, d = 0) { return n === null || n === undefined ? "—" : `${Number(n).toFixed(d)}%`; },
  ms(n) { if (n === null || n === undefined) return "—"; return n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`; },
  dur(secs) {
    if (secs === null || secs === undefined) return "—";
    secs = Math.round(secs);
    if (secs < 60) return `${secs}s`;
    const m = Math.floor(secs / 60), s = secs % 60;
    if (m < 60) return `${m}m ${String(s).padStart(2, "0")}s`;
    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
  },
  time(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"; },
  datetime(ts) { return ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"; },
  ago(ts) {
    if (!ts) return "—";
    const s = Date.now() / 1000 - ts;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return fmt.datetime(ts);
  },
  bytes(n) { if (!n && n !== 0) return "—"; const u = ["B", "KB", "MB", "GB"]; let i = 0; while (n >= 1024 && i < 3) { n /= 1024; i++; } return `${n.toFixed(i ? 1 : 0)} ${u[i]}`; },
  usd(n) { return n === null || n === undefined ? "—" : `$${Number(n).toFixed(n < 1 ? 4 : 2)}`; },
};

// ── status ──────────────────────────────────────────────────────────────────
export const STATUS = {
  queued: { label: "Queued", color: "", live: true },
  starting: { label: "Starting", color: "blue", live: true },
  running: { label: "Running", color: "blue", live: true },
  pausing: { label: "Pausing…", color: "amber", live: true },
  paused: { label: "Paused", color: "amber", live: false },
  stopping: { label: "Stopping…", color: "amber", live: true },
  completed: { label: "Completed", color: "green" },
  completed_with_errors: { label: "Completed with errors", color: "amber" },
  limit_reached: { label: "Limit reached", color: "green" },
  cancelled: { label: "Stopped", color: "amber" },
  failed: { label: "Failed", color: "red" },
  killed: { label: "Killed", color: "red" },
  crashed: { label: "Crashed", color: "red" },
};

export function statusBadge(status) {
  const s = STATUS[status] || { label: status, color: "" };
  return h("span", { class: `badge ${s.color} ${s.live ? "pulse" : ""}` }, h("span", { class: "dot" }), s.label);
}

export function levelColor(level) {
  return { DEBUG: "", INFO: "blue", WARNING: "amber", ERROR: "red", CRITICAL: "red" }[level] || "";
}

export function severityBadge(sev) {
  const c = { warning: "amber", error: "red", fatal: "dark" }[sev] || "";
  return h("span", { class: `badge ${c}` }, sev);
}

// ── toasts ──────────────────────────────────────────────────────────────────
export function toast(title, body = "", color = "", { timeout = 6000, onClick } = {}) {
  const box = document.getElementById("toasts");
  const ic = { green: "check", red: "alert", amber: "alert", blue: "info" }[color] || "info";
  const el = h("div", { class: `toast ${color}`, onclick: () => { onClick?.(); el.remove(); } },
    icon(ic),
    h("div", { style: { flex: 1, minWidth: 0 } }, h("div", { class: "t-title" }, title), body ? h("div", { class: "t-body" }, body) : null),
    h("button", { class: "icon-btn", style: { width: "22px", height: "22px" }, onclick: (e) => { e.stopPropagation(); el.remove(); } }, icon("x")));
  box.append(el);
  while (box.children.length > 5) box.firstChild.remove();
  if (timeout) setTimeout(() => el.remove(), timeout);
  return el;
}

// ── drawer ──────────────────────────────────────────────────────────────────
export function openDrawer(title, body, actions = null) {
  const drawer = document.getElementById("drawer");
  const back = document.getElementById("drawer-backdrop");
  clear(drawer,
    h("div", { class: "drawer-head" }, h("h2", { class: "ellipsis" }, title), h("div", { class: "row" }, actions, h("button", { class: "icon-btn", onclick: closeDrawer, title: "Close (Esc)" }, icon("x")))),
    h("div", { class: "drawer-body" }, body));
  drawer.hidden = false; back.hidden = false;
  back.onclick = closeDrawer;
}
export function closeDrawer() {
  document.getElementById("drawer").hidden = true;
  document.getElementById("drawer-backdrop").hidden = true;
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ── menu ────────────────────────────────────────────────────────────────────
export function popMenu(anchor, items) {
  document.querySelectorAll(".menu").forEach((m) => m.remove());
  const r = anchor.getBoundingClientRect();
  const menu = h("div", { class: "menu", style: { top: `${r.bottom + window.scrollY + 6}px`, left: `${Math.max(8, r.right - 280)}px`, position: "fixed" } },
    items.map((it) => h("button", { disabled: it.disabled, onclick: () => { menu.remove(); it.onClick(); } }, h("span", {}, it.label), it.hint ? h("small", {}, it.hint) : null)));
  document.body.append(menu);
  setTimeout(() => document.addEventListener("click", function off(e) { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", off); } }), 0);
}

export function confirmDialog(message) { return window.confirm(message); }

// Small SVG bar chart: series = [{label, values:{key:n}}], keys=[{key, cls}]
export function barChart(series, keys, { height = 160 } = {}) {
  const w = Math.max(series.length * 14, 300);
  const max = Math.max(1, ...series.map((s) => keys.reduce((a, k) => a + (s[k.key] || 0), 0)));
  const bw = w / Math.max(series.length, 1);
  let bars = "";
  series.forEach((s, i) => {
    let y = height - 18;
    for (const k of keys) {
      const v = s[k.key] || 0; if (!v) continue;
      const bh = (v / max) * (height - 30);
      y -= bh;
      bars += `<rect class="${k.cls}" x="${i * bw + 1}" y="${y}" width="${Math.max(bw - 2, 1)}" height="${bh}" rx="1.5"><title>${s.label}: ${k.key} ${v}</title></rect>`;
    }
  });
  const ticks = series.length > 1 ? `<text x="0" y="${height - 4}">${series[0].label}</text><text x="${w}" y="${height - 4}" text-anchor="end">${series[series.length - 1].label}</text>` : "";
  const svg = `<svg class="chart" viewBox="0 0 ${w} ${height}" preserveAspectRatio="none">${bars}${ticks}<text x="${w}" y="10" text-anchor="end">max ${max}</text></svg>`;
  return h("div", { html: svg });
}

export function hbars(entries, { total } = {}) {
  const max = total || Math.max(1, ...entries.map(([, v]) => v));
  return h("div", {}, entries.map(([k, v]) => h("div", { class: "hbar" }, h("span", { class: "ellipsis", title: k }, k), h("div", { class: "track" }, h("div", { style: { width: `${(v / max) * 100}%` } })), h("span", { class: "num small" }, fmt.num(v)))));
}

// Line diff (LCS) for prompts.
export function lineDiff(a, b) {
  const x = a.split("\n"), y = b.split("\n");
  const n = x.length, m = y.length;
  if (n * m > 4_000_000) return [{ t: "del", s: a }, { t: "add", s: b }];
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = []; let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { out.push({ t: "eq", s: x[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ t: "del", s: x[i++] });
    else out.push({ t: "add", s: y[j++] });
  }
  while (i < n) out.push({ t: "del", s: x[i++] });
  while (j < m) out.push({ t: "add", s: y[j++] });
  return out;
}

export function diffView(a, b) {
  return h("div", { class: "diff" }, lineDiff(a, b).map((l) => h("div", { class: l.t === "eq" ? "" : l.t }, (l.t === "add" ? "+ " : l.t === "del" ? "- " : "  ") + l.s)));
}

export function debounce(fn, ms = 300) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
