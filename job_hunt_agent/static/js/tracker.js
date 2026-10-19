// Tracker: loading, toolbar, Move to…, phone cards; the desktop grid is grid.js (loaded with the tab).
import { $, $$, B, btn, OPEN_TIP, S, STATUS_LABELS, api, closePop, exactAge, extLink, fail, fullJd, get, h, icon, isoToday, menu, mini, muted, openPop, opt, titled,
  optimistic, phone, popOpen, q, shortDate, sm, store, tabName, toast, still } from "./core.js";
import { cellDisplay, makeEditable, setInlineHooks, syncBadge, undoLastEdit, watchSync } from "./inline.js";
import { openApplyDialog, pickResumeFile, removeApp, resumeBtns, xBtn } from "./dialogs.js";
import { openAddJob, openRowEditor, openVendors, refreshEditor, refreshFollowups } from "./editor.js";
import { sponsorBadge } from "./sponsor.js";

const GROUPS = { applied: "applied", interviewing: "interviewing", tracker: "" };
const GROUP_STATUSES = { applied: ["applied"], interviewing: ["screen", "interview", "offer"] };
const LIMIT = 2000;
const SEARCH_DEBOUNCE_MS = 300;
const MARK_TIP = "Mark applied (asks for the resume)";
const UPLOAD_TIP = "Upload the resume you sent (PDF/DOCX)";
export const T = { rows: [], layout: null, look: null, counts: {}, total: 0, q: "", status: "", sheetName: "", gone: {} };
let loadToken = 0;
let searchTimer = 0;
let grid = null;
let gridMenu = null;
const gridOn = () => grid?.hasGrid() && !cardsMode();

setInlineHooks({ redraw: redrawRow, badge: setBadge, editor: refreshEditor, patch: patchRow });

const view = () => $("#view");
const cardsMode = () => phone.matches;
const overdue = (row) => row.fu1?.state === "overdue" || row.fu2?.state === "overdue";
export const preApply = (row) => ["saved", "tailored"].includes(row.status);
export const shownTab = () => T.layout.tabs.find((t) => t.name === T.sheetName) || T.layout.tabs[0];
const shownStatuses = () => (GROUPS[S.tab] === "" ? (T.status ? [T.status] : null) : GROUP_STATUSES[S.tab]);
const fits = (row) => !shownStatuses() || shownStatuses().includes(row.status);

export function resetTracker() { Object.assign(T, { q: "", status: "", sheetName: "", rows: [], layout: null, gone: {} }); }
export const focusSearch = () => $("#t-search")?.focus();

export async function loadTracker({ quiet = false } = {}) {
  const token = ++loadToken;
  const tab = S.tab;
  const status = GROUPS[tab] === "" ? T.status : GROUPS[tab];
  if (!quiet) { S.mail = null; view().replaceChildren(toolbar(), h("div", "t-wrap", h("p", "muted pad", "Loading…"))); }
  try {
    const sheetTab = T.layout?.tabs.find((x) => x.name === T.sheetName);
    if (sheetTab?.kind === "vendors") return await loadVendors(sheetTab, quiet);
    const sheet = sheetTab?.kind === "apps" ? sheetTab.index : "";
    const data = await get("/api/tracker", { status, q: T.q, limit: LIMIT, compact: "1", sheet });
    if (token !== loadToken || tab !== S.tab) return;
    const same = quiet && gridOn() && data.layout && T.layout && sheet === (sheetTab?.kind === "apps" ? sheetTab.index : "");
    Object.assign(T, { rows: data.rows, layout: data.layout, look: data.look, counts: data.counts || {}, total: data.total, gone: {} });
    if (same) { paintCounts(); grid.refresh(); return; }
    draw();
  } catch (err) {
    if (token === loadToken) view().replaceChildren(toolbar(), h("p", "pad", `Could not load: ${err.message}`));
  }
}

function syncChip() {
  const sync = S.google?.sync;
  if (!sync || !(sync.linked || []).includes(S.profile)) return muted("Google Sheet: not linked", "chip-note");
  const error = (sync.errors || {})[S.profile] || (sync.errors || {}).all;
  const when = sync.last_sync_at ? exactAge(sync.last_sync_at, false) : "never";
  return h("span", { class: `chip-note ${error || !sync.healthy ? "warn" : "ok"}`,
    title: `Sheet sync: last good sync ${sync.last_ok_at || "never"}; conflicts in 7 days: ${sync.conflicts_7d ?? 0}` },
  error ? `Sheet sync failed: ${error}` : `Google Sheet · synced ${when}`);
}

