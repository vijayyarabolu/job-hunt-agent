// Grid writes: optimistic, in order, many cells per request; undo / redo of every edit, paste, fill,
// clear and row insert / delete (the Sheet follows); the clipboard as TSV both ways with Sheets / Excel; the fill handle.
import { $, S, api, ask, fail, get, toast } from "./core.js";
import { T, dropIds, patchRow, putRow, showStatus } from "./tracker.js";
import { G, clipped, curRow, fieldOf, go, isRow, paintSel, range, refresh, rowAtY, selRows, text } from "./grid.js";
import { dateText, editing, listKey, parseDate, toDate } from "./gridedit.js";

const UNDO_MAX = 200;
const SYNC_POLL_MS = 4000;
const SYNC_POLL_MAX = 20;
const DAY_MS = 86400000;
const UNDO_TOAST_MS = 8000;
const done = [];
const again = [];
let chain = Promise.resolve();
let syncWatch = 0;
const left = new Map();               // rows a status write took out of the tab: Undo puts them back from here

function saved(state, detail = "") {
  const el = $("#g-saved");
  if (!el) return;
  el.textContent = { saving: "Saving…", saved: "Saved", err: "Not saved", synced: "Saved · in your Sheet ✓" }[state];
  el.className = `g-saved${state === "err" ? " err" : ""}`;
  el.title = detail || (state === "saved" ? "Saved in the app; your Google Sheet follows within a minute" : "");
}

async function watchSheet(id) {
  const mine = ++syncWatch;
  for (let i = 0; i < SYNC_POLL_MAX && mine === syncWatch; i++) {
    await new Promise((r) => setTimeout(r, SYNC_POLL_MS));
    try {
      const s = await get("/api/tracker/sync", { app: id });
      if (mine !== syncWatch) return;
      const errors = Object.values(s.errors || {});
      if (errors.length) return saved("err", `Sheet sync: ${errors.join("; ")}`);
      if (s.state === "synced") return saved("synced", `In your Google Sheet (synced ${s.synced_at})`);
      if (s.state === "not_on_sheet") return saved("saved", s.why);
    } catch { /* tried again */ }
  }
}

function entry(label, undo, redo) {
  const e = { label, undo, redo };
  done.push(e);
  if (done.length > UNDO_MAX) done.shift();
  again.length = 0;
  return e;
}

// cells: [{id, field, value}]; the page shows them at once and puts a row back if the server refuses it
export function write(cells, { label = "edit", undoing = false } = {}) {
  const olds = new Map();
  const moved = new Set(cells.filter((x) => x.field === "status").map((x) => x.id));
  const show = (row) => { if (!moved.has(row.id)) return putRow(row); left.set(row.id, row); return showStatus(row); };
  for (const x of cells) {
    const row = T.rows.find((r) => r.id === x.id) || (moved.has(x.id) && T.gone[x.id] && left.get(x.id));
    if (!row) continue;
    if (!olds.has(x.id)) olds.set(x.id, row);
    show(patchRow(row, x.field, x.value));
  }
  if (!olds.size) return Promise.resolve();
  saved("saving");
  const edits = [...olds.keys()].map((app) => ({ app, changes: Object.fromEntries(cells.filter((x) => x.id === app).map((x) => [x.field, x.value])) }));
  const run = chain.then(async () => {
    const out = await api("/api/tracker/update_many", { edits, undo: undoing });
    for (const row of out.rows) show(row);
    for (const e of out.errors) {
      if (olds.has(e.app)) show(olds.get(e.app));
      toast(`Not saved (${olds.get(e.app)?.company || `row ${e.app}`}): ${e.error}`, { error: true });
    }
    if (!undoing && out.before.length) {
      const after = new Map(cells.map((x) => [`${x.id}|${x.field}`, x.value]));
      const back = out.before.map((b) => ({ id: b.app, field: b.field, value: b.value ?? "" }));
      const fwd = out.before.map((b) => ({ id: b.app, field: b.field, value: after.get(`${b.app}|${b.field}`) ?? "" }));
      entry(label, () => write(back, { undoing: true }), () => write(fwd, { undoing: true }));
    }
    saved(out.errors.length ? "err" : "saved", out.errors.map((e) => e.error).join("; "));
    if (out.rows[0]) watchSheet(out.rows[0].id);
  }).catch((err) => {
    for (const row of olds.values()) show(row);
    saved("err", err.message);
    toast(`Not saved: ${err.message}. Put back as it was.`, { error: true });
  });
  chain = run;
  return run;
}

async function step(from, to, verb) {
  editing()?.cancel();
  const e = from.pop();
  if (!e) { toast(`Nothing to ${verb.toLowerCase()}`, { ms: 2000 }); return; }
  try { await e[verb === "Undo" ? "undo" : "redo"](); to.push(e); toast(`${verb === "Undo" ? "Undone" : "Redone"}: ${e.label}`, { ms: 2500 }); }
  catch (err) { from.push(e); fail(err); }
}
export const undo = () => step(done, again, "Undo");
export const redo = () => step(again, done, "Redo");

