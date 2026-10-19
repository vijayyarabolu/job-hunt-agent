// One EventSource per tab; closed while hidden.
import { q } from "./core.js";

const REOPEN_MS = 15000;
let es = null;
let last = null;
let reopenTimer = 0;
let handlers = { onRun: () => {}, onEdit: () => {} };

function seen(data, isHello) {
  const prev = last;
  last = data;
  if (isHello && !prev) return;
  if (!prev || data.run !== prev.run) handlers.onRun();
  if (!prev || data.edit !== prev.edit) handlers.onEdit();
}

function parse(e) {
  try { return JSON.parse(e.data); } catch { return null; }
}

function open() {
  if (es || document.hidden || !window.EventSource) return;
  es = new EventSource(`/api/events?${q({})}`);
  es.addEventListener("hello", (e) => { const d = parse(e); if (d) seen(d, true); });
  es.addEventListener("change", (e) => { const d = parse(e); if (d) seen(d, false); });
  es.addEventListener("error", () => {
    if (es?.readyState !== EventSource.CLOSED) return;
    es = null;
    clearTimeout(reopenTimer);
    reopenTimer = setTimeout(open, REOPEN_MS);
  });
}

function close() {
  clearTimeout(reopenTimer);
  es?.close();
  es = null;
}

export function startLive(given) {
  handlers = { ...handlers, ...given };
  document.addEventListener("visibilitychange", () => (document.hidden ? close() : open()));
  addEventListener("pagehide", close);
  open();
}

export function restartLive() {
  close();
  last = null;
  open();
}
