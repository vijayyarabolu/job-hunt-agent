// Tracker grid: Sheets-like cells over the tracker rows; loaded with a table tab, desktop only.
import { B, FAMILY_LABELS, S, STATUS_LABELS, EMPLOYMENTS, WORKPLACES, h, sm, store, still } from "./core.js";
import { T, dueText, resumeBits, shownTab } from "./tracker.js";
import { css } from "./gridcss.js";
import { sponsorBadge } from "./sponsor.js";
import { editKey, editing, startEdit } from "./gridedit.js";
import { clearSel, deleteRows, fillDrag, onClip, redo, undo } from "./gridops.js";
import { applyView, findKey, headMenu, openCell, rowMenu, stateLine } from "./gridmenu.js";

export const ROW_H = 25;
const HEAD_H = 30;
const RN_W = 46;
const BUF = 10;
const MIN_W = 48;
const DEF_W = 140;
const FROZEN = 2;
const FIT_MIN_W = 96;
const RESIZE_MS = 200;          // columns shrink to fit the screen, never below this
const CLIP_TEXT = "\u200b";     // an invisible character (see build)
const WIDTHS = { company: 170, role_title: 240, jd: 220, notes: 240, date_applied: 110, status: 120, address: 180,
  location: 180, resume: 220 };
export const LISTS = { status: Object.entries(STATUS_LABELS), workplace: WORKPLACES,
  employment_type: [...EMPLOYMENTS, ["part_time", "Part-time"], ["internship", "Internship"]] };
const APP_EDIT = ["status", "job_url", "apply_url", "salary", "workplace", "notes", "location", "employment_type"];
export const G = { wrap: null, body: null, cols: [], view: [], r: 0, c: 0, ar: 0, ac: 0, first: 0, last: -1, drawn: new Map(),
  sort: null, filters: {}, hits: null, grouped: store.get("jh.g.days") === "1" };

const prefKey = () => `jh.cols.${S.profile}.${T.sheetName || "main"}`;
export const prefs = () => ({ hidden: [], widths: {}, ...store.json(prefKey(), {}) });
export const savePrefs = (p) => store.set(prefKey(), JSON.stringify(p));
export const fieldOf = (c) => c.field || `sheet:${c.label}`;
export const isRow = (i) => !!G.view[i] && !G.view[i].head;
const tabLook = () => (T.look || {})[shownTab().name] || null;
// the Sheet's colors are light ones: shown in the light theme only (widths always)
const paint = () => G.colors;

function kindOf(c) {
  if (c.field === "status") return "status";
  if (LISTS[c.field] || (c.group === "sheet" && /status/i.test(c.label))) return "list";
  if (c.field === "date_applied" || /date/i.test(c.label)) return "date";
  if (c.field === "resume") return "resume";
  if (["jd", "job_url", "apply_url"].includes(c.field)) return "link";
  return c.field === "notes" ? "long" : "text";
}

// W14: the app's Status sits right after the frozen columns (it was column ~20 of 31); the Sheet keeps its order
const STATUS_TIP = "Where this application is now. Your Sheet's Status(A/R) column follows it";
function ordered(sheet, app) {
  const st = app.find((x) => x.field === "status");
  if (!st) return [...sheet, ...app];
  return [...sheet.slice(0, FROZEN), { ...st, label: "Status", tip: STATUS_TIP }, ...sheet.slice(FROZEN), ...app.filter((x) => x !== st)];
}

export function columns() {
  if (!T.layout) return [];
  const p = prefs();
  const look = tabLook();
  return ordered(shownTab().columns.map((c, i) => ({ key: `s:${c.header}`, label: c.header, field: c.field, idx: i, group: "sheet" })),
    T.layout.extras.map((x) => ({ key: `x:${x.field}`, label: x.label, field: x.field, group: "app" })))
    .map((c) => ({ ...c, kind: kindOf(c), edit: c.group === "sheet" || APP_EDIT.includes(c.field), hidden: p.hidden.includes(c.key),
      width: p.widths[c.key] || look?.widths?.[c.label] || WIDTHS[c.field] || DEF_W, sized: !!p.widths[c.key] }));
}

