// Grid menus: a column's sort / filter by values or condition / hide, the find bar (⌘F), the right-click
// menu (insert / delete rows, the job's link, JD, resume), ⌘-click on a link, and the optional view grouped by day.
import { $, B, S, closePop, copyText, fail, get, h, menu, muted, openExt, openPop, opt, still, titled, toast } from "./core.js";
import { T, moveTo, preApply } from "./tracker.js";
import { G, build, columns, curRow, focusGrid, go, openRow, paintSel, prefs, refresh, savePrefs, selRows, setGrouped, text } from "./grid.js";
import { parseDate } from "./gridedit.js";
import { deleteRows, insertRow } from "./gridops.js";

const BLANK = "(Blanks)";
const DAY_MS = 86400000;
const OLD_DAYS = 30;
const OLD = `More than ${OLD_DAYS} days`;
const CONDS = [["empty", "Is empty"], ["filled", "Is not empty"], ["has", "Text contains"], ["not", "Text does not contain"],
  ["is", "Text is exactly"], ["before", "Date is before"], ["after", "Date is after"]];
const colOf = (key) => columns().find((c) => c.key === key);

function passes(row, key, f) {
  const c = colOf(key);
  if (!c) return true;
  const t = text(row, c).trim(), low = t.toLowerCase(), v = (f.text || "").trim().toLowerCase();
  if (f.values) return f.values.includes(t || BLANK);
  const d = parseDate(t), at = parseDate(f.text);
  return { empty: !t, filled: !!t, has: low.includes(v), not: !low.includes(v), is: low === v,
    before: !!(d && at && d < at), after: !!(d && at && d > at) }[f.op] ?? true;
}

function dayHead(iso, today) {
  if (!iso) return OLD;
  const d = new Date(`${iso}T12:00:00`), days = Math.round((today - d) / DAY_MS);
  if (days > OLD_DAYS) return OLD;
  const name = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  return days === 0 ? `Today · ${name}` : days === 1 ? `Yesterday · ${name}` : name;
}

function sorted(rows) {
  const c = G.sort && colOf(G.sort.key);
  if (!c) return rows;
  const key = (r) => { const t = text(r, c).trim(); const d = parseDate(t); return d ? String(d.getTime()).padStart(15, "0") : t.toLowerCase(); };
  return [...rows].sort((a, b) => { const x = key(a), y = key(b); return (x === "") - (y === "") || (x < y ? -1 : x > y ? 1 : 0) * G.sort.dir; });
}

// T.rows -> G.view: filtered, sorted, numbered; grouped by the day applied when that view is on
export function applyView() {
  const rows = sorted(T.rows.filter((r) => Object.entries(G.filters).every(([k, f]) => passes(r, k, f)))).map((r, i) => ({ ...r, n: i + 1 }));
  if (!G.grouped) { G.view = rows; return; }
  const today = new Date(`${new Date().toLocaleDateString("en-CA")}T12:00:00`);
  const groups = new Map();
  for (const r of rows) { const k = dayHead(r.applied_on, today); groups.set(k, [...(groups.get(k) || []), r]); }
  const order = [...groups.keys()].filter((k) => k !== OLD).concat(groups.has(OLD) ? [OLD] : []);
  G.view = order.flatMap((k) => [{ head: `${k} (${groups.get(k).length})` }, ...groups.get(k).map((r, i) => ({ ...r, n: i + 1 }))]);
}

const redraw = (keep = true) => { closePop({ refocus: false }); refresh(keep); focusGrid(); };

export function headMenu(c, anchor) {
  const sortBy = (dir) => { G.sort = { key: c.key, dir }; redraw(); };
  openPop(menu(h("p", "pop-h", c.label),
    opt("Sort A → Z", () => sortBy(1), G.sort?.key === c.key && G.sort.dir > 0),
    opt("Sort Z → A", () => sortBy(-1), G.sort?.key === c.key && G.sort.dir < 0),
    opt("Filter by values…", () => setTimeout(() => valuesPop(c, anchor))),
    opt("Filter by condition…", () => setTimeout(() => condPop(c, anchor))),
    G.filters[c.key] ? opt("Clear this filter", () => { const { [c.key]: _, ...rest } = G.filters; G.filters = rest; redraw(); }) : null,
    opt("Hide column", () => { const p = prefs(); savePrefs({ ...p, hidden: [...p.hidden, c.key] }); redraw(); })), anchor, `${c.label} column`);
}

const okRow = (ok) => h("div", "g-row2", B({ class: "btn ghost", onclick: () => closePop() }, "Cancel"), B({ class: "btn primary", onclick: ok }, "OK"));

