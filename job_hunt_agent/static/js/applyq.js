// "Apply on laptop". On the phone a job goes to the Apply queue from its panel ("On laptop") or by
// swiping its row left; on the laptop "Apply queue (N)" (top bar) lists them and "Open next 5" opens their apply
// pages in new tabs. With the Autofill extension (1.1+) the tabs are opened by it and each one knows its job; without
// it a page may open only one tab per click, so the first opens and the rest wait in the list.
import { $, B, S, api, fail, get, h, icon, phone, toast } from "./core.js";

const POLL_MS = 120000;
const SWIPE_PX = 90;
const SWIPE_SLOP_PX = 40;
const RELAY_MS = 4000;
let ext = null;                                        // the extension's version once its relay answered
let btn = null;
let dlg = null;

addEventListener("message", (e) => { if (e.source === window && e.data && e.data.type === "jh:ext") ext = e.data.version || "1.1"; });

const what = (job) => (job.outside ? { app: job.outside } : { uid: job.uid });

export async function add(job) {
  try {
    const r = await api("/api/applyq/add", what(job));
    const t = toast(`On the laptop's Apply queue (${r.count}) ✓`, { undo: () => api("/api/applyq/remove", { id: r.id }).then(paintCount).catch(fail) });
    t.dataset.queued = String(r.id);
    paintCount();
  } catch (err) { fail(err); }
}

// the phone: a row swiped left goes to the queue (vertical scrolling stays as it was)
function wireSwipe() {
  const list = $("#list");
  if (!list) return;
  let start = null;
  list.addEventListener("touchstart", (e) => {
    const row = e.target.closest("li.row[id^=job-]");
    start = row && e.touches.length === 1 ? { row, x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
  }, { passive: true });
  list.addEventListener("touchend", (e) => {
    if (!start || !phone.matches) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - start.x, dy = t.clientY - start.y;
    const job = S.items[Number(start.row.id.slice(4))];
    start = null;
    if (dx > -SWIPE_PX || Math.abs(dy) > SWIPE_SLOP_PX || !job) return;
    add(job);
  }, { passive: true });
}

async function paintCount() {
  if (!btn) return;
  try {
    const r = await get("/api/applyq");
    btn.hidden = !r.count;
    btn.querySelector(".status-text").textContent = `Apply queue (${r.count})`;
    if (dlg?.open) paintList(r.items);
  } catch { /* the status button says when the app is unwell */ }
}

function relay(items) {                                // the extension opens the tabs; null when it is not here
  if (!ext) return Promise.resolve(null);
  return new Promise((resolve) => {
    const done = (e) => {
      if (e.source !== window || !e.data || e.data.type !== "jh:opened") return;
      removeEventListener("message", done);
      clearTimeout(timer);
      resolve(e.data);
    };
    const timer = setTimeout(() => { removeEventListener("message", done); resolve(null); }, RELAY_MS);
    addEventListener("message", done);
    postMessage({ type: "jh:open", items: items.map(({ url, uid, app, company, title }) => ({ url, uid, app, company, title })) }, location.origin);
  });
}

async function openNext() {
  try {
    const { items } = await api("/api/applyq/open", { n: 5 });
    if (!items.length) return toast("Nothing in the queue has an apply page");
    const got = await relay(items);
    if (got && got.opened) toast(`Opened ${got.opened} apply page${got.opened === 1 ? "" : "s"} in new tabs: Autofill knows each one's job`);
    else {
      window.open(items[0].url, "_blank", "noopener");
      if (items.length > 1) toast("Opened the first one. Chrome lets a page open one tab per click: use Open on the others (with the Autofill extension 1.1+, Open next 5 opens all five)", { ms: 12000 });
    }
    paintCount();
  } catch (err) { fail(err); }
}

function row(it) {
  const act = (path, okText) => () => api(path, { id: it.id }).then(() => { toast(okText); paintCount(); }).catch(fail);
  return h("li", { class: "q-row" }, h("div", {}, h("strong", {}, it.company || "(company)"), ` · ${it.title}`,
    h("div", "muted small", it.opened_at ? "Opened before" : "Not opened yet")),
  h("div", "row-btns", it.url ? B({ class: "btn sm", title: "Open its apply page in a new tab", onclick: () => relay([it]).then((r) => { if (!r) window.open(it.url, "_blank", "noopener"); }) }, "Open") : null,
    B({ class: "btn sm", title: "Same as Mark applied: it leaves the queue", onclick: act("/api/applyq/applied", "Marked applied ✓") }, "Mark applied"),
    B({ class: "btn sm ghost", title: "Take it out of the queue", onclick: act("/api/applyq/remove", "Removed from the queue") }, "Remove")));
}

function paintList(items) {
  dlg.querySelector("#aq-h").textContent = `Apply queue (${items.length})`;
  dlg.querySelector(".q-list").replaceChildren(...(items.length ? items.map(row) : [h("li", "muted", "Empty. On the phone: On laptop in a job, or swipe its row left.")]));
}

async function show() {
  if (!dlg) {
    dlg = h("dialog", { class: "form-dialog", id: "applyq", "aria-labelledby": "aq-h" }, h("h2", { id: "aq-h" }, "Apply queue"),
      h("p", "muted small", "Jobs you sent from the phone. Open next 5 opens their apply pages in new tabs, each one matched to its job."),
      h("ul", { class: "q-list", style: "display:grid;gap:8px;max-height:60vh;overflow:auto;padding:0;list-style:none" }),
      h("div", "row-btns", B({ class: "btn ghost", onclick: () => dlg.close() }, "Close"), B({ class: "btn primary", onclick: openNext }, "Open next 5")));
    document.body.append(dlg);
  }
  dlg.showModal();
  try { paintList((await get("/api/applyq")).items); } catch (err) { fail(err); }
}

// the phone: "On laptop" next to Mark applied in an open job (added here, so the job panel's startup code stays as it was)
const isApplied = (job) => job.app && !["saved", "tailored"].includes(job.app.status);
function laptopButton() {
  const acts = $("#detail .acts");
  const job = S.detail?.uid && S.items.find((j) => j.uid === S.detail.uid);
  if (!acts || !job || isApplied(job) || acts.querySelector(".q-laptop")) return;
  const tip = "Apply on the laptop: it waits in the laptop's Apply queue";
  acts.querySelector(".acts-icons")?.before(B({ class: "lbl-btn q-laptop", "aria-label": tip, title: tip, onclick: () => add(job) },
    icon("sent"), h("span", "lbl-w", "On laptop")));
}

function initQueue() {
  import("./afhealth.js").then((m) => m.initNotices()).catch(() => {});   // a nightly-check drop, a newer extension
  wireSwipe();
  postMessage({ type: "jh:ping" }, location.origin);   // the extension's relay says it is here
  if (phone.matches) {                                 // the phone sends; the laptop's top bar shows the queue
    const pane = $("#detail");
    if (pane) new MutationObserver(laptopButton).observe(pane, { childList: true, subtree: true });
    return;
  }
  btn = B({ class: "status", id: "applyq-btn", hidden: true, title: "Jobs sent from the phone to apply on the laptop", onclick: show },
    h("span", "status-text", "Apply queue"));
  $("#status")?.before(btn);
  setInterval(() => { if (!document.hidden) paintCount(); }, POLL_MS);
  addEventListener("focus", paintCount);
}

// at start and on a person switch (with the Answers badge)
let started = false;
export function refresh() {
  if (!started) { started = true; initQueue(); }
  paintCount();
}
