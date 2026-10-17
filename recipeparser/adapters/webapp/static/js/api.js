// JSON API client. Every failure becomes an Error whose message is the server's explanation.
import { toast } from "./ui.js";

async function request(method, path, body, { quiet = false } = {}) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    const err = new Error(`The web app server is not reachable (${e.message}). Is it still running?`);
    if (!quiet) toast("Server not reachable", err.message, "red");
    throw err;
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const d = data && data.detail !== undefined ? data.detail : data;
    let msg = typeof d === "string" ? d : d?.message || `HTTP ${res.status}`;
    const problems = d && typeof d === "object" && d.problems ? d.problems : null;
    if (problems) msg += " " + (Array.isArray(problems) ? problems.join(" ") : Object.values(problems).join(" "));
    const err = new Error(msg);
    err.status = res.status; err.problems = problems;
    if (!quiet) toast(`Request failed (HTTP ${res.status})`, msg, "red", { timeout: 10000 });
    throw err;
  }
  return data;
}

export const api = {
  get: (p, o) => request("GET", p, undefined, o),
  post: (p, b, o) => request("POST", p, b ?? {}, o),
  put: (p, b, o) => request("PUT", p, b, o),
  patch: (p, b, o) => request("PATCH", p, b, o),
  del: (p, o) => request("DELETE", p, undefined, o),
  upload: (p, file) => { const f = new FormData(); f.append("file", file); return request("POST", p, f); },
};