// Company and Role stay pinned (Status right after); the other columns give up width, down to FIT_MIN_W, so
// the view fits the screen when it can. A dragged width stays; the Sheet itself is untouched (view only).
const pinned = (c, j) => j < FROZEN && c.group === "sheet";
function fitWidths(cols, room) {
  cols.forEach((c, j) => { if (pinned(c, j) && !c.sized) c.width = Math.max(c.width, WIDTHS[c.field] || 0); });
  const total = cols.reduce((a, c) => a + c.width, 0);
  const give = cols.filter((c, j) => !pinned(c, j) && !c.sized && c.width > FIT_MIN_W);
  const spare = give.reduce((a, c) => a + c.width - FIT_MIN_W, 0);
  if (total <= room || !spare) return;
  const share = Math.min(1, (total - room) / spare);
  for (const c of give) c.width = Math.round(c.width - (c.width - FIT_MIN_W) * share);
}

function sheetIndex(row, c) {
  if (!row.sheet || row.sheet.tab === shownTab().name) return c.idx;
  const own = T.layout.tabs.find((t) => t.name === row.sheet.tab);
  return own ? own.columns.findIndex((x) => x.header === c.label) : -1;
}

// what a cell shows / what editing it starts from
export function text(row, c) {
  if (c.group === "sheet") { const i = sheetIndex(row, c); return (row.sheet && i >= 0 ? row.sheet.cells[i] : null) ?? ""; }
  if (c.field === "status") return STATUS_LABELS[row.status] || row.status || "";
  if (c.field === "fu1_due" || c.field === "fu2_due") return dueText(row[c.field.slice(0, 3)], c.field[2]);
  if (c.field === "resume_used") { const v = (row.extras || {}).resume_used; return FAMILY_LABELS[v] || v || ""; }   // not "ai_engineer"
  return String((row.extras || {})[c.field] ?? "");
}
export const value = (row, c) => (c.field === "status" ? row.status : c.field && row[c.field] != null ? String(row[c.field]) : text(row, c));
export const clipped = (row, c) => (row.clipped || []).some((k) => k === c.field || k === `sheet:${sheetIndex(row, c)}`);

// the Sheet's conditional colors
function ruled(c, t) {
  for (const r of paint()?.rules || []) {
    if (!r.headers.includes(c.label)) continue;
    const v = String(r.value ?? "").toLowerCase(), s = String(t).trim().toLowerCase();
    if ({ not_blank: !!s, blank: !s, text_eq: s === v, text_contains: !!v && s.includes(v) }[r.when]) return r;
  }
  return null;
}

const CHIP = ["#e8eaed", "#d4edbc", "#bfe1f6", "#ffe5a0", "#ffcfc9", "#e6cff2", "#c6dbe1", "#ffc8aa"];
export const chipColor = (t) => { let n = 0; for (const ch of String(t)) n = (n * 31 + ch.charCodeAt(0)) % 997; return CHIP[n % CHIP.length]; };

function conflictOf(row, c, t) {
  const f = (row.conflicts || {})[c.label];
  if (!f) return null;
  const won = f.winner === "sheet" ? f.sheet : f.app;
  if (String(won ?? "").trim() !== String(t).trim()) return null;
  return f.winner === "sheet" ? `Changed here and in your Sheet: the Sheet's edit was newer and won. Your value was: ${f.app || "(empty)"}`
    : `Changed here and in your Sheet: your edit was newer and won. The Sheet had: ${f.sheet || "(empty)"}`;
}

function cellEl(row, c) {
  const t = text(row, c);
  const td = h("td", { role: "gridcell", class: `k-${c.kind}${c.fz ? " fz" : ""}` });
  if (c.fz) td.style.left = `${c.fz}px`;
  if (c.kind === "status") td.append(h("span", `st st-${row.status}`, t));
  else if (c.kind === "list" && t) { const chip = h("span", "g-chip", t); chip.style.background = chipColor(t); td.append(chip); }
  else if (c.kind === "resume") {                                  // stays: × / Replace ▾ / Upload resume
    td.classList.add("c-resume");
    td.append(t && row.has_upload ? h("span", { class: "g-file", title: `${t} (⌘-click opens it in Drive)` }, sm("open"), t) : t, ...resumeBits(row));
  }
  else if (c.kind === "link" && t) td.append(h("span", { class: "g-link", title: `${t} (⌘-click opens it)` }, t));
  else if (c.field === "company" && row.sponsor) td.append(h("div", "g-co", h("span", {}, t), sponsorBadge(row.sponsor, { compact: true })));
   else td.append(t);
  const rule = ruled(c, t);
  if (rule) { if (rule.bg) td.style.background = rule.bg; if (rule.fg) td.style.color = rule.fg; if (rule.bold) td.style.fontWeight = "700"; }
  const cf = conflictOf(row, c, t);
  if (cf) { td.classList.add("g-cf"); td.title = cf; } else if (t.length > 30) td.title = t.slice(0, 600);
  if (!c.edit) td.classList.add("ro");
  return td;
}