function searchBox() {
  const search = h("input", { type: "search", id: "t-search", placeholder: "Search company, role, contact, notes…",
    "aria-label": "Search the tracker", title: "Search the tracker (/)" });
  search.value = T.q;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { T.q = search.value.trim(); loadTracker({ quiet: true }); }, SEARCH_DEBOUNCE_MS);
  });
  return search;
}

function exportMenu(anchor) {
  const link = (kind, text, title) => h("a", { class: "opt", href: `/api/tracker/export?${q({ kind })}`, title, onclick: () => closePop() }, text);
  openPop(menu(
    link("original", "Excel: my sheet's layout", "Download Excel in your sheet's layout"),
    link("full", "Excel: all fields", "Download Excel with every field")), anchor, "Export");
}

function chipRow(label, items, isOn, pick, tipOf) {
  return h("div", { class: "t-chips", role: "group", "aria-label": label },
    items.map(([k, v]) => B({ class: "chip", "data-k": k, "data-fk": `${label}:${k}`, "aria-pressed": String(isOn(k)), title: tipOf(k, v),
      onclick: (e) => { for (const c of e.currentTarget.parentNode.children) c.setAttribute("aria-pressed", String(c === e.currentTarget)); pick(k); } }, v)));
}

const statusLabel = (k) => (k ? `${STATUS_LABELS[k]} ${T.counts[k] || 0}` : `All ${Object.values(T.counts).reduce((a, b) => a + b, 0)}`);

function statusChips() {
  if (GROUPS[S.tab] !== "") return null;
  return chipRow("Filter by status", ["", ...Object.keys(STATUS_LABELS)].map((k) => [k, statusLabel(k)]),
    (k) => T.status === k, (k) => { T.status = k; loadTracker({ quiet: true }); }, (k) => (k ? `Show only ${STATUS_LABELS[k]} rows` : "Show every row"));
}

function sheetTabs() {
  if (!T.layout || T.layout.tabs.length < 2) return null;
  const first = T.layout.tabs[0].name;
  return chipRow("Sheet tab", T.layout.tabs.map((t) => [t.name, t.kind === "vendors" ? `${t.name} (vendors)` : t.name]),
    (k) => (T.sheetName || first) === k, (k) => { T.sheetName = k === first ? "" : k; loadTracker({ quiet: true }); },
    (k, v) => `Show sheet tab ${v}`);
}

function toolbar() {
  const acts = h("div", "t-acts",
    B({ class: "btn primary", "data-fk": "add", onclick: () => openAddJob(), title: "Add a job by link (n)" }, sm("plus"), "Add job"),
    B({ class: "btn", "data-fk": "export", "aria-haspopup": "menu", "aria-expanded": "false", title: "Export the tracker to Excel",
      onclick: (e) => exportMenu(e.currentTarget) }, "Export", sm("down")),
    B({ class: "btn", "data-fk": "vendors", title: "Vendors and recruiters", onclick: () => openVendors() }, "Vendors"),
    B({ class: "btn", "data-fk": "undo", onclick: () => undoLastEdit(), title: "Undo your last tracker edit (u)" }, sm("undo"), "Undo"),
    cardsMode() ? null : B({ class: "btn", "data-fk": "cols", "aria-haspopup": "dialog", "aria-expanded": "false", title: "Show or hide columns",
      onclick: (e) => { const a = e.currentTarget; if (gridMenu) gridMenu.columnsPop(a); else import("./gridmenu.js").then((m) => m.columnsPop(a)); } }, sm("cols"), "Columns"));
  const head = h("div", "t-head", h("h1", { id: "t-h" }, tabName(S.tab), muted(` ${T.total ?? ""}`, "muted n")),
    searchBox(), acts, h("div", "t-sync", syncChip(), S.google?.sheet_url ? extLink(S.google.sheet_url, "Open my sheet ↗", "lnk", "Open my Google Sheet") : null));
  const grid = cardsMode() ? [] : [h("div", { id: "g-state", class: "g-state" }), h("span", { id: "g-saved", class: "g-saved", role: "status" })];
  return h("div", "t-top", head, h("div", "t-row2", statusChips(), sheetTabs(), ...grid));   // one line: the grid keeps the room
}

