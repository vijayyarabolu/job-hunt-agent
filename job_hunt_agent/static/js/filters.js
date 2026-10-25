// Filter chips (popovers); kept per person until reload.

import { $, $$, B, EMPLOYMENTS, FAMILY_LABELS, S, STATES, WORKPLACES, h, openPop, opt, phone, popOpen, sm, toast, still } from "./core.js";
import { sponsView } from "./sponsor.js";

const POSTED = ["", "1h", "6h", "24h", "3d", "7d", "30d"];
let boards = [];
export const setBoards = (b) => { boards = b || []; };
const DEFS = [
  { key: "family", label: "Role", tip: "filter by job family", opts: Object.entries(FAMILY_LABELS).filter(([k]) => k !== "other") },
  { key: "state", label: "Location", tip: "filter by US state", opts: Object.values(STATES).map((s) => [s, s]), grid: true },
  { key: "workplace", label: "Workplace", tip: "remote, hybrid or onsite", opts: WORKPLACES },
  { key: "min_match", label: "Match", tip: "minimum match %", opts: ["50", "60", "70", "80", "90"].map((v) => [v, `${v}%+`]) },
  { key: "salary", label: "Posted pay", tip: "minimum posted pay", opts: [80, 100, 120, 150, 180, 200].map((k) => [String(k * 1000), `$${k}k+`]),
    note: "Jobs without posted pay stay visible." },
  { key: "board", label: "Job board", tip: "filter by job board", multi: true, get opts() { return boards.map((b) => [b.id, b.label, b.n]); } },
  { key: "level", label: "Experience asked", more: true, opts: [["le2", "Up to 2 yrs (or not stated)"],
    ["3to5", "3 to 5 yrs"], ["6plus", "6+ yrs"]] },
  { key: "size", label: "Company size", more: true, opts: [["big", "Big company"], ["startup", "Startup"],
    ["other", "Mid-size / enterprise"], ["public_sector", "Public sector"]] },
  { key: "employment", label: "Type", more: true, opts: EMPLOYMENTS },
  { key: "senior", label: "Show too senior", more: true, toggle: true, tip: "Include jobs asking more years than your cap" },
  { key: "hide_applied", label: "Hide applied", more: true, toggle: true, tip: "Hide jobs you already applied to" },
  { key: "dupes", label: "Duplicates only", more: true, toggle: true, tip: "Only the older copies of a job posted twice (× removes a copy)" },
];
const MORE_TIP = "More filters: experience asked, company size, job type";
const made = { idle: new Map(), active: new Map(), posted: null, more: null };
let changed = () => {};

const kept = {};
export function applyProfileFilters() {
  S.filters = kept[S.profile] || {};
  renderFilters();
}
// GOAL35 §1: a person's filters as the address names them (boot, back / forward)
export function restoreFilters(pid, filters) {
  kept[pid] = Object.fromEntries(Object.entries(filters || {}).filter(([, v]) => v));
  if (pid === S.profile) applyProfileFilters();
}
export const filterParams = () => ({ ...S.filters, ...(S.tab === "recommended" && sponsView() ? { spons: sponsView() } : {}) });

export function dropDupes() {
  if (!S.filters?.dupes) return;
  const { dupes, ...rest } = S.filters;
  S.filters = kept[S.profile] = rest;
  renderFilters();
}

export function setFilters(next, note, key) {
  const before = S.filters;
  S.filters = Object.fromEntries(Object.entries(next).filter(([, v]) => v));
  kept[S.profile] = S.filters;
  const had = document.activeElement;
  renderFilters();
  if (popOpen()) paintTicks(); else if (had?.isConnected && $("#fchips").contains(had)) still(had); else focusChip(key);
  changed();
  if (note) toast(note, { undo: () => setFilters(before) });
}
const setOne = (key, value, note) => setFilters({ ...S.filters, [key]: value }, note, key);
const picked = (key) => (S.filters[key] || "").split(",").filter(Boolean);
const is = (key, v) => (key === "board" && v ? picked(key).includes(v) : (S.filters[key] || "") === v);
const flip = (key, v) => (is(key, v) ? picked(key).filter((x) => x !== v) : [...picked(key), v]).join(",");

const textOf = (d, v) => (d.toggle ? d.label : `${d.label}: ${v.split(",").map((x) => (d.opts.find(([k]) => k === x) || [, x])[1]).join(", ")}`);
const titled = (el, title) => { el.title = title; return el; };
const focusChip = (key) => [made.active.get(key)?.firstChild, made.idle.get(key), DEFS.find((d) => d.key === key)?.more ? made.more : null]
  .find((el) => el?.isConnected)?.focus({ preventScroll: true });
