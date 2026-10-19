// Grid cell editors: text, long text, a list (type to filter, Enter picks), a date (picker or typed).
import { B, fail, get, h, isoToday, still, toast } from "./core.js";
import { T, moveTo, preApply } from "./tracker.js";
import { G, LISTS, cellAt, chipColor, clipped, fieldOf, go, isRow, paintSel, text, value } from "./grid.js";
import { write } from "./gridops.js";

const LIST_MAX = 40;
let ed = null;
export const editing = () => ed;
export const editKey = (k) => startEdit(k, false);

const pad = (n) => String(n).padStart(2, "0");
const fmt = (d) => `${pad(d.getMonth() + 1)}/${pad(d.getDate())}/${d.getFullYear()}`;
const WORDS = { today: 0, yesterday: -1, tomorrow: 1 };

// "10/12", "10/12/26", "2026-10-12", "today" -> MM/DD/YYYY; anything else stays as typed
export function toDate(s) {
  const t = String(s ?? "").trim().toLowerCase();
  if (!t) return "";
  if (t in WORDS) { const d = new Date(); d.setDate(d.getDate() + WORDS[t]); return fmt(d); }
  let m = t.match(/^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2}|\d{4}))?$/);
  let y, mo, da;
  if (m) [mo, da, y] = [+m[1], +m[2], m[3] ? (m[3].length === 2 ? 2000 + +m[3] : +m[3]) : new Date().getFullYear()];
  else if ((m = t.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) [y, mo, da] = [+m[1], +m[2], +m[3]];
  else return String(s).trim();
  const d = new Date(y, mo - 1, da);
  return d.getMonth() === mo - 1 && d.getDate() === da ? fmt(d) : String(s).trim();
}
export const parseDate = (s) => { const m = toDate(s).match(/^(\d\d)\/(\d\d)\/(\d{4})$/); return m ? new Date(+m[3], +m[1] - 1, +m[2]) : null; };
export const dateText = fmt;

// a typed list label -> its key ("Applied" -> "applied"); free text stays for the Sheet's own lists
export function listKey(c, v) {
  const fixed = LISTS[c.field];
  if (!fixed) return v;
  const t = String(v).trim().toLowerCase();
  return (fixed.find(([k, l]) => k === t || l.toLowerCase() === t) || [v])[0];
}

function options(c) {
  if (LISTS[c.field]) return LISTS[c.field];
  const n = new Map();
  for (const row of T.rows) { const t = text(row, c).trim(); if (t) n.set(t, (n.get(t) || 0) + 1); }
  return [...n.entries()].sort((a, b) => b[1] - a[1]).slice(0, LIST_MAX).map(([t]) => [t, t]);
}

export async function startEdit(typed, keep) {
  if (ed || !isRow(G.r)) return;
  const c = G.cols[G.c], row = G.view[G.r];
  if (!c?.edit) { toast(`${c?.label || "This cell"} is filled in by the app`, { ms: 2500 }); return; }
  let start = value(row, c);
  if (clipped(row, c)) {
    try { const full = await get("/api/tracker/row", { app: row.id }); start = c.field ? full[c.field] ?? "" : full.sheet?.cells[c.idx] ?? ""; }
    catch (err) { fail(err); return; }
  }
  open(row, c, keep ? String(start ?? "") : typed ?? "", String(start ?? ""));
}

function listBox(c, input, before, typed) {
  const list = h("div", { class: "g-list", role: "listbox" });
  let pick = 0;
  list.touched = typed;
  const paint = () => {
    const q = list.touched ? input.value.trim().toLowerCase() : "";
    const found = options(c).filter(([, l]) => !q || l.toLowerCase().includes(q));
    if (!list.touched) pick = Math.max(0, found.findIndex(([k, l]) => k === before || l === before));
    pick = Math.min(pick, Math.max(0, found.length - 1));
    list.replaceChildren(...found.map(([k, l], i) => {
      const chip = h("span", c.kind === "list" ? "g-chip" : `st st-${k}`, l);
      if (c.kind === "list") chip.style.background = chipColor(l);
      return B({ class: "g-opt", role: "option", title: `Pick ${l}`, "aria-selected": String(i === pick), "data-k": k, onmousedown: (e) => { e.preventDefault(); list.done(k); } }, chip);
    }));
  };
  input.addEventListener("input", () => { list.touched = true; pick = 0; paint(); });
  list.move = (step) => {
    const opts = [...list.children];
    list.touched = true;
    pick = Math.max(0, Math.min(opts.length - 1, pick + step));
    opts.forEach((o, i) => o.setAttribute("aria-selected", String(i === pick)));
    opts[pick]?.scrollIntoView({ block: "nearest" });
  };
  list.value = () => (list.touched ? list.querySelector('[aria-selected="true"]')?.dataset.k ?? input.value : input.value);
  paint();
  return list;
}

