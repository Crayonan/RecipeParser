// One Server-Sent Events connection for the whole app.
//   live.on("events", (events) => ...)   every new event of every run, in order
//   live.on("runs", (runs) => ...)       active / just-finished runs, once a second
//   live.on("status", (connected) => ...)
const listeners = { events: new Set(), runs: new Set(), status: new Set() };
let source = null;
let lastId = null;
let retry = 1000;

function emit(name, payload) { for (const fn of listeners[name]) { try { fn(payload); } catch (e) { console.error(e); } } }

function connect() {
  const url = lastId === null ? "/api/stream" : `/api/stream?after=${lastId}`;
  source = new EventSource(url);
  source.addEventListener("hello", (e) => { const d = JSON.parse(e.data); if (lastId === null) lastId = d.after; retry = 1000; emit("status", true); });
  source.addEventListener("events", (e) => { const evs = JSON.parse(e.data); if (evs.length) lastId = evs[evs.length - 1].id; emit("events", evs); });
  source.addEventListener("runs", (e) => emit("runs", JSON.parse(e.data)));
  source.onerror = () => {
    emit("status", false);
    source.close();
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 10000);
  };
}

export const live = {
  start() { if (!source) connect(); },
  on(name, fn) { listeners[name].add(fn); return () => listeners[name].delete(fn); },
};