function statusChip(row) {
  const label = STATUS_LABELS[row.status] || row.status;
  return B({ class: `st st-${row.status}`, "aria-haspopup": "menu", "aria-expanded": "false",
    "aria-label": `Status ${label}. Move to…`, title: "Move to another status",
    onclick: (e) => { e.stopPropagation(); moveMenu(row, e.currentTarget); } }, label, icon("down", "ic xs"));
}

function moveMenu(row, anchor) {
  openPop(menu( h("p", "pop-h", "Move to…"),
    Object.entries(STATUS_LABELS).map(([k, v]) => titled(opt(v, () => moveTo(row, k), row.status === k), `Move this row to ${v}`))), anchor, "Move to");
}

export function moveTo(row, status) {
  if (status === row.status) return;
  if (status === "applied" && preApply(row)) {
    openApplyDialog({ appId: row.id, onDone: () => settleApplied(row) });
    return;
  }
  const was = row.status;
  optimistic(() => { redrawRow({ ...row, status }); focusRowChip(row.id); }, () => { redrawRow({ ...row, status: was }); focusRowChip(row.id); },
    async () => {
      const fresh = await api("/api/tracker/update", { app: row.id, changes: { status } });
      redrawRow(fresh, "saved");
      watchSync(row.id);
      refreshFollowups();
    },
    `${row.company || "Row"} moved to ${STATUS_LABELS[status]}`, () => undoLastEdit(row.id));
}

function focusRowChip(id) {
  const a = document.activeElement;
  if (a && a !== document.body && a.isConnected && !a.matches(".t-wrap, .t-cards, .g-wrap")) return;
  requestAnimationFrame(() => still($(`#view .t-cards [data-app="${id}"] .st`) || $(".g-wrap, .t-wrap, .t-cards")));
}

async function settleApplied(row) {
  redrawRow({ ...row, status: "applied", applied_on: isoToday() });
  still($(`#view .t-cards [data-app="${row.id}"] button`) || $(".g-wrap, .t-wrap"));
  try { redrawRow(await fullRow(row), "saved"); } catch (err) { fail(err); }
  refreshFollowups();
}

export const dueText = (fu, n) => (!fu ? "" : fu.state === "done" ? "done"
  : { overdue: `FU${n} overdue ${shortDate(fu.due)}`, today: `FU${n} today`, upcoming: `FU${n} ${shortDate(fu.due)}` }[fu.state] || "");
const dueBadge = (fu, n) => (!fu ? null : fu.state === "done" ? muted("done") : h("span", `due due-${fu.state}`, dueText(fu, n)));

// a card's own × removes the row, so its resume button says "Remove resume"
export function resumeBits(row, rmLabel) {
  const done = (fresh) => { redrawRow(fresh, "saved"); refreshEditor(fresh); };
  return [row.resume_missing ? h("span", { class: "tag warn", title: "No resume file on this row yet" }, "resume missing") : null,
    row.has_resume ? null : mini("Upload resume", UPLOAD_TIP, () => pickResumeFile(row.id, done)),
    ...resumeBtns(row.id, row.has_resume, done, rmLabel)].filter(Boolean);
}

const fullRow = (row) => get("/api/tracker/row", { app: row.id });

export function patchRow(row, field, value) {
  const own = T.layout?.tabs.find((t) => t.name === row.sheet?.tab);
  const i = own ? own.columns.findIndex((c) => (field.startsWith("sheet:") ? c.header === field.slice(6) : c.field === field)) : -1;
  return { ...row, [field]: value,
    sheet: i < 0 ? row.sheet : { ...row.sheet, cells: row.sheet.cells.map((v, j) => (j === i ? value : v)) },
    extras: row.extras && field in row.extras ? { ...row.extras, [field]: value } : row.extras,
    clipped: (row.clipped || []).filter((k) => k !== field && k !== `sheet:${i}`) };
}