function valuesPop(c, anchor) {
  const counts = new Map();
  for (const r of T.rows) { const t = text(r, c).trim() || BLANK; counts.set(t, (counts.get(t) || 0) + 1); }
  const now = G.filters[c.key]?.values;
  const boxes = [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([v, n]) => {
    const box = h("input", { type: "checkbox", value: v, "aria-label": v, title: `Show the rows with ${v || "(blank)"}` });
    box.checked = !now || now.includes(v);
    return h("label", { "data-v": v.toLowerCase() }, box, h("span", {}, v), muted(` ${n}`));
  });
  const search = h("input", { type: "search", placeholder: "Search values", "aria-label": "Search values" });
  search.oninput = () => { for (const b of boxes) b.hidden = !b.dataset.v.includes(search.value.trim().toLowerCase()); };
  const all = (on) => { for (const b of boxes) if (!b.hidden) b.firstChild.checked = on; };
  const ok = () => {
    const values = boxes.filter((b) => b.firstChild.checked).map((b) => b.firstChild.value);
    const { [c.key]: _, ...rest } = G.filters;
    G.filters = values.length === boxes.length ? rest : { ...rest, [c.key]: { values } };
    redraw(false);
  };
  openPop(h("div", { class: "pop-body g-vals", onkeydown: (e) => { if (e.key === "Enter" && e.target.type !== "checkbox") ok(); } },
    h("p", "pop-h", `Filter ${c.label} by values`), search,
    h("div", "g-row2", B({ class: "mini", onclick: () => all(true) }, "Select all"), B({ class: "mini", onclick: () => all(false) }, "Clear")),
    h("div", "g-boxes", boxes), okRow(ok)), anchor, `Filter ${c.label}`);
}

function condPop(c, anchor) {
  const now = G.filters[c.key];
  const sel = h("select", { "aria-label": "Condition" }, CONDS.map(([k, l]) => h("option", { value: k }, l)));
  sel.value = now?.op || "has";
  const val = h("input", { type: "text", "aria-label": "Value", placeholder: "Value (dates: 10/12/2026)" });
  val.value = now?.text || "";
  const ok = () => { G.filters = { ...G.filters, [c.key]: { op: sel.value, text: val.value } }; redraw(false); };
  openPop(h("div", { class: "pop-body g-vals", onkeydown: (e) => { if (e.key === "Enter") ok(); } },
    h("p", "pop-h", `Filter ${c.label} by condition`), sel, val, okRow(ok)), anchor, `Filter ${c.label}`);
}

// the line under the toolbar: sort and filters shown and clearable, the day view, the find bar
export function stateLine() {
  const line = $("#g-state");
  if (!line) return;
  const names = Object.keys(G.filters).map((k) => colOf(k)?.label || k);
  const sortC = G.sort && colOf(G.sort.key);
  const days = h("input", { type: "checkbox", title: "Group the rows by the day you applied", onchange: (e) => setGrouped(e.target.checked) });
  days.checked = G.grouped;
  line.replaceChildren(...[
    sortC ? h("span", {}, `Sorted by ${sortC.label} ${G.sort.dir > 0 ? "A → Z" : "Z → A"} `, B({ class: "mini", title: "Back to the tracker's order", onclick: () => { G.sort = null; redraw(); } }, "×")) : null,
    names.length ? h("span", {}, `Filtered: ${names.join(", ")} (${G.view.filter((r) => !r.head).length} of ${T.rows.length}) `,
      B({ class: "mini", title: "Show every row", onclick: () => { G.filters = {}; redraw(false); } }, "Clear filters")) : null,
    h("label", { class: "check", title: "Group the rows by the day you applied" }, days, " Group by day"),
    G.hits ? findBar() : null].filter(Boolean));
}

function hitsFor(q) {
  const list = [];
  if (q) G.view.forEach((r, i) => { if (!r.head) G.cols.forEach((c, j) => { if (text(r, c).toLowerCase().includes(q)) list.push([i, j]); }); });
  return list;
}

function findBar() {
  const input = h("input", { type: "search", id: "g-find-q", placeholder: "Find in the tracker", "aria-label": "Find in the tracker" });
  input.value = G.hits.q;
  const n = h("span", { class: "muted", id: "g-find-n", role: "status" });
  input.oninput = () => { G.hits = { q: input.value, list: hitsFor(input.value.trim().toLowerCase()), at: 0 }; show(); };
  input.onkeydown = (e) => {
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); findKey(e.shiftKey ? "prev" : "next"); }
    if (e.key === "Escape") { e.preventDefault(); findKey("close"); }
  };
  setTimeout(() => show(true));
  return h("span", "g-find", input, n,
    B({ class: "mini", title: "Previous (Shift+Enter)", onclick: () => findKey("prev") }, "↑"),
    B({ class: "mini", title: "Next (Enter)", onclick: () => findKey("next") }, "↓"),
    B({ class: "mini", title: "Close (Esc)", onclick: () => findKey("close") }, "×"));
}