// the selection's cells -> [{row, c}]
function cellsIn([r0, r1, c0, c1]) {
  const out = [];
  for (let i = r0; i <= r1; i++) if (isRow(i)) for (let j = c0; j <= c1; j++) out.push({ row: G.view[i], c: G.cols[j] });
  return out;
}

export function clearSel(label = "Clear") {
  const cells = cellsIn(range()).filter(({ row, c }) => c.edit && c.kind !== "status" && text(row, c) !== "");
  if (cells.length) write(cells.map(({ row, c }) => ({ id: row.id, field: fieldOf(c), value: "" })), { label: `${label} (${cells.length} cells)` });
}

// ---- clipboard (TSV, quoted like Google Sheets: a cell with a tab, a newline or a quote is "..." with "" for ") ----
const quote = (s) => (/[\t\n\r"]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
export const toTsv = (rows) => rows.map((r) => r.map((v) => quote(String(v ?? ""))).join("\t")).join("\n");
export function parseTsv(t) {
  const rows = [[]];
  let cell = "", quoted = false;
  for (let i = 0; i < t.length; i++) {
    const ch = t[i];
    if (quoted) { if (ch !== '"') cell += ch; else if (t[i + 1] === '"') { cell += '"'; i++; } else quoted = false; }
    else if (ch === '"' && cell === "") quoted = true;
    else if (ch === "\t") { rows.at(-1).push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") { if (ch === "\r" && t[i + 1] === "\n") i++; rows.at(-1).push(cell); cell = ""; rows.push([]); }
    else cell += ch;
  }
  rows.at(-1).push(cell);
  if (rows.length > 1 && rows.at(-1).length === 1 && rows.at(-1)[0] === "") rows.pop();
  return rows;
}

async function selTsv(full) {
  const [r0, r1, c0, c1] = range();
  const rows = [];
  for (let i = r0; i <= r1; i++) {
    if (!isRow(i)) continue;
    const row = G.view[i], cols = G.cols.slice(c0, c1 + 1);
    const got = full && cols.some((c) => clipped(row, c)) ? await get("/api/tracker/row", { app: row.id }) : null;
    rows.push(cols.map((c) => (got && clipped(row, c) ? (c.field ? got[c.field] : got.sheet?.cells[c.idx]) ?? "" : text(row, c))));
  }
  return toTsv(rows);
}

export function onClip(e) {
  if (editing() || e.target !== G.wrap) return;
  e.preventDefault();
  if (e.type === "paste") { pasteText(e.clipboardData.getData("text/plain")); return; }
  const rows = [];
  const [r0, r1, c0, c1] = range();
  for (let i = r0; i <= r1; i++) if (isRow(i)) rows.push(G.cols.slice(c0, c1 + 1).map((c) => text(G.view[i], c)));
  e.clipboardData.setData("text/plain", toTsv(rows));
  if (cellsIn(range()).some(({ row, c }) => clipped(row, c)) && navigator.clipboard) {
    selTsv(true).then((t) => navigator.clipboard.writeText(t)).catch(() => toast("Long cells were copied cut short", { error: true }));
  }
  if (e.type === "cut") clearSel("Cut");
}

const fit = (c, v) => (c.kind === "date" ? toDate(v) : c.kind === "list" || c.kind === "status" ? listKey(c, v.trim()) : v);

export function pasteText(t) {
  const block = parseTsv(t.replace(/\r?\n$/, ""));
  const [r0, r1, c0, c1] = range();
  const one = block.length === 1 && block[0].length === 1;
  const height = one ? r1 - r0 + 1 : block.length, width = one ? c1 - c0 + 1 : Math.max(...block.map((r) => r.length));
  const rows = [];
  for (let i = r0; i < G.view.length && rows.length < height; i++) if (isRow(i)) rows.push(i);
  const cells = [];
  let locked = 0;
  rows.forEach((i, y) => {
    for (let x = 0; x < width; x++) {
      const c = G.cols[c0 + x];
      if (!c) break;
      if (!c.edit) { locked++; continue; }
      cells.push({ id: G.view[i].id, field: fieldOf(c), value: fit(c, one ? block[0][0] : block[y]?.[x] ?? "") });
    }
  });
  write(cells, { label: `Paste (${cells.length} cells)` });
  G.ar = r0; G.ac = c0; G.r = rows.at(-1) ?? r0; G.c = Math.min(G.cols.length - 1, c0 + width - 1);
  paintSel();
  const past = height - rows.length;
  if (past > 0) toast(`${past} row${past === 1 ? "" : "s"} below the last row not pasted: insert rows first (right-click → Insert row)`, { error: true });
  if (locked) toast(`${locked} cell${locked === 1 ? "" : "s"} the app fills in left as they are`, { ms: 3000 });
}

// ---- fill handle: copies the selection down; dates and number steps continue ----
export function series(vals, n) {
  const dates = vals.map(parseDate);
  if (vals.length && dates.every(Boolean)) {
    const days = vals.length > 1 ? Math.round((dates.at(-1) - dates.at(-2)) / DAY_MS) : 1;
    return Array.from({ length: n }, (_, k) => { const d = new Date(dates.at(-1)); d.setDate(d.getDate() + (k + 1) * days); return dateText(d); });
  }
  const nums = vals.map((v) => (/^-?\d+(\.\d+)?$/.test(v.trim()) ? Number(v) : null));
  if (vals.length > 1 && nums.every((x) => x !== null)) {
    const d = nums.at(-1) - nums.at(-2);
    return Array.from({ length: n }, (_, k) => String(nums.at(-1) + (k + 1) * d));
  }
  return Array.from({ length: n }, (_, k) => vals[k % vals.length]);
}

export function fillDrag(e) {
  e.preventDefault();
  e.stopPropagation();
  const [r0, r1, c0, c1] = range();
  let to = r1;
  G.wrap.classList.add("g-fill");
  const move = (ev) => {
    const i = rowAtY(ev.clientY);
    if (i <= r1 || i === to) return;
    to = i;
    Object.assign(G, { ar: r0, r: to, ac: c0, c: c1 });
    paintSel();
  };
  addEventListener("pointermove", move);
  addEventListener("pointerup", (ev) => {
    move(ev);                                                    // the last move can be coalesced into the release
    removeEventListener("pointermove", move);
    G.wrap.classList.remove("g-fill");
    const src = [], dst = [];
    for (let i = r0; i <= to; i++) if (isRow(i)) (i <= r1 ? src : dst).push(i);
    const cells = [];
    for (let j = c0; j <= c1; j++) {
      const c = G.cols[j];
      if (!c.edit || c.kind === "status") continue;
      const vals = series(src.map((i) => text(G.view[i], c)), dst.length);
      dst.forEach((i, k) => cells.push({ id: G.view[i].id, field: fieldOf(c), value: fit(c, vals[k]) }));
    }
    if (cells.length) write(cells, { label: `Fill (${cells.length} cells)` });
  }, { once: true });
}

// ---- rows ----
const TAB_STATUS = { applied: "applied", interviewing: "screen" };

export async function insertRow(below) {
  const at = curRow();
  try {
    const row = await api("/api/tracker/insert", { status: TAB_STATUS[S.tab] || "applied" });
    const i = at ? T.rows.findIndex((r) => r.id === at.id) + (below ? 1 : 0) : 0;
    T.rows = [...T.rows.slice(0, i), row, ...T.rows.slice(i)];
    T.total += 1;
    refresh(false);
    const k = G.view.findIndex((r) => r.id === row.id);
    if (k >= 0) go(k, 0);
    let rid = null;
    entry("Insert row", async () => { rid = (await api("/api/remove", { app: row.id })).removed; dropIds([row.id]); refresh(); },
      async () => { await api("/api/remove/undo", { id: rid }); T.rows = [row, ...T.rows]; T.total += 1; refresh(); });
    toast("Row inserted: type into it (it reaches your Sheet once it names a company or a role)", { ms: 4000 });
  } catch (err) { fail(err); }
}

export async function deleteRows(rows = selRows()) {
  if (!rows.length) return;
  const what = rows.length === 1 ? `${rows[0].company || "(company)"} · ${rows[0].role_title || "(role)"}` : `${rows.length} rows`;
  if (!(await ask(`Delete ${what}?`, "It is removed here and from your Google Sheet. You can Undo.", "Delete"))) return;
  const gone = [];
  try { for (const r of rows) gone.push({ row: r, id: (await api("/api/remove", { app: r.id, why: r.dup_of ? "duplicate" : "unwanted" })).removed }); }
  catch (err) { fail(err); }
  if (!gone.length) return;
  dropIds(gone.map((g) => g.row.id));
  refresh();
  const back = async () => {
    for (const g of gone) await api("/api/remove/undo", { id: g.id });
    T.rows = [...gone.map((g) => g.row), ...T.rows];
    T.total += gone.length;
    refresh();
  };
  const redoIt = async () => { for (const g of gone) g.id = (await api("/api/remove", { app: g.row.id, why: g.row.dup_of ? "duplicate" : "unwanted" })).removed; dropIds(gone.map((g) => g.row.id)); refresh(); };
  const e = entry(`Delete ${what}`, back, redoIt);
  toast(`Deleted ${what}`, { ms: UNDO_TOAST_MS, undo: () => { const k = done.indexOf(e); if (k >= 0) { done.splice(k, 1); back().then(() => again.push(e), fail); } } });
}