const paintTicks = () => { for (const el of $$("#pop [data-k]")) el.setAttribute("aria-pressed", String(is(el.dataset.k, el.dataset.v))); };

function choice(d, v, text, n) {
  const el = opt(n == null ? text : `${text} (${n.toLocaleString()})`, () => setOne(d.key, d.multi && v ? flip(d.key, v) : is(d.key, v) && v ? "" : v),
    is(d.key, v), { keep: d.more || d.multi });
  Object.assign(el.dataset, { k: d.key, v });
  if (n === 0) el.classList.add("muted");
  return titled(el, v ? `Only ${text}` : `Every ${d.label.toLowerCase()}`);
}
function toggle(d) {
  const el = opt(d.label, () => setOne(d.key, is(d.key, "1") ? "" : "1"), is(d.key, "1"), { keep: true });
  Object.assign(el.dataset, { k: d.key, v: "1" });
  return titled(el, d.tip);
}
const choices = (d) => [h("p", "pop-h", d.label), choice(d, "", "Any"),
  h("div", { class: `opts${d.grid ? " grid" : ""}`, role: "group", "aria-label": d.label }, d.opts.map(([v, t, n]) => choice(d, v, t, n)))];
const popFor = (d, anchor) => openPop(h("div", "pop-body", choices(d), d.note ? h("p", "pop-note", d.note) : null), anchor, d.label);
const morePop = (anchor) => openPop(h("div", "pop-body", DEFS.filter((d) => d.more).map((d) => (d.toggle ? toggle(d) : choices(d)))),
  anchor, "More filters");

const chipBtn = (label, onclick, title) => B({ class: "chip", "aria-haspopup": "dialog", "aria-expanded": "false", title, onclick },
  label, sm("down"));
const idleChip = (d) => made.idle.get(d.key) || made.idle.set(d.key, chipBtn(d.label, (e) => popFor(d, e.currentTarget), `${d.label}: ${d.tip}`)).get(d.key);
function activeChip(d) {
  const chip = made.active.get(d.key) || made.active.set(d.key, h("span", "chip on",
    B({ class: "chip-body", "aria-haspopup": "dialog", "aria-expanded": "false", onclick: (e) => (d.more ? morePop(e.currentTarget) : popFor(d, e.currentTarget)) }),
    B({ class: "chip-x", onclick: () => setOne(d.key, "", `Removed “${textOf(d, S.filters[d.key])}”`) }, sm("x")))).get(d.key);
  const text = textOf(d, S.filters[d.key]);
  const [body, x] = chip.children;
  body.textContent = text;
  body.title = chip.title = `${text} (change)`;
  x.setAttribute("aria-label", `Remove filter ${text}`);
  x.title = `Remove ${text}`;
  return chip;
}
const postedSeg = () => made.posted || (made.posted = h("div", { class: "seg", role: "group", "aria-label": "Posted within" }, h("span", "seg-l", "Posted"),
  POSTED.map((v) => B({ "data-v": v, title: v ? `Posted in the last ${v}` : "Any time", onclick: () => setOne("posted", is("posted", v) ? "" : v) }, v || "Any"))));
const moreChip = () => made.more || (made.more = chipBtn("More", (e) => morePop(e.currentTarget), MORE_TIP));

function renderFilters() {
  const on = Object.keys(S.filters).filter((k) => k !== "sort");
  const clear = on.length > 1 ? B({ class: "chip", title: "Remove every filter", onclick: () => setFilters({ sort: S.filters.sort }, "Cleared all filters") },
    "Clear all") : null;
  const sort = $("#sort");
  const inStrip = phone.matches;
  $("#fchips").replaceChildren(...[h("span", "active", DEFS.filter((d) => S.filters[d.key]).map(activeChip)), clear,
    ...DEFS.filter((d) => !d.more && !S.filters[d.key]).map(idleChip), postedSeg(), moreChip(), inStrip ? sort : null].filter(Boolean));
  if (!inStrip && sort.parentElement !== $("#filters")) $("#filters").append(sort);
  for (const b of $$("[data-v]", made.posted)) b.setAttribute("aria-pressed", String(is("posted", b.dataset.v)));
  for (const b of $$("#sort [data-v]")) b.setAttribute("aria-pressed", String(is("sort", b.dataset.v)));
}

export function initFilters(onChange) {
  changed = onChange;
  $("#sort").addEventListener("click", (e) => {
    const b = e.target.closest("[data-v]");
    if (b) setOne("sort", b.dataset.v);
  });
  phone.addEventListener("change", renderFilters);
}