function rowEl(i) {
  const row = G.view[i];
  if (row.head) return h("tr", { class: "g-day", "data-i": i }, h("th", { colspan: String(G.cols.length + 1) }, row.head));
  const band = paint()?.band;
  const dup = row.dup_of ? `Duplicate of ${row.dup_of.label} (a newer row): × removes this one · ` : "";
  const tr = h("tr", { "data-i": i, "data-app": String(row.id) },
    h("th", { class: `g-rn${dup ? " g-dup" : ""}`, scope: "row", title: `${dup}Double-click: row editor · right-click: more` }, String(row.n),
      B({ class: "g-x", title: "Remove: duplicate or not wanted (asks first; Undo)", "aria-label": `Remove row ${row.n}`, onclick: () => deleteRows([row]) }, "×")));
  if (band?.length > 1) tr.style.background = band[row.n % 2 ? 0 : 1];
  for (const c of G.cols) tr.append(cellEl(row, c));
  tr.row = row;
  return tr;
}

function headEl(c, j) {
  const sorted = G.sort?.key === c.key ? (G.sort.dir > 0 ? " ↑" : " ↓") : "";
  const th = h("th", { scope: "col", class: `${c.group === "app" ? "app" : ""}${c.fz ? " fz" : ""}`, "data-j": j, title: c.tip ? `${c.tip}. Click selects the column` : `${c.label}: click selects the column` },
    h("span", "g-hl", c.label, sorted), G.filters[c.key] ? h("span", { class: "g-fi", title: "Filtered" }, "⧩") : null,
    B({ class: "g-hm", "aria-label": `${c.label} column menu`, title: "Sort, filter, hide", "aria-haspopup": "menu",
      onclick: (e) => { e.stopPropagation(); headMenu(c, e.currentTarget); } }, sm("down")),
    grip(c));
  if (c.fz) th.style.left = `${c.fz}px`;
  const look = paint();
  if (look?.header_bg && c.group === "sheet") { th.style.background = look.header_bg; th.style.color = look.header_fg || ""; }
  return th;
}

function grip(c) {
  const g = h("span", { class: "g-rz", title: "Drag to resize the column" });
  g.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const col = G.wrap.querySelector(`col[data-key="${CSS.escape(c.key)}"]`);
    const x0 = e.clientX, w0 = c.width;
    g.setPointerCapture(e.pointerId);
    const move = (ev) => { c.width = Math.max(MIN_W, w0 + ev.clientX - x0); col.style.width = `${c.width}px`; };
    g.addEventListener("pointermove", move);
    g.addEventListener("pointerup", () => {
      g.removeEventListener("pointermove", move);
      const p = prefs();
      savePrefs({ ...p, widths: { ...p.widths, [c.key]: Math.round(c.width) } });
      if (c.fz) build();
      else paintSel();
    }, { once: true });
  });
  return g;
}

const spacer = () => h("tr", { class: "g-sp", "aria-hidden": "true" }, h("td", { colspan: String(G.cols.length + 1) }));