function card(row) {
  return h("li", { class: `t-card${overdue(row) ? " overdue" : ""}`, "data-app": String(row.id) },
    h("div", "tc-top", B({ class: "tc-open", title: OPEN_TIP, onclick: () => openRowEditor(row.id) },
      h("b", {}, row.company || "(company)", " ", sponsorBadge(row.sponsor, { compact: true })), h("span", "tc-role", row.role_title || "(role)")), statusChip(row),
      xBtn(`${row.company} · ${row.role_title}`, () => removeApp(row, () => { dropIds([row.id]); $(`#view .t-cards [data-app="${row.id}"]`)?.remove(); },
        () => loadTracker({ quiet: true })))),
    row.dup_of ? h("p", { class: "tag warn", title: "Same company, role and place as a newer row: × removes this one" }, `Duplicate of ${row.dup_of.label}`) : null,
    h("p", "tc-meta muted", row.applied_on ? `Applied ${shortDate(row.applied_on)}` : "Not applied yet",
      dueBadge(row.fu1, 1), dueBadge(row.fu2, 2), ...resumeBits(row, "Remove resume"), fullJd({ app: row.id })),
    preApply(row) ? btn("", MARK_TIP, () => moveTo(row, "applied"), sm("check"), "Mark applied") : null,
    h("span", "sync-slot"));
}

const emptyNote = () => (T.q ? `Nothing matches “${T.q}”.` : "Nothing here yet.");

function bodyEls() {
  if (!T.layout) return [h("p", "pad muted", "No tracker layout for this profile.")];
  if (cardsMode()) {
    return [h("ul", { class: "t-cards", "aria-label": "Applications" }, T.rows.length ? T.rows.map(card) : h("li", "empty", emptyNote()))];
  }
  const host = h("div", { "data-fk": "wrap" }, h("p", "pad muted", "Loading…"));
  import("./grid.js").then((m) => { grid = m; if (host.isConnected) m.mount(host); return import("./gridmenu.js"); })
    .then((m) => { gridMenu = m; }).catch(fail);                // loaded with the grid: Columns opens in the same click
  return [host];
}

function keepFocus(paint) {
  const act = document.activeElement;
  const fk = act?.closest("[data-fk]")?.dataset.fk;
  const typed = act?.id === "t-search" ? [act.value, act.selectionStart, act.selectionEnd] : null;
  if (popOpen() && $("#view [aria-expanded=true]")) closePop({ refocus: false });
  paint();
  const search = typed && $("#t-search");
  if (search) { search.value = typed[0]; search.focus(); search.setSelectionRange(typed[1], typed[2]); }
  else if (fk) still($(`#view [data-fk="${CSS.escape(fk)}"]`));
}

// mail/panel.py HTML (panel | settings); data-mail = act
export function mailMount(el, view) {
  const go = (a) => api("/api/mail/act", { view, ...a }).then((r) => {
    el.innerHTML = r.html; try { const k = sessionStorage.mo; if (k) $("details.mail", el).open = k > 0; } catch {} r.msg && toast(r.msg);
  }, fail);
  el.addEventListener("toggle", (e) => { try { if (e.target.matches(".mail")) sessionStorage.mo = +e.target.open; } catch {} }, true);
  el.onclick = (e) => {
    const b = e.target.closest("[data-mail]"), g = b?.closest("[data-id]"), v = (s) => g?.querySelector(s)?.value;
    if (b) go({ act: b.dataset.mail, id: g?.dataset.id, app: v("[data-pick]"), status: v("[data-st]"),
      value: v("[data-v]") ?? b.value, secret: v("[data-pw]"), host: v("[data-host]") });
  };
  go({});
  if (view === "settings") redraw = () => el.isConnected && go({});
  return el;
}
let redraw; // Settings redraws after Google sign-in
document.addEventListener("visibilitychange", () => document.hidden || redraw?.());
const mailBox = () => ["applied", "tracker"].includes(S.tab) ? [S.mail ||= mailMount(h("div", "mail-box"), "panel")] : [];

function draw() {
  keepFocus(() => view().replaceChildren(toolbar(), ...mailBox(), ...bodyEls()));
}

function redrawRow(row, badge) {
  const i = T.rows.findIndex((r) => r.id === row.id);
  const gone = T.gone[row.id];
  if (i < 0 && !gone) return;
  const old = i < 0 ? null : T.rows[i];
  const next = { ...(old || {}), ...row, clipped: row.clipped || [] };
  const was = old ? old.status : gone.status;
  if (was !== next.status) T.counts = { ...T.counts, [was]: Math.max(0, (T.counts[was] || 0) - 1), [next.status]: (T.counts[next.status] || 0) + 1 };
  if (!fits(next)) dropRow(i, next);
  else if (old) replaceRow(i, next);
  else insertRow(gone.at, next);
  if (badge && fits(next)) setBadge(row.id, badge);
  paintCounts();
}