function show(quiet) {
  const hit = G.hits?.list[G.hits.at];
  const n = $("#g-find-n");
  if (n) n.textContent = G.hits.list.length ? `${G.hits.at + 1} of ${G.hits.list.length}` : G.hits.q ? "0 found" : "";
  if (hit && !quiet) { go(hit[0], hit[1]); $("#g-find-q")?.focus(); } else paintSel();
}

export function findKey(what) {
  if (what === "open") {
    G.hits ||= { q: "", list: [], at: 0 };
    stateLine();
    $("#g-find-q")?.focus();
    $("#g-find-q")?.select();
  } else if (what === "close") {
    G.hits = null;
    stateLine();
    paintSel();
    focusGrid();
  } else if (G.hits?.list.length) {
    const k = G.hits.list.length;
    G.hits.at = (G.hits.at + (what === "prev" ? k - 1 : 1)) % k;
    show();
  }
}

// ⌘-click: a link, the resume file in Drive, a URL typed in a cell
export function openCell(i, j) {
  const row = G.view[i], c = G.cols[j];
  if (!row || row.head || !c) return;
  if (c.kind === "resume") { openResume(row); return; }
  const url = c.field === "jd" ? row.job_url : (text(row, c).match(/https?:\/\/\S+/) || [])[0];
  if (url) openExt(url);
}

async function openResume(row) {
  try {
    const full = await get("/api/tracker/row", { app: row.id });
    const link = full.uploads?.[0]?.link || (String(full.resume || "").match(/https?:\/\/\S+/) || [])[0];
    if (link) openExt(link); else toast("No resume file on this row yet", { ms: 2500 });
  } catch (err) { fail(err); }
}

// right-click: a menu at the pointer
export function rowMenu(e) {
  const row = curRow();
  const rows = selRows();
  const spot = h("span", { class: "g-spot", "aria-hidden": "true" });
  Object.assign(spot.style, { position: "fixed", left: `${e.clientX}px`, top: `${e.clientY}px`, width: "1px", height: "1px" });
  document.body.append(spot);
  const job = row && (row.job_url || row.apply_url);
  const item = (text, tip, run) => titled(opt(text, () => { still(G.wrap); run(); }), tip);   // focus comes back to the grid
  openPop(menu(
    item("Insert row above", "A blank row above this one", () => insertRow(false)), item("Insert row below", "A blank row below this one", () => insertRow(true)),
    item(rows.length > 1 ? `Delete ${rows.length} rows…` : "Delete row…", "Delete here and from your Google Sheet (asks first; Undo)", () => deleteRows(rows)),
    job ? item("Copy link to the job", "Copy the posting's link", () => copyText(job, "Job link copied")) : null,
    row ? item("Open JD", "The saved job description in a new tab", () => window.open(`/jd?profile=${encodeURIComponent(S.profile)}&app=${row.id}`, "_blank", "noopener")) : null,
    row?.has_resume ? item("Open resume", "The resume on this row", () => openResume(row)) : null,
    row ? item("Open row editor", "Every field of this row in the side panel", () => openRow(G.r)) : null,
    row && preApply(row) ? item("Mark applied…", "Mark it applied and attach the resume you sent", () => moveTo(row, "applied")) : null), spot, "Row");
  const gone = () => { if ($("#pop").hidden) { spot.remove(); if (!document.activeElement || document.activeElement === document.body) still(G.wrap); } else setTimeout(gone, 300); };
  setTimeout(gone, 300);
}

export function columnsPop(anchor) {
  const boxes = columns().map((c) => {
    const box = h("input", { type: "checkbox", id: `col-${c.key.replace(/[^\w-]/g, "_")}`, title: `Show or hide ${c.label}` });
    box.checked = !c.hidden;
    box.onchange = () => {
      const p = prefs();
      savePrefs({ ...p, hidden: box.checked ? p.hidden.filter((k) => k !== c.key) : [...p.hidden, c.key] });
      build();
    };
    return h("label", { class: "check", for: box.id }, box, h("span", {}, c.label), c.group === "app" ? muted(" · app") : null);
  });
  openPop(h("div", { class: "pop-body cols", onkeydown: (e) => { if (e.key === "Escape") { e.stopPropagation(); closePop(); } } },
    h("p", "pop-h", "Show columns"), boxes,
    B({ class: "opt", title: "Show every column at its default width", onclick: () => { savePrefs({ hidden: [], widths: {} }); build(); } }, "Reset columns and widths"),
    h("p", "pop-note", `Remembered for ${S.profile} on this device. Drag a header's right edge to resize.`)), anchor, "Columns");
}