export function mount(host) {
  css();
  G.wrap = host;
  host.classList.add("g-wrap");
  host.tabIndex = 0;
  host.setAttribute("role", "region");
  host.setAttribute("aria-label", "Tracker grid: arrows move, Enter edits, ⌘C / ⌘V copy and paste, ⌘Z undo");
  host.title = "Click a cell and type to edit · Enter, F2 or double-click edits in place · right-click: row actions";
  host.onkeydown = onKey;
  host.onscroll = () => requestAnimationFrame(() => paintRows());
  host.onpointerdown = onDown;
  host.ondblclick = (e) => { if (e.target.closest("td button, th button")) return; const at = cellOf(e.target); if (at && at[1] >= 0) startEdit(null, true); else if (at) openRow(at[0]); };
  host.oncontextmenu = (e) => { const at = cellOf(e.target); if (!at) return; e.preventDefault(); if (!inRange(...at)) go(at[0], Math.max(0, at[1])); rowMenu(e); };
  host.onclick = (e) => { const at = (e.metaKey || e.ctrlKey) && cellOf(e.target); if (at && at[1] >= 0) openCell(...at); };
  for (const ev of ["copy", "cut", "paste"]) host.addEventListener(ev, onClip);
  if (!G.onResize) {                   // the columns fit the window again after it changes size
    G.onResize = () => { clearTimeout(G.resizeT); G.resizeT = setTimeout(() => { if (G.wrap?.isConnected && !editing()) build(); }, RESIZE_MS); };
    addEventListener("resize", G.onResize);
  }
  refresh();
}

export function refresh(keep = true) {
  if (!G.wrap?.isConnected) return;
  const id = keep && G.view[G.r]?.id;
  applyView();
  const i = id ? G.view.findIndex((r) => r.id === id) : -1;
  if (i >= 0) { G.ar += i - G.r; G.r = i; }
  G.r = Math.max(0, Math.min(G.r, G.view.length - 1));
  G.ar = Math.max(0, Math.min(G.ar, G.view.length - 1));
  build();
}

export function build() {
  G.colors = getComputedStyle(document.documentElement).colorScheme === "dark" ? null : tabLook();
  G.cols = columns().filter((c) => !c.hidden);
  fitWidths(G.cols, (G.wrap.clientWidth || innerWidth) - RN_W - 2);
  let left = RN_W;
  G.cols.forEach((c, j) => { c.fz = pinned(c, j) ? left : 0; if (c.fz) left += c.width; });
  G.c = Math.max(0, Math.min(G.c, G.cols.length - 1));
  G.ac = Math.max(0, Math.min(G.ac, G.cols.length - 1));
  const head = h("tr", {}, h("th", { class: "g-rn g-corner", title: "Select all (⌘A)", onclick: () => selectAll() }), G.cols.map(headEl));
  const table = h("table", { class: "g", role: "grid", "aria-rowcount": String(G.view.length + 1), "aria-colcount": String(G.cols.length + 1) },
    h("colgroup", {}, h("col", { class: "g-rnc" }), G.cols.map((c) => { const col = h("col", { "data-key": c.key }); col.style.width = `${c.width}px`; return col; })),
    h("thead", {}, head), G.body = h("tbody", {}, spacer(), spacer()));
  G.drawn = new Map();
  G.first = 0;
  G.last = -1;
  const keep = [G.wrap.scrollTop, G.wrap.scrollLeft];          // a rebuild (a column hidden, a row gone) keeps the place
  // the trailing text node is load-bearing: with no text in the grid Chrome sends a real ⌘V nowhere (it printed "null")
  G.wrap.replaceChildren(table, h("div", { class: "g-fh", title: "Drag down to fill", onpointerdown: (e) => fillDrag(e) }),
    G.view.length ? CLIP_TEXT : h("p", "pad muted g-empty", T.q || Object.keys(G.filters).length ? "Nothing matches." : "Nothing here yet."));
  stateLine();
  G.body.lastChild.style.height = `${G.view.length * ROW_H}px`;       // full height first, so the old place still exists
  [G.wrap.scrollTop, G.wrap.scrollLeft] = keep;
  paintRows(true);
}

export function paintRows(force) {
  if (!G.body?.isConnected) return;
  const vis = Math.ceil(G.wrap.clientHeight / ROW_H) + 1;
  const first = Math.max(0, Math.floor(G.wrap.scrollTop / ROW_H) - BUF);
  const last = Math.min(G.view.length - 1, first + vis + 2 * BUF);
  if (!force && first === G.first && last === G.last) return;
  const next = new Map();
  for (let i = first; i <= last; i++) {
    const old = G.drawn.get(i);
    next.set(i, !force && old?.row === G.view[i] && old.row ? old : rowEl(i));
  }
  for (const [i, el] of G.drawn) if (next.get(i) !== el) el.remove();
  let at = G.body.firstChild;
  for (const el of next.values()) { if (at.nextSibling !== el) at.after(el); at = el; }
  G.body.firstChild.style.height = `${first * ROW_H}px`;
  G.body.lastChild.style.height = `${Math.max(0, G.view.length - 1 - last) * ROW_H}px`;
  Object.assign(G, { drawn: next, first, last });
  paintSel();
}