function dropRow(i, next) {
  if (i < 0) { T.gone = { ...T.gone, [next.id]: { ...T.gone[next.id], status: next.status } }; return; }
  T.gone = { ...T.gone, [next.id]: { at: i, status: next.status } };
  T.rows = T.rows.filter((r) => r.id !== next.id);
  T.total -= 1;
  if (gridOn()) grid.refresh(); else $(`#view [data-app="${next.id}"]`)?.remove();
}

// rows gone for good (deleted): no way back through a status change
export function dropIds(ids) {
  T.rows = T.rows.filter((r) => !ids.includes(r.id));
  T.total -= ids.length;
  paintCounts();
}

// a grid status write: counts follow, and a row that no longer fits the tab / status filter leaves it (Undo brings it back)
export const showStatus = (row) => redrawRow(row);

// a row's new data (a grid write or its answer): shown without the badge / counts bookkeeping of redrawRow
export function putRow(row) {
  const i = T.rows.findIndex((r) => r.id === row.id);
  if (i < 0) return;
  replaceRow(i, { ...T.rows[i], ...row, clipped: row.clipped || [] });
}

function insertRow(at, next) {
  const { [next.id]: _, ...rest } = T.gone;
  T.gone = rest;
  const k = Math.min(at, T.rows.length);
  T.rows = [...T.rows.slice(0, k), next, ...T.rows.slice(k)];
  T.total += 1;
  const list = $(".t-cards");
  if (list) { list.querySelector(".empty")?.remove(); list.children[k] ? list.children[k].before(card(next)) : list.append(card(next)); }
  if (gridOn()) grid.refresh();
}

function replaceRow(i, next) {
  T.rows = T.rows.map((r, j) => (j === i ? next : r));
  if (gridOn()) { grid.rowChanged(next); return; }
  const old = $(`#view [data-app="${next.id}"]`);
  if (!old) return;
  const act = old.contains(document.activeElement) ? document.activeElement : null;
  const fresh = card(next);
  old.replaceWith(fresh);
  const same = act?.classList[0] && fresh.querySelector(`.${CSS.escape(act.classList[0])}`);   // the same control in the new card
  if (act) still(same || fresh.querySelector("button"));
}

function paintCounts() {
  const n = $("#t-h .n");
  if (n) n.textContent = ` ${T.total ?? ""}`;
  for (const c of $$('#view [aria-label="Filter by status"] .chip')) c.textContent = statusLabel(c.dataset.k);
}

function setBadge(appId, state, detail) {
  $(`#view [data-app="${appId}"] .sync-slot`)?.replaceChildren(syncBadge(state, detail));
}

async function loadVendors(tab, quiet) {
  let { vendors } = await get("/api/vendors");
  const save = (td, i, field) => (val) => {
    const next = vendors.map((v, j) => (j === i ? { ...v, [field]: val } : v));
    const paint = (v) => { td.classList.remove("editing"); td.replaceChildren(cellDisplay(v)); still(td); };
    return optimistic(() => paint(val), () => paint(vendors[i][field] ?? ""),
      async () => { await api("/api/vendors", { vendors: next }); vendors = next; }, "Vendor saved (syncs to your sheet)");
  };
  const body = h("tbody", {}, vendors.map((v, i) => h("tr", {}, h("th", { class: "rh", scope: "row" }, String(i + 1)),
    tab.columns.map((c) => {
      const td = h("td", { "data-fk": `v:${i}:${c.field || c.header}` }, cellDisplay(c.field ? v[c.field] : ""));
      if (c.field) makeEditable(td, { field: c.field, value: () => vendors[i][c.field] ?? "", save: save(td, i, c.field) });
      else td.title = `${c.header}: kept from your sheet`;
      return td;
    }))));
  keepFocus(() => view().replaceChildren(toolbar(), h("div", { class: "t-wrap", tabindex: "0", role: "region", "aria-label": `Vendors on ${tab.name}`, "data-fk": "wrap",
    title: `Vendors on your sheet tab ${tab.name}` },
  h("table", "t vendors", h("thead", {}, h("tr", {}, h("th", { class: "rh", scope: "col" }, "#"),
    tab.columns.map((c) => h("th", { scope: "col", title: `${c.header} column` }, c.header)))), body))));
  if (!quiet) toast(`${vendors.length} vendors on ${tab.name}`, { ms: 2500 });
}