function open(row, c, initial, before) {
  const td = cellAt(G.r, G.c);
  if (!td) return;
  const long = c.kind === "long" || initial.includes("\n") || initial.length > 80;
  const tip = `${c.label}: Enter saves, Esc cancels${long ? ", Shift+Enter is a new line" : ", Tab saves and moves right"}`;
  const input = long ? h("textarea", { "aria-label": c.label, title: tip }) : h("input", { type: "text", "aria-label": c.label, autocomplete: "off", title: tip });
  input.value = initial;
  const box = h("div", { class: "g-ed", role: "dialog", "aria-label": `Edit ${c.label}` }, input);
  box.style.left = `${c.fz ? G.wrap.scrollLeft + c.fz : td.offsetLeft}px`;
  box.style.top = `${td.offsetTop}px`;
  box.style.minWidth = `${Math.max(td.offsetWidth, long ? 380 : 0)}px`;
  const list = c.kind === "list" || c.kind === "status" ? listBox(c, input, before, initial !== before) : null;
  if (list) box.append(list, h("div", "g-hint", LISTS[c.field] ? "Type to filter · Enter picks" : "Type to filter, or a new value · Enter picks"));
  if (c.kind === "date") {
    const cal = h("input", { type: "date", "aria-label": "Pick a date", title: "Pick a date" });
    const at = parseDate(initial || before);
    if (at) cal.value = `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`;
    cal.onchange = () => cal.value && finish(toDate(cal.value), 1, 0);
    box.append(h("div", "g-date", cal, B({ class: "mini", onmousedown: (e) => { e.preventDefault(); finish(toDate(isoToday()), 1, 0); } }, "Today")),
      h("div", "g-hint", "Type 10/12, 10/12/2026 or today"));
  }
  G.wrap.append(box);
  const close = () => { ed = null; box.remove(); paintSel(); };
  const cancel = () => { if (ed) { close(); still(G.wrap); } };
  function finish(raw, dr, dc) {
    if (!ed) return;
    let next = String(raw ?? "").trim();
    if (c.kind === "date") next = toDate(next);
    if (list) next = listKey(c, next);
    close();
    still(G.wrap);
    if (next !== before.trim()) {
      if (c.field === "status" && next === "applied" && preApply(row)) moveTo(row, "applied");
      else write([{ id: row.id, field: fieldOf(c), value: next }], { label: c.label });
    }
    if (dr || dc) go(G.r + dr, G.c + dc);
  }
  if (list) list.done = (k) => finish(k, 1, 0);
  const now = () => (list ? list.value() : input.value);
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    const k = e.key;
    if (k === "Escape") { e.preventDefault(); cancel(); }
    else if (list && (k === "ArrowDown" || k === "ArrowUp")) { e.preventDefault(); list.move(k === "ArrowDown" ? 1 : -1); }
    else if (k === "Enter" && !(long && (e.shiftKey || e.altKey))) { e.preventDefault(); finish(now(), long || !e.shiftKey ? 1 : -1, 0); }
    else if (k === "Tab") { e.preventDefault(); finish(now(), 0, e.shiftKey ? -1 : 1); }
  });
  box.addEventListener("focusout", (e) => { if (ed && !box.contains(e.relatedTarget)) finish(now(), 0, 0); });
  ed = { commit: () => finish(now(), 0, 0), cancel, box };
  paintSel();
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}