// one row's data changed (a save, the row editor, a status move)
export function rowChanged(row) {
  const i = G.view.findIndex((r) => r.id === row.id);
  if (i < 0) return refresh();
  G.view[i] = { ...row, n: G.view[i].n };
  const old = G.drawn.get(i);
  if (old) { const fresh = rowEl(i); old.replaceWith(fresh); G.drawn.set(i, fresh); paintSel(); }
}

export const range = () => [Math.min(G.r, G.ar), Math.max(G.r, G.ar), Math.min(G.c, G.ac), Math.max(G.c, G.ac)];
const inRange = (r, c) => { const [r0, r1, c0, c1] = range(); return r >= r0 && r <= r1 && (c < 0 || (c >= c0 && c <= c1)); };
export const cellAt = (r, c) => G.drawn.get(r)?.children[c + 1] || null;

export function paintSel() {
  for (const el of G.wrap.querySelectorAll(".sl, .cur, .hs, .hit")) el.classList.remove("sl", "cur", "hs", "hit");
  const [r0, r1, c0, c1] = range();
  for (const [i, tr] of G.drawn) {
    if (G.hits) for (const [hr, hc] of G.hits.list) if (hr === i) tr.children[hc + 1]?.classList.add("hit");
    if (i < r0 || i > r1 || tr.row == null) continue;
    tr.firstChild.classList.add("hs");
    for (let j = c0; j <= c1; j++) tr.children[j + 1]?.classList.add("sl");
  }
  const heads = G.wrap.querySelectorAll("thead th[data-j]");
  for (let j = c0; j <= c1; j++) heads[j]?.classList.add("hs");
  const cur = cellAt(G.r, G.c);
  G.wrap.querySelector("#g-cur")?.removeAttribute("id");
  if (cur) { cur.classList.add("cur"); cur.id = "g-cur"; G.wrap.setAttribute("aria-activedescendant", "g-cur"); }
  const corner = cellAt(r1, c1), fh = G.wrap.querySelector(".g-fh");
  if (fh) {
    fh.hidden = !corner || !!editing() || !G.cols[c1]?.edit;
    if (corner) { fh.style.top = `${corner.offsetTop + corner.offsetHeight - 4}px`; fh.style.left = `${corner.offsetLeft + corner.offsetWidth - 4}px`; }
  }
}

function nextRow(r, step) {
  let i = r;
  while (G.view[i]?.head) i += step;
  if (i >= 0 && i < G.view.length) return i;
  i = r;
  while (G.view[i]?.head) i -= step;
  return i >= 0 && i < G.view.length ? i : G.r;
}

export function go(r, c, extend = false) {
  if (!G.view.length) return;
  r = nextRow(Math.max(0, Math.min(G.view.length - 1, r)), r < G.r ? -1 : 1);
  G.r = r;
  G.c = Math.max(0, Math.min(G.cols.length - 1, c));
  if (!extend) { G.ar = G.r; G.ac = G.c; }
  reveal();
  paintSel();
}

function reveal() {
  const w = G.wrap, top = G.r * ROW_H, frozen = RN_W + G.cols.filter((c) => c.fz).reduce((a, c) => a + c.width, 0);
  if (top < w.scrollTop) w.scrollTop = top;
  else if (top + ROW_H > w.scrollTop + w.clientHeight - HEAD_H) w.scrollTop = top + ROW_H - w.clientHeight + HEAD_H;
  const c = G.cols[G.c];
  if (c && !c.fz) {
    const left = RN_W + G.cols.slice(0, G.c).reduce((a, x) => a + x.width, 0);
    if (left < w.scrollLeft + frozen) w.scrollLeft = left - frozen;
    else if (left + c.width > w.scrollLeft + w.clientWidth) w.scrollLeft = left + c.width - w.clientWidth;
  }
  paintRows();
}

export function selectAll() { G.ar = 0; G.ac = 0; G.r = G.view.length - 1; G.c = G.cols.length - 1; paintSel(); still(G.wrap); }

// the view row under a screen y (rows are all ROW_H tall), whatever covers it (a toast, the fill handle)
export const rowAtY = (y) => Math.max(0, Math.min(G.view.length - 1, Math.floor((y - G.wrap.getBoundingClientRect().top - HEAD_H + G.wrap.scrollTop) / ROW_H)));
export function cellOf(el) {
  const td = el?.closest?.("td, th");
  const tr = td?.parentNode;
  if (!tr?.dataset?.i || tr.row == null) return null;
  return [Number(tr.dataset.i), [...tr.children].indexOf(td) - 1];
}

function onDown(e) {
  if (e.button !== 0 || e.target.closest(".g-fh, .g-rz, .g-hm, .g-ed, td button, th button")) return;
  const th = e.target.closest("thead th[data-j]");
  if (th) { const j = Number(th.dataset.j); G.ar = 0; G.r = G.view.length - 1; G.c = j; if (!e.shiftKey) G.ac = j; paintSel(); still(G.wrap); return; }
  const at = cellOf(e.target);
  if (!at) return;
  editing()?.commit();
  e.preventDefault();
  still(G.wrap);
  if (at[1] < 0) { G.r = at[0]; if (!e.shiftKey) G.ar = at[0]; G.ac = 0; G.c = G.cols.length - 1; paintSel(); return; }
  go(at[0], at[1], e.shiftKey);
  const move = (ev) => { const to = cellOf(document.elementFromPoint(ev.clientX, ev.clientY)); if (to && to[1] >= 0) go(to[0], to[1], true); };
  G.wrap.addEventListener("pointermove", move);
  addEventListener("pointerup", () => G.wrap.removeEventListener("pointermove", move), { once: true });
}

export function openRow(i) { if (isRow(i)) { still(G.wrap); import("./editor.js").then((m) => m.openRowEditor(G.view[i].id)); } }   // Esc comes back here

const page = () => Math.max(1, Math.floor(G.wrap.clientHeight / ROW_H) - 2);

function onKey(e) {
  if (e.target !== G.wrap) return;
  const mod = e.metaKey || e.ctrlKey, k = e.key, shift = e.shiftKey;
  if (mod && k.toLowerCase() === "k") return;
  const last = G.view.length - 1, end = G.cols.length - 1;
  const moves = {
    ArrowDown: () => go(mod ? last : G.r + 1, G.c, shift), ArrowUp: () => go(mod ? 0 : G.r - 1, G.c, shift),
    ArrowRight: () => go(G.r, mod ? end : G.c + 1, shift), ArrowLeft: () => go(G.r, mod ? 0 : G.c - 1, shift),
    Tab: () => go(G.r, G.c + (shift ? -1 : 1)), Home: () => go(mod ? 0 : G.r, 0, shift), End: () => go(mod ? last : G.r, end, shift),
    PageDown: () => go(G.r + page(), G.c, shift), PageUp: () => go(G.r - page(), G.c, shift),
    Enter: () => (shift ? go(G.r - 1, G.c) : startEdit(null, true)), F2: () => startEdit(null, true),
    Delete: () => clearSel(), Backspace: () => clearSel(),
    Escape: () => (G.hits ? findKey("close") : go(G.r, G.c)),
  };
  const cmds = { a: selectAll, z: () => (shift ? redo() : undo()), y: redo, f: () => findKey("open") };
  let act = mod ? cmds[k.toLowerCase()] || (k.startsWith("Arrow") || k === "Home" || k === "End" ? moves[k] : null) : moves[k];
  const wholeRows = Math.min(G.ac, G.c) === 0 && Math.max(G.ac, G.c) === end;          // a row number was clicked
  if (!mod && k === "X" && wholeRows) act = () => deleteRows(selRows());               // Shift+X (asks first)
  if (!act && !mod && !e.altKey && k.length === 1) act = () => editKey(k);
  if (!act) return;
  e.preventDefault();
  e.stopPropagation();
  act();
}

export const focusGrid = () => still(G.wrap);
export const curRow = () => (isRow(G.r) ? G.view[G.r] : null);
export const selRows = () => { const [r0, r1] = range(); return G.view.slice(r0, r1 + 1).filter((r) => !r.head); };
export const setGrouped = (on) => { G.grouped = on; store.set("jh.g.days", on ? "1" : "0"); refresh(); };
export const hasGrid = () => !!G.wrap?.isConnected;
